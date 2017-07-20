#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ./soc.py --output-dir build --csr-csv build/csr.csv

import argparse
from fractions import Fraction

from litex.gen import *
from litex.soc.integration.builder import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.soc_sdram import *
from litex.soc.interconnect.csr import *
from litex.soc.interconnect import stream
from litex.soc.cores.uart import UARTWishboneBridge, RS232PHY
from litescope.core import LiteScopeAnalyzer
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from sdcard.phy.sdphy import SDPHY, SDCtrl
from sdcard.frontend.ram import RAMReader, RAMWriter, RAMWrAddr
from sdcard.core.downc import Stream32to8
from sdcard.core.upc import Stream8to32
from sdcard.core.clocker import SDClocker

class _CRG(Module):
    def __init__(self, platform, clk_freq):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys_ps = ClockDomain()

        f0 = 32*1000000
        clk32 = platform.request("clk32")
        clk32a = Signal()
        self.specials += Instance("IBUFG", i_I=clk32, o_O=clk32a)
        clk32b = Signal()
        self.specials += Instance("BUFIO2", p_DIVIDE=1,
                                  p_DIVIDE_BYPASS="TRUE", p_I_INVERT="FALSE",
                                  i_I=clk32a, o_DIVCLK=clk32b)
        f = Fraction(int(clk_freq), int(f0))
        n, m, p = f.denominator, f.numerator, 16
        assert f0/n*m == clk_freq
        pll_lckd = Signal()
        pll_fb = Signal()
        pll = Signal(6)
        self.specials.pll = Instance("PLL_ADV", p_SIM_DEVICE="SPARTAN6",
                                     p_BANDWIDTH="OPTIMIZED", p_COMPENSATION="INTERNAL",
                                     p_REF_JITTER=.01, p_CLK_FEEDBACK="CLKFBOUT",
                                     i_DADDR=0, i_DCLK=0, i_DEN=0, i_DI=0, i_DWE=0, i_RST=0, i_REL=0,
                                     p_DIVCLK_DIVIDE=1, p_CLKFBOUT_MULT=m*p//n, p_CLKFBOUT_PHASE=0.,
                                     i_CLKIN1=clk32b, i_CLKIN2=0, i_CLKINSEL=1,
                                     p_CLKIN1_PERIOD=1000000000/f0, p_CLKIN2_PERIOD=0.,
                                     i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb, o_LOCKED=pll_lckd,
                                     o_CLKOUT0=pll[0], p_CLKOUT0_DUTY_CYCLE=.5,
                                     o_CLKOUT1=pll[1], p_CLKOUT1_DUTY_CYCLE=.5,
                                     o_CLKOUT2=pll[2], p_CLKOUT2_DUTY_CYCLE=.5,
                                     o_CLKOUT3=pll[3], p_CLKOUT3_DUTY_CYCLE=.5,
                                     o_CLKOUT4=pll[4], p_CLKOUT4_DUTY_CYCLE=.5,
                                     o_CLKOUT5=pll[5], p_CLKOUT5_DUTY_CYCLE=.5,
                                     p_CLKOUT0_PHASE=0., p_CLKOUT0_DIVIDE=p//1,
                                     p_CLKOUT1_PHASE=0., p_CLKOUT1_DIVIDE=p//1,
                                     p_CLKOUT2_PHASE=0., p_CLKOUT2_DIVIDE=p//1,
                                     p_CLKOUT3_PHASE=0., p_CLKOUT3_DIVIDE=p//1,
                                     p_CLKOUT4_PHASE=0., p_CLKOUT4_DIVIDE=p//1,  # sys
                                     p_CLKOUT5_PHASE=270., p_CLKOUT5_DIVIDE=p//1,  # sys_ps
        )
        self.specials += Instance("BUFG", i_I=pll[4], o_O=self.cd_sys.clk)
        self.specials += Instance("BUFG", i_I=pll[5], o_O=self.cd_sys_ps.clk)
        self.specials += AsyncResetSynchronizer(self.cd_sys, ~pll_lckd)

        self.specials += Instance("ODDR2", p_DDR_ALIGNMENT="NONE",
                                  p_INIT=0, p_SRTYPE="SYNC",
                                  i_D0=0, i_D1=1, i_S=0, i_R=0, i_CE=1,
                                  i_C0=self.cd_sys.clk, i_C1=~self.cd_sys.clk,
                                  o_Q=platform.request("sdram_clock"))

class _CRGsys(Module, AutoCSR):
    def __init__(self, platform):
        self.clock_domains.cd_sys = ClockDomain()
        self.comb += [
            self.cd_sys.clk.eq(platform.request("clk50")),
        ]
        self.clock_domains.cd_sd = ClockDomain()
        self.submodules.sdclocker = SDClocker()
        self.comb += [
            self.cd_sd.clk.eq(self.sdclocker.clk),
        ]

class SDSoC(SoCCore):
    default_platform = "papilio_pro"
    csr_map = {
        'sdphy': 20,
        'sdctrl': 21,
        'ramreader': 22,
        'ramwraddr': 23,
    }
    csr_map.update(SoCCore.csr_map)

    def __init__(self, platform, **kwargs):
        clk_freq = 50*1000000
        SoCCore.__init__(self, platform,
                         # clk_freq=int((1/(platform.default_clk_period))*1000000000),
                         clk_freq=clk_freq,
                         cpu_type=None,
                         csr_data_width=32,
                         with_uart=False,
                         with_timer=False,
                         ident="Generic LiteX SoC",
                         integrated_sram_size=512, # XXX tmp !
                         **kwargs)

        # self.submodules.crg = _CRG(platform, clk_freq)
        self.submodules.crg = _CRGsys(platform)

        self.add_cpu_or_bridge(
           UARTWishboneBridge(platform.request("serial", 0), clk_freq, baudrate=115200)
        )
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        # SDPHY
        self.submodules.sdphy = ClockDomainsRenamer("sd")(SDPHY(platform.request('sdcard')))
        self.submodules.sdctrl = SDCtrl()

        # ASYNC FIFOs SDPHY <-> SDCTRL
        self.submodules.fifo_c2p = ClockDomainsRenamer({"write": "sys", "read": "sd"})(
            stream.AsyncFIFO(self.sdctrl.source.description, 2)
        )
        self.submodules.fifo_p2c = ClockDomainsRenamer({"write": "sd", "read": "sys"})(
            stream.AsyncFIFO(self.sdphy.source.description, 2)
        )

        # RAM Interface
        self.submodules.ramreader = RAMReader()
        self.submodules.ramwriter = RAMWriter()
        self.submodules.ramwraddr = RAMWrAddr()
        self.add_wb_master(self.ramreader.bus)
        self.add_wb_master(self.ramwriter.bus)

        self.submodules.stream32to8 = Stream32to8()
        self.submodules.stream8to32 = Stream8to32()

        self.comb += [
            self.sdctrl.source.connect(self.fifo_c2p.sink),
            self.fifo_c2p.source.connect(self.sdphy.sink),
            self.sdphy.source.connect(self.fifo_p2c.sink),
            self.fifo_p2c.source.connect(self.sdctrl.sink),

            self.sdctrl.rsource.connect(self.stream8to32.sink),
            self.stream8to32.source.connect(self.ramwraddr.sink),
            self.ramwraddr.source.connect(self.ramwriter.sink),

            self.ramreader.source.connect(self.stream32to8.sink),
            self.stream32to8.source.connect(self.sdctrl.rsink),
        ]

class LiteSoC(SDSoC):
    csr_map = {
        "analyzer": 24,
    }
    csr_map.update(SDSoC.csr_map)

    def __init__(self, platform, **kwargs):
        SDSoC.__init__(self, platform)

        bridge = UARTWishboneBridge(platform.request("serial", 1), self.clk_freq, baudrate=3000000)
        self.submodules.bridge = bridge
        self.add_wb_master(self.bridge.wishbone)

        analyzer = LiteScopeAnalyzer([
            self.sdphy.cmdr.sink.valid,
            self.sdphy.cmdr.sink.data,
            self.sdphy.cmdr.sink.ctrl,
            self.sdphy.cmdr.sink.last,
            self.sdphy.cmdr.sink.ready,
            self.sdphy.cmdr.source.valid,
            self.sdphy.cmdr.source.data,
            self.sdphy.cmdr.source.ctrl,
            self.sdphy.cmdr.source.last,
            self.sdphy.cmdr.source.ready,
            self.sdphy.cmdr.cmdrfb.sel,
            self.sdphy.cmdr.cmdrfb.data,
            self.sdphy.cmdr.cmdrfb.enable,
            self.sdphy.cmdr.cmdrfb.pads.cmd.i,
            self.sdphy.cmdr.cmdrfb.source.valid,
            self.sdphy.cmdr.cmdrfb.source.data,
            self.sdphy.cmdr.cmdrfb.source.last,
            self.sdphy.cmdr.cmdrfb.source.ready,
            self.sdphy.sdpads.cmd.i,
            self.sdphy.sdpads.cmd.o,
            self.sdphy.sdpads.cmd.oe,
            self.sdphy.sdpads.data.i,
            self.sdphy.sdpads.data.o,
            self.sdphy.sdpads.data.oe,
            self.sdphy.sdpads.clk,
        ], 256)
        self.submodules.analyzer = analyzer

    def do_exit(self, vns):
        self.analyzer.export_csv(vns, "build/analyzer.csv")

default_subtarget = SDSoC

def main():
    parser = argparse.ArgumentParser(description="Generic LiteX SoC")
    builder_args(parser)
    soc_core_args(parser)
    args = parser.parse_args()

    # import papilio_pro
    # platform = papilio_pro.Platform()
    import lx16ddr
    platform = lx16ddr.Platform()

    # soc = SDSoC(platform, **soc_core_argdict(args))
    soc = LiteSoC(platform, **soc_core_argdict(args))
    builder = Builder(soc, **builder_argdict(args))
    vns = builder.build()
    soc.do_exit(vns)

if __name__ == "__main__":
    main()
