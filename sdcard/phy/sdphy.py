from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import *

from sdcard.core.crcgeneric import CRC, CRCChecker
from sdcard.core.crcchecker import DOWNCRCChecker, UPCRCAdd

SDCARD_STREAM_CMD = 0
SDCARD_STREAM_DATA = 1

SDCARD_STREAM_READ = 0
SDCARD_STREAM_WRITE = 1

SDCARD_STREAM_VOLTAGE_3_3 = 0
SDCARD_STREAM_VOLTAGE_1_8 = 1

SDCARD_STREAM_XFER = 0
SDCARD_STREAM_CFG_TIMEOUT_CMD_HH = 1
SDCARD_STREAM_CFG_TIMEOUT_CMD_HL = 2
SDCARD_STREAM_CFG_TIMEOUT_CMD_LH = 3
SDCARD_STREAM_CFG_TIMEOUT_CMD_LL = 4
SDCARD_STREAM_CFG_TIMEOUT_DATA_HH = 5
SDCARD_STREAM_CFG_TIMEOUT_DATA_HL = 6
SDCARD_STREAM_CFG_TIMEOUT_DATA_LH = 7
SDCARD_STREAM_CFG_TIMEOUT_DATA_LL = 8
SDCARD_STREAM_CFG_BLKSIZE_H = 9
SDCARD_STREAM_CFG_BLKSIZE_L = 10
SDCARD_STREAM_CFG_VOLTAGE = 11

SDCARD_STREAM_STATUS_OK = 0
SDCARD_STREAM_STATUS_TIMEOUT = 1
SDCARD_STREAM_STATUS_DATAACCEPTED = 0b010
SDCARD_STREAM_STATUS_CRCERROR = 0b101
SDCARD_STREAM_STATUS_WRITEERROR = 0b110

SDCARD_CTRL_DATA_TRANSFER_NONE = 0
SDCARD_CTRL_DATA_TRANSFER_READ = 1
SDCARD_CTRL_DATA_TRANSFER_WRITE = 2

SDCARD_CTRL_RESPONSE_NONE = 0
SDCARD_CTRL_RESPONSE_SHORT = 1
SDCARD_CTRL_RESPONSE_LONG = 2

class SDCtrl(Module, AutoCSR):
    def __init__(self):
        self.argument = CSRStorage(32)
        self.command = CSRStorage(32)
        self.response = CSRStatus(120)
        self.datatimeout = CSRStorage(32)
        self.cmdtimeout = CSRStorage(32)
        self.voltage = CSRStorage(8)
        self.cmdevt = CSRStatus(32)
        self.dataevt = CSRStatus(32)
        self.blocksize = CSRStorage(16)
        self.blockcount = CSRStorage(16)
        self.debug = CSRStatus(32)

        self.submodules.crc7 = CRC(9, 7, 40)
        self.submodules.crc7checker = CRCChecker(9, 7, 120)
        self.submodules.crc16 = UPCRCAdd()
        self.submodules.crc16checker = DOWNCRCChecker()

        self.rsink = self.crc16.sink
        self.rsource = self.crc16checker.source

        self.sink = stream.Endpoint([("data", 8), ("ctrl", 8)])
        self.source = stream.Endpoint([("data", 8), ("ctrl", 8)])

        fsm = FSM()
        self.submodules += fsm

        csel = Signal(max=6)
        waitresp = Signal(2)
        dataxfer = Signal(2)
        cmddone = Signal(reset=1)
        datadone = Signal(reset=1)
        blkcnt = Signal(16)
        pos = Signal(2)

        cmddata = Signal()
        rdwr = Signal()
        mode = Signal(6)
        status = Signal(4)

        cerrtimeout = Signal()
        cerrcrc_en = Signal()
        derrtimeout = Signal()
        derrwrite = Signal()
        derrread_en = Signal()

        self.comb += [
            waitresp.eq(self.command.storage[0:2]),
            dataxfer.eq(self.command.storage[5:7]),
            self.cmdevt.status.eq(Cat(cmddone, C(0, 1), cerrtimeout, cerrcrc_en & ~self.crc7checker.valid)),
            self.dataevt.status.eq(Cat(datadone, derrwrite, derrtimeout, derrread_en & ~self.crc16checker.valid)),

            self.source.ctrl.eq(Cat(cmddata, rdwr, mode)),
            status.eq(self.sink.ctrl[1:5]),

            self.crc7.val.eq(Cat(self.argument.storage, self.command.storage[8:14], 1, 0)),
            self.crc7.clr.eq(1),
            self.crc7.enable.eq(1),

            self.crc7checker.val.eq(self.response.status),
        ]

        ccases = {} # To send command and CRC
        ccases[0] = self.source.data.eq(Cat(self.command.storage[8:14], 1, 0))
        for i in range(4):
            ccases[i+1] = self.source.data.eq(self.argument.storage[24-8*i:32-8*i])
        ccases[5] = [
            self.source.data.eq(Cat(1, self.crc7.crc)),
            self.source.last.eq(waitresp == SDCARD_CTRL_RESPONSE_NONE),
        ]

        fsm.act("IDLE",
            NextValue(pos, 0),
            If(self.datatimeout.re,
                NextState("CFG_TIMEOUT_DATA"),
            ).Elif(self.cmdtimeout.re,
                NextState("CFG_TIMEOUT_CMD"),
            ).Elif(self.voltage.re,
                NextState("CFG_VOLTAGE"),
            ).Elif(self.blocksize.re,
                NextState("CFG_BLKSIZE"),
            ).Elif(self.command.re,
                NextValue(cmddone, 0),
                NextValue(cerrtimeout, 0),
                NextValue(cerrcrc_en, 0),
                NextValue(datadone, 0),
                NextValue(derrtimeout, 0),
                NextValue(derrwrite, 0),
                NextValue(derrread_en, 0),
                NextValue(self.debug.status, 0),
                NextValue(self.response.status, 0),
                NextState("SEND_CMD"),
            ),
        )

        dtcases = {}
        for i in range(4):
            dtcases[i] = [
                self.source.data.eq(self.datatimeout.storage[24-(8*i):32-(8*i)]),
                mode.eq(SDCARD_STREAM_CFG_TIMEOUT_DATA_HH + i),
            ]
        ctcases = {}
        for i in range(4):
            ctcases[i] = [
                self.source.data.eq(self.cmdtimeout.storage[24-(8*i):32-(8*i)]),
                mode.eq(SDCARD_STREAM_CFG_TIMEOUT_CMD_HH + i),
            ]
        blkcases = {}
        for i in range(2):
            blkcases[i] = [
                self.source.data.eq(self.blocksize.storage[8-(8*i):16-(8*i)]),
                mode.eq(SDCARD_STREAM_CFG_BLKSIZE_H + i),
            ]

        fsm.act("CFG_TIMEOUT_DATA",
            self.source.valid.eq(1),
            Case(pos, dtcases),
            If(self.source.valid & self.source.ready,
                NextValue(pos, pos + 1),
                If(pos == 3,
                    NextState("IDLE"),
                ),
            ),
        )

        fsm.act("CFG_TIMEOUT_CMD",
            self.source.valid.eq(1),
            Case(pos, ctcases),
            If(self.source.valid & self.source.ready,
                NextValue(pos, pos + 1),
                If(pos == 3,
                    NextState("IDLE"),
                ),
            ),
        )

        fsm.act("CFG_BLKSIZE",
            self.source.valid.eq(1),
            Case(pos, blkcases),
            If(self.source.valid & self.source.ready,
                NextValue(pos, pos + 1),
                If(pos == 1,
                    NextState("IDLE"),
                ),
            ),
        )

        fsm.act("CFG_VOLTAGE",
            self.source.valid.eq(1),
            self.source.data.eq(self.voltage.storage[0:8]),
            mode.eq(SDCARD_STREAM_CFG_VOLTAGE),
            If(self.source.valid & self.source.ready,
                NextState("IDLE"),
            ),
        )

        fsm.act("SEND_CMD",
            self.source.valid.eq(1),
            cmddata.eq(SDCARD_STREAM_CMD),
            rdwr.eq(SDCARD_STREAM_WRITE),
            mode.eq(SDCARD_STREAM_XFER),
            Case(csel, ccases),
            If(self.source.valid & self.source.ready,
                If(csel < 5,
                    NextValue(csel, csel + 1),
                ).Else(
                    NextValue(csel, 0),
                    If(waitresp == SDCARD_CTRL_RESPONSE_NONE,
                        NextValue(cmddone, 1),
                        NextState("IDLE"),
                    ).Else(
                        NextValue(cerrcrc_en, 1),
                        NextState("RECV_RESP"),
                    ),
                ),
            ),
        )

        fsm.act("RECV_RESP",
            If(waitresp == SDCARD_CTRL_RESPONSE_SHORT,
                self.source.data.eq(5), # (5+1)*8 == 48bits
            ).Elif(waitresp == SDCARD_CTRL_RESPONSE_LONG,
                self.source.data.eq(16), # (16+1)*8 == 136bits
            ),
            self.source.valid.eq(1),
            self.source.last.eq(dataxfer == SDCARD_CTRL_DATA_TRANSFER_NONE),
            cmddata.eq(SDCARD_STREAM_CMD),
            rdwr.eq(SDCARD_STREAM_READ),
            mode.eq(SDCARD_STREAM_XFER),

            If(self.sink.valid,
                self.sink.ready.eq(1),
                If(self.sink.ctrl[0] == SDCARD_STREAM_CMD,
                    If(status == SDCARD_STREAM_STATUS_TIMEOUT,
                        NextValue(cerrtimeout, 1),
                        NextValue(cmddone, 1),
                        NextValue(datadone, 1),
                        NextState("IDLE"),
                    ).Elif(self.sink.last,
                        NextValue(self.crc7checker.check, self.sink.data[1:8]), # Check response CRC
                        NextValue(cmddone, 1),
                        If(dataxfer == SDCARD_CTRL_DATA_TRANSFER_READ,
                            NextValue(derrread_en, 1),
                            NextState("RECV_DATA"),
                        ).Elif(dataxfer == SDCARD_CTRL_DATA_TRANSFER_WRITE,
                            NextState("SEND_DATA"),
                        ).Else(
                            NextValue(datadone, 1),
                            NextState("IDLE"),
                        ),
                    ).Else(
                        NextValue(self.response.status, Cat(self.sink.data, self.response.status[0:112])),
                    ),
                ),
            ),
        )

        fsm.act("RECV_DATA",
            self.source.data.eq(0), # Read 1 block
            cmddata.eq(SDCARD_STREAM_DATA),
            rdwr.eq(SDCARD_STREAM_READ),
            mode.eq(SDCARD_STREAM_XFER),
            self.source.last.eq(self.blockcount.storage == blkcnt),
            self.source.valid.eq(1),

            If(self.sink.ctrl[0] == SDCARD_STREAM_DATA,
                If(status == SDCARD_STREAM_STATUS_OK,
                    self.crc16checker.sink.data.eq(self.sink.data), # Manual connect
                    self.crc16checker.sink.valid.eq(self.sink.valid),
                    self.crc16checker.sink.last.eq(self.sink.last),
                    self.sink.ready.eq(self.crc16checker.sink.ready),
                ),
            ),

            If(self.sink.valid & (status == SDCARD_STREAM_STATUS_TIMEOUT),
                NextValue(derrtimeout, 1),
                self.sink.ready.eq(1),
                NextValue(blkcnt, 0),
                NextValue(datadone, 1),
                NextState("IDLE"),
            ).Elif(self.source.valid & self.source.ready,
                If(self.blockcount.storage > blkcnt,
                    NextValue(blkcnt, blkcnt + 1),
                ).Else(
                    NextValue(blkcnt, 0),
                    NextValue(datadone, 1),
                    NextState("IDLE"),
                ),
            ),
        )

        fsm.act("SEND_DATA",
            self.source.data.eq(self.crc16.source.data),
            cmddata.eq(SDCARD_STREAM_DATA),
            rdwr.eq(SDCARD_STREAM_WRITE),
            mode.eq(SDCARD_STREAM_XFER),
            self.source.last.eq(self.crc16.source.last),
            self.source.valid.eq(self.crc16.source.valid),
            self.crc16.source.ready.eq(self.source.ready),

            If(self.crc16.source.valid & self.crc16.source.last & self.crc16.source.ready,
                If(self.blockcount.storage > blkcnt,
                    NextValue(blkcnt, blkcnt + 1),
                ).Else(
                    NextValue(blkcnt, 0),
                    NextValue(datadone, 1),
                    NextState("IDLE"),
                ),
            ),

            If(self.sink.valid,
                self.sink.ready.eq(1),
                NextValue(self.debug.status, status), # XXX debug
                If(status != SDCARD_STREAM_STATUS_DATAACCEPTED,
                    NextValue(derrwrite, 1),
                ),
            ),
        )

class SDPHYCFG(Module):
    def __init__(self):
        # Data timeout
        self.cfgdtimeout = Signal(32)
        # Command timeout
        self.cfgctimeout = Signal(32)
        # Blocksize
        self.cfgblksize = Signal(16)
        # Voltage config: 0: 3.3v, 1: 1.8v
        self.cfgvoltage = Signal()

        mode = Signal(6)

        self.sink = stream.Endpoint([("data", 8), ("ctrl", 8)])

        cfgcases = {} # PHY configuration
        for i in range(4):
            cfgcases[SDCARD_STREAM_CFG_TIMEOUT_DATA_HH + i] = self.cfgdtimeout[24-(8*i):32-(8*i)].eq(self.sink.data)
            cfgcases[SDCARD_STREAM_CFG_TIMEOUT_CMD_HH + i] = self.cfgctimeout[24-(8*i):32-(8*i)].eq(self.sink.data)
        for i in range(2):
            cfgcases[SDCARD_STREAM_CFG_BLKSIZE_H + i] = self.cfgblksize[8-(8*i):16-(8*i)].eq(self.sink.data)
        cfgcases[SDCARD_STREAM_CFG_VOLTAGE] = self.cfgvoltage.eq(self.sink.data[0])

        self.comb += [
            mode.eq(self.sink.ctrl[2:8]),
            self.sink.ready.eq(self.sink.valid)
        ]

        self.sync += [
            If(self.sink.valid,
               Case(mode, cfgcases),
            )
        ]

SDPADS = [
    ("data", [
        ("i", 4, DIR_S_TO_M),
        ("o", 4, DIR_M_TO_S),
        ("oe", 1, DIR_M_TO_S)
    ]),
    ("cmd", [
        ("i", 1, DIR_S_TO_M),
        ("o", 1, DIR_M_TO_S),
        ("oe", 1, DIR_M_TO_S)
    ]),
    ("clk", 1, DIR_M_TO_S),
]

class SDPHYCMDRFB(Module):
    def __init__(self, pads, enable):
        self.source = stream.Endpoint([("data", 8)])

        sel = Signal(3)
        data = Signal(8)

        # debug
        self.sel = sel
        self.data = data
        self.enable = enable
        self.pads = pads

        cases = {}
        for i in range(7): # LSB is comb
            cases[i] = NextValue(data[6-i], pads.cmd.i)

        fsm = FSM()
        self.submodules.fsm = fsm

        fsm.act("IDLE",
            If(enable,
                NextValue(sel, 0),
                NextState("READSTART")
            ),
        )

        fsm.act("READSTART",
            If(~enable,
                NextState("IDLE")
            ).Elif(pads.cmd.i == 0,
                NextValue(data, 0),
                NextValue(sel, 1),
                NextState("READ"),
            ),
        )

        fsm.act("READ",
            If(~enable,
                NextState("IDLE")
            ).Elif(sel == 7,
                self.source.valid.eq(1),
                self.source.data.eq(Cat(pads.cmd.i, data)),
                NextValue(sel, 0),
            ).Else(
                Case(sel, cases),
                NextValue(sel, sel + 1),
            )
        )

class SDPHYCMDR(Module):
    def __init__(self, cfg):
        self.pads = Record(SDPADS)

        self.sink = stream.Endpoint([("data", 8), ("ctrl", 8)])
        self.source = stream.Endpoint([("data", 8), ("ctrl", 8)])

        enable = Signal()

        # self.submodules.cmdrfb = ClockDomainsRenamer("fb")(SDPHYCMDRFB(self.pads, enable))
        self.submodules.cmdrfb = SDPHYCMDRFB(self.pads, enable)
        # fifo = stream.AsyncFIFO([("data", 8)], 2)
        # self.submodules.fifo = ClockDomainsRenamer({"write": "fb", "read": "sys"})(fifo)
        self.submodules.fifo = stream.SyncFIFO(self.cmdrfb.source.description, 2)

        self.comb += [
            self.cmdrfb.source.connect(self.fifo.sink),
        ]

        ctimeout = Signal(32)

        cread = Signal(8)
        ctoread = Signal(8)
        cnt = Signal(8)

        status = Signal(4)

        self.comb += [
            self.source.ctrl.eq(Cat(SDCARD_STREAM_READ, status))
        ]

        fsm = FSM()
        self.submodules.fsm = fsm

        fsm.act("IDLE",
            If(self.sink.valid,
                NextValue(ctimeout, 0),
                NextValue(cread, 0),
                NextValue(ctoread, self.sink.data),
                # enable.eq(1),
                NextState("CMD_READSTART")
            )
        )

        fsm.act("CMD_READSTART",
            enable.eq(1),
            self.pads.cmd.oe.eq(0),
            self.pads.clk.eq(1),
            NextValue(ctimeout, ctimeout + 1),
            If(self.fifo.source.valid,
                NextState("CMD_READ"),
            ).Elif(ctimeout > (cfg.cfgctimeout),
                NextState("TIMEOUT"),
            ),
        )

        fsm.act("CMD_READ",
            enable.eq(1),
            self.pads.cmd.oe.eq(0),
            self.pads.clk.eq(1),

            self.source.valid.eq(self.fifo.source.valid),
            self.source.data.eq(self.fifo.source.data),
            status.eq(SDCARD_STREAM_STATUS_OK),
            self.source.last.eq(cread == ctoread),
            self.fifo.source.ready.eq(self.source.ready),

            If(self.source.valid & self.source.ready,
                NextValue(cread, cread + 1),
                If(cread == ctoread,
                    If(self.sink.last,
                        NextState("CMD_CLK8"),
                    ).Else(
                        self.sink.ready.eq(1),
                        NextState("IDLE"),
                    ),
                ),
            ),
        )

        fsm.act("CMD_CLK8",
            self.pads.cmd.oe.eq(1),
            self.pads.cmd.o.eq(1),
            If(cnt < 7,
                NextValue(cnt, cnt + 1),
                self.pads.clk.eq(1),
            ).Else(
                NextValue(cnt, 0),
                self.sink.ready.eq(1),
                NextState("IDLE"),
            ),
        )

        fsm.act("TIMEOUT",
            self.source.valid.eq(1),
            self.source.data.eq(0),
            status.eq(SDCARD_STREAM_STATUS_TIMEOUT),
            self.source.last.eq(1),
            If(self.source.valid & self.source.ready,
                self.sink.ready.eq(1),
                NextState("IDLE"),
            ),
        )

class SDPHYCMDW(Module):
    def __init__(self):
        self.pads = Record(SDPADS)

        self.sink = stream.Endpoint([("data", 8), ("ctrl", 8)])

        isinit = Signal()
        cntinit = Signal(8)
        cnt = Signal(8)
        wrsel = Signal(3)
        wrtmpdata = Signal(8)

        wrcases = {} # For command write
        for i in range(8):
            wrcases[i] =  self.pads.cmd.o.eq(wrtmpdata[7-i])

        self.comb += [
            self.pads.cmd.oe.eq(1),
        ]

        fsm = FSM()
        self.submodules.fsm = fsm

        fsm.act("IDLE",
            If(self.sink.valid,
                If(~isinit,
                    NextState("INIT")
                ).Else(
                    self.pads.clk.eq(1),
                    self.pads.cmd.o.eq(self.sink.data[7]),
                    NextValue(wrtmpdata, self.sink.data),
                    NextValue(wrsel, 1),
                    NextState("CMD_WRITE")
                )
            )
        )

        fsm.act("INIT",
            # Initialize sdcard with 80 clock cycles
            self.pads.clk.eq(1),
            self.pads.cmd.o.eq(1),
            If(cntinit < 80,
                NextValue(cntinit, cntinit + 1),
                NextValue(self.pads.data.oe, 1),
                NextValue(self.pads.data.o, 0xf),
            ).Else(
                NextValue(cntinit, 0),
                NextValue(isinit, 1),
                NextValue(self.pads.data.oe, 0),
                NextState("IDLE"),
            ),
        )

        fsm.act("CMD_WRITE",
            Case(wrsel, wrcases),
            NextValue(wrsel, wrsel + 1),
            If(wrsel == 0,
                If(self.sink.last,
                    self.pads.clk.eq(1),
                    NextState("CMD_CLK8"),
                ).Else(
                    self.sink.ready.eq(1),
                    NextState("IDLE"),
                ),
            ).Else(
                self.pads.clk.eq(1),
            ),
        )

        fsm.act("CMD_CLK8",
            self.pads.cmd.o.eq(1),
            If(cnt < 7,
                NextValue(cnt, cnt + 1),
                self.pads.clk.eq(1),
            ).Else(
                NextValue(cnt, 0),
                self.sink.ready.eq(1),
                NextState("IDLE"),
            ),
        )

class SDPHY(Module):
    def __init__(self, pads):
        sdpads = Record(SDPADS)
        # debug
        self.sdpads = sdpads

        cmddata = Signal()
        rdwr = Signal()
        mode = Signal(6)

        # Streams
        self.sink = stream.Endpoint([("data", 8), ("ctrl", 8)])
        self.source = stream.Endpoint([("data", 8), ("ctrl", 8)])

        # Data tristate
        self.data_t = TSTriple(4)
        self.specials += self.data_t.get_tristate(pads.data)

        # Cmd tristate
        self.cmd_t = TSTriple()
        self.specials += self.cmd_t.get_tristate(pads.cmd)

        # Clk domain feedback
        self.clock_domains.cd_fb = ClockDomain()
        self.specials += Instance("IBUFG", i_I=pads.clk_fb, o_O=self.cd_fb.clk)

        # Clk output
        self.specials += Instance("ODDR2", p_DDR_ALIGNMENT="NONE",
            p_INIT=1, p_SRTYPE="SYNC",
            i_D0=0, i_D1=sdpads.clk, i_S=0, i_R=0, i_CE=1,
            i_C0=ClockSignal("sd"), i_C1=~ClockSignal("sd"),
            o_Q=pads.clk
        )

        # Cmd input DDR
        cmd_i1 = Signal()
        self.specials += Instance("IDDR2",
            p_DDR_ALIGNMENT="C1", p_INIT_Q0=0, p_INIT_Q1=0, p_SRTYPE="ASYNC",
            i_C0=ClockSignal("fb"), i_C1=~ClockSignal("fb"),
            i_CE=1, i_S=0, i_R=0,
            i_D=self.cmd_t.i, o_Q0=cmd_i1, o_Q1=sdpads.cmd.i
        )

        self.comb += [
            self.cmd_t.oe.eq(sdpads.cmd.oe),
            self.cmd_t.o.eq(sdpads.cmd.o),

            self.data_t.oe.eq(sdpads.data.oe),
            self.data_t.o.eq(sdpads.data.o),
        ]

        self.comb += [
            cmddata.eq(self.sink.ctrl[0]),
            rdwr.eq(self.sink.ctrl[1]),
            mode.eq(self.sink.ctrl[2:8]),
        ]

        self.submodules.cfg = SDPHYCFG()
        self.submodules.cmdw = SDPHYCMDW()
        self.submodules.cmdr = SDPHYCMDR(self.cfg)

        fsm = FSM()
        self.submodules.fsm = fsm

        fsm.act("IDLE",
            If(self.sink.valid,
                # Configuration mode
                If(mode != SDCARD_STREAM_XFER,
                    self.sink.connect(self.cfg.sink),
                    sdpads.clk.eq(0),
                    sdpads.cmd.oe.eq(1),
                    sdpads.cmd.o.eq(1),
                # Command mode
                ).Elif(cmddata == SDCARD_STREAM_CMD,
                    # Write command
                    If(rdwr == SDCARD_STREAM_WRITE,
                        self.sink.connect(self.cmdw.sink),
                        self.cmdw.pads.connect(sdpads),
                    # Read response
                    ).Elif(rdwr == SDCARD_STREAM_READ,
                        self.sink.connect(self.cmdr.sink),
                        self.cmdr.pads.connect(sdpads),
                        self.cmdr.source.connect(self.source),
                    )
                # Data mode
                ).Else(
                    sdpads.clk.eq(0),
                    sdpads.cmd.oe.eq(1),
                    sdpads.cmd.o.eq(1),
                )
            ).Else(
                sdpads.clk.eq(0),
                sdpads.cmd.oe.eq(1),
                sdpads.cmd.o.eq(1),
            )
        )

        fsm.act("TOTO",
            NextState("IDLE")
        )
