"""FreeDV tuning aid: software frequency shift + search (AFC), spectrum, signal-position estimate, and how the service uses them."""
import copy
import types

import numpy as np
import pytest
from test_audio import until
from test_freedv import FakeTx, fake_modem  # noqa: F401  (the stand-in modem fixture)

from radio_remote import config
from radio_remote.audio import freedv, freedv_tune, service
from radio_remote.audio.backends import RecordingSink, ToneSource
from radio_remote.audio.freedv_tune import (N_BANDS, Afc, FreqShifter, Spectrum, candidates, estimate_offset)
from radio_remote.audio.levels import FRAME_BYTES, FRAME_SAMPLES, RATE
from radio_remote.audio.service import AudioService


def peak_hz(x, rate):
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / rate)[int(np.argmax(X))]


@pytest.mark.parametrize("shift", [-300.0, 0.0, 120.0, 250.0])
def test_shifter_moves_a_tone_by_the_shift(shift):
    sh = FreqShifter()
    sh.shift = shift
    t = np.arange(16000) / 8000
    x = 8000 * np.sin(2 * np.pi * 1000 * t)
    y = np.concatenate([sh.process(x[i:i + 160]) for i in range(0, len(x), 160)])[400:]
    assert abs(peak_hz(y, 8000) - (1000 + shift)) < 4
    assert 0.8 < np.abs(y).max() / 8000 < 1.2                                  # same amplitude, no image left over


def test_shifter_is_continuous_when_the_shift_changes():
    sh = FreqShifter()
    t = np.arange(8000) / 8000
    x = 8000 * np.sin(2 * np.pi * 1000 * t)
    out = []
    for i in range(0, 8000, 160):
        sh.shift = 100.0 if (i // 160) % 2 else -100.0
        out.append(sh.process(x[i:i + 160]))
    y = np.concatenate(out)[400:]
    assert np.abs(np.diff(y)).max() < 0.8 * 8000 * 2 * np.pi * 1200 / 8000 * 2                 # no jumps larger than a 1.2 kHz tone allows


def test_candidates_cover_the_range_nearest_first():
    c = candidates("700D", 0.0)
    assert c[0] == 0.0 and c[1:3] == [140.0, -140.0] and max(abs(v) for v in c) <= freedv_tune.SEARCH_RANGE_HZ
    assert len(set(c)) == len(c)
    c2 = candidates("RADE", 210.0)
    assert c2[0] == 210.0 and all(abs(v) <= freedv_tune.SEARCH_RANGE_HZ for v in c2)


def run_afc(afc, script, dt=0.02):
    """script: list of (seconds, locked). Returns the shift after each block."""
    out = []
    for seconds, locked in script:
        for _ in range(int(seconds / dt)):
            out.append(afc.update(locked, dt))
    return out


def test_afc_holds_a_lock_rides_out_a_dropout_and_searches_afterwards():
    a = Afc("700D")
    shifts = run_afc(a, [(2.0, False)])                                          # nothing there: it steps through the offsets
    assert len(set(shifts)) > 1 and a.searching and not a.locked
    a.shift = 140.0
    run_afc(a, [(1.0, True)])                                                    # lock
    assert a.locked and a.last_good == 140.0 and a.offset == -140.0
    assert set(run_afc(a, [(2.5, False)])) == {140.0}                            # a short dropout keeps the shift
    run_afc(a, [(2.0, False)])                                                   # longer: the search starts again, around the last good shift
    assert a.searching and not a.locked


def test_afc_tries_the_spectrum_hint_first_and_stays_on_a_lock():
    a = Afc("700E")
    a.hint = 200.0                                                               # the spectrum says: signal 200 Hz above its place
    first = a.update(False, 0.02)
    assert first == -200.0                                                       # so the audio is shifted down by 200 Hz first
    for _ in range(int(3.0 / 0.02)):
        a.update(True, 0.02)
    assert a.locked and a.shift == -200.0


def test_afc_nudge_and_reset():
    a = Afc("RADE")
    a.shift = -100.0
    a.nudge(30.0)
    assert a.shift == -70.0 and a.last_good == -70.0
    a.nudge(10_000.0)
    assert a.shift == freedv_tune.SEARCH_RANGE_HZ * 2
    a.reset()
    assert a.shift == 0.0 and not a.locked and not a.searching


def band_noise(low, high, level=1.0, seed=1):
    """A power spectrum (50 Hz bands) with a flat signal between low and high Hz over a weak noise floor."""
    p = np.full(N_BANDS, 1e-9) * (1 + np.random.default_rng(seed).random(N_BANDS) * 0.5)
    for i in range(N_BANDS):
        if low <= i * 50 + 25 <= high:
            p[i] = level * 1e-6
    return p


@pytest.mark.parametrize("mode", ["700D", "700E", "1600", "RADE"])
@pytest.mark.parametrize("off", [-300, -100, 0, 150, 350])
def test_the_estimate_finds_where_the_signal_is(mode, off):
    pr = freedv_tune.PARAMS[mode]
    p = band_noise(pr["centre"] + off - pr["width"] / 2, pr["centre"] + off + pr["width"] / 2)
    est = estimate_offset(p, mode)
    assert est is not None and abs(est - off) <= 60, (mode, off, est)


def test_the_estimate_says_nothing_for_noise_or_silence():
    rng = np.random.default_rng(3)
    assert estimate_offset(rng.random(N_BANDS) * 1e-6, "700D") is None
    assert estimate_offset(np.zeros(N_BANDS), "700D") is None
    assert estimate_offset(band_noise(1000, 2000, level=1e-6), "700D") is None                  # a signal far below -85 dBFS is not trusted


def test_spectrum_shows_a_tone_in_its_band_and_flags_clipping():
    sp = Spectrum("700D")
    t = np.arange(FRAME_SAMPLES * 30) / RATE
    x = (20000 * np.sin(2 * np.pi * 1030 * t)).astype("<i2")
    fresh = [sp.push(x[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes()) for i in range(30)]
    assert sum(fresh) == 6                                                                       # every 5th frame
    b = sp.bands()
    assert len(b) == N_BANDS and int(np.argmax(b)) == 1030 // 50 and b[20] > 90 and b[40] < 40
    assert -8.0 < sp.level_db < -6.5 and sp.clipping is False                                    # rms of a 20000-peak sine: -7.3 dBFS
    loud = np.full(FRAME_SAMPLES, 32500, dtype="<i2").tobytes()
    sp.push(loud)
    assert sp.clipping is True


# ---------------------------------------------------------------- how the service uses it
class TunedRx:
    """A stand-in receive modem that has the tuning aid's attributes."""
    sync, snr, spec_fresh = 1, 6.0, True

    def __init__(self, mode):
        self.afc = Afc(mode)
        self.afc.shift = -120.0
        self.spec = types.SimpleNamespace(bands=lambda: [7] * N_BANDS, level_db=-23.4, clipping=True, hint=95.0)

    def process(self, pcm):
        return pcm

    def tune_state(self):
        return "locked"

    def close(self):
        pass


@pytest.fixture
async def tsvc(fake_modem, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service.freedv, "RxChain", TunedRx)
    cfg = copy.deepcopy(config.DEFAULTS)["audio"]
    s = AudioService(cfg, is_mock=True, tx_gate=lambda cid: False, on_levels=lambda d: None, source_factory=ToneSource, sink_factory=RecordingSink)
    s.freedv_ok = True
    await s.start()
    yield s
    await s.stop()


async def test_the_tuning_aid_values_reach_the_published_state(tsvc):
    tsvc.set_freedv(True, "700D")
    await tsvc._ensure_capture()
    await until(lambda: tsvc._levels["freedv_spec"])
    lv = tsvc._levels
    assert lv["freedv_spec"] == [7] * N_BANDS and lv["freedv_offset"] == 120 and lv["freedv_hint"] == 95
    assert lv["freedv_afc"] == "locked" and lv["freedv_level"] == -23 and lv["freedv_clip"] is True
    tsvc.set_freedv(False)
    assert tsvc._levels["freedv_spec"] == [] and tsvc._levels["freedv_offset"] == 0 and tsvc._levels["freedv_afc"] == ""


async def test_moving_the_dial_keeps_the_software_tuning(tsvc):
    tsvc.set_freedv(True, "700D")
    afc = tsvc._fd_rx.afc
    tsvc.freedv_dial_moved(14_236_000, 14_236_100, "USB")                       # upper sideband: every audio frequency moved down by 100 Hz
    assert afc.shift == -120.0 + 100.0
    tsvc.freedv_dial_moved(14_236_100, 14_236_050, "USB")
    assert afc.shift == -120.0 + 100.0 - 50.0
    afc.shift = 0.0
    tsvc.freedv_dial_moved(7_177_000, 7_177_100, "LSB")                         # lower sideband: they moved up, so the shift goes the other way
    assert afc.shift == -100.0
    tsvc.freedv_dial_moved(7_177_000, 14_236_000, "USB")                        # a band change: start the search afresh
    assert afc.shift == 0.0 and not afc.locked
    tsvc.freedv_afc_reset()
    tsvc.set_freedv(False)
    tsvc.freedv_dial_moved(1, 2, "USB")                                         # off: nothing happens (and nothing breaks)
