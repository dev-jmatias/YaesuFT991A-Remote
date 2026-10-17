"""FT8 / FT4: the codec library (ft8_lib + glue), round trips through the real decoder, the slot cutter."""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from conftest import ftx_slot

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.mark.parametrize("mode", ["FT8", "FT4"])
def test_round_trip_clean(ftx, mode):
    msgs = [("CQ K1FM FN30", 1000.0), ("K1FM WV1W -10", 1500.0), ("WV1W K1FM R+02", 2100.0)]
    got = ftx.decode(ftx_slot(ftx, mode, msgs), mode)
    assert {d.text for d in got} == {m for m, _ in msgs}
    for d in got:
        f = dict(msgs)[d.text]
        assert abs(d.freq - f) < 8
        assert abs(d.dt) < 0.15


def test_ft8_in_noise(ftx):
    got = ftx.decode(ftx_slot(ftx, "FT8", [("CQ DX EA4XYZ IN80", 1234.0), ("EA4XYZ DL1ABC JO31", 1800.0)], snr_db=-8), "FT8")
    assert "EA4XYZ DL1ABC JO31" in {d.text for d in got}
    d = next(d for d in got if d.text == "EA4XYZ DL1ABC JO31")
    assert -16 < d.snr < 0                                   # the estimate is only good to a few dB


def test_snr_tracks_the_real_snr(ftx):
    rows = []
    for snr in (5, -5, -12):
        got = ftx.decode(ftx_slot(ftx, "FT8", [("K1ABC W9XYZ EN52", 1500.0)], snr_db=snr, seed=3), "FT8")
        rows.append((snr, got[0].snr if got else None))
    assert all(r[1] is not None for r in rows[:2]), rows
    assert rows[0][1] > rows[1][1]                           # a stronger signal reports a higher SNR
    assert abs(rows[0][1] - 5) < 7, rows


def test_encode_reports_the_message_as_sent_and_refuses_nonsense(ftx):
    sig, canon = ftx.encode("cq k1fm fn30", "FT8", 1000.0, 16000)
    assert canon == "CQ K1FM FN30"
    assert abs(len(sig) / 16000 - 79 * 0.16) < 0.01
    sig, _ = ftx.encode("CQ K1FM FN30", "FT4", 1000.0, 16000)
    assert abs(len(sig) / 16000 - 105 * 0.048) < 0.01
    with pytest.raises(ValueError):
        ftx.encode("THIS IS FAR TOO LONG FOR FREE TEXT", "FT8", 1000.0)


def test_slot_cutter_hands_over_whole_slots(ftx):
    mode = "FT8"
    rx = ftx.SlotRx(mode)
    rate = 48000
    frame = 960
    sig = ftx_slot(ftx, mode, [("CQ K1FM FN30", 1000.0)])           # 12 kHz audio of exactly one slot
    up = np.repeat(sig, 4)                                       # a crude 48 kHz version: plenty for a test of the cutter
    pcm_all = (np.clip(up, -1, 1) * 30000).astype("<i2")
    t0 = 1_000_005.0 * 1.0                                       # start 5 s into a slot-aligned minute (1_000_005 // 15 * 15 = 1_000_005 - 0 ...)
    t0 = (int(t0 // 15) + 1) * 15.0 - 5.0
    got = []
    t = t0
    seq = np.concatenate([np.zeros(5 * rate, "<i2"), pcm_all, np.zeros(20 * rate, "<i2")])      # 5 s of nothing before the slot starts
    for i in range(0, len(seq) - frame, frame):
        t = t0 + (i + frame) / rate
        r = rx.feed(seq[i:i + frame].tobytes(), t)
        if r:
            got.append(r)
    assert got, "no slot was handed over"
    assert [g[3] for g in got[:2]] == [False, True] and got[0][0] == got[1][0]      # the early pass, then the final one, of the same slot
    idx, start, audio, final = got[1]
    assert start % 15 == 0 and len(audio) == 15 * 12000
    msgs = {d.text for d in ftx.decode(audio, mode)}
    assert "CQ K1FM FN30" in msgs


def test_snr_is_measured_where_the_signal_is_not_over_the_whole_band(ftx):
    """A receiver's audio is not flat (here 16 dB lower at the top of the passband than at the bottom). A signal in the quiet part has a high SNR, one in the loud part a low one,
    and the report has to follow, or every report sent is wrong (the whole-band noise estimate was off by up to 9 dB)."""
    rate = 12000
    n = int(15 * rate)
    rng = np.random.default_rng(1)
    f = np.fft.rfftfreq(n, 1 / rate)
    gain = 10 ** ((-16 * np.clip((f - 300) / 2400, 0, 1) + np.where(f < 300, -20, 0) + np.where(f > 2700, -25, 0)) / 20)
    x = np.fft.irfft((rng.normal(size=len(f)) + 1j * rng.normal(size=len(f))) * gain, n)
    x /= np.sqrt(np.var(x))
    sig = np.zeros(n)
    items = (("CQ K1ABC FN42", 600, 0.30), ("CQ W9XYZ EN52", 2400, 0.30))
    for text, hz, amp in items:
        s, _ = ftx.encode(text, "FT8", hz, rate)
        sig[6000:6000 + len(s)] += s * amp
    got = {d.text: d for d in ftx.decode((x + sig).astype(np.float32), "FT8")}
    for text, hz, amp in items:
        local = np.var(np.fft.irfft(np.where(np.abs(f - hz) < 150, np.fft.rfft(x), 0), n)) / 300.0          # noise power per Hz around the signal
        true = 10 * np.log10((amp ** 2 / 2) / (local * 2500))
        assert text in got, text
        assert abs(got[text].snr - true) < 2.0, (text, got[text].snr, true)
