"""Level metering, gain and the TX limiter. Internal audio is mono int16 @ 48 kHz in 20 ms frames."""
from __future__ import annotations

import numpy as np

RATE = 48_000
FRAME_SAMPLES = 960                 # 20 ms
FRAME_BYTES = FRAME_SAMPLES * 2
_KNEE = 0.7
_CEIL = 0.89                         # about -1 dBFS: the limiter never reaches digital full scale


def level_pct(pcm: bytes) -> int:
    """RMS level 0..100 mapped from -60..0 dBFS."""
    if not pcm:
        return 0
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x)))
    if rms <= 1e-6:
        return 0
    db = 20.0 * np.log10(rms)
    return int(max(0.0, min(100.0, (db + 60.0) / 60.0 * 100.0)))


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def apply_gain(pcm: bytes, gain: float, limit: bool = False) -> bytes:
    """Scale samples. With limit=True a soft-knee limiter guarantees |y| <= -1 dBFS (no hard digital clipping)."""
    if gain == 1.0 and not limit:
        return pcm
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0 * gain
    if limit:
        a = np.abs(x)
        over = a > _KNEE
        x = np.where(over, np.sign(x) * (_KNEE + (_CEIL - _KNEE) * np.tanh((a - _KNEE) / (_CEIL - _KNEE))), x)
    return (np.clip(x, -1.0, 32767 / 32768) * 32768.0).astype("<i2").tobytes()
