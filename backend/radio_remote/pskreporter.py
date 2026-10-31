"""PSK Reporter (https://pskreporter.info): spots of the stations this program has heard, with the SNR we received them at.

Written from the public description of the reporting protocol (https://pskreporter.info/pskdev.html): a simplified IPFIX over UDP to report.pskreporter.info port 4739. One datagram holds a
16-byte header, optionally the two template sets (the first three datagrams after the program starts, then once an hour), one receiver record (who we are: callsign, locator, software) and
the sender records (the stations we heard: callsign, frequency, SNR, mode, source, time). Its rules are kept: a callsign is reported once an hour unless its frequency changed, a datagram is
sent at most every five minutes (sooner only when it is full), the same UDP source port is used throughout.

Off by default ([reporter] psk). It needs the station callsign and grid square of the FreeDV Reporter settings. Nothing is sent when nothing was heard.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import socket
import struct
import time
from typing import Callable

log = logging.getLogger("psk")

HOST, PORT = "report.pskreporter.info", 4739
ENTERPRISE = 30351                       # PSK Reporter's enterprise number
RECEIVER_TEMPLATE, SENDER_TEMPLATE = 0x9992, 0x9993
INTERVAL_S = 300.0                       # at most one datagram every five minutes ...
JITTER_S = 30.0                          # ... with a little randomness, so many stations do not report together
MAX_RECORDS = 80                         # a datagram holds about 80 to 90 records; a full one may go at once
SAME_STATION_S = 3600.0                  # a callsign that has not changed is reported once an hour
CHANGED_HZ = 2000                        # ... "changed" here: its frequency moved by more than this
TEMPLATE_EVERY_S = 3600.0
FIRST_TEMPLATE_DATAGRAMS = 3             # UDP is lossy: the templates go in the first three datagrams
MODE = "FREEDV"                          # the ADIF sub-mode of digital voice (RADE V1 and V2 both)
SOURCE_AUTOMATIC = 1                     # informationSource: automatically extracted
CALL_RE = re.compile(r"[A-Z0-9/]{3,15}")


def _field(fid: int, length: int, enterprise: bool = True) -> bytes:
    return struct.pack(">HH", (0x8000 | fid) if enterprise else fid, length) + (struct.pack(">I", ENTERPRISE) if enterprise else b"")


def _vstr(s: str) -> bytes:
    b = s.encode("utf-8")[:254]
    return bytes([len(b)]) + b


def _set(set_id: int, body: bytes) -> bytes:
    """A set: id, length (the 4-byte header, the body and the zero padding to a multiple of 4), body, padding."""
    n = 4 + len(body)
    pad = -n % 4
    return struct.pack(">HH", set_id, n + pad) + body + b"\0" * pad


def receiver_template() -> bytes:
    """Options template set (id 3): receiverCallsign, receiverLocator, decodingSoftware, all variable length."""
    return _set(3, struct.pack(">HHH", RECEIVER_TEMPLATE, 3, 0) + _field(2, 0xFFFF) + _field(4, 0xFFFF) + _field(8, 0xFFFF))


def sender_template() -> bytes:
    """Template set (id 2): senderCallsign, frequency (4 bytes), sNR (1 byte), mode, informationSource (1 byte), flowStartSeconds (4 bytes)."""
    return _set(2, struct.pack(">HH", SENDER_TEMPLATE, 6) + _field(1, 0xFFFF) + _field(5, 4) + _field(6, 1) + _field(10, 0xFFFF) + _field(11, 1) + _field(150, 4, enterprise=False))


def locator(grid: str) -> str:
    """A Maidenhead locator the way the site writes it: FN42hn."""
    g = (grid or "").strip()
    return g[:2].upper() + g[2:4] + g[4:6].lower() + g[6:8]


def datagram(call: str, grid: str, software: str, records: list[tuple], seq: int, domain: int, templates: bool, now: int) -> bytes:
    """One datagram. records: (callsign, frequency Hz, SNR dB, unix seconds)."""
    receiver = _set(RECEIVER_TEMPLATE, _vstr(call.upper()) + _vstr(locator(grid)) + _vstr(software))
    rows = b"".join(_vstr(c.upper()) + struct.pack(">I", int(hz) & 0xFFFFFFFF) + struct.pack(">b", max(-128, min(127, int(round(snr))))) + _vstr(MODE)
                    + bytes([SOURCE_AUTOMATIC]) + struct.pack(">I", int(t) & 0xFFFFFFFF) for c, hz, snr, t in records)
    senders = _set(SENDER_TEMPLATE, rows) if records else b""
    payload = (receiver_template() + sender_template() if templates else b"") + receiver + senders
    return struct.pack(">HHIII", 10, 16 + len(payload), int(now) & 0xFFFFFFFF, seq & 0xFFFFFFFF, domain) + payload


class PskReporter:
    def __init__(self, cfg: Callable[[], dict], version: str, clock: Callable[[], float] = time.time, send: Callable[[bytes], None] | None = None):
        self._cfg, self._version, self._clock, self._send_fn = cfg, version, clock, send
        self.domain = random.getrandbits(32)                 # the observation domain: constant for a session
        self.pending: dict[str, tuple] = {}                  # callsign -> (frequency, SNR, time): the latest of each station not yet sent
        self.last_sent: dict[str, tuple] = {}                # callsign -> (time sent, frequency)
        self.seq = 0                                         # reports sent so far (the header counts reports, not datagrams)
        self.datagrams = 0
        self.templates_at = 0.0
        self.sent_total = 0
        self.last_at = 0.0
        self.error = ""
        self._task: asyncio.Task | None = None
        self._transport = None                               # one UDP socket for the whole session: the same source port every time

    # ------------------------------------------------------------- settings
    def settings(self) -> dict:
        return self._cfg()["reporter"]

    def _station(self) -> tuple[str, str]:
        c = self.settings()
        return c.get("callsign", "").upper(), c.get("grid_square", "")

    def why_not(self) -> str:
        """Why spots are not being collected, or "" when they are."""
        c = self.settings()
        if not c.get("psk"):
            return "PSK Reporter is switched off in the settings"
        call, grid = self._station()
        if not (call and grid):
            return "needs the callsign and grid square of the FreeDV Reporter settings"
        return ""

    # ------------------------------------------------------------- spots
    def add(self, callsign: str, snr, hz: int, now: float | None = None) -> str:
        """A station was heard. Returns "" when the spot is queued, otherwise the reason it is not."""
        why = self.why_not()
        if why:
            return why
        call = (callsign or "").upper()
        if not CALL_RE.fullmatch(call) or not isinstance(snr, (int, float)) or isinstance(snr, bool) or snr != snr or snr in (float("inf"), float("-inf")):
            return "no usable callsign or SNR"
        if not hz or hz < 100_000:
            return "the radio's frequency is not known"
        now = self._clock() if now is None else now
        last = self.last_sent.get(call)
        if last and now - last[0] < SAME_STATION_S and abs(last[1] - hz) <= CHANGED_HZ:
            return "this station was reported less than an hour ago"
        self.pending[call] = (int(hz), float(snr), int(now))
        return ""

    # ------------------------------------------------------------- sending
    def due(self, now: float) -> bool:
        return bool(self.pending) and (len(self.pending) >= MAX_RECORDS or now - self.last_at >= INTERVAL_S)

    def build(self, now: float) -> tuple[bytes, list[str]] | None:
        """The next datagram from the queue (up to MAX_RECORDS spots), or None when there is nothing to send."""
        if not self.pending or self.why_not():
            return None
        calls = sorted(self.pending, key=lambda k: self.pending[k][2])[:MAX_RECORDS]
        records = [(c, self.pending[c][0], self.pending[c][1], self.pending[c][2]) for c in calls]
        templates = self.datagrams < FIRST_TEMPLATE_DATAGRAMS or now - self.templates_at >= TEMPLATE_EVERY_S
        call, grid = self._station()
        data = datagram(call, grid, f"Radio Remote {self._version}", records, self.seq, self.domain, templates, int(now))
        if templates:
            self.templates_at = now
        return data, calls

    async def flush(self) -> int:
        """Send what is due. Returns the number of spots sent."""
        now = self._clock()
        if not self.due(now):
            return 0
        built = self.build(now)
        if built is None:
            return 0
        data, calls = built
        try:
            await self._transmit(data)
        except OSError as e:
            self.error = str(e)[:120] or e.__class__.__name__
            log.warning("PSK Reporter: %s", self.error)
            return 0
        self.error = ""
        for c in calls:
            hz, snr, _ = self.pending.pop(c)
            self.last_sent[c] = (now, hz)
        self.seq += len(calls)
        self.sent_total += len(calls)
        self.datagrams += 1
        self.last_at = now
        log.info("PSK Reporter: sent %d spot(s): %s", len(calls), ", ".join(calls[:8]) + (" ..." if len(calls) > 8 else ""))
        return len(calls)

    async def _transmit(self, data: bytes) -> None:
        if self._send_fn is not None:
            self._send_fn(data)
            return
        loop = asyncio.get_running_loop()
        if self._transport is None:
            self._transport, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, family=socket.AF_INET, local_addr=("0.0.0.0", 0))
        infos = await loop.getaddrinfo(HOST, PORT, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        self._transport.sendto(data, infos[0][4])

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        t, self._task = self._task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    async def _run(self) -> None:
        self.last_at = self._clock() + random.uniform(0, JITTER_S)          # timers start with the program, not with the clock
        while True:
            await asyncio.sleep(5.0)
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("PSK Reporter: sending failed")

    def status(self) -> dict:
        why = self.why_not()
        return {"enabled": bool(self.settings().get("psk")), "ready": not why, "reason": why, "queued": len(self.pending), "sent": self.sent_total,
                "last_at": int(self.last_at) if self.sent_total else 0, "error": self.error}
