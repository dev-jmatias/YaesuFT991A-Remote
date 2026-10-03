"""Byte transports for CAT: real serial, in-memory (tests/simulator), port detection."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from abc import ABC, abstractmethod

log = logging.getLogger("cat")

SILABS_VID = 0x10C4       # CP210x bridge used by the FT-991A USB interface (VID only; PID not assumed)


class TransportClosed(Exception):
    pass


class PortBusy(Exception):
    pass


class Transport(ABC):
    @abstractmethod
    async def open(self) -> None: ...
    @abstractmethod
    async def close(self) -> None: ...
    @abstractmethod
    async def write(self, data: bytes) -> None: ...
    @abstractmethod
    async def read(self) -> bytes:
        """Next chunk of bytes; raises TransportClosed when the link is gone."""


class MemoryTransport(Transport):
    """One end of an in-memory byte pipe. Use MemoryTransport.pair()."""

    def __init__(self):
        self._rx: asyncio.Queue = asyncio.Queue()
        self.peer: "MemoryTransport | None" = None
        self.closed = False

    @classmethod
    def pair(cls) -> tuple["MemoryTransport", "MemoryTransport"]:
        a, b = cls(), cls()
        a.peer, b.peer = b, a
        return a, b

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._rx.put_nowait(None)
            if self.peer and not self.peer.closed:
                self.peer._rx.put_nowait(None)

    async def write(self, data: bytes) -> None:
        if self.closed or not self.peer or self.peer.closed:
            raise TransportClosed("memory link closed")
        self.peer._rx.put_nowait(data)

    async def read(self) -> bytes:
        item = await self._rx.get()
        if item is None:
            self._rx.put_nowait(None)
            raise TransportClosed("memory link closed")
        return item


class SerialTransport(Transport):
    """pyserial wrapped for asyncio (reader thread + executor writes).

    DTR and RTS are forced LOW before the port is opened: the radio can be
    configured to treat them as PTT (menus 071/076/108), so merely opening
    the port must never be able to assert them.
    """

    def __init__(self, port: str, baud: int):
        self.port, self.baud = port, baud
        self._ser = None
        self._q: asyncio.Queue = asyncio.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock_fh = None

    async def open(self) -> None:
        if os.environ.get("RADIO_REMOTE_TESTING") == "1":
            raise RuntimeError("real serial ports are disabled while RADIO_REMOTE_TESTING=1")
        import serial

        self._acquire_lock()
        try:
            ser = serial.Serial()
            ser.port, ser.baudrate = self.port, self.baud
            ser.timeout, ser.write_timeout = 0.05, 1.0
            ser.dtr = False
            ser.rts = False
            ser.open()
        except Exception:
            self._release_lock()
            raise
        self._ser = ser
        loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, args=(loop,), name="cat-reader", daemon=True)
        self._thread.start()

    def _reader(self, loop) -> None:
        import serial

        ser = self._ser
        try:
            while not self._stop.is_set():
                data = ser.read(256)
                if data:
                    loop.call_soon_threadsafe(self._q.put_nowait, data)
        except (serial.SerialException, OSError, TypeError):
            pass
        loop.call_soon_threadsafe(self._q.put_nowait, None)

    async def write(self, data: bytes) -> None:
        import serial

        ser = self._ser
        if ser is None:
            raise TransportClosed("not open")
        try:
            await asyncio.get_running_loop().run_in_executor(None, ser.write, data)
        except (serial.SerialException, OSError) as e:
            raise TransportClosed(str(e)) from e

    async def read(self) -> bytes:
        item = await self._q.get()
        if item is None:
            self._q.put_nowait(None)
            raise TransportClosed("serial port closed")
        return item

    async def close(self) -> None:
        self._stop.set()
        if self._thread:
            await asyncio.get_running_loop().run_in_executor(None, self._thread.join, 1.0)
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._release_lock()

    # single-owner guard (POSIX); on Windows the OS already gives exclusive COM access
    def _acquire_lock(self) -> None:
        if sys.platform == "win32":
            return
        import fcntl
        import tempfile

        path = os.path.join(tempfile.gettempdir(), "radio-remote-" + os.path.basename(self.port) + ".lock")
        fh = open(path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise PortBusy(f"{self.port} is in use by another process") from None
        self._lock_fh = fh

    def _release_lock(self) -> None:
        if self._lock_fh:
            self._lock_fh.close()
            self._lock_fh = None


def candidate_ports() -> list[str]:
    """Ports that could plausibly be the radio (Silicon Labs bridge). We never probe unrelated ports."""
    from serial.tools import list_ports

    out = []
    for p in list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if p.vid == SILABS_VID or "silicon labs" in desc or "cp210" in desc:
            out.append(p.device)
    return sorted(out)


def probe_port(port: str, baud: int, expect_id: str, timeout: float = 0.6) -> bool:
    """Blocking, READ-ONLY probe: sends 'ID;' only."""
    import serial
    import time

    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, 0.1
    ser.dtr = False
    ser.rts = False
    try:
        ser.open()
        ser.reset_input_buffer()
        ser.write(b"ID;")
        end, buf = time.monotonic() + timeout, b""
        while time.monotonic() < end and b";" not in buf:
            buf += ser.read(64)
        return buf.decode("ascii", "replace").strip().startswith("ID" + expect_id)
    except (serial.SerialException, OSError):
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass


def detect_port(preferred_baud: int, expect_id: str) -> tuple[str, int] | None:
    bauds = [preferred_baud] + [b for b in (38400, 19200, 9600, 4800) if b != preferred_baud]
    for port in candidate_ports():
        for baud in bauds:
            if probe_port(port, baud, expect_id):
                log.info("radio found on %s @ %d", port, baud)
                return port, baud
    return None
