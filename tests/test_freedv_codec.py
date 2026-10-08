"""The real RADE V1 and V2 modems: transmit chain -> receive chain loopback (needs the RADE library; skipped where it is not installed)."""
import numpy as np
import pytest

from radio_remote.audio import freedv
from radio_remote.audio.levels import FRAME_SAMPLES, RATE

pytestmark = pytest.mark.skipif(not freedv.available()[0], reason="the RADE library is not installed")


def speechlike(n_frames, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n_frames * FRAME_SAMPLES) / RATE
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    x = (np.sin(2 * np.pi * 220 * t) + 0.6 * np.sin(2 * np.pi * 660 * t) + 0.4 * np.sin(2 * np.pi * 1320 * t)) * env
    x += 0.05 * rng.standard_normal(len(t))
    return (x / np.abs(x).max() * 12000).astype("<i2")


@pytest.mark.parametrize("mode", ["RADE", "RADE2"])
def test_loopback_decodes_speech_and_noise_does_not(mode):
    if mode not in freedv.available_modes():
        pytest.skip(f"{mode} is not available in this RADE library")
    tx, rx = freedv.TxChain(mode, level=0.5), freedv.RxChain(mode)
    mic = speechlike(600)
    out, tone_peak, synced = [], 0, False
    for i in range(600):
        for f in tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()):
            tone_peak = max(tone_peak, int(np.abs(np.frombuffer(f, dtype="<i2")).max()))
            out.append(rx.process(f))
            synced = synced or rx.sync == 1
    for _ in range(30):                                                  # let the last frames come out
        out.append(rx.process(bytes(FRAME_SAMPLES * 2)))
    speech = np.frombuffer(b"".join(out), dtype="<i2")
    assert synced and tone_peak > 2000
    assert np.abs(speech).max() > 2000                                   # decoded speech, not silence
    rng = np.random.default_rng(2)
    quiet = freedv.RxChain(mode)
    noise = (rng.standard_normal(RATE * 4) * 3000).astype("<i2")
    heard = b"".join(quiet.process(noise[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()) for i in range(200))
    assert np.abs(np.frombuffer(heard, dtype="<i2")).max() < 1500        # no signal: no speech (at most a little start-up residue)


def test_an_old_library_without_v2_still_does_v1_and_says_why_v2_is_missing(monkeypatch):
    from radio_remote.audio import rade
    lib = rade._load()
    if lib is None or not hasattr(lib, "rg_open_mode"):
        pytest.skip("this library has no V2 to hide")

    class V1Only:                                                    # what a library built before V2 support looks like to the loader
        def __getattr__(self, name):
            if name == "rg_open_mode":
                raise AttributeError(name)
            return getattr(lib, name)
    monkeypatch.setattr(rade, "_LIB", V1Only())
    assert rade.v2_available()[0] is False and "reinstall" in rade.v2_available()[1].lower()
    assert freedv.mode_status()["RADE"] == "" and "no V2" in freedv.mode_status()["RADE2"]
    with pytest.raises(freedv.FreeDVUnavailable):
        freedv.open_core("RADE2")


def test_resamplers_keep_the_band_and_the_frame_size():
    t = np.arange(FRAME_SAMPLES * 20) / RATE
    x = (np.sin(2 * np.pi * 1000 * t) * 10000)
    d, u = freedv.Decimator(), freedv.Interpolator()
    y = np.concatenate([d.process(x[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]) for i in range(20)])
    assert len(y) == 160 * 20 and 8000 < np.abs(y[200:]).max() < 12000
    z = u.process(y)
    assert len(z) == len(y) * 6 and 8000 < np.abs(z[1000:]).max() < 12000


def ssb_shift(x, hz):
    """Move every frequency in a real signal by hz (analytic signal, FFT based): what a mistuned dial does to the audio."""
    n = len(x)
    h = np.zeros(n)
    h[0] = 1
    h[1:(n + 1) // 2] = 2
    if n % 2 == 0:
        h[n // 2] = 1
    return np.real(np.fft.ifft(np.fft.fft(x) * h) * np.exp(2j * np.pi * hz * np.arange(n) / RATE))


@pytest.mark.parametrize("mode", ["RADE", "RADE2"])
@pytest.mark.parametrize("off", [-250, 200])
def test_software_tuning_finds_a_mistuned_signal_and_holds_it(mode, off):
    if mode not in freedv.available_modes():
        pytest.skip(f"{mode} is not available in this RADE library")
    tx = freedv.TxChain(mode, level=1.0)
    mic = speechlike(50 * 60)                                       # 60 s of speech
    tones = []
    for i in range(50 * 60):
        tones += tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes())
    x = np.frombuffer(b"".join(tones), dtype="<i2").astype(float)
    pad = np.random.default_rng(9).standard_normal(RATE) * 300     # a second of noise before the signal
    y = np.clip(ssb_shift(np.concatenate([pad, x]), off), -32768, 32767).astype("<i2")
    rx = freedv.RxChain(mode)
    n, first, good_after = len(y) // FRAME_SAMPLES, None, 0
    for i in range(n):
        rx.process(y[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes())
        if rx.afc.locked and first is None:
            first = i * 0.02
        if first is not None and i * 0.02 > first + 5:
            good_after += rx.afc.locked
    assert first is not None and first < 40, f"{mode} at {off:+d} Hz never locked"
    assert good_after / max(1, n - int((first + 5) / 0.02)) > 0.7                  # and it keeps the lock
    if mode == "RADE":                                                            # V1 needs the software shift; V2 follows the offset by itself (it locked with no shift)
        assert abs(rx.afc.offset - off) <= 120                                     # the offset it settled on is close to the real one

@pytest.mark.parametrize("mode", ["RADE", "RADE2"])
def test_the_modem_locks_again_after_a_restart_and_keeps_its_tuning(mode):
    """After every transmission the receiver gets a fresh modem (restart); the lock must come back on the same signal and the tuning found must survive."""
    if mode not in freedv.available_modes():
        pytest.skip(f"{mode} is not available in this RADE library")
    tx, rx = freedv.TxChain(mode, level=0.5), freedv.RxChain(mode)
    mic = speechlike(500)
    tones = []
    for i in range(500):
        tones += tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes())
    for f in tones[:250]:
        rx.process(f)
    assert rx.sync == 1
    shift_before = rx.shifter.shift
    rx.restart()                                                         # what the capture loop does after a transmission
    assert rx.shifter.shift == shift_before                              # the tuning is kept
    locked = False
    for f in tones[250:]:                                                # the same signal goes on: a fresh modem must find it
        rx.process(f)
        locked = locked or rx.sync == 1
    assert locked


@pytest.mark.parametrize("mode", ["RADE", "RADE2"])
def test_flush_completes_the_last_modem_frame_and_sends_nothing_extra_when_nothing_waits(mode):
    if mode not in freedv.available_modes():
        pytest.skip(f"{mode} is not available in this RADE library")
    tx = freedv.TxChain(mode, level=0.5)
    mic = speechlike(87)                                            # 1.74 s: not a whole number of modem frames
    got = []
    for i in range(87):
        got += tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes())
    assert tx.fd.pending() != 0 or len(tx._speech)                  # some speech is still inside the modem
    last = tx.flush()
    assert last and all(len(f) == len(got[0]) for f in last)        # whole 20 ms frames of tones
    assert tx.fd.pending() == 0 and tx.flush() == []                # now nothing waits, and a second flush adds nothing
