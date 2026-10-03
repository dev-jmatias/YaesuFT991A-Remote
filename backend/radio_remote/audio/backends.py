"""Audio sources/sinks. All frames are FRAME_BYTES of mono s16le @ 48 kHz.

AlsaSource / AlsaSink drive `arecord` / `aplay` with fixed argv lists (no shell, config never concatenated).
ToneSource / RecordingSink are hardware-free test doubles.
"""
from __future__ import annotations

import asyncio
import logging
import math
from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

from .levels import FRAME_BYTES, FRAME_SAMPLES, RATE

log = logging.getLogger("audio")


class AudioDeviceError(Exception):
    pass


class Source(ABC):
    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def read_frame(self) -> bytes: ...
    @abstractmethod
    async def stop(self) -> None: ...


class Sink(ABC):
    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def write_frame(self, pcm: bytes) -> None: ...
    @abstractmethod
    async def stop(self) -> None: ...


def alsa_capture_argv(device: str) -> list[str]:
    return ["arecord", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1",
            "--period-time=20000", "--buffer-time=80000"]


def alsa_playback_argv(device: str) -> list[str]:
    return ["aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1",
            "--period-time=20000", "--buffer-time=100000"]


class AlsaSource(Source):
    def __init__(self, device: str, argv_builder: Callable[[str], list[str]] = alsa_capture_argv):
        self.device, self._argv = device, argv_builder
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv(self.device), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise AudioDeviceError(f"cannot start capture: {e}") from None

    async def read_frame(self) -> bytes:
        try:
            return await self._proc.stdout.readexactly(FRAME_BYTES)
        except asyncio.IncompleteReadError:
            err = (await self._proc.stderr.read(300)).decode("utf-8", "replace").strip()
            raise AudioDeviceError(f"capture ended: {err or 'device closed'}") from None

    async def stop(self) -> None:
        await _terminate(self._proc)
        self._proc = None


class AlsaSink(Sink):
    def __init__(self, device: str, argv_builder: Callable[[str], list[str]] = alsa_playback_argv):
        self.device, self._argv = device, argv_builder
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv(self.device), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise AudioDeviceError(f"cannot start playback: {e}") from None

    async def write_frame(self, pcm: bytes) -> None:
        p = self._proc
        if not p or p.returncode is not None:
            err = (await p.stderr.read(300)).decode("utf-8", "replace").strip() if p else ""
            raise AudioDeviceError(f"playback ended: {err or 'device closed'}")
        try:
            p.stdin.write(pcm)
            await p.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise AudioDeviceError("playback pipe closed") from None

    async def stop(self) -> None:
        await _terminate(self._proc)
        self._proc = None


async def _terminate(proc) -> None:
    if not proc or proc.returncode is not None:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        await asyncio.wait_for(proc.wait(), 2)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class ToneSource(Source):
    """Real-time-paced test tone (440 Hz at about -20 dBFS) so the whole chain can be exercised without a radio."""

    def __init__(self, freq: float = 440.0, level_dbfs: float = -20.0):
        self.freq, self.amp = freq, 10 ** (level_dbfs / 20)
        self._n = 0
        self._t0 = 0.0

    async def start(self) -> None:
        self._n, self._t0 = 0, asyncio.get_running_loop().time()

    async def read_frame(self) -> bytes:
        due = self._t0 + (self._n + 1) * FRAME_SAMPLES / RATE
        delay = due - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)
        t = (np.arange(FRAME_SAMPLES) + self._n * FRAME_SAMPLES) / RATE
        self._n += 1
        return (np.sin(2 * math.pi * self.freq * t) * self.amp * 32767).astype("<i2").tobytes()

    async def stop(self) -> None:
        pass


class RecordingSink(Sink):
    """Keeps what would have been sent to the radio (bounded)."""

    def __init__(self, keep: int = 500):
        self.frames: list[bytes] = []
        self.keep = keep
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def write_frame(self, pcm: bytes) -> None:
        self.frames.append(pcm)
        del self.frames[: -self.keep]

    async def stop(self) -> None:
        self.started = False
