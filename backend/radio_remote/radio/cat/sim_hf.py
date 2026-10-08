"""Software model of a newer Yaesu HF radio's CAT port (FTDX10 / FTDX101 / FT-710), for tests and hardware-free development.

Built from the same manuals as cat/proto.py, so it can only prove that the driver agrees with the manual text, not with the radios.
Commands the radio does not have (for example FT on these radios, RT/RU/RD/RC/QS on the FT-710) get '?;', like the real radio.
"""
from __future__ import annotations

from .proto import HF_MODE_CODES, HF_MODES, MODELS
from .sim import BAND_START, REG_DEFAULTS, SimulatedFT991A
from .transport import MemoryTransport


# registers / attributes that exist once per receiver on the FTDX101; the SUB copy is swapped in while a P1 = 1 command is handled
_SUB_REG = ("NA0", "CO00", "CO01", "BP00", "BP01", "BC0", "NR0", "RL0", "NB0", "PA0", "RA0", "SH0")
_SUB_CMDS = ("NA", "IS", "CO", "BP", "BC", "NR", "RL", "NB", "PA", "RA", "GT", "SH")


class SimulatedYaesuHf(SimulatedFT991A):
    def __init__(self, transport: MemoryTransport, model: str = "ftdx10"):
        super().__init__(transport)
        self.m = MODELS[model]
        self.model = model
        self.split = 0
        self.fr = "01"                                                   # FTDX101: MAIN listening, SUB muted
        self.ag1 = 2                                                     # FTDX101: SUB AF gain
        self.vs = 0                                                      # FTDX101: 0 = the radio operates MAIN, 1 = SUB
        self.ft_rx = 0                                                  # FTDX101: 0 = TX on MAIN, 1 = TX on SUB
        self.agc = 0
        self.mode_b = "LSB"
        reg = {k: v for k, v in REG_DEFAULTS.items() if k not in ("EX153", "EX106", "PR0", "PR1", "PL", "RA0", "IS0", "SH0")}
        reg.update({"SH0": "000", "RA0": "0", "EX" + self.m.mic_menu: "1"})
        reg.update({"AO": "070", "KS": "025", "KP": "40", "KR": "0", "BI": "0"})      # AMC output level, key speed, pitch, keyer, break-in (as read from a real FTDX101D)
        if model.startswith("ftdx101"):
            reg.update({"EX010112": "0", "EX010213": "0", "EX010312": "0", "EX010414": "1"})        # REAR SELECT (0 DATA / 1 USB) as read from a real FTDX101D
            reg.update({"PR0": "0", "PR1": "0", "PL": "050"})            # speech processor, mic equalizer (0 OFF / 1 ON like the real radio) and the processor level
        if model.startswith("ftdx101"):                                  # OUT LEVEL / RPORT GAIN of SSB, AM and FM (0..100, 3 digits)
            for g in ("010109", "010209", "010309", "010411", "010511", "020109", "010113", "010214", "010313"):
                reg["EX" + g] = "050"
        self.reg = reg
        self.reg_defaults = dict(reg)
        self.is_shift = 0
        self.sub_reg = {k: reg[k] for k in _SUB_REG if k in reg}
        self.sub_agc = 0
        self.sub_is = 0
        if model.startswith("ftdx101"):
            self.levels["RG1"] = 252                                     # SUB RF gain
            self.levels["SQ1"] = 0                                       # SUB squelch (MAIN is SQ0, from the base class)

    def _swap_sub(self) -> None:
        for k in self.sub_reg:
            self.reg[k], self.sub_reg[k] = self.sub_reg[k], self.reg[k]
        self.agc, self.sub_agc = self.sub_agc, self.agc
        self.is_shift, self.sub_is = self.sub_is, self.is_shift

    async def _handle_sub(self, name: str, p: str) -> None:
        """A P1 = 1 command: run the MAIN handler on the swapped-in SUB state with P1 = 0 and give the answers P1 = 1 back."""
        answers: list[str] = []
        self.log.append(name + p)                                        # what the driver really sent (the inner call logs the MAIN-style copy too)

        async def capture(text: str) -> None:
            answers.append(text)

        real_send = self._send
        self._send = capture
        self._swap_sub()
        try:
            await self._handle(f"{name}0{p[1:]}")
        finally:
            self._swap_sub()
            self._send = real_send
        for a in answers:
            await real_send(name + "1" + a[len(name) + 1:] if a.startswith(name + "0") else a)

    def _if_frame(self) -> str:
        sign = "-" if self.clar < 0 else "+"
        return (f"IF{max(self.mem_ch, 1):03d}{self.freq:09d}{sign}{abs(self.clar):04d}{self.reg.get('RT', '0')}{self.reg.get('XT', '0')}"
                f"{HF_MODE_CODES[self.mode]}{1 if self.mem_ch else 0}0000;")                 # like the real radio: channel 001 in VFO mode, the flag says memory

    async def _handle(self, c: str) -> None:
        name, p = c[:2], c[2:]
        if not self.powered or self.drop:
            return await super()._handle(c)
        if self.model.startswith("ftdx101") and name in _SUB_CMDS and p[:1] == "1":
            return await self._handle_sub(name, p)
        if name == "ID" and not p:
            self.log.append(c)
            return await self._send(f"ID{self.m.radio_id};")
        if name == "OI" and not p:
            self.log.append(c)
            return await self._send(f"OI001{self.freq_b:09d}+0000{self.reg.get('RT', '0')}{self.reg.get('XT', '0')}"
                                    f"{HF_MODE_CODES[self.mode_b]}00000;")
        if name == "MD" and p[:1] == "0":
            self.log.append(c)
            if len(p) == 1:
                return await self._send(f"MD0{HF_MODE_CODES[self.mode]};")
            if len(p) == 2 and p[1] in HF_MODES:
                self.mode = HF_MODES[p[1]]
                return
            return await self._send("?;")
        if name == "RM" and len(p) == 1 and p in self.meters:
            self.log.append(c)
            return await self._send(f"RM{p}{self.meters[p]:03d}000;")
        if name == "FR" and self.model.startswith("ftdx101"):          # P1 MAIN / P2 SUB: 0 listening, 1 muted
            self.log.append(c)
            if not p:
                return await self._send(f"FR{self.fr};")
            if len(p) == 2 and p[0] in "01" and p[1] in "01":
                self.fr = p
                return
            return await self._send("?;")
        if name == "AG" and self.model.startswith("ftdx101") and p[:1] == "1":     # AG1 = the SUB receiver's volume
            self.log.append(c)
            if len(p) == 1:
                return await self._send(f"AG1{self.ag1:03d};")
            if len(p) == 4 and p[1:].isdigit() and int(p[1:]) <= 255:
                self.ag1 = int(p[1:])
                return
            return await self._send("?;")
        if name == "VS" and self.model.startswith("ftdx101"):          # 0 = MAIN band operation, 1 = SUB band operation
            self.log.append(c)
            if not p:
                return await self._send(f"VS{self.vs};")
            if p in ("0", "1"):
                self.vs = int(p)
                return
            return await self._send("?;")
        if name == "FT" and self.model.startswith("ftdx101"):          # set 2 = TX on MAIN, 3 = TX on SUB; read -> FT0 / FT1
            self.log.append(c)
            if not p:
                return await self._send(f"FT{self.ft_rx};")
            if p in ("2", "3"):
                self.ft_rx = int(p) - 2
                return
            return await self._send("?;")
        if name == "FT" or (name == "QS" and self.model == "ft710") or (name in ("RT", "RU", "RD", "RC") and not self.m.has_rt):
            self.log.append(c)
            return await self._send("?;")                                  # not in that radio's command list
        if name == "ST":
            self.log.append(c)
            if not p:
                return await self._send(f"ST{self.split};")
            if p in ("0", "1", "2"):
                self.split = int(p)
                if self.ai:
                    await self._send(f"ST{self.split};")
                return
            return await self._send("?;")
        if name == "IS":
            self.log.append(c)
            if p == "0":
                return await self._send(f"IS00{'-' if self.is_shift < 0 else '+'}{abs(self.is_shift):04d};")
            if len(p) == 7 and p[:2] == "00" and p[2] in "+-" and p[3:].isdigit():
                self.is_shift = int(p[3:]) * (-1 if p[2] == "-" else 1)
                return
            return await self._send("?;")
        if name == "GT":
            self.log.append(c)
            if p == "0":
                return await self._send(f"GT0{5 if self.agc == 4 else self.agc};")       # AUTO is answered as AUTO-MID here
            if len(p) == 2 and p[0] == "0" and p[1] in "01234":
                self.agc = int(p[1])
                return
            return await self._send("?;")
        if name == "RA":
            self.log.append(c)
            if p == "0":
                return await self._send(f"RA0{self.reg['RA0']};")
            if len(p) == 2 and p[0] == "0" and p[1] in "0123":
                self.reg["RA0"] = p[1]
                return
            return await self._send("?;")
        if name == "AC" and self.model == "ft710" and p in ("003", "000"):
            self.log.append(c)
            if p == "003" and not self.tuning:
                self.tune_calls += 1
                self.tuning, self.tx = True, 2
                self.meters["5"] = 120
            elif p == "000" and self.tuning:
                await self._end_tune()
            return
        if name == "BS" and len(p) == 2 and p.isdigit() and int(p) in BAND_START and int(p) <= 10:
            return await super()._handle(c)
        return await super()._handle(c)
