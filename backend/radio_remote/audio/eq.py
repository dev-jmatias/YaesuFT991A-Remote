"""Parametric equalizer (5, 10 or 18 bands, each with a frequency, a gain and a Q) for the audio the listeners hear (receive) and the audio the operator sends (transmit).

Every band is a peaking filter; the equalizer's response is the sum of their responses in dB. That response is realised as a linear-phase FIR filter run frame by frame with
overlap-save FFTs: only numpy, a fraction of a millisecond per 20 ms frame, and a fixed delay of about 10 ms while it is on. The FreeDV modem tones are never passed through it: only speech.
Narrow bands below about 150 Hz come out somewhat wider and weaker than drawn (the filter is 1023 taps long); that is why the lowest centre frequency is limited to 30 Hz and Q to 10.
"""
from __future__ import annotations

import numpy as np

RATE = 48000
MIN_HZ, MAX_HZ = 30.0, 12000.0
MAX_DB = 12.0
MIN_Q, MAX_Q = 0.3, 10.0
BAND_COUNTS = (5, 10, 18)
LOW_CUT = (30.0, 1000.0)       # the low-cut (high-pass) corner, Hz; 0 = off
HIGH_CUT = (1000.0, 12000.0)   # the high-cut (low-pass) corner, Hz; 0 = off
CUT_ORDER = 4                  # 24 dB per octave
TAPS = 1023                    # odd: the filter is symmetric, so its phase is linear (all frequencies are delayed alike, by (TAPS - 1) / 2 samples, about 10 ms)
NFFT = 2048                    # >= frame length + TAPS - 1 (a 20 ms frame is 960 samples)

# default layouts: centre frequencies and the Q that makes neighbouring bands overlap about as in a graphic equalizer
_LAYOUT_HZ = {
    5: ([100, 300, 1000, 3000, 6000], 0.9),
    10: ([60, 125, 250, 500, 1000, 2000, 3000, 4000, 6000, 8000], 1.4),
    18: ([50, 80, 125, 200, 315, 500, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000], 2.8),
}
LAYOUTS = {str(n): [{"f": float(f), "g": 0.0, "q": q} for f in hz] for n, (hz, q) in _LAYOUT_HZ.items()}


def default_bands(n: int = 10) -> list[dict]:
    return [dict(b) for b in LAYOUTS[str(n)]]


def clean_bands(values) -> list[dict]:
    """5, 10 or 18 bands, each {"f": Hz, "g": dB, "q": Q} within the limits (gain rounded to 0.1 dB, Q to 0.05, frequency to 1 Hz). Raises ValueError otherwise."""
    if not isinstance(values, (list, tuple)) or len(values) not in BAND_COUNTS:
        raise ValueError("expected 5, 10 or 18 bands")
    out = []
    for b in values:
        if not isinstance(b, dict):
            raise ValueError("each band must be {f, g, q}")
        f, g, q = (b.get(k) for k in ("f", "g", "q"))
        for v in (f, g, q):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
                raise ValueError("band values must be numbers")
        if not MIN_HZ <= f <= MAX_HZ:
            raise ValueError(f"frequency must be {MIN_HZ:g}..{MAX_HZ:g} Hz")
        if not -MAX_DB <= g <= MAX_DB:
            raise ValueError(f"gain must be -{MAX_DB:g}..{MAX_DB:g} dB")
        if not MIN_Q <= q <= MAX_Q:
            raise ValueError(f"Q must be {MIN_Q:g}..{MAX_Q:g}")
        out.append({"f": float(round(f)), "g": round(float(g), 1), "q": round(round(float(q) / 0.05) * 0.05, 2)})
    return out


def clean_cut(value) -> dict:
    """{"low": Hz or 0, "high": Hz or 0}: the low-cut and high-cut corners (0 = off). Raises ValueError otherwise."""
    if not isinstance(value, dict):
        raise ValueError("expected {low, high}")
    out = {}
    for key, (lo, hi) in (("low", LOW_CUT), ("high", HIGH_CUT)):
        v = value.get(key, 0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not (v == 0 or lo <= v <= hi):
            raise ValueError(f"{key}-cut must be 0 (off) or {lo:g}..{hi:g} Hz")
        out[key] = float(round(v))
    if out["low"] and out["high"] and out["low"] >= out["high"]:
        raise ValueError("the low-cut must be below the high-cut")
    return out


def from_old_gains(gains) -> list[dict]:
    """Settings saved by the first (8 fixed bands) version: 63 Hz .. 8 kHz. They become a flat-spaced 10-band layout following the same curve."""
    old_hz = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
    bands = default_bands(10)
    for b in bands:
        b["g"] = round(float(np.interp(np.log(b["f"]), np.log(old_hz), [float(g) for g in gains])), 1)
    return clean_bands(bands)


def response_db(bands, freqs: np.ndarray) -> np.ndarray:
    """The equalizer's gain in dB at each frequency: the peaking filters of the bands added together (analogue prototype, RBJ), clipped to +-24 dB."""
    f = np.maximum(np.asarray(freqs, dtype=float), 1.0)
    total = np.zeros_like(f)
    for b in bands:
        if not b["g"]:
            continue
        a = 10.0 ** (b["g"] / 40.0)
        x = f / b["f"]
        d = (1.0 - x * x) ** 2
        total += 10.0 * np.log10((d + (x * a / b["q"]) ** 2) / (d + (x / (a * b["q"])) ** 2))
    return np.clip(total, -2 * MAX_DB, 2 * MAX_DB)


def cut_db(cut, freqs: np.ndarray) -> np.ndarray:
    """The low-cut and high-cut as Butterworth responses (24 dB per octave), in dB."""
    f = np.maximum(np.asarray(freqs, dtype=float), 1.0)
    total = np.zeros_like(f)
    if cut and cut.get("low"):
        total -= 10.0 * np.log10(1.0 + (cut["low"] / f) ** (2 * CUT_ORDER))
    if cut and cut.get("high"):
        total -= 10.0 * np.log10(1.0 + (f / cut["high"]) ** (2 * CUT_ORDER))
    return np.maximum(total, -80.0)


class Equalizer:
    def __init__(self, rate: int = RATE) -> None:
        self.rate = rate
        self.on = False
        self.bands = default_bands(10)
        self.cut = {"low": 0.0, "high": 0.0}
        self._H = None
        self._tail = np.zeros(TAPS - 1)
        self._build()

    @property
    def flat(self) -> bool:
        return not any(b["g"] for b in self.bands) and not self.cut["low"] and not self.cut["high"]

    def set(self, on: bool | None = None, bands=None, cut=None) -> None:
        if bands is not None or cut is not None:
            if bands is not None:
                self.bands = clean_bands(bands)
            if cut is not None:
                self.cut = clean_cut(cut)
            self._build()
        if on is not None:
            if on and not self.on:
                self.reset()
            self.on = bool(on)

    def reset(self) -> None:
        self._tail = np.zeros(TAPS - 1)

    def _build(self) -> None:
        f = np.fft.rfftfreq(NFFT, 1.0 / self.rate)
        h0 = np.fft.fftshift(np.fft.irfft(10.0 ** ((response_db(self.bands, f) + cut_db(self.cut, f)) / 20.0), NFFT))
        mid = NFFT // 2
        h = h0[mid - TAPS // 2: mid + TAPS // 2 + 1] * np.kaiser(TAPS, 4.0)
        self._H = np.fft.rfft(h, NFFT)

    def process(self, pcm: bytes) -> bytes:
        """One frame of 16-bit mono audio in, the same length out. Untouched (and free) while the equalizer is off or flat."""
        if not self.on or self.flat:
            return pcm
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        n = len(x)
        if n == 0 or n > NFFT - TAPS + 1:
            return pcm
        buf = np.concatenate((self._tail, x))
        y = np.fft.irfft(np.fft.rfft(buf, NFFT) * self._H, NFFT)[TAPS - 1: TAPS - 1 + n]
        self._tail = buf[-(TAPS - 1):]
        return np.clip(np.rint(y), -32768, 32767).astype("<i2").tobytes()

    def state(self) -> dict:
        return {"on": self.on, "bands": [dict(b) for b in self.bands], "cut": dict(self.cut)}
