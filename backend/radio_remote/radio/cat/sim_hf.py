"""Software model of a newer Yaesu HF radio's CAT port (FTDX10 / FTDX101 / FT-710), for tests and hardware-free development.

Built from the same manuals as cat/proto.py, so it can only prove that the driver agrees with the manual text, not with the radios.
Commands the radio does not have (for example FT on these radios, RT/RU/RD/RC/QS on the FT-710) get '?;', like the real radio.
"""
from __future__ import annotations

from .proto import HF_MODE_CODES, HF_MODES, MODELS
from .sim import BAND_START, REG_DEFAULTS, SimulatedFT991A
from .transport import MemoryTransport


class SimulatedYaesuHf(SimulatedFT991A):
    def __init__(self, transport: MemoryTransport, model: str = "ftdx10"):
        super().__init__(transport)
        self.m = MODELS[model]
        self.model = model
        self.split = 0
        self.agc = 0
        self.mode_b = "LSB"
        reg = {k: v for k, v in REG_DEFAULTS.items() if k not in ("EX153", "EX106", "PR0", "PR1", "PL", "RA0", "IS0", "SH0")}
        reg.update({"SH0": "000", "RA0": "0", "EX" + self.m.mic_menu: "1"})
        self.reg = reg
        self.reg_defaults = dict(reg)
        self.is_shift = 0

    def _if_frame(self) -> str:
        sign = "-" if self.clar < 0 else "+"
        return (f"IF001{self.freq:09d}{sign}{abs(self.clar):04d}{self.reg.get('RT', '0')}{self.reg.get('XT', '0')}"
                f"{HF_MODE_CODES[self.mode]}00000;")

    async def _handle(self, c: str) -> None:
        name, p = c[:2], c[2:]
        if not self.powered or self.drop:
            return await super()._handle(c)
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
