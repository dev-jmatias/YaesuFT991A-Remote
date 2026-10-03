"""Control lease: many clients may watch, exactly one holds control.

Control means: changing the radio, PTT, power, and sending mic audio. The holder is a *connection*
(one browser tab), not just a user, so a second tab of the same person must take control explicitly.

Transfer rules
  * free lease           -> an operator/admin who asks (or connects first) gets it
  * same user, new device-> immediate hand-over (refused while transmitting)
  * different user       -> holder is asked; accept/deny; no answer in REQUEST_TIMEOUT_S = granted
  * never while the transmitter is keyed (the holder must release PTT first)
  * admin force          -> immediate, un-keys first
The lease never keys or un-keys anything itself except through the TX guard.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("lease")

REQUEST_TIMEOUT_S = 10.0


class LeaseError(Exception):
    pass


@dataclass
class Holder:
    conn: str
    user: str


@dataclass
class Pending:
    conn: str
    user: str


class ControlLease:
    def __init__(self, *, guard, broadcast: Callable[[dict], None], send_to: Callable[[str, dict], None],
                 audit: Callable[[str, str, str], None] = lambda *a: None, timeout_s: float = REQUEST_TIMEOUT_S):
        self.guard, self.broadcast, self.send_to, self.audit, self.timeout_s = guard, broadcast, send_to, audit, timeout_s
        self.holder: Holder | None = None
        self.pending: Pending | None = None
        self._timer: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------ state
    def state(self) -> dict:
        return {
            "holder": {"conn": self.holder.conn, "user": self.holder.user} if self.holder else None,
            "pending": {"user": self.pending.user, "conn": self.pending.conn} if self.pending else None,
            "timeout_s": self.timeout_s,
        }

    def holds(self, conn: str) -> bool:
        return self.holder is not None and self.holder.conn == conn

    def _publish(self) -> None:
        self.broadcast({"t": "lease", "d": self.state()})

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    async def _grant(self, conn: str, user: str, reason: str) -> None:
        old = self.holder
        self._cancel_timer()
        self.pending = None
        if old and old.conn != conn:
            await self.guard.release_owner(old.conn)          # the previous holder can never stay keyed
        self.holder = Holder(conn, user)
        log.info("control -> %s (%s) [%s]", user, conn, reason)
        self.audit("control_granted", user, reason)
        self._publish()

    # ------------------------------------------------------------- operations
    async def on_connect(self, conn: str, user: str, role: str) -> None:
        """A free lease goes to the first operator/admin to show up."""
        if self.holder is None and role in ("operator", "admin"):
            await self._grant(conn, user, "free on connect")
        else:
            self.send_to(conn, {"t": "lease", "d": self.state()})

    async def on_disconnect(self, conn: str) -> None:
        if self.pending and self.pending.conn == conn:
            self._cancel_timer()
            self.pending = None
            self._publish()
        if self.holds(conn):
            self.holder = None
            self._cancel_timer()
            nxt, self.pending = self.pending, None
            if nxt:
                await self._grant(nxt.conn, nxt.user, "holder disconnected")
            else:
                self.audit("control_released", "", "holder disconnected")
                self._publish()

    async def request(self, conn: str, user: str, role: str, trusted: bool = False) -> str:
        if role not in ("operator", "admin"):
            raise LeaseError("this account cannot take control")
        if self.holds(conn):
            return "already"
        if self.holder is None:
            await self._grant(conn, user, "requested, was free")
            return "granted"
        if self.guard.keyed:
            raise LeaseError(f"{self.holder.user} is transmitting; try again after they release PTT")
        if self.holder.user == user:
            await self._grant(conn, user, "same user, other device")
            return "granted"
        if trusted:
            old = self.holder
            await self._grant(conn, user, "trusted user")
            self.send_to(old.conn, {"t": "lease_taken", "by": user})      # tell the previous holder who took over
            return "granted"
        if self.pending:
            raise LeaseError(f"{self.pending.user} has already asked for control")
        self.pending = Pending(conn, user)
        self.send_to(self.holder.conn, {"t": "lease_request", "from": user, "timeout_s": self.timeout_s})
        self._timer = asyncio.get_running_loop().call_later(self.timeout_s, lambda: asyncio.ensure_future(self._expire(conn)))
        self._publish()
        return "pending"

    async def _expire(self, conn: str) -> None:
        if self.pending and self.pending.conn == conn:
            if self.guard.keyed:                                # still transmitting: keep waiting rather than cut them off
                self._timer = asyncio.get_running_loop().call_later(2.0, lambda: asyncio.ensure_future(self._expire(conn)))
                return
            p = self.pending
            await self._grant(p.conn, p.user, "holder did not answer")

    async def respond(self, conn: str, accept: bool) -> None:
        if not self.holds(conn):
            raise LeaseError("only the current holder can answer")
        if not self.pending:
            raise LeaseError("no request pending")
        p = self.pending
        if accept:
            if self.guard.keyed:
                raise LeaseError("release PTT before handing over control")
            await self._grant(p.conn, p.user, "holder accepted")
        else:
            self._cancel_timer()
            self.pending = None
            self.send_to(p.conn, {"t": "lease_denied", "by": self.holder.user})
            self._publish()

    async def release(self, conn: str) -> None:
        if not self.holds(conn):
            raise LeaseError("you do not hold control")
        if self.guard.keyed:
            await self.guard.release_owner(conn)
        self.holder = None
        self._cancel_timer()
        nxt, self.pending = self.pending, None
        self.audit("control_released", "", "released")
        if nxt:
            await self._grant(nxt.conn, nxt.user, "holder released")
        else:
            self._publish()

    async def force(self, conn: str, user: str) -> None:
        """Admin override: un-key first, then take control."""
        await self.guard.emergency_unkey("admin forced control")
        await self._grant(conn, user, "admin forced")
