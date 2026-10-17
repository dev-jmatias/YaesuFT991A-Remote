"""FT8 and FT4 on the Pi: receive (decode every slot of the radio's audio) and transmit (message text -> the audio of one transmission).

The codec is ft8_lib (MIT) with a small glue file (native/ftx), built into libftx-rr (scripts/build_ftx.sh) and shipped with the program. This module is the ctypes wrapper
(`decode`, `encode`) and `SlotRx`, which cuts the radio's 48 kHz audio into the UTC-aligned time slots (15 s for FT8, 7.5 s for FT4) and hands each finished slot to the decoder.
Everything here is plain functions and classes: the audio service and the application call them (audio/service.py, app.py).
"""
from __future__ import annotations

import ctypes
import logging
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .freedv import Decimator
from .levels import RATE

log = logging.getLogger("ftx")

ABI = 1
DECODE_RATE = 12000
MODES = {"FT8": {"proto": 8, "slot": 15.0, "symbol": 0.160, "tones": 79, "bw": 50.0}, "FT4": {"proto": 4, "slot": 7.5, "symbol": 0.048, "tones": 105, "bw": 83.3}}
TX_START_S = 0.5                         # a transmission starts this long after its slot starts (nominal time, "DT 0")
MAX_FREQ_HZ, MIN_FREQ_HZ = 2900, 200     # the audio frequency range the decoder searches


class FtxUnavailable(Exception):
    pass


class _Msg(ctypes.Structure):
    _fields_ = [("freq_hz", ctypes.c_float), ("dt_s", ctypes.c_float), ("snr_db", ctypes.c_float), ("score", ctypes.c_int), ("text", ctypes.c_char * 48)]


_lib = None
_lib_err = ""
_tried = False


def _candidates() -> list[Path]:
    ext = "dylib" if sys.platform == "darwin" else "so"
    here = Path(__file__).resolve().parent
    out = []
    env = os.environ.get("RADIO_REMOTE_FTX_LIB")
    if env:
        out.append(Path(env))
    arch = {"arm64": "aarch64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
    # the release carries one build per architecture (scripts/build_ftx_container.sh, the release workflow puts them here before the program is packed)
    if sys.platform.startswith("linux"):
        out.append(here / "native" / f"linux-{arch}" / "libftx-rr.so")
    out += [here / "native" / f"libftx-rr.{ext}", here.parents[2] / "dist" / "ftx" / f"libftx-rr.{ext}", Path("/usr/local/lib") / f"libftx-rr.{ext}"]
    return out


def _load():
    global _lib, _lib_err, _tried
    if _tried:
        return _lib
    _tried = True
    errs = []
    for p in _candidates():
        if not p.exists():
            continue
        try:
            lib = ctypes.CDLL(str(p))
            lib.ftx_rr_abi.restype = ctypes.c_int
            if lib.ftx_rr_abi() != ABI:
                errs.append(f"{p}: wrong version")
                continue
            lib.ftx_rr_decode.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(_Msg), ctypes.c_int]
            lib.ftx_rr_decode.restype = ctypes.c_int
            lib.ftx_rr_encode.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_float, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_char_p]
            lib.ftx_rr_encode.restype = ctypes.c_int
            lib.ftx_rr_hash_reset.restype = None
            _lib = lib
            log.info("FT8/FT4 library: %s", p)
            return _lib
        except OSError as e:
            errs.append(f"{p}: {e}")
    _lib_err = "; ".join(errs) or f"libftx-rr is not installed (platform {platform.machine()})"
    return None


def available() -> tuple[bool, str]:
    return (True, "") if _load() else (False, _lib_err)


@dataclass
class Decode:
    freq: float            # audio Hz
    dt: float              # seconds from the nominal start
    snr: float             # dB (2500 Hz reference, estimated)
    text: str


def decode(samples: np.ndarray, mode: str) -> list[Decode]:
    """Decode one slot of 12 kHz audio (float, +-1.0)."""
    lib = _load()
    if lib is None:
        raise FtxUnavailable(_lib_err)
    x = np.ascontiguousarray(samples, dtype=np.float32)
    out = (_Msg * 80)()
    n = lib.ftx_rr_decode(x.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(x), DECODE_RATE, MODES[mode]["proto"], out, 80)
    return [Decode(float(out[i].freq_hz), float(out[i].dt_s), float(out[i].snr_db), out[i].text.decode("ascii", "replace").strip()) for i in range(max(0, n))]


def encode(text: str, mode: str, f0: float, rate: int = 16000) -> tuple[np.ndarray, str]:
    """The audio of one transmission (float, +-1.0, starts and ends with the signal; no silence) and the message as it will really be sent."""
    lib = _load()
    if lib is None:
        raise FtxUnavailable(_lib_err)
    m = MODES[mode]
    cap = int(m["tones"] * m["symbol"] * rate) + 1024
    buf = (ctypes.c_float * cap)()
    canon = ctypes.create_string_buffer(48)
    n = lib.ftx_rr_encode(" ".join(text.upper().split()).encode("ascii", "replace"), m["proto"], float(f0), rate, buf, cap, canon)
    if n == -1:
        raise ValueError("this message cannot be sent in FT8/FT4 (callsign, grid or report not understood; free text is limited to 13 characters A-Z 0-9 space + - . / ?)")
    if n < 0:
        raise ValueError("message could not be encoded")
    return np.frombuffer(buf, dtype=np.float32, count=n).copy(), canon.value.decode("ascii", "replace").strip()


def slot_of(t: float, mode: str) -> tuple[int, float]:
    """(slot index, its start time) for a UTC time."""
    s = MODES[mode]["slot"]
    i = int(t // s)
    return i, i * s


class SlotRx:
    """Cuts the radio's 48 kHz frames into the UTC slots. feed() returns (index, start time, 12 kHz float audio, final) when a pass over a slot is due, else None."""
    KEEP_S = 40.0

    def __init__(self, mode: str):
        self.mode = mode
        self.slot_len = MODES[mode]["slot"]
        self.dec = Decimator(RATE // DECODE_RATE, 5400.0)
        self.chunks: list[np.ndarray] = []                # the audio of the last KEEP_S seconds, as the chunks it arrived in (joined only when a slot is cut)
        self.total = 0                                    # samples in chunks
        self.t_end = 0.0                                  # UTC time of the end of the audio in chunks
        self.next_slot: int | None = None                 # the slot that is due next
        self.early_done = False                           # the first pass of that slot has been handed over
        self.fresh_samples = 0                            # samples since the receiver started (a slot needs to be covered by them)
        self.t_start = 0.0
        self.last_chunk = np.zeros(0, dtype=np.float32)

    def _add(self, a: np.ndarray) -> None:
        self.chunks.append(a)
        self.total += len(a)
        keep = int(self.KEEP_S * DECODE_RATE)
        while len(self.chunks) > 1 and self.total - len(self.chunks[0]) >= keep:
            self.total -= len(self.chunks.pop(0))

    def feed(self, pcm: bytes, now: float):
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
        y = self.dec.process(x).astype(np.float32)
        self.last_chunk = y
        if not self.fresh_samples:
            self.t_start = now - len(x) / RATE
        else:
            gap = (now - len(x) / RATE) - self.t_end                # frames did not arrive (our own transmission, a stall): keep the time axis true with silence
            if gap > 0.05:
                self._add(np.zeros(int(gap * DECODE_RATE), dtype=np.float32))
        self.fresh_samples += len(y)
        self._add(y)
        self.t_end = now
        cur, _ = slot_of(now, self.mode)
        if self.next_slot is None:
            self.next_slot = cur                          # the slot in progress is only partly heard: it is dropped, decoding starts with the next one
            return None
        # Two passes per slot. The EARLY one comes as soon as an ordinary message is over (FT8 at 13.4 s, FT4 at 6.0 s), so the operator can answer a CQ before the next slot
        # starts; the FINAL one just before the slot ends picks up the late and the weak ones. Each hands over the whole slot; the engine drops what the first pass had.
        end = (self.next_slot + 1) * self.slot_len
        early_at = end - (1.6 if self.mode == "FT8" else 1.5)
        covered = self.t_start <= self.next_slot * self.slot_len + 1.0
        if self.next_slot == cur and covered and (now >= end - 0.2 or (not self.early_done and now >= early_at)):
            final = now >= end - 0.2
            idx = self.next_slot
            if final:
                self.next_slot, self.early_done = idx + 1, False
            else:
                self.early_done = True
            buf = np.concatenate(self.chunks)
            n = int(self.slot_len * DECODE_RATE)
            first = int(round((idx * self.slot_len - (self.t_end - len(buf) / DECODE_RATE)) * DECODE_RATE))
            seg = np.zeros(n, dtype=np.float32)
            lo, hi = max(first, 0), min(first + n, len(buf))
            if hi > lo:
                seg[lo - first:hi - first] = buf[lo:hi]
            return idx, idx * self.slot_len, seg, final
        if cur > self.next_slot:
            self.next_slot, self.early_done = cur, False  # fell behind (a stall): skip to the slot in progress
        return None
