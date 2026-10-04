"""The real codec2 modem: transmit chain -> receive chain loopback (needs libcodec2; skipped where it is not installed)."""
import numpy as np
import pytest

from radio_remote.audio import freedv
from radio_remote.audio.levels import FRAME_SAMPLES, RATE

pytestmark = pytest.mark.skipif(not freedv.available()[0], reason="libcodec2 is not installed")


def speechlike(n_frames, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n_frames * FRAME_SAMPLES) / RATE
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    x = (np.sin(2 * np.pi * 220 * t) + 0.6 * np.sin(2 * np.pi * 660 * t) + 0.4 * np.sin(2 * np.pi * 1320 * t)) * env
    x += 0.05 * rng.standard_normal(len(t))
    return (x / np.abs(x).max() * 12000).astype("<i2")


@pytest.mark.parametrize("mode", ["700D", "700E"])
def test_loopback_decodes_speech_and_noise_does_not(mode):
    tx, rx = freedv.TxChain(mode, level=0.5), freedv.RxChain(mode)
    mic = speechlike(400)
    out, tone_peak, synced = [], 0, False
    for i in range(400):
        for f in tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()):
            tone_peak = max(tone_peak, int(np.abs(np.frombuffer(f, dtype="<i2")).max()))
            out.append(rx.process(f))
            synced = synced or rx.sync == 1
    speech = np.frombuffer(b"".join(out), dtype="<i2")
    assert synced and tone_peak > 2000
    assert np.abs(speech).max() > 3000                                   # decoded speech, not silence
    rng = np.random.default_rng(2)
    quiet = freedv.RxChain(mode)
    noise = (rng.standard_normal(RATE * 4) * 3000).astype("<i2")
    heard = b"".join(quiet.process(noise[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()) for i in range(200))
    assert np.abs(np.frombuffer(heard, dtype="<i2")).max() == 0         # no signal: no speech, no noise passed on


def test_resamplers_keep_the_band_and_the_frame_size():
    t = np.arange(FRAME_SAMPLES * 20) / RATE
    x = (np.sin(2 * np.pi * 1000 * t) * 10000)
    d, u = freedv.Decimator(), freedv.Interpolator()
    y = np.concatenate([d.process(x[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]) for i in range(20)])
    assert len(y) == 160 * 20 and 8000 < np.abs(y[200:]).max() < 12000
    z = u.process(y)
    assert len(z) == len(y) * 6 and 8000 < np.abs(z[1000:]).max() < 12000
