"""Software model of an FT-991A's CAT port, for tests and hardware-free development.

It implements only what the manual documents and what the driver uses; anything
else gets '?;', as the real radio does. It is a test double, not a spec.
"""
from __future__ import annotations

import asyncio

from .frame import MODE_CODES, MODES, RADIO_ID
from .transport import MemoryTransport, TransportClosed


# Generic read/set registers: key -> default payload (payload width fixes the set-command format).
REG_DEFAULTS = {
    "NA0": "0", "SH0": "00", "IS0": "+0000", "CO00": "0000", "CO01": "1500", "CO02": "0000", "CO03": "0025",
    "BP00": "000", "BP01": "150", "BC0": "0", "NR0": "0", "RL0": "05", "NB0": "0", "NL0": "005", "PA0": "0",
    "RA0": "0", "RT": "0", "XT": "0", "PR0": "0", "PR1": "0", "PL": "050", "ML0": "000", "ML1": "050", "AC": "000", "EX153": "00", "EX106": "1",
}
BAND_START = {0: 1_840_000, 1: 3_573_000, 2: 5_357_000, 3: 7_074_000, 4: 10_136_000, 5: 14_074_000, 6: 18_100_000,
              7: 21_074_000, 8: 24_915_000, 9: 28_074_000, 10: 50_313_000, 15: 144_174_000, 16: 432_100_000}


class SimulatedFT991A:
    def __init__(self, transport: MemoryTransport):
        self.t = transport
        self.freq, self.freq_b, self.mode = 14_200_000, 7_100_000, "USB"
        self.mode_b = "LSB"
        self.memories = {5: (14_200_000, "USB", "20m DX"), 6: (7_100_000, "LSB", "40m net"), 11: (145_500_000, "FM", "2m calling"),
                         99: (28_400_000, "USB", "")}
        self.label_001 = False     # True: MT/MR answers always carry channel 001 (what a real FT-991A was seen to do)
        self.mem_ch = 0            # 0 = VFO mode, else the recalled memory channel
        self.mc_keeps_vfo = False  # True: a recall changes the channel only, IF keeps answering with the VFO frequency (seen on a friend's FT-991A)
        self.vfo_freq = None       # the VFO frequency while a memory is recalled
        self.levels = {"AG0": 80, "RG0": 255, "MG": 50, "PC": 50}
        self.meters = {"1": 40, "3": 0, "4": 0, "5": 0, "6": 0}
        self.tx = 0              # 0 off, 1 CAT, 2 radio
        self.ai = 0
        self.powered = True
        self.clar = 0
        self.reg = dict(REG_DEFAULTS)
        self.reg_defaults = dict(REG_DEFAULTS)          # payload widths of the generic registers (subclasses may change them)
        self.tune_calls = 0
        self.ft = 0                      # 0 = TX on VFO A, 1 = TX on VFO B (split)
        self.tuning = False
        self.tune_seconds = 0.4          # how long a simulated tune transmits by itself
        self._tune_task = None
        self.drop = 0            # swallow the next N commands (simulate timeouts)
        self.log: list[str] = []
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    # ---- things a user does on the front panel
    async def front_panel_freq(self, hz: int) -> None:
        self.freq = hz
        if self.ai:
            await self._send(f"FA{hz:09d};")

    async def front_panel_ptt(self, on: bool) -> None:
        self.tx = 2 if on else 0
        if self.ai:
            await self._send(f"TX{self.tx};")

    async def _finish_tune(self) -> None:
        await asyncio.sleep(self.tune_seconds)
        await self._end_tune()

    async def _end_tune(self) -> None:
        if self._tune_task and self._tune_task is not asyncio.current_task():
            self._tune_task.cancel()
        self.tuning, self.tx = False, 0
        self.meters["5"] = 0
        if self.ai:
            await self._send("TX0;")

    def power_cycle(self) -> None:
        self.ai = 0              # manual p.4: AI resets to 0 when the radio is switched off

    async def _send(self, s: str) -> None:
        try:
            await self.t.write(s.encode())
        except TransportClosed:
            pass

    def _if_frame(self) -> str:
        code = MODE_CODES[self.mode]
        sign = "-" if self.clar < 0 else "+"
        return (f"IF{self.mem_ch:03d}{self.freq:09d}{sign}{abs(self.clar):04d}{self.reg['RT']}{self.reg['XT']}{code}"
                f"{1 if self.mem_ch else 0}0000;")

    async def _run(self) -> None:
        buf = bytearray()
        try:
            while True:
                buf += await self.t.read()
                while (i := buf.find(b";")) >= 0:
                    cmd = bytes(buf[:i]).decode("ascii", "replace")
                    del buf[: i + 1]
                    await self._handle(cmd)
        except TransportClosed:
            return

    async def _handle(self, c: str) -> None:
        self.log.append(c)
        if not self.powered:
            return
        if self.drop:
            self.drop -= 1
            return
        name, p = c[:2], c[2:]
        if name == "ID" and not p:
            return await self._send(f"ID{RADIO_ID};")
        if name == "AI":
            if p in ("0", "1"):
                self.ai = int(p)
                return
            return await self._send(f"AI{self.ai};")
        if name == "IF" and not p:
            return await self._send(self._if_frame())
        if name == "OI" and not p:
            return await self._send(f"OI001{self.freq_b:09d}+0000{self.reg['RT']}{self.reg['XT']}{MODE_CODES[self.mode_b]}00000;")
        if name in ("FA", "FB"):
            attr = "freq" if name == "FA" else "freq_b"
            if not p:
                return await self._send(f"{name}{getattr(self, attr):09d};")
            if len(p) == 9 and p.isdigit() and 30_000 <= int(p) <= 470_000_000:
                setattr(self, attr, int(p))
                return
            return await self._send("?;")
        if name == "MD" and p[:1] == "0":
            if len(p) == 1:
                return await self._send(f"MD0{MODE_CODES[self.mode]};")
            if len(p) == 2 and p[1] in MODES:
                self.mode = MODES[p[1]]
                return
            return await self._send("?;")
        if name == "TX":
            if not p:
                return await self._send(f"TX{self.tx};")
            if p in ("0", "1"):
                self.tx = int(p)
                return
            return await self._send("?;")
        if name == "SM" and p == "0":
            return await self._send(f"SM0{self.meters['1']:03d};")
        if name == "RM" and p in self.meters:
            return await self._send(f"RM{p}{self.meters[p]:03d};")
        if name == "PS":
            if p == "0":
                self.powered = False
                self.ai = 0
                await self.t.close()          # radio goes to standby: the link dies
            return
        if name == "BS" and len(p) == 2 and p.isdigit() and int(p) in BAND_START:
            self.freq = BAND_START[int(p)]
            return
        if name == "FT":
            if not p:
                return await self._send(f"FT{self.ft};")
            if p in ("2", "3"):
                self.ft = 1 if p == "3" else 0
                if self.ai:
                    await self._send(f"FT{self.ft};")
                return
            return await self._send("?;")
        if name == "QS" and not p:
            self.freq_b, self.ft = self.freq + 1000, 1
            if self.ai:
                await self._send(f"FB{self.freq_b:09d};FT1;")
            return
        if name == "SV" and not p:
            self.freq, self.freq_b = self.freq_b, self.freq
            self.mode, self.mode_b = self.mode_b, self.mode
            if self.ai:
                await self._send(f"FA{self.freq:09d};FB{self.freq_b:09d};")
            return
        if name == "AB" and not p:
            self.freq_b, self.mode_b = self.freq, self.mode
            if self.ai:
                await self._send(f"FB{self.freq_b:09d};")
            return
        if name == "BA" and not p:
            self.freq, self.mode = self.freq_b, self.mode_b
            if self.ai:
                await self._send(f"FA{self.freq:09d};")
            return
        if name == "MT" and len(p) == 3 and p.isdigit():            # memory read (the READ form only; there is no write in the simulator)
            ch = int(p)
            if ch not in self.memories:
                return await self._send("?;")
            hz, md, tag = self.memories[ch]
            label = 1 if self.label_001 else ch               # a real FT-991A writes 001 in the answer whatever channel was asked
            return await self._send(f"MT{label:03d}{hz:09d}+000000{MODE_CODES[md]}100000{tag.ljust(12)};")
        if name == "MR" and len(p) == 3 and p.isdigit():             # memory read without the tag (read form only)
            ch = int(p)
            if ch not in self.memories:
                return await self._send("?;")
            hz, md, _tag = self.memories[ch]
            label = 1 if self.label_001 else ch
            return await self._send(f"MR{label:03d}{hz:09d}+000000{MODE_CODES[md]}10000;")
        if name == "MC" and len(p) == 3 and p.isdigit():
            ch = int(p)
            if ch not in self.memories:
                return await self._send("?;")
            if not self.mem_ch:
                self.vfo_freq = (self.freq, self.mode)
            self.mem_ch = ch
            if not self.mc_keeps_vfo:
                self.freq, self.mode = self.memories[ch][0], self.memories[ch][1]
            return
        if name == "VM" and not p:
            if self.mem_ch:
                self.mem_ch = 0
                if self.vfo_freq:
                    self.freq, self.mode = self.vfo_freq
            else:
                first = min(self.memories)
                self.vfo_freq = (self.freq, self.mode)
                self.mem_ch = first
                self.freq, self.mode = self.memories[first][0], self.memories[first][1]
            return
        if name == "RC" and not p:
            self.clar = 0
            return
        if name in ("RU", "RD") and len(p) == 4 and p.isdigit():
            self.clar = int(p) * (1 if name == "RU" else -1)
            return
        if name == "AC" and p == "002":
            # START/STOP toggle, as documented. A stray AC002 on an idle radio STARTS a tune (the TxGuard must never send that).
            self.tune_calls += 1
            if not self.tuning:
                self.tuning, self.tx = True, 2
                self.meters["5"] = 120
                if self.ai:
                    await self._send("TX2;")
                self._tune_task = asyncio.create_task(self._finish_tune())
            else:
                await self._end_tune()
            return
        for k in sorted(self.reg, key=len, reverse=True):
            if c.startswith(k):
                rest = c[len(k):]
                default = self.reg_defaults[k]
                if rest == "":
                    return await self._send(f"{k}{self.reg[k]};")
                if k in ("PR0", "PR1") and rest not in ("0", "1"):      # real FT-991A: PR02; -> ?; (bench-verified)
                    return await self._send("?;")
                ok = len(rest) == len(default) and (rest[1:].isdigit() and rest[0] in "+-" if default[0] in "+-"
                                                    else rest.isdigit())
                if ok:
                    self.reg[k] = rest
                    return
                return await self._send("?;")
        key = name + (p[:1] if name in ("AG", "RG") else "")
        if key in self.levels:
            val = p[1:] if name in ("AG", "RG") else p
            if not val:
                return await self._send(f"{key}{self.levels[key]:03d};")
            if val.isdigit() and len(val) == 3:
                self.levels[key] = int(val)
                return
        await self._send("?;")
