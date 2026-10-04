"""The real RADE V1 library (built by scripts/build_rade.sh; point RADIO_REMOTE_RADE_LIB at librade-rr.so). Skipped where it is not installed."""
import numpy as np
import pytest

from radio_remote.audio import freedv, rade
from radio_remote.audio.levels import FRAME_SAMPLES, RATE

pytestmark = pytest.mark.skipif(not rade.available()[0], reason="the RADE library is not installed")


def speechlike(n_frames, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n_frames * FRAME_SAMPLES) / RATE
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    x = (np.sin(2 * np.pi * 150 * t) + 0.7 * np.sin(2 * np.pi * 450 * t) + 0.5 * np.sin(2 * np.pi * 1100 * t)
         + 0.3 * np.sin(2 * np.pi * 2300 * t)) * env
    x += 0.05 * rng.standard_normal(len(t))
    return (x / np.abs(x).max() * 12000).astype("<i2")


def test_rade_is_listed_and_loopback_decodes_speech_and_noise_does_not():
    assert "RADE" in freedv.available_modes()
    tx, rx = freedv.TxChain("RADE", level=0.5), freedv.RxChain("RADE")
    mic = speechlike(600)                                       # 12 s
    out, tone_peak, synced = [], 0, False
    for i in range(600):
        for f in tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()):
            tone_peak = max(tone_peak, int(np.abs(np.frombuffer(f, dtype="<i2")).max()))
            out.append(rx.process(f))
            synced = synced or rx.sync == 1
    for _ in range(30):                                          # let the last frames come out
        out.append(rx.process(bytes(FRAME_SAMPLES * 2)))
    speech = np.frombuffer(b"".join(out), dtype="<i2")
    assert synced and tone_peak > 2000
    assert np.abs(speech).max() > 2000                           # decoded speech, not silence
    rng = np.random.default_rng(3)
    quiet = freedv.RxChain("RADE")
    noise = (rng.standard_normal(RATE * 4) * 3000).astype("<i2")
    heard = b"".join(quiet.process(noise[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()) for i in range(200))
    assert np.abs(np.frombuffer(heard, dtype="<i2")).max() < 1500   # no signal: no speech (at most a little start-up residue)


def test_cpu_cost_is_small():
    import time
    tx, rx = freedv.TxChain("RADE", level=0.5), freedv.RxChain("RADE")
    mic = speechlike(250)                                        # 5 s
    t0, tones = time.perf_counter(), []
    for i in range(250):
        tones += tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes())
    t_tx = time.perf_counter() - t0
    t0 = time.perf_counter()
    for f in tones:
        rx.process(f)
    t_rx = time.perf_counter() - t0
    assert t_tx < 2.5 and t_rx < 2.5                             # real time is 5 s; a PC needs a few percent of that
