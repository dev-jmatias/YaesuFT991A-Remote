"""Range scan: step the VFO between a start and a stop frequency, listen at every step, wait on a signal, and start over at the end.

The radio's own scan (CAT `SC`) cannot be given limits, so this one runs here: set the frequency, read the S-meter, move on. It only ever receives (it stops as soon as
anything would transmit) and it belongs to the connection that holds the control lease: when that connection leaves or loses control, or any other command touches the radio's
frequency, mode or memory, the scan ends where it is.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .radio.base import RadioError

log = logging.getLogger("radio_remote.scan")

MIN_STEP_HZ = 10
MAX_STEPS = 20000          # frequencies in one pass (a full 10 MHz at 500 Hz is 20000)
SETTLE_S = 0.08            # let the receiver settle after a frequency change, before the S-meter is read
HOLD_POLL_S = 0.25         # how often the signal is checked while the scan waits on it
MAX_HOLD_S = 30.0


class ScanError(Exception):
    pass


class RangeScanner:
    def __init__(self, hub) -> None:
        self.hub = hub
        self.owner: str | None = None
        self.params: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._holding = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ----------------------------------------------------------------- control
    async def start(self, conn_id: str, start: int, stop: int, step: int, level: int, hold_s: float, stop_on_signal: bool) -> None:
        d, caps = self.hub.driver, self.hub.driver.caps
        if not d.state.get("connected"):
            raise ScanError("the radio is not connected")
        if self.hub.guard.keyed or d.state.get("tx") or d.state.get("tuning"):
            raise ScanError("not while transmitting")
        if d.state.get("vfo_memory") == "memory":
            raise ScanError("the radio is in memory mode: go back to the VFO first")
        if not (caps.frequency_ok(start) and caps.frequency_ok(stop)):
            raise ScanError("frequency outside radio coverage")
        if stop <= start:
            raise ScanError("the stop frequency must be above the start frequency")
        if step < MIN_STEP_HZ or (stop - start) // step + 1 > MAX_STEPS:
            raise ScanError(f"step too small for that range (at most {MAX_STEPS} frequencies, step at least {MIN_STEP_HZ} Hz)")
        if not 0 <= level <= 255:
            raise ScanError("signal level must be 0..255")
        if not 0 <= hold_s <= MAX_HOLD_S:
            raise ScanError(f"wait must be 0..{MAX_HOLD_S:.0f} s")
        await self.stop("restarted")
        if d.state.get("scan") and caps.has("scan"):                 # the radio's own scan would fight this one
            await d.set_control("scan", False)
        self.owner = conn_id
        self.params = {"start": start, "stop": stop, "step": step, "level": level, "hold_s": hold_s, "stop_on_signal": stop_on_signal}
        self._holding = False
        self._publish()
        self._task = asyncio.create_task(self._run(), name="range-scan")

    async def start_list(self, conn_id: str, channels: list[int], level: int, hold_s: float, stop_on_signal: bool) -> None:
        """Scan only the chosen memory channels, in order, and start again at the first one. Receive only, like the range scan."""
        d = self.hub.driver
        if not d.state.get("connected"):
            raise ScanError("the radio is not connected")
        if self.hub.guard.keyed or d.state.get("tx") or d.state.get("tuning"):
            raise ScanError("not while transmitting")
        if not (isinstance(channels, list) and all(isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 99 for c in channels)) or len(set(channels)) != len(channels):
            raise ScanError("the scan list must hold memory channels 1..99, each once")
        if len(channels) < 2:
            raise ScanError("choose at least two channels for the scan list")
        if not 0 <= level <= 255:
            raise ScanError("signal level must be 0..255")
        if not 0 <= hold_s <= MAX_HOLD_S:
            raise ScanError(f"wait must be 0..{MAX_HOLD_S:.0f} s")
        await self.stop("restarted")
        if d.state.get("scan") and d.caps.has("scan"):
            await d.set_control("scan", False)
        self.owner = conn_id
        self.params = {"channels": list(channels), "level": level, "hold_s": hold_s, "stop_on_signal": stop_on_signal}
        self._holding = False
        self._publish()
        self._task = asyncio.create_task(self._run(), name="range-scan")

    async def stop(self, reason: str = "stopped") -> None:
        t, self._task = self._task, None
        if t is not None and not t.done() and t is not asyncio.current_task():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
            log.info("range scan ended: %s", reason)
        if self.params is not None:
            self.params, self.owner, self._holding = None, None, False
            self._publish()

    def _publish(self) -> None:
        p = self.params
        self.hub.push({"range_scan": None if p is None else {**p, "holding": self._holding}})

    def _must_stop(self) -> str | None:
        hub, d = self.hub, self.hub.driver
        if hub.guard.keyed or d.state.get("tx") or d.state.get("tuning"):
            return "transmitting"
        if not d.state.get("connected"):
            return "the radio disconnected"
        if self.owner is None or not hub.lease.holds(self.owner):
            return "control was released"
        return None

    # -------------------------------------------------------------------- loop
    async def _run(self) -> None:
        d, p = self.hub.driver, self.params
        assert p is not None
        reason = "finished"
        chans = p.get("channels")
        f = p["start"] if chans is None else 0                       # a frequency, or the position in the channel list
        failed = 0                                                   # channels in a row that could not be recalled (emptied in the radio since the list was made)
        try:
            while True:
                why = self._must_stop()
                if why:
                    reason = why
                    return
                if chans is None:
                    await d.set_frequency(f)
                else:
                    try:
                        await d.memory_select(chans[f])
                        failed = 0
                    except RadioError as e:
                        failed += 1
                        if failed >= len(chans) or "transmitting" in str(e):
                            reason = f"error: {e}"
                            return
                        f = (f + 1) % len(chans)
                        continue
                level = await d.read_signal(SETTLE_S)
                if level is not None and level >= p["level"]:
                    if p["stop_on_signal"]:
                        reason = f"signal found at {f} Hz" if chans is None else f"signal found on channel {chans[f]}"
                        return
                    why = await self._wait_out_signal(p)
                    if why:
                        reason = why
                        return
                if chans is not None:
                    f = (f + 1) % len(chans)                         # the loop: back to the first channel
                    continue
                f += p["step"]
                if f > p["stop"]:
                    f = p["start"]                                   # the loop: back to the start
        except asyncio.CancelledError:
            raise
        except Exception as e:                                       # a radio error (offline, refused command): end the scan, keep the server running
            reason = f"error: {e}"
            log.warning("range scan failed: %s", e)
        finally:
            if self._task is asyncio.current_task():
                self._task = None
            if self.params is p:
                self.params, self.owner, self._holding = None, None, False
                self._publish()
                log.info("range scan ended: %s", reason)

    async def _wait_out_signal(self, p: dict[str, Any]) -> str | None:
        """Stay on this frequency while the signal is there and for hold_s seconds after it has gone. Returns a reason when the scan must end."""
        d = self.hub.driver
        self._holding = True
        self._publish()
        quiet_since: float | None = None
        try:
            while True:
                await asyncio.sleep(HOLD_POLL_S)
                why = self._must_stop()
                if why:
                    return why
                level = d.state.get("smeter")
                if level is not None and level >= p["level"]:
                    quiet_since = None
                else:
                    now = time.monotonic()
                    quiet_since = quiet_since if quiet_since is not None else now
                    if now - quiet_since >= p["hold_s"]:
                        return None
        finally:
            self._holding = False
            if self.params is p:
                self._publish()
