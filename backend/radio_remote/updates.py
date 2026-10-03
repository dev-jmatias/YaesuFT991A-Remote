"""Optional update check: tells the administrators when a newer release exists on GitHub.

It only ASKS (one anonymous, read-only request to api.github.com about the repository's newest release, about once a day) and keeps the
answer. Nothing is downloaded or installed here: updating is done on the Pi with scripts/self_update.sh (or update.sh), after a backup.
Switch it off with [updates] check = false (Admin > Config > Updates).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

import aiohttp

from . import __version__

log = logging.getLogger("updates")

FIRST_CHECK_S = 90                 # let the radio and the audio come up first
EVERY_S = 24 * 3600
RETRY_S = 6 * 3600                 # after a failure (no internet, rate limit, ...)
_TAG = re.compile(r"v?(\d+(?:\.\d+){0,3})")


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.2.0.1' -> (1, 2, 0, 1); anything that is not a plain dotted number -> None (ignored)."""
    m = _TAG.fullmatch(text.strip()) if isinstance(text, str) and len(text) <= 40 else None
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))        # v1.0.0.0 == v1.0.0


async def fetch_latest_release(repo: str, timeout_s: float = 10.0) -> dict:
    """The newest published (not draft, not pre-release) release of `repo`: {tag, name, url, published}."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"radio-remote/{__version__}"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as s:
        async with s.get(url, headers=headers) as r:
            if r.status == 404:
                raise RuntimeError("no release has been published yet")
            if r.status != 200:
                raise RuntimeError(f"GitHub answered {r.status}")
            data = await r.json(content_type=None)
    tag, page = data.get("tag_name"), data.get("html_url")
    if not isinstance(tag, str) or parse_version(tag) is None:
        raise RuntimeError("the newest release has no usable version tag")
    if not isinstance(page, str) or not page.startswith(f"https://github.com/{repo}/"):
        page = f"https://github.com/{repo}/releases"                  # never show a link the server did not expect
    name = data.get("name") if isinstance(data.get("name"), str) else tag
    return {"tag": tag, "name": name[:120], "url": page, "published": str(data.get("published_at") or "")[:30]}


class UpdateChecker:
    """Holds the last answer; `settings()` returns the live [updates] section so a change in Admin > Config applies at once."""

    def __init__(self, settings: Callable[[], dict], current: str = __version__,
                 fetch: Callable[[str], Awaitable[dict]] = fetch_latest_release, clock: Callable[[], float] = time.time):
        self.settings, self.current, self._fetch, self._clock = settings, current, fetch, clock
        self.latest: dict | None = None
        self.checked_at: float | None = None
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.settings().get("check"))

    def state(self) -> dict:
        s = self.settings()
        latest = self.latest["tag"] if self.latest else None
        return {"enabled": bool(s.get("check")), "repo": s.get("repo"), "current": self.current, "latest": latest,
                "newer": bool(latest and is_newer(latest, self.current)), "url": self.latest["url"] if self.latest else None,
                "name": self.latest["name"] if self.latest else None, "published": self.latest["published"] if self.latest else None,
                "checked_at": self.checked_at, "error": self.error}

    async def check(self) -> dict:
        """Ask GitHub now (does nothing when the check is switched off). Failures are kept as a message, never raised."""
        async with self._lock:
            if not self.enabled:
                return self.state()
            try:
                self.latest = await self._fetch(self.settings()["repo"])
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:                                   # no internet is normal for a radio shack: stay quiet
                self.error = f"{type(e).__name__}: {e}"[:200]
                log.info("update check failed: %s", self.error)
            self.checked_at = self._clock()
            if self.latest and is_newer(self.latest["tag"], self.current):
                log.info("a newer release is available: %s (this is %s)", self.latest["tag"], self.current)
            return self.state()

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_CHECK_S)
        while True:
            if self.enabled:
                await self.check()
            await asyncio.sleep(RETRY_S if self.error else EVERY_S)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
