"""FreeDV digital voice: RADE V1 ("RADE") and RADE V2 ("RADE2"), both through the library in rade.py.

The radio's USB audio carries the modem tones. On receive this module turns them back into speech; on transmit it turns the
operator's speech into modem tones. Audio inside the server is mono int16 at 48 kHz; the modems work at 8 kHz (RADE: speech at 16 kHz), so each
chain below resamples on the way in and out. Nothing here talks to the radio: it only transforms audio frames.
"""
from __future__ import annotations

import logging
from collections import deque

import numpy as np

from .freedv_tune import PARAMS, Afc, FreqShifter, Spectrum
from .levels import FRAME_BYTES, FRAME_SAMPLES, RATE

log = logging.getLogger("freedv")

ALL_MODES = ("RADE", "RADE2")                        # RADE comes from its own optional library (rade.py); RADE2 needs a library built with V2 support
MODEM_RATE = 8000


class FreeDVUnavailable(Exception):
    pass


def available() -> tuple[bool, str]:
    """(usable, reason when not): is at least one FreeDV mode usable? (RADE comes from its own library, see rade.py)"""
    from . import rade
    return rade.available()


def mode_status() -> dict[str, str]:
    """mode -> "" when usable, else the reason it is not (shown on the FreeDV tab)."""
    from . import rade
    ok1, why1 = rade.available()
    ok2, why2 = rade.v2_available()
    return {"RADE": "" if ok1 else why1, "RADE2": "" if ok2 else why2}


def available_modes() -> list[str]:
    return [m for m, why in mode_status().items() if not why]


def open_core(mode: str):
    """The modem for a mode. It has rx(), tx(), sync, snr and the sample rates."""
    from . import rade
    if mode not in ALL_MODES:
        raise FreeDVUnavailable(f"unsupported FreeDV mode {mode}")
    try:
        return rade.RadeCore(v2=(mode == "RADE2"))
    except rade.RadeUnavailable as e:
        raise FreeDVUnavailable(str(e)) from None


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


class RxChain:
    """Radio audio (48 kHz frames of modem tones) -> speech frames (48 kHz) for the listeners."""

    MAX_FIFO = 20                                    # frames (400 ms): a stalled consumer never builds up unbounded delay

    def __init__(self, mode: str):
        self.mode = mode
        self.fd = open_core(mode)
        self.dec = Decimator(RATE // self.fd.modem_rate, 0.45 * self.fd.modem_rate)           # radio audio -> the modem rate
        self.up = Interpolator(RATE // self.fd.speech_rate, 0.45 * self.fd.speech_rate)       # decoded speech -> 48 kHz
        self.shifter, self.afc, self.spec = FreqShifter(self.fd.modem_rate), Afc(mode), Spectrum(mode)     # software tuning (freedv_tune.py)
        self.spec_fresh = False
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
        self.spec_fresh = self.spec.push(pcm)
        if self.spec_fresh:
            self.afc.hint = self.spec.hint
        good = self.fd.sync == 1 and self.fd.snr >= PARAMS[self.mode]["min_snr"]
        self.shifter.shift = self.afc.update(good, FRAME_SAMPLES / RATE)          # nothing locked: step the shift until the modem locks, then hold it
        speech = self.fd.rx(_to_i16(self.shifter.process(self.dec.process(x))))
        if len(speech):
            self._rest += _to_i16(self.up.process(speech.astype(np.float64))).tobytes()
            while len(self._rest) >= FRAME_BYTES:
                self.fifo.append(self._rest[:FRAME_BYTES])
                self._rest = self._rest[FRAME_BYTES:]
            while len(self.fifo) > self.MAX_FIFO:
                self.fifo.popleft()
        return self.fifo.popleft() if self.fifo else bytes(FRAME_BYTES)

    def restart(self) -> None:
        """After a transmission: a fresh modem and fresh filters (the old modem saw a gap of silence, and on a friend's PC RADE did not find the signal again until FreeDV was
        switched off and on), but the tuning found so far (shifter and AFC) is kept, so the lock comes back as soon as the signal does."""
        fresh = open_core(self.mode)
        self.fd.close()
        self.fd = fresh
        self.dec = Decimator(RATE // self.fd.modem_rate, 0.45 * self.fd.modem_rate)
        self.up = Interpolator(RATE // self.fd.speech_rate, 0.45 * self.fd.speech_rate)
        self.spec = Spectrum(self.mode)
        self.spec_fresh = False
        self.fifo.clear()
        self._rest = b""

    def tune_state(self) -> str:
        return "locked" if self.afc.locked else "searching" if self.afc.searching else "idle"

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

    def flush(self) -> list[bytes]:
        """End of an over: the last words may still be inside the modem (it only sends whole modem frames, 40 to 120 ms of speech each). Silence is fed until that last frame is
        complete, so the end of the speech is transmitted instead of being cut off. Nothing is added when nothing is waiting."""
        if not hasattr(self.fd, "pending"):
            return []
        n = self.fd.n_speech
        for _ in range(64):                                   # at most one modem frame of 10 ms blocks
            if not len(self._speech) and not self.fd.pending():
                break
            fill = n - len(self._speech) if len(self._speech) else n
            self._speech = np.concatenate([self._speech, np.zeros(fill, dtype="<i2")])
            while len(self._speech) >= n:
                block, self._speech = self._speech[:n], self._speech[n:]
                self._rest += _to_i16(self.up.process(self.fd.tx(block).astype(np.float64)) * self.level).tobytes()
        out = []
        while len(self._rest) >= FRAME_BYTES:
            out.append(self._rest[:FRAME_BYTES])
            self._rest = self._rest[FRAME_BYTES:]
        return out

    def close(self) -> None:
        self.fd.close()


__all__ = ["ALL_MODES", "Afc", "FreqShifter", "Spectrum", "FRAME_SAMPLES", "FreeDVUnavailable", "RxChain", "TxChain", "available", "available_modes",
           "mode_status", "open_core"]
