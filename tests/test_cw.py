"""CW decoder (audio/cw.py) and engine (cw_engine.py): synthetic Morse at different speeds, pitches and noise levels; the engine with a fake audio service."""
import asyncio
import copy
import difflib

import numpy as np
import pytest

from radio_remote import config
from radio_remote.audio import cw
from radio_remote.cw_engine import CwEngine

TEXT = "CQ CQ DE M0DHT M0DHT K THE QUICK BROWN FOX 599 73"


def decode(pcm: bytes, **kw):
    rx = cw.CwRx(**kw)
    out, stats = "", []
    for i in range(0, len(pcm), 1920):                                  # 20 ms frames, as the audio service delivers them
        for e in rx.feed(pcm[i:i + 1920]):
            if e["k"] == "text":
                out += e["s"]
            else:
                stats.append(e)
    for e in rx.flush():
        out += e["s"]
    return out.strip(), stats


def test_morse_tables_are_consistent():
    assert cw.morse_of("CQ DE") == "-.-. --.- / -.. ."
    assert cw.duration_units("E") == 1 and cw.duration_units("T") == 3 and cw.duration_units("E E") == 1 + 7 + 1
    assert cw.duration_units("A") == 1 + 1 + 3                           # dot, gap, dash
    assert all(cw.MORSE[cw.CODE[c]] == c for c in cw.CODE)


@pytest.mark.parametrize("wpm", [6, 12, 20, 30, 40])
def test_clean_morse_at_any_speed_is_read_exactly(wpm):
    out, stats = decode(cw.synth(TEXT, wpm=wpm))
    assert out == TEXT
    assert abs(stats[-1]["wpm"] - wpm) < wpm * 0.12                      # the speed is learnt
    assert abs(stats[-1]["hz"] - 700) < 15 and stats[-1]["locked"] in (True, False)


@pytest.mark.parametrize("snr", [15, 6, 0])
def test_noisy_morse_is_read_with_few_mistakes(snr):
    out, _ = decode(cw.synth(TEXT, wpm=20, snr_db=snr, jitter=0.1))
    assert difflib.SequenceMatcher(None, out, TEXT).ratio() > 0.93


@pytest.mark.parametrize("hz", [350, 600, 850, 1000])
def test_the_pitch_is_found_by_itself(hz):
    out, stats = decode(cw.synth("CQ DE M0DHT K", wpm=18, hz=hz, snr_db=12, lead_s=1.5))
    assert out == "CQ DE M0DHT K" and abs(stats[-1]["hz"] - hz) < 12


def test_noise_alone_prints_nothing():
    rng = np.random.default_rng(7)
    out, _ = decode((rng.normal(0, 0.1, 48000 * 15) * 32767).astype("<i2").tobytes())
    assert out == ""


def test_a_second_station_at_another_speed_is_followed():
    a = cw.synth("CQ TEST DE G4ABC", wpm=14, snr_db=12, tail_s=4)
    b = cw.synth("G4ABC DE M0DHT 599", wpm=27, snr_db=12, lead_s=1)
    out, _ = decode(a + b)
    assert out == "CQ TEST DE G4ABC G4ABC DE M0DHT 599"


def test_fixed_pitch_and_speed_skip_the_learning():
    out, stats = decode(cw.synth("E E E T", wpm=20, hz=620, snr_db=10), hz=620, wpm=20)
    assert out == "E E E T"
    assert stats[-1]["hz"] == 620 and stats[-1]["wpm"] == 20.0


def test_the_spectrum_has_64_bars_and_peaks_at_the_tone():
    _, stats = decode(cw.synth("CQ CQ CQ", wpm=15, hz=700))
    spec = max((s["spec"] for s in stats), key=max)                       # a look while the tone was on
    assert len(spec) == 64 and all(0 <= v <= 255 for v in spec)
    peak = int(np.argmax(spec))
    assert abs((300 + (peak + 0.5) * 800 / 64) - 700) < 25


class FakeAudio:
    def __init__(self):
        self.cw_feed, self.holds = None, set()

    async def hold_capture(self, on, who="ftx"):
        (self.holds.add if on else self.holds.discard)(who)


class FakeGuard:
    keyed = False


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.05)


def make():
    cfg = copy.deepcopy(config.DEFAULTS)
    ev, audio = [], FakeAudio()
    eng = CwEngine(lambda: cfg, audio, ev.append, lambda: 7030000, FakeGuard(), lambda cid: True)
    return eng, audio, ev


async def test_engine_decodes_the_audio_and_tells_the_pages():
    eng, audio, ev = make()
    await eng.set_on(True)
    assert audio.cw_feed is not None and audio.holds == {"cw"} and eng.state()["on"]
    pcm = np.frombuffer(cw.synth("CQ DE M0DHT K", wpm=20, snr_db=15), "<i2")
    for i in range(0, len(pcm) - 960, 960):
        eng.feed(pcm[i:i + 960].tobytes())
        if eng._waiting > 150:
            await until(lambda: eng._waiting < 20)
    await until(lambda: eng._waiting == 0)
    await asyncio.sleep(0.1)
    assert eng.text.strip() == "CQ DE M0DHT K"
    assert "".join(e["s"] for e in ev if e.get("k") == "text").strip() == "CQ DE M0DHT K"
    assert any(e.get("k") == "stat" for e in ev) and eng.state()["stat"]["hz"] > 600
    assert eng.state()["text"].strip() == "CQ DE M0DHT K"                    # a page that joins later gets the text
    eng.clear()
    assert eng.text == "" and ev[-1] == {"t": "cw", "k": "clear"}
    await eng.set_on(False)
    assert audio.cw_feed is None and audio.holds == set() and not eng.state()["on"]


async def test_engine_settings_reach_the_decoder():
    eng, _, ev = make()
    await eng.set_on(True)
    eng.set_params(640, 18)
    assert eng.rx.fixed_hz == 640 and eng.rx.fixed_wpm == 18 and ev[-1] == {"t": "cw", "k": "params", "pitch": 640.0, "wpm": 18.0}
    await eng.set_on(False)
    await eng.set_on(True)                                                    # kept across a switch off and on
    assert eng.rx.fixed_hz == 640
    eng.set_params(None, None)
    assert eng.rx.fixed_hz is None and eng.rx.fixed_wpm is None
    await eng.set_on(False)


async def test_engine_refuses_while_transmitting():
    eng, _, _ = make()
    eng.guard.keyed = True
    with pytest.raises(ValueError, match="transmitting"):
        await eng.set_on(True)
