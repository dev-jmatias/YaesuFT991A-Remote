"""FreeDV digital voice: the codec2 modes (1600 / 700D / 700E) through libcodec2 (LGPL, loaded with ctypes) and RADE through rade.py.

The radio's USB audio carries the modem tones. On receive this module turns them back into speech; on transmit it turns the
operator's speech into modem tones. Audio inside the server is mono int16 at 48 kHz; the modems work at 8 kHz (RADE: speech at 16 kHz), so each
chain below resamples on the way in and out. Nothing here talks to the radio: it only transforms audio frames.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import threading
from collections import deque

import numpy as np

from .levels import FRAME_BYTES, FRAME_SAMPLES, RATE

log = logging.getLogger("freedv")

MODES = {"1600": 0, "700D": 7, "700E": 13}                   # the codec2 modes: FREEDV_MODE_* values of freedv_api.h
ALL_MODES = tuple(MODES) + ("RADE",)                 # RADE comes from its own optional library (rade.py)
MODEM_RATE = 8000
_LIB = None
_LIB_ERR = ""
_LOCK = threading.Lock()


class FreeDVUnavailable(Exception):
    pass


def _load():
    global _LIB, _LIB_ERR
    with _LOCK:
        if _LIB is not None or _LIB_ERR:
            return _LIB
        names = [ctypes.util.find_library("codec2"), "libcodec2.so.1.2", "libcodec2.so.1.1", "libcodec2.so.1.0", "libcodec2.so",
                 "codec2.dll", "libcodec2.dylib"]
        lib, last = None, ""
        for n in dict.fromkeys(x for x in names if x):
            try:
                lib = ctypes.CDLL(n)
                break
            except OSError as e:
                last = str(e)
        if lib is None:
            _LIB_ERR = f"libcodec2 is not installed ({last or 'not found'}); on the Pi: sudo apt install libcodec2-1.2"
            return None
        c_short_p, c_int_p, c_float_p = ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_float)
        try:
            lib.freedv_open.argtypes, lib.freedv_open.restype = [ctypes.c_int], ctypes.c_void_p
            lib.freedv_close.argtypes, lib.freedv_close.restype = [ctypes.c_void_p], None
            lib.freedv_nin.argtypes, lib.freedv_nin.restype = [ctypes.c_void_p], ctypes.c_int
            lib.freedv_rx.argtypes, lib.freedv_rx.restype = [ctypes.c_void_p, c_short_p, c_short_p], ctypes.c_int
            lib.freedv_tx.argtypes, lib.freedv_tx.restype = [ctypes.c_void_p, c_short_p, c_short_p], None
            lib.freedv_get_modem_stats.argtypes, lib.freedv_get_modem_stats.restype = [ctypes.c_void_p, c_int_p, c_float_p], None
            for n in ("freedv_get_speech_sample_rate", "freedv_get_n_speech_samples", "freedv_get_n_max_speech_samples",
                      "freedv_get_modem_sample_rate", "freedv_get_n_max_modem_samples", "freedv_get_n_nom_modem_samples",
                      "freedv_get_n_tx_modem_samples", "freedv_get_sync"):
                f = getattr(lib, n)
                f.argtypes, f.restype = [ctypes.c_void_p], ctypes.c_int
        except AttributeError as e:
            _LIB_ERR = f"this libcodec2 is too old or incomplete ({e})"
            return None
        _LIB = lib
        return lib


def available() -> tuple[bool, str]:
    """(usable, reason when not): is at least one FreeDV mode usable? (codec2 modes need libcodec2, RADE needs its own library)"""
    if _load():
        return True, ""
    from . import rade
    ok, why = rade.available()
    return (True, "") if ok else (False, _LIB_ERR)


def mode_status() -> dict[str, str]:
    """mode -> "" when usable, else the reason it is not (shown on the FreeDV tab)."""
    from . import rade
    c2 = "" if _load() else _LIB_ERR
    r = "" if rade.available()[0] else rade.available()[1]
    return {**{m: c2 for m in MODES}, "RADE": r}


def available_modes() -> list[str]:
    return [m for m, why in mode_status().items() if not why]


def open_core(mode: str):
    """The modem for a mode: the codec2 one (FreeDV) or the RADE one. Both have rx(), tx(), sync, snr and the sample rates."""
    if mode == "RADE":
        from . import rade
        try:
            return rade.RadeCore()
        except rade.RadeUnavailable as e:
            raise FreeDVUnavailable(str(e)) from None
    return FreeDV(mode)


def _lowpass(cutoff_hz: float, rate: float, taps: int = 129) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2.0
    h = np.sinc(2.0 * cutoff_hz / rate * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float64)


class Decimator:
    """48 kHz -> 8 kHz (factor 6). Feed whole 20 ms frames (960 samples): every call returns 160 samples."""

    def __init__(self, factor: int = RATE // MODEM_RATE, cutoff_hz: float = 3600.0):
        self.f, self.h = factor, _lowpass(cutoff_hz, RATE)
        self.hist = np.zeros(len(self.h) - 1)

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self.hist, x.astype(np.float64)])
        self.hist = buf[-(len(self.h) - 1):]
        return np.convolve(buf, self.h, "valid")[self.f - 1::self.f]


class Interpolator:
    """8 kHz -> 48 kHz (factor 6)."""

    def __init__(self, factor: int = RATE // MODEM_RATE, cutoff_hz: float = 3600.0):
        self.f, self.h = factor, _lowpass(cutoff_hz, RATE) * factor
        self.hist = np.zeros(len(self.h) - 1)

    def process(self, x: np.ndarray) -> np.ndarray:
        up = np.zeros(len(x) * self.f)
        up[::self.f] = x
        buf = np.concatenate([self.hist, up])
        self.hist = buf[-(len(self.h) - 1):]
        return np.convolve(buf, self.h, "valid")


def _to_i16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x), -32768, 32767).astype("<i2")


class FreeDV:
    """One FreeDV modem (receive and transmit state) for 1600, 700D or 700E."""

    def __init__(self, mode: str):
        lib = _load()
        if lib is None:
            raise FreeDVUnavailable(_LIB_ERR)
        if mode not in MODES:
            raise FreeDVUnavailable(f"unsupported FreeDV mode {mode}")
        self.lib, self.mode = lib, mode
        self.speech_rate = self.modem_rate = MODEM_RATE
        self.f = lib.freedv_open(MODES[mode])
        if not self.f:
            raise FreeDVUnavailable(f"libcodec2 could not open FreeDV {mode}")
        self.n_speech = lib.freedv_get_n_speech_samples(self.f)
        self.n_max_speech = lib.freedv_get_n_max_speech_samples(self.f)
        self.n_max_modem = lib.freedv_get_n_max_modem_samples(self.f)
        self.n_tx = lib.freedv_get_n_tx_modem_samples(self.f)
        if lib.freedv_get_speech_sample_rate(self.f) != MODEM_RATE or lib.freedv_get_modem_sample_rate(self.f) != MODEM_RATE:
            raise FreeDVUnavailable("unexpected FreeDV sample rate")
        self._in = np.empty(0, dtype="<i2")
        self._out = (ctypes.c_short * self.n_max_speech)()
        self._demod = (ctypes.c_short * self.n_max_modem)()
        self.sync, self.snr = 0, 0.0

    def close(self) -> None:
        if self.f:
            self.lib.freedv_close(self.f)
            self.f = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def rx(self, modem: np.ndarray) -> np.ndarray:
        """8 kHz modem samples (any length) -> decoded 8 kHz speech (possibly empty until a whole frame has arrived)."""
        self._in = np.concatenate([self._in, modem.astype("<i2")])
        out = []
        while True:
            nin = self.lib.freedv_nin(self.f)
            if len(self._in) < nin:
                break
            ctypes.memmove(self._demod, self._in[:nin].tobytes(), nin * 2)
            self._in = self._in[nin:]
            n = self.lib.freedv_rx(self.f, self._out, self._demod)
            if n > 0 and self.lib.freedv_get_sync(self.f):          # speech only while the modem is locked on a signal
                out.append(np.ctypeslib.as_array(self._out, shape=(self.n_max_speech,))[:n].copy())
        sync, snr = ctypes.c_int(), ctypes.c_float()
        self.lib.freedv_get_modem_stats(self.f, ctypes.byref(sync), ctypes.byref(snr))
        self.sync, self.snr = int(sync.value), float(snr.value)
        return np.concatenate(out) if out else np.empty(0, dtype="<i2")

    def tx(self, speech: np.ndarray) -> np.ndarray:
        """Exactly n_speech 8 kHz speech samples -> modem samples to send."""
        spk = (ctypes.c_short * self.n_speech)()
        ctypes.memmove(spk, speech.astype("<i2").tobytes(), self.n_speech * 2)
        out = (ctypes.c_short * self.n_tx)()
        self.lib.freedv_tx(self.f, out, spk)
        return np.ctypeslib.as_array(out, shape=(self.n_tx,)).copy()


class RxChain:
    """Radio audio (48 kHz frames of modem tones) -> speech frames (48 kHz) for the listeners."""

    MAX_FIFO = 20                                    # frames (400 ms): a stalled consumer never builds up unbounded delay

    def __init__(self, mode: str):
        self.fd = open_core(mode)
        self.dec = Decimator(RATE // self.fd.modem_rate, 0.45 * self.fd.modem_rate)           # radio audio -> the modem rate
        self.up = Interpolator(RATE // self.fd.speech_rate, 0.45 * self.fd.speech_rate)       # decoded speech -> 48 kHz
        self.fifo: deque[bytes] = deque()
        self._rest = b""

    @property
    def sync(self) -> int:
        return self.fd.sync

    @property
    def snr(self) -> float:
        return self.fd.snr

    def process(self, pcm: bytes) -> bytes:
        """One 20 ms frame in, one 20 ms frame out (silence until speech has been decoded)."""
        x = np.frombuffer(pcm, dtype="<i2")
        speech = self.fd.rx(_to_i16(self.dec.process(x)))
        if len(speech):
            self._rest += _to_i16(self.up.process(speech.astype(np.float64))).tobytes()
            while len(self._rest) >= FRAME_BYTES:
                self.fifo.append(self._rest[:FRAME_BYTES])
                self._rest = self._rest[FRAME_BYTES:]
            while len(self.fifo) > self.MAX_FIFO:
                self.fifo.popleft()
        return self.fifo.popleft() if self.fifo else bytes(FRAME_BYTES)

    def close(self) -> None:
        self.fd.close()


class TxChain:
    """Microphone speech (48 kHz frames) -> modem tone frames (48 kHz) for the radio."""

    def __init__(self, mode: str, level: float = 0.5):
        self.fd = open_core(mode)
        self.dec = Decimator(RATE // self.fd.speech_rate, 0.45 * self.fd.speech_rate)         # microphone -> the speech rate
        self.up = Interpolator(RATE // self.fd.modem_rate, 0.45 * self.fd.modem_rate)         # modem output -> 48 kHz
        self.level = level                           # linear gain applied to the modem tones
        self._speech = np.empty(0, dtype="<i2")
        self._rest = b""

    def process(self, pcm: bytes) -> list[bytes]:
        """One 20 ms mic frame in; zero or more 20 ms modem frames out (the modem works in blocks of 40-160 ms)."""
        self._speech = np.concatenate([self._speech, _to_i16(self.dec.process(np.frombuffer(pcm, dtype="<i2")))])
        out = []
        while len(self._speech) >= self.fd.n_speech:
            block, self._speech = self._speech[:self.fd.n_speech], self._speech[self.fd.n_speech:]
            tones = self.fd.tx(block)
            self._rest += _to_i16(self.up.process(tones.astype(np.float64)) * self.level).tobytes()
        while len(self._rest) >= FRAME_BYTES:
            out.append(self._rest[:FRAME_BYTES])
            self._rest = self._rest[FRAME_BYTES:]
        return out

    def close(self) -> None:
        self.fd.close()


__all__ = ["ALL_MODES", "FRAME_SAMPLES", "FreeDV", "FreeDVUnavailable", "MODES", "RxChain", "TxChain", "available", "available_modes",
           "mode_status", "open_core"]
