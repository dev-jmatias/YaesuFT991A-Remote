"""Tuning help for FreeDV: a software frequency search (AFC), a spectrum of the received audio and a "where is the signal" estimate.

Why: the FreeDV modems only lock when the signal is close to where they expect it (measured here with ideal signals: RADE V1 about +-50 Hz, RADE V2 at least +-300 Hz),
and a radio dial cannot be set that finely by hand. So the received audio is shifted in software: when no signal is locked the shift is
stepped through the possible offsets until the modem locks, and then held. Nothing here touches the radio.

Offset convention: "offset" is how far ABOVE its nominal place in the audio the signal is (Hz). The applied shift is the opposite (shift = -offset).
"""
from __future__ import annotations

import threading

import numpy as np

AUDIO_RATE = 8000
BAND_HZ = 50                                    # width of one spectrum band
N_BANDS = 80                                    # 0..4000 Hz
SPEC_N = 4096                                   # FFT length at 48 kHz (85 ms, 11.7 Hz per bin)
FLOOR_DB, CEIL_DB = -100.0, -20.0               # spectrum scale: dBFS mapped to 0..100

# nominal centre of the modem's carriers in the audio (Hz), their width, the search step (about the width of the lock range), the dwell per step
PARAMS = {
    "RADE": {"centre": 1465, "width": 900, "step": 70, "dwell": 2.0, "min_snr": -5.0},
    "RADE2": {"centre": 1490, "width": 850, "step": 400, "dwell": 2.0, "min_snr": 0.0},      # measured with ideal signals: V2 locked across the whole +-300 Hz tried, so a wide step is enough
}
# "min_snr": the modems can also report "sync" on noise or a badly mistuned signal, but with a low SNR estimate. Only sync together with an SNR above this counts as a lock for the search.
SEARCH_RANGE_HZ = 450
HOLD_S = 3.0                                    # a locked signal that goes quiet for this long starts the search again (RADE's sync flag flickers)


class FreqShifter:
    """Streaming single-sideband frequency shifter for real audio (Hilbert filter + complex mixer). Always running, so its delay (about 8 ms) never changes."""

    def __init__(self, rate: int = AUDIO_RATE, taps: int = 129):
        n = np.arange(taps) - (taps - 1) / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            h = np.where(n % 2 != 0, 2.0 / (np.pi * n), 0.0)
        self.h = h * np.hamming(taps)
        self.delay = (taps - 1) // 2
        self.hist = np.zeros(taps - 1)
        self.rate = rate
        self.phase = 0.0
        self.shift = 0.0                         # Hz added to every frequency in the audio

    def process(self, x: np.ndarray) -> np.ndarray:
        x = x.astype(np.float64)
        buf = np.concatenate([self.hist, x])
        self.hist = buf[-(len(self.h) - 1):]
        q = np.convolve(buf, self.h, "valid")                       # Hilbert transform, aligned with buf[k + delay]
        real = buf[self.delay:self.delay + len(x)]
        ph = self.phase + 2.0 * np.pi * self.shift / self.rate * np.arange(1, len(x) + 1)
        self.phase = float(ph[-1]) % (2.0 * np.pi)
        return real * np.cos(ph) - q * np.sin(ph)


def candidates(mode: str, centre_shift: float) -> list[float]:
    """The shifts to try, nearest to centre_shift first, within +-SEARCH_RANGE_HZ of zero."""
    step = PARAMS[mode]["step"]
    out, k = [], 0
    while abs(k) * step <= 2 * SEARCH_RANGE_HZ:
        for s in ((0,) if k == 0 else (k, -k)):
            c = centre_shift + s * step
            if abs(c) <= SEARCH_RANGE_HZ and c not in out:
                out.append(c)
        k += 1
    return out


class Afc:
    """Decides which shift to apply. Call update() for every block with whether the modem is locked and how many seconds the block lasted."""

    def __init__(self, mode: str):
        self.mode = mode
        self.shift = 0.0                         # Hz applied to the audio (= minus the signal's offset)
        self.locked = False
        self.searching = False
        self.last_good = 0.0
        self._lost = 0.0                         # seconds without lock
        self._dwell = 0.0
        self._todo: list[float] = []
        self.hint: float | None = None           # signal offset estimated from the spectrum (Hz), or None
        self._lk = threading.Lock()              # update() runs in the decoding thread, nudge() and reset() in the event loop: they are tiny, so one lock keeps them apart

    @property
    def offset(self) -> float:
        return -self.shift

    def nudge(self, hz: float) -> None:
        """The radio's dial moved: every audio frequency moved by hz, so the shift that was right needs the same change."""
        with self._lk:
            self.shift = max(-SEARCH_RANGE_HZ * 2.0, min(SEARCH_RANGE_HZ * 2.0, self.shift + hz))
            self.last_good = self.shift
            self._todo = []

    def reset(self) -> None:
        with self._lk:
            self.shift = self.last_good = 0.0
            self._lost = self._dwell = 0.0
            self._todo = []
            self.locked = self.searching = False

    def update(self, synced: bool, dt: float) -> float:
        with self._lk:
            return self._update(synced, dt)

    def _update(self, synced: bool, dt: float) -> float:
        if synced:
            self.locked, self.searching = True, False
            self._lost = self._dwell = 0.0
            self.last_good = self.shift
            self._todo = []
            return self.shift
        self._lost += dt
        if self.locked and self._lost < HOLD_S:
            return self.shift                    # a short dropout: keep the shift
        self.locked = False
        if not self.searching:                   # start a search: the spectrum estimate first, else around the last good shift
            self.searching, self._dwell = True, 0.0
            self._todo = candidates(self.mode, self.last_good)          # sweep around the last good shift ...
            if self.hint is not None:                                  # ... after trying where the spectrum says the signal is
                self._todo = [c for c in self._todo if abs(c + self.hint) > 1.0]
                self._todo.insert(0, -self.hint)
            self.shift = self._todo.pop(0)
            return self.shift
        self._dwell += dt
        if self._dwell >= PARAMS[self.mode]["dwell"]:
            self._dwell = 0.0
            if not self._todo:
                self._todo = candidates(self.mode, 0.0)
            self.shift = self._todo.pop(0)
        return self.shift


class Spectrum:
    """Rolling spectrum of the received audio (48 kHz int16 frames): 80 bands of 50 Hz, level, and the signal-position estimate."""

    def __init__(self, mode: str = "RADE"):
        self.mode = mode
        self.buf = np.zeros(SPEC_N)
        self.win = np.hanning(SPEC_N)
        self.pow = np.zeros(N_BANDS)             # smoothed linear power per band
        self._n = 0
        self._hints: list[float] = []
        self.hint: float | None = None
        self.level_db = -120.0
        self.clipping = False
        edges = np.arange(N_BANDS + 1) * BAND_HZ
        self._idx = np.minimum((edges / (48000.0 / SPEC_N)).astype(int), SPEC_N // 2)

    def push(self, pcm: bytes) -> bool:
        """Feed one 20 ms frame; True when the spectrum was refreshed (every 5th frame, so 10 times a second)."""
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        self.buf = np.concatenate([self.buf[len(x):], x])
        self.level_db = float(10 * np.log10(max(np.mean(x ** 2), 1.0) / 32768.0 ** 2))
        self.clipping = bool(np.abs(x).max() >= 32000)
        self._n += 1
        if self._n % 5:
            return False
        s = np.abs(np.fft.rfft(self.buf * self.win)) ** 2 * 4.0 / (self.win.sum() ** 2)         # a full-scale sine of amplitude A gives A^2 in its bin
        p = np.array([s[self._idx[i]:max(self._idx[i + 1], self._idx[i] + 1)].sum() for i in range(N_BANDS)]) / 32768.0 ** 2
        self.pow = 0.6 * self.pow + 0.4 * p if self.pow.any() else p
        h = estimate_offset(self.pow, self.mode)
        if h is not None:
            self._hints = (self._hints + [h])[-5:]
            self.hint = float(np.median(self._hints))
        else:
            self._hints = self._hints[-2:]
            self.hint = None if len(self._hints) < 2 else self.hint
            if not self._hints:
                self.hint = None
        return True

    def bands(self) -> list[int]:
        db = 10 * np.log10(np.maximum(self.pow, 1e-12))
        return [int(v) for v in np.clip((db - FLOOR_DB) * 100.0 / (CEIL_DB - FLOOR_DB), 0, 100)]


def estimate_offset(power: np.ndarray, mode: str) -> float | None:
    """Where is the modem's signal, as an offset (Hz) above its nominal place? None when nothing stands out of the noise."""
    pr = PARAMS[mode]
    lo, hi = int(300 / BAND_HZ), int(3000 / BAND_HZ)
    seg = np.asarray(power[lo:hi], dtype=np.float64)
    if seg.max() <= 0:
        return None
    w = max(int(round(pr["width"] / BAND_HZ)), 3)
    best, best_r, ratios = None, 0.0, {}
    for start in range(0, len(seg) - w + 1):
        inside = seg[start:start + w].mean()
        outside = np.concatenate([seg[:start], seg[start + w:]]).mean() if len(seg) > w else 0.0
        r = inside / max(outside, 1e-14)
        ratios[start] = r
        if r > best_r:
            best, best_r = start, r
    if best is None or best_r < 3.0 or seg[best:best + w].mean() < 10 ** (-85 / 10):        # not 5 dB above the rest, or too weak
        return None
    pos = float(best)                                                        # refine between neighbouring starts (parabola on the ratios)
    a, b, c = ratios.get(best - 1), ratios[best], ratios.get(best + 1)
    if a is not None and c is not None and (a - 2 * b + c) != 0:
        pos += max(-0.5, min(0.5, 0.5 * (a - c) / (a - 2 * b + c)))
    centre_hz = (lo + pos + w / 2.0) * BAND_HZ
    return float(centre_hz - pr["centre"])
