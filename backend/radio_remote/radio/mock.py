"""Simulated radio. Never touches hardware; safe to key."""
from __future__ import annotations

import asyncio
import random

from . import controls
from .bands import band_for
from .base import Capabilities, RadioDriver, RadioError
from .cat import ft991a_controls as fc


class MockDriver(RadioDriver):
    def __init__(self, caps: Capabilities | None = None, seed: int | None = None, tick_s: float = 0.1):
        super().__init__(caps or Capabilities.load("mock"))
        self._rng = random.Random(seed)
        self._tick_s = tick_s
        self._task: asyncio.Task | None = None
        self.ptt_calls: list[bool] = []  # for tests
        self.state.update(
            connected=False, model="mock", frequency=14_200_000, band="20m", mode="USB",
            tx=False, af_gain=80, rf_gain=255, mic_gain=50, rf_power=50,
            smeter=0, swr=1.0, alc=0, comp=0, rf_power_out=0,
            narrow=False, width_code=0, if_shift=0, contour=False, contour_freq=1500, apf=False, apf_freq=0,
            notch=False, notch_freq=1500, auto_notch=False, nr=False, nr_level=5, nb=False, nb_level=5,
            ipo="IPO", att=False, rit=False, xit=False, clarifier_hz=0, processor=False, processor_level=50,
            monitor=False, monitor_level=50, tuner=False, tuning=False, dgid="AUTO", agc="AUTO", att_level="OFF", mic_select="REAR", frequency_b=7_100_000, band_b="40m", mode_b="LSB", split=False,
        )
        self.state.update(vfo_memory="vfo", memory_channel=None)
        self.memories = [                                    # the simulated radio's stored channels
            {"channel": 1, "frequency": 3_573_000, "mode": "DATA-U", "tag": "FT8 80m"},
            {"channel": 2, "frequency": 7_074_000, "mode": "DATA-U", "tag": "FT8 40m"},
            {"channel": 3, "frequency": 14_074_000, "mode": "DATA-U", "tag": "FT8 20m"},
            {"channel": 5, "frequency": 14_200_000, "mode": "USB", "tag": "20m DX"},
            {"channel": 6, "frequency": 7_100_000, "mode": "LSB", "tag": "40m net"},
            {"channel": 11, "frequency": 145_500_000, "mode": "FM", "tag": "2m calling"},
            {"channel": 12, "frequency": 433_500_000, "mode": "FM", "tag": "70cm calling"},
            {"channel": 99, "frequency": 28_400_000, "mode": "USB", "tag": "10m"},
        ]
        for m in self.memories:
            m["band"] = band_for(m["frequency"])
        self.tune_calls: list[str] = []           # for tests: "start" / "stop"
        self.tune_duration_s = 1.5                # how long the simulated tune transmits
        self._tune_task: asyncio.Task | None = None
        self._refresh_width()

    async def start(self) -> None:
        self._update(connected=True)
        self._task = asyncio.create_task(self._simulate())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._update(connected=False)

    async def _simulate(self) -> None:
        s = 40
        while True:
            await asyncio.sleep(self._tick_s)
            if self.state["tx"]:
                p = self.state["rf_power"]
                self._update(
                    rf_power_out=int(p * (0.9 + 0.1 * self._rng.random())),
                    swr=round(1.1 + 0.2 * self._rng.random(), 2),
                    alc=self._rng.randint(20, 60), comp=self._rng.randint(0, 40), smeter=0,
                )
            else:
                s = max(0, min(255, s + self._rng.randint(-12, 12)))
                self._update(smeter=s, rf_power_out=0, alc=0, comp=0, swr=1.0)

    async def memory_channels(self, refresh: bool = False) -> list[dict]:
        return [{"tone_mode": "off", "shift": "simplex", **m} for m in self.memories]

    async def memory_select(self, channel: int) -> None:
        m = next((x for x in self.memories if x["channel"] == channel), None)
        if m is None:
            raise RadioError("that memory channel is empty")
        if self.state["tx"]:
            raise RadioError("cannot change channel while transmitting")
        self._update(frequency=m["frequency"], band=band_for(m["frequency"]), mode=m["mode"], vfo_memory="memory", memory_channel=channel)
        self._refresh_width()

    async def memory_to_vfo(self) -> None:
        self._update(vfo_memory="vfo", memory_channel=None)

    async def memory_tone(self, channel: int) -> dict:
        m = next((x for x in self.memories if x["channel"] == channel), None)
        if m is None:
            raise RadioError("that memory channel is empty")
        return {"tone_mode": m.get("tone_mode", "off"), "tone_hz": m.get("tone_hz") or 67.0, "dcs_code": m.get("dcs_code") or "023", "shift": m.get("shift", "simplex")}

    async def memory_delete(self, channel: int) -> None:
        if self.state["tx"]:
            raise RadioError("not while transmitting")
        if not any(m["channel"] == channel for m in self.memories):
            raise RadioError(f"channel {channel} is already empty")
        self.memories = [m for m in self.memories if m["channel"] != channel]

    async def memory_write(self, channel: int, frequency: int, mode: str, tone_mode: str = "off", shift: str = "simplex", name: str = "",
                           tone_hz: float | None = None, dcs_code: str | None = None) -> dict:
        from .cat import frame
        if self.state["tx"]:
            raise RadioError("not while transmitting")
        try:
            frame.memory_write(channel, frequency, mode, tone_mode, shift, name)               # the same checks as for the real radio
            if tone_mode in ("ctcss_enc", "ctcss_encdec"):
                if tone_hz is None:
                    raise frame.FrameError("a tone frequency is needed for this tone mode")
                frame.tone_index(tone_hz)
            if tone_mode in ("dcs_enc", "dcs_encdec"):
                if dcs_code is None:
                    raise frame.FrameError("a DCS code is needed for this tone mode")
                frame.dcs_index(dcs_code)
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        item = {"channel": channel, "frequency": frequency, "mode": mode, "tag": name.strip(), "tone_mode": tone_mode, "shift": shift, "band": band_for(frequency),
                "tone_hz": tone_hz if tone_mode.startswith("ctcss") else None, "dcs_code": dcs_code if tone_mode.startswith("dcs") else None}
        self.memories = sorted([m for m in self.memories if m["channel"] != channel] + [item], key=lambda m: m["channel"])
        return dict(item)

    async def set_frequency(self, hz: int) -> None:
        self._update(frequency=hz, band=band_for(hz))

    async def set_mode(self, mode: str) -> None:
        self._update(mode=mode, width_code=0)
        self._refresh_width()

    async def set_level(self, name: str, value: int) -> None:
        self._update(**{name: value})

    async def vfo_op(self, op: str) -> None:
        s = self.state
        a, b = s["frequency"], s["frequency_b"]
        ma, mb = s["mode"], s.get("mode_b", s["mode"])
        if op == "swap":
            a, b, ma, mb = b, a, mb, ma
        elif op == "a_to_b":
            b, mb = a, ma
        elif op == "b_to_a":
            a, ma = b, mb
        elif op == "quick_split":
            b = a + 5_000                                  # the radio uses the offset from menu 035; the mock just picks one
            self._update(split=True)
        else:
            raise RadioError("unknown VFO operation")
        self._update(frequency=a, band=band_for(a), mode=ma, frequency_b=b, band_b=band_for(b), mode_b=mb)

    async def set_frequency_b(self, hz: int) -> None:
        self._update(frequency_b=hz, band_b=band_for(hz))

    async def set_split(self, on: bool) -> None:
        self._update(split=bool(on))

    async def tune_start(self) -> None:
        self.tune_calls.append("start")
        self._update(tx=True)
        self._tune_task = asyncio.create_task(self._finish_tune())

    async def _finish_tune(self) -> None:
        await asyncio.sleep(self.tune_duration_s)
        self._update(tx=False)

    async def tune_stop(self) -> None:
        if not self.state["tx"]:
            return                                 # nothing to stop (a second AC002 would START a tune on the real radio)
        self.tune_calls.append("stop")
        if self._tune_task:
            self._tune_task.cancel()
        self._update(tx=False)

    async def set_ptt(self, on: bool) -> None:
        self.ptt_calls.append(on)
        self._update(tx=on)

    def _refresh_width(self) -> None:
        s = self.state
        self._update(width_options=fc.width_options(s["mode"], s["narrow"]),
                     width=fc.width_hz(s["mode"], s["narrow"], s["width_code"]))

    async def set_control(self, name: str, value, receiver: str = "main") -> None:
        spec = controls.SPEC_BY_NAME.get(name)
        if not spec or not self.caps.has(spec["feature"]):
            raise RadioError(f"control {name} not supported")
        value = controls.coerce(spec, value)
        if name == "mic_select" and self.state.get("tx"):
            raise RadioError("cannot change the mic input while transmitting")
        if name == "width":
            code = {o["hz"]: o["code"] for o in self.state["width_options"]}.get(value)
            if code is None:
                raise RadioError("width not available in this mode / filter setting")
            self._update(width_code=code)
        else:
            self._update(**{name: value})
        self._refresh_width()

    async def set_band(self, band: str) -> None:
        from .bands import band_start
        if band not in self.caps.data["bands"]["list"]:
            raise RadioError("unsupported band")
        await self.set_frequency(band_start(band))
