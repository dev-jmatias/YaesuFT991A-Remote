"""8-band equalizer for the audio the listeners hear (receive) and the audio the operator sends (transmit).

A linear-phase FIR filter whose response is drawn through the eight band gains (smoothly, on a log-frequency axis), run frame by frame with overlap-save FFTs. It needs only numpy,
costs a fraction of a millisecond per 20 ms frame, and adds a fixed delay of about 10 ms while it is on. The FreeDV modem tones are never passed through it: only speech.
"""
from __future__ import annotations

import numpy as np

RATE = 48000
BANDS_HZ = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
MAX_DB = 12.0
TAPS = 1023                    # odd: the filter is symmetric, so its phase is linear (all frequencies are delayed alike, by (TAPS - 1) / 2 samples, about 10 ms)
NFFT = 2048                    # >= frame length + TAPS - 1 (a 20 ms frame is 960 samples)

PRESETS = {
    "flat": [0, 0, 0, 0, 0, 0, 0, 0],
    "voice": [-12, -8, -3, 0, 3, 5, 3, -3],             # a little clarity around 2-4 kHz, less rumble
    "bass_cut": [-12, -9, -5, -2, 0, 0, 0, 0],
    "treble_cut": [0, 0, 0, 0, 0, -3, -7, -12],
    "bass_boost": [8, 6, 3, 0, 0, 0, 0, 0],
    "treble_boost": [0, 0, 0, 0, 0, 3, 6, 9],
}


def clean_gains(values) -> list[float]:
    """Eight numbers in dB, each within +-MAX_DB (rounded to 0.5 dB). Raises ValueError otherwise."""
    if not isinstance(values, (list, tuple)) or len(values) != len(BANDS_HZ):
        raise ValueError(f"expected {len(BANDS_HZ)} band gains")
    out = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -MAX_DB <= v <= MAX_DB:
            raise ValueError(f"each gain must be a number from -{MAX_DB:g} to {MAX_DB:g} dB")
        out.append(round(float(v) * 2) / 2)
    return out


def response_db(gains, freqs: np.ndarray) -> np.ndarray:
    """The wanted gain at each frequency: straight lines between the band centres on a log-frequency axis, flat beyond the first and last band."""
    return np.interp(np.log(np.maximum(freqs, 1.0)), np.log(BANDS_HZ), gains)


class Equalizer:
    def __init__(self, rate: int = RATE) -> None:
        self.rate = rate
        self.on = False
        self.gains = [0.0] * len(BANDS_HZ)
        self._H = None
        self._tail = np.zeros(TAPS - 1)
        self._build()

    @property
    def flat(self) -> bool:
        return not any(self.gains)

    def set(self, on: bool | None = None, gains=None) -> None:
        if gains is not None:
            self.gains = clean_gains(gains)
            self._build()
        if on is not None:
            if on and not self.on:
                self.reset()
            self.on = bool(on)

    def reset(self) -> None:
        self._tail = np.zeros(TAPS - 1)

    def _build(self) -> None:
        f = np.fft.rfftfreq(NFFT, 1.0 / self.rate)
        h0 = np.fft.fftshift(np.fft.irfft(10.0 ** (response_db(self.gains, f) / 20.0), NFFT))
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
        return {"on": self.on, "gains": list(self.gains)}
