"""Server-side TX safety. The browser is never trusted to un-key the radio.

Rules enforced here:
  * PTT on a real radio requires safety.allow_ptt = true in the config file.
  * One PTT owner (a WebSocket connection); others are refused while keyed.
  * Owner must send heartbeats; silence, disconnect or timeout => un-key.
  * Hard maximum TX duration; after it fires, PTT is locked until the owner releases.
  * Un-key is retried until it succeeds.

Antenna-tuner "TUNE" (the radio transmits a carrier while it matches the antenna) is a transmission too, so it goes
through the same guard: same permission flag, same single owner, own hard time limit, stopped if the owner disconnects.
A tune is a single click, not a hold, so it needs no heartbeat.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

log = logging.getLogger("ptt")

MIN_ON_INTERVAL_S = 0.15
TUNE_MAX_S = 15.0           # a tune normally takes a few seconds; never allow longer
TUNE_SETTLE_S = 3.0         # if the radio is not transmitting this long after the start, the tune is over (or never began)


class TxRefused(Exception):
    pass


class TxGuard:
    def __init__(self, driver, *, allow_ptt: bool, max_tx_s: float, heartbeat_timeout_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic):
        self.driver = driver
        self.allow_ptt = allow_ptt
        self.max_tx_s = max_tx_s
        self.hb_timeout = heartbeat_timeout_s
        self.clock = clock
        self.owner: str | None = None
        self.keyed = False
        self.tuning = False
        self._keyed_at = 0.0
        self._last_hb = 0.0
        self._last_on_request = -1e9
        self._retry_needed = False
        self._locked = False          # set after a timeout until owner sends "off"
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def permitted(self) -> bool:
        return self.allow_ptt or self.driver.is_mock

    @property
    def transmitting(self) -> bool:
        """Does the radio itself say RF is going out (keyed state or any power-meter reading)?"""
        st = self.driver.state
        return bool(st.get("tx")) or (st.get("po_raw") or 0) > 0 or (st.get("rf_power_out") or 0) > 0

    # ------------------------------------------------------------------ PTT
    async def request(self, owner: str, on: bool) -> None:
        async with self._lock:
            if on:
                if not self.permitted:
                    raise TxRefused("PTT is disabled (safety.allow_ptt = false)")
                if not self.driver.state.get("connected"):
                    raise TxRefused("radio not connected")
                if self._locked:
                    raise TxRefused("PTT locked after timeout; release first")
                if self.tuning:
                    raise TxRefused("the antenna tuner is tuning; wait for it to finish")
                if self.keyed:
                    if owner != self.owner:
                        raise TxRefused("another client holds PTT")
                    self._last_hb = self.clock()
                    return
                now = self.clock()
                if now - self._last_on_request < MIN_ON_INTERVAL_S:
                    raise TxRefused("PTT requests too frequent")
                self._last_on_request = now
                await self.driver.set_ptt(True)
                self.keyed, self.owner = True, owner
                self._keyed_at = self._last_hb = now
                log.info("PTT on owner=%s", owner)
            else:
                if self.keyed and owner != self.owner:
                    raise TxRefused("only the PTT owner can release")
                if owner == self.owner or not self.keyed:
                    self._locked = False
                await self._unkey("requested")

    # ----------------------------------------------------------------- TUNE
    async def start_tune(self, owner: str) -> None:
        async with self._lock:
            if not self.permitted:
                raise TxRefused("transmitting is disabled (safety.allow_ptt = false)")
            if not self.driver.state.get("connected"):
                raise TxRefused("radio not connected")
            if self._locked:
                raise TxRefused("transmit is locked after a timeout; press PTT once to reset")
            if self.keyed or self.tuning:
                raise TxRefused("already transmitting")
            now = self.clock()
            if now - self._last_on_request < 1.0:
                raise TxRefused("tune requested too soon after the last transmission")
            self._last_on_request = now
            await self.driver.tune_start()                 # raises RadioError if the radio refuses
            self.keyed, self.tuning, self.owner = True, True, owner
            self._keyed_at = self._last_hb = now
            self.driver.publish(tuning=True)
            log.warning("TUNE started owner=%s", owner)

    async def stop_tune(self, owner: str) -> None:
        async with self._lock:
            if not self.tuning:
                return
            if owner != self.owner:
                raise TxRefused("only the client that started the tune can stop it")
            await self._unkey("tune stopped by user")

    # ------------------------------------------------------------- housekeeping
    async def heartbeat(self, owner: str) -> None:
        if self.keyed and owner == self.owner:
            self._last_hb = self.clock()

    async def release_owner(self, owner: str) -> None:
        """Called when a connection closes for any reason."""
        if self.keyed and owner == self.owner:
            async with self._lock:
                await self._unkey("owner disconnected")

    async def emergency_unkey(self, reason: str = "emergency") -> None:
        async with self._lock:
            await self._unkey(reason)

    def _forget(self) -> None:
        was_tuning = self.tuning
        self.keyed, self.tuning, self.owner = False, False, None
        if was_tuning:
            self.driver.publish(tuning=False)

    async def _unkey(self, reason: str) -> None:
        if not self.keyed:
            return
        try:
            if self.tuning:
                await self.driver.tune_stop()              # only ever sends a stop while the radio is really transmitting
            else:
                await self.driver.set_ptt(False)
        except Exception:
            log.exception("failed to un-key (%s); will retry", reason)
            self._retry_needed = True
            return                    # keyed stays True -> watchdog retries
        self._retry_needed = False
        log.info("%s off (%s) owner=%s", "TUNE" if self.tuning else "PTT", reason, self.owner)
        self._forget()

    async def tick(self) -> None:
        """Watchdog step; also called by tests with a fake clock."""
        if not self.keyed:
            return
        now = self.clock()
        reason = None
        if self.tuning:
            elapsed = now - self._keyed_at
            if not self.transmitting and elapsed > TUNE_SETTLE_S and not self._retry_needed:
                log.info("tune finished (radio is no longer transmitting)")
                self._forget()
                return
            if elapsed > min(TUNE_MAX_S, self.max_tx_s):
                reason = "tune timeout"
            if reason or self._retry_needed:
                async with self._lock:
                    await self._unkey(reason or "retry")
            return
        if self.driver.state.get("connected") and self.driver.state.get("tx") is False and not self._retry_needed:
            # Radio un-keyed itself (e.g. CAT reconnect released it): drop our belief, never re-key silently.
            log.warning("radio is no longer keyed; clearing PTT ownership")
            self.keyed, self.owner = False, None
            return
        if now - self._keyed_at > self.max_tx_s:
            reason = "TX timeout"
            self._locked = True
        elif now - self._last_hb > self.hb_timeout:
            reason = "heartbeat lost"
        if reason or self._retry_needed:
            async with self._lock:
                await self._unkey(reason or "retry")

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.emergency_unkey("shutdown")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            try:
                await self.tick()
            except Exception:
                log.exception("watchdog error")
