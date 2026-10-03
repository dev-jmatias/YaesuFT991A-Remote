"""Minimal systemd notification (sd_notify) and watchdog, stdlib only. A no-op when not run by systemd.

READY=1     sent once the app has started (the unit uses Type=notify)
WATCHDOG=1  sent periodically while the event loop and the TX safety watchdog are alive. If either dies,
            pings stop and systemd restarts the service (WatchdogSec in the unit).
The radio being switched off or unplugged is NOT a reason to restart: it is normal and handled by reconnect logic.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

log = logging.getLogger("systemd")


def notify(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):                       # abstract namespace socket
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode())
        return True
    except (OSError, AttributeError) as e:         # AttributeError: no AF_UNIX (Windows)
        log.warning("sd_notify failed: %s", e)
        return False


def watchdog_interval_s() -> float | None:
    """Half of systemd's WatchdogSec, or None when the watchdog is not enabled for this process."""
    try:
        usec = int(os.environ.get("WATCHDOG_USEC", ""))
    except ValueError:
        return None
    if usec <= 0:
        return None
    pid = os.environ.get("WATCHDOG_PID")
    if pid and pid != str(os.getpid()):
        return None
    return usec / 2_000_000


class Watchdog:
    def __init__(self, healthy, interval_s: float | None = None):
        self.healthy = healthy
        self.interval = interval_s if interval_s is not None else watchdog_interval_s()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        notify("READY=1")
        if self.interval:
            self._task = asyncio.create_task(self._run())
            log.info("systemd watchdog every %.1fs", self.interval)

    async def _run(self) -> None:
        while True:
            try:
                ok = bool(self.healthy())
            except Exception:
                ok = False
            if ok:
                notify("WATCHDOG=1")
            else:
                log.error("health check failed; withholding watchdog ping (systemd will restart us)")
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        notify("STOPPING=1")
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
