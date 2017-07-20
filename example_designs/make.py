#!/usr/bin/env python3

import sys
import os
import argparse
import subprocess
import struct
import importlib

from litex.gen.fhdl import verilog
from litex.gen.fhdl.structure import _Fragment

from litex.build.tools import write_to_file
from litex.build.xilinx.common import *

from litex.soc.integration import cpu_interface

litesdcard_path = "../"
sys.path.append(litesdcard_path) # XXX


def autotype(s):
    if s == "True":
        return True
    elif s == "False":
        return False
    try:
        return int(s, 0)
    except ValueError:
        pass
    return s


def _import(default, name):
    return importlib.import_module(default + "." + name)


def _get_args():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
LiteSDCard - based on Migen.

This program builds and/or loads LiteSDCard components.
One or several actions can be specified:

clean           delete previous build(s).
build-rtl       build verilog rtl.
build-bitstream build-bitstream build FPGA bitstream.
build-csr-csv   save CSR map into CSV file.
build-csr-header save CSR map into C header file.
build-csr-python save CSR map into Python header file.

load-bitstream  load bitstream into volatile storage.

all             clean, build-csr-csv, build-csr-header, build-bitstream, load-bitstream.
""")

    parser.add_argument("-t", "--target", default="simple", help="Core type to build")
    parser.add_argument("-s", "--sub-target", default="", help="variant of the Core type to build")
    parser.add_argument("-p", "--platform", default=None, help="platform to build for")
    parser.add_argument("-Ot", "--target-option", default=[], nargs=2, action="append", help="set target-specific option")
    parser.add_argument("-Op", "--platform-option", default=[], nargs=2, action="append", help="set platform-specific option")
    parser.add_argument("-Ob", "--build-option", default=[], nargs=2, action="append", help="set build option")
    parser.add_argument("--csr_csv", default="./build/csr.csv", help="CSV file to save the CSR map into")
    parser.add_argument("--csr_header", default="../sdcard/software/generated/csr.h", help="C header file to save the CSR map into")
    parser.add_argument("--csr_python", default="../sdcard/software/generated/csr.py", help="Python header file to save the CSR map into")

    parser.add_argument("action", nargs="+", help="specify an action")

    return parser.parse_args()

if __name__ == "__main__":
    args = _get_args()

    # create top-level Core object
    target_module = _import("targets", args.target)
    if args.sub_target:
        top_class = getattr(target_module, args.sub_target)
    else:
        top_class = target_module.default_subtarget

    if args.platform is None:
        if hasattr(top_class, "default_platform"):
            platform_name = top_class.default_platform
        else:
            raise ValueError("Target has no default platform, specify a platform with -p your_platform")
    else:
        platform_name = args.platform
    platform_module = _import("platforms", platform_name)
    platform_kwargs = dict((k, autotype(v)) for k, v in args.platform_option)
    platform = platform_module.Platform(**platform_kwargs)

    build_name = top_class.__name__.lower() + "_" + platform_name
    top_kwargs = dict((k, autotype(v)) for k, v in args.target_option)
    soc = top_class(platform, **top_kwargs)
    soc.finalize()
    try:
        memory_regions = soc.get_memory_regions()
        csr_regions = soc.get_csr_regions()
    except:
        pass

    # decode actions
    action_list = ["clean", "build-csr-csv", "build-csr-header", "build-csr-python", "build-bitstream", "load-bitstream", "all"]
    actions = {k: False for k in action_list}
    for action in args.action:
        if action in actions:
            actions[action] = True
        else:
            print("Unknown action: "+action+". Valid actions are:")
            for a in action_list:
                print("  "+a)
            sys.exit(1)

    print("""
   _ _ _       ____  ____   ____              _
  | (_) |_ ___/ ___||  _ \ / ___|__ _ _ __ __| |
  | | | __/ _ \___ \| | | | |   / _` | '__/ _` |
  | | | ||  __/___) | |_| | |__| (_| | | | (_| |
  |_|_|\__\___|____/|____/ \____\__,_|_|  \__,_|


  A small footprint and configurable SDCard core
             powered by Migen

====== Building options: ======
Platform:  {}
Target:    {}
Subtarget: {}
System Clk: {} MHz
===============================""".format(
    platform_name,
    args.target,
    top_class.__name__,
    soc.clk_freq/1000000
    )
)

    # dependencies
    if actions["all"]:
        actions["build-csr-csv"] = True
        actions["build-csr-header"] = True
        actions["build-csr-python"] = True
        actions["build-bitstream"] = True
        actions["load-bitstream"] = True

    if actions["build-bitstream"]:
        actions["build-csr-csv"] = True
        actions["build-csr-header"] = True
        actions["build-csr-python"] = True

    if actions["clean"]:
        subprocess.call(["rm", "-rf", "build/*"])

    if actions["build-csr-csv"]:
        csr_csv = cpu_interface.get_csr_csv(csr_regions, soc.get_constants(), memory_regions)
        write_to_file(args.csr_csv, csr_csv)

    if actions["build-csr-header"]:
        csr_header = cpu_interface.get_csr_header(csr_regions, soc.get_constants(), with_access_functions=False)
        write_to_file(args.csr_header, csr_header)

    if actions["build-csr-python"] and hasattr(cpu_interface, "get_csr_python"):
        csr_python = cpu_interface.get_csr_python(csr_regions, soc.get_constants(), memory_regions)
        write_to_file(args.csr_python, csr_python)

    if actions["build-bitstream"]:
        build_kwargs = dict((k, autotype(v)) for k, v in args.build_option)
        vns = platform.build(soc, build_name=build_name, **build_kwargs)
        if hasattr(soc, "do_exit") and vns is not None:
            if hasattr(soc.do_exit, '__call__'):
                soc.do_exit(vns)

    if actions["load-bitstream"]:
        prog = platform.create_programmer()
        prog.load_bitstream("build/" + build_name + platform.bitstream_ext)
