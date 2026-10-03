"""Async CAT command client: one command in flight, priorities, timeouts, unsolicited frames."""
from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Callable

from ..base import RadioError
from .frame import split_frames
from .transport import Transport, TransportClosed

log = logging.getLogger("cat")

PRIO_PTT, PRIO_USER, PRIO_METER, PRIO_POLL = 0, 1, 2, 3


class CatError(RadioError):
    pass


class CatTimeout(CatError):
    pass


class CatRejected(CatError):
    """Radio answered '?;'."""


class CatDisconnected(CatError):
    pass


@dataclass(order=True)
class _Job:
    prio: int
    seq: int
    cmd: str = field(compare=False)
    expect: str | None = field(compare=False)
    fut: asyncio.Future = field(compare=False)


class CatClient:
    def __init__(self, transport: Transport, on_frame: Callable[[str], None], timeout: float = 1.0):
        self.transport, self.on_frame, self.timeout = transport, on_frame, timeout
        self._q: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._inflight: _Job | None = None
        self._tasks: list[asyncio.Task] = []
        self.closed = asyncio.Event()
        self.stats = {"tx": 0, "rx": 0, "timeouts": 0, "rejected": 0, "unsolicited": 0}

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._worker())]

    async def close(self) -> None:
        self._fail_all(CatDisconnected("client closed"))
        self.closed.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.transport.close()

    def _submit(self, cmd: str, expect: str | None, prio: int) -> asyncio.Future:
        if self.closed.is_set():
            raise CatDisconnected("not connected")
        fut = asyncio.get_running_loop().create_future()
        self._q.put_nowait(_Job(prio, next(self._seq), cmd, expect, fut))
        return fut

    async def request(self, cmd: str, *, prio: int = PRIO_USER, expect: str | None = None) -> str:
        """Send a read command and return the matching answer frame."""
        return await self._submit(cmd, expect or cmd[:-1], prio)

    async def send(self, cmd: str, *, prio: int = PRIO_USER) -> None:
        """Send a set command (the radio does not answer successful sets)."""
        await self._submit(cmd, None, prio)

    # ------------------------------------------------------------------ internals
    def _fail_all(self, exc: Exception) -> None:
        jobs = [self._inflight] if self._inflight else []
        self._inflight = None
        while not self._q.empty():
            jobs.append(self._q.get_nowait())
        for j in jobs:
            if j and not j.fut.done():
                j.fut.set_exception(exc)

    async def _reader(self) -> None:
        buf = bytearray()
        try:
            while True:
                buf += await self.transport.read()
                for frame in split_frames(buf):
                    self.stats["rx"] += 1
                    self._dispatch(frame)
        except TransportClosed as e:
            log.warning("CAT link lost: %s", e)
        except asyncio.CancelledError:
            raise
        self._fail_all(CatDisconnected("link lost"))
        self.closed.set()

    def _dispatch(self, frame: str) -> None:
        job = self._inflight
        if frame == "?;":
            self.stats["rejected"] += 1
            if job and not job.fut.done():
                job.fut.set_exception(CatRejected(f"radio rejected {job.cmd}"))
            else:
                log.warning("radio replied '?;' to a command that expects no answer")
            return
        if job and job.expect and frame.startswith(job.expect) and not job.fut.done():
            job.fut.set_result(frame)
        else:
            self.stats["unsolicited"] += 1
        self.on_frame(frame)

    async def _worker(self) -> None:
        try:
            while True:
                job: _Job = await self._q.get()
                if job.fut.done():
                    continue
                self._inflight = job
                try:
                    await self.transport.write(job.cmd.encode("ascii"))
                except TransportClosed:
                    if not job.fut.done():
                        job.fut.set_exception(CatDisconnected("write failed"))
                    self._inflight = None
                    self.closed.set()
                    return
                self.stats["tx"] += 1
                if job.expect is None:
                    # No answer expected, but a '?;' may follow quickly; give it a beat to be attributed.
                    await asyncio.sleep(0.02)
                    if not job.fut.done():
                        job.fut.set_result(None)
                else:
                    try:
                        await asyncio.wait_for(asyncio.shield(job.fut), self.timeout)
                    except asyncio.TimeoutError:
                        self.stats["timeouts"] += 1
                        if not job.fut.done():
                            job.fut.set_exception(CatTimeout(f"no answer to {job.cmd}"))
                    except CatError:
                        pass
                self._inflight = None
        except asyncio.CancelledError:
            raise
