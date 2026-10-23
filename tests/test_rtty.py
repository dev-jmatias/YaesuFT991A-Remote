"""RTTY: the Baudot tables, the decoder on synthetic AFSK (speeds, shifts, tones, noise), the engine's receive and its sending through the TxGuard."""
import asyncio
import copy
import difflib

import numpy as np
import pytest

from radio_remote import config, qsolog
from radio_remote.audio import rtty
from radio_remote.radio.mock import MockDriver
from radio_remote.rtty_engine import RttyEngine
from radio_remote.safety import TxGuard

TEXT = "CQ CQ DE M0DHT M0DHT K RST 599 73 THE QUICK BROWN FOX 1234567890"


def decode(pcm: bytes, **kw):
    rx = rtty.RttyRx(**kw)
    out, stats = "", []
    for i in range(0, len(pcm), 1920):
        for e in rx.feed(pcm[i:i + 1920]):
            if e["k"] == "text":
                out += e["s"]
            else:
                stats.append(e)
    return out.strip(), stats


def close(out, want):
    return difflib.SequenceMatcher(None, out, want).ratio()


def test_baudot_tables_have_the_standard_codes():
    assert rtty.LTRS[0x01] == "E" and rtty.LTRS[0x03] == "A" and rtty.LTRS[0x10] == "T" and rtty.LTRS[0x04] == " " and rtty.LTRS[0x08] == "\r" and rtty.LTRS[0x02] == "\n"
    assert rtty.FIGS[0x01] == "3" and rtty.FIGS[0x16] == "0" and rtty.FIGS[0x17] == "1" and rtty.FIGS[0x0D] == "!" and rtty.FIGS[0x19] == "?" and rtty.FIGS[0x1C] == "."
    assert len(rtty.LTRS) == 32 and len(rtty.FIGS) == 32


def test_encoding_adds_shifts_where_the_kind_of_character_changes():
    c = rtty.baudot_encode("A1 B2")
    assert c[:2] == [rtty.SHIFT_LTRS] * 2
    assert c[2:] == [0x03, rtty.SHIFT_FIGS, 0x17, 0x04, rtty.SHIFT_LTRS, 0x19, rtty.SHIFT_FIGS, 0x13]      # A  FIGS 1  space  LTRS B  FIGS 2
    assert rtty.baudot_encode("E ")[-1] == 0x04
    assert rtty.baudot_encode("é~") == [rtty.SHIFT_LTRS] * 2                                          # what the code cannot carry is dropped
    assert rtty.duration_s("E", 45.45) == pytest.approx(3 * 7.5 / 45.45)


def test_the_audio_has_two_tones_and_the_right_length():
    sig = rtty.encode("RYRYRY", 45.45, 170, 2125.0, rate=16000, amp=1.0, lead_s=0.2, tail_s=0.2)
    assert abs(len(sig) / 16000 - (0.4 + len(rtty.baudot_encode("RYRYRY")) * 7.5 / 45.45)) < 0.02
    spec = np.abs(np.fft.rfft(sig[3200:-3200] * np.hanning(len(sig) - 6400)))
    f = np.fft.rfftfreq(len(sig) - 6400, 1 / 16000)
    top = f[np.argsort(spec)[-30:]]
    assert any(abs(top - 2125) < 25) and any(abs(top - 2295) < 25)
    assert np.abs(sig).max() <= 1.0 and not np.isnan(sig).any()


def test_clean_and_noisy_text_is_read_back():
    out, st = decode(rtty.synth(TEXT, snr_db=20))
    assert close(out, TEXT) > 0.95 and abs(st[-1]["lo"] - 2125) < 8 and st[-1]["baud"] == 45.45 and st[-1]["locked"]
    out, _ = decode(rtty.synth(TEXT, snr_db=8))
    assert close(out, TEXT) > 0.9


@pytest.mark.parametrize("baud,shift", [(50, 170), (75, 170), (45.45, 425), (100, 850), (45.45, 200)])
def test_other_speeds_and_shifts(baud, shift):
    out, _ = decode(rtty.synth(TEXT, baud=baud, shift=shift, snr_db=15), baud=baud, shift=shift)
    assert close(out, TEXT) > 0.9


@pytest.mark.parametrize("lo", [900, 1275, 1500, 2400])
def test_the_tones_are_found_wherever_they_are(lo):
    out, st = decode(rtty.synth(TEXT, lo=lo, snr_db=15))
    assert close(out, TEXT) > 0.9 and abs(st[-1]["lo"] - lo) < 10


def test_a_transmitter_that_is_a_little_fast_or_slow_is_still_read():
    for err in (-0.015, 0.015):
        out, _ = decode(rtty.synth(TEXT, snr_db=15, baud_error=err))
        assert close(out, TEXT) > 0.9


def test_reverse_swaps_mark_and_space():
    pcm = rtty.synth(TEXT, rev=True, snr_db=15)
    out, st = decode(pcm, rev=True)
    assert close(out, TEXT) > 0.9 and st[-1]["mark"] == st[-1]["hi"]
    wrong, _ = decode(pcm)
    assert close(wrong, TEXT) < 0.4


def test_noise_alone_prints_nothing_and_fixed_tone_works():
    rng = np.random.default_rng(5)
    out, _ = decode((rng.normal(0, 0.1, 48000 * 20) * 32767).astype("<i2").tobytes())
    assert out == ""
    out, st = decode(rtty.synth("TEST 123", lo=1700, snr_db=12), lo=1700)
    assert out.endswith("TEST 123") or close(out, "TEST 123") > 0.8


def test_unshift_on_space_is_read_both_ways():
    # a station that does not send a shift after a space (USOS): "A 1" is A, space, FIGS 1 ... then "B" without LTRS
    codes = [rtty.SHIFT_LTRS, 0x03, 0x04, rtty.SHIFT_FIGS, 0x17, 0x04, 0x19]          # A  space  FIGS 1  space  B (a USOS sender sends no LTRS before B)
    rx = rtty.RttyRx()
    ev: list = []
    for c in codes:
        rx._baudot(c, ev)
    assert ev[0]["s"] == "A 1 B"
    rx2 = rtty.RttyRx(usos=False)
    ev2: list = []
    for c in codes:
        rx2._baudot(c, ev2)
    assert ev2[0]["s"] == "A 1 ?"                                                      # without USOS the code of B stays a figure


# ---------------------------------------------------------------- the engine
class FakeAudio:
    def __init__(self, guard=None):
        self.rtty_feed, self.holds, self.clip, self.guard, self._fd_tx = None, set(), None, guard, None
        self.release_after = 0.05

    async def hold_capture(self, on, who="ftx"):
        (self.holds.add if on else self.holds.discard)(who)

    def arm_playback(self, samples, owner=None):
        self.clip = samples
        if samples is not None and owner is not None:
            asyncio.get_running_loop().create_task(self._play(owner))

    async def _play(self, owner):                                      # the audio service releases PTT a moment after the clip has gone out
        await asyncio.sleep(self.release_after)
        if self.guard.keyed and self.guard.owner == owner and self.clip is not None:
            await self.guard.request(owner, False)


async def until(cond, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.05)


async def rig(mode="DATA-U", max_tx=60, clients=("c1",)):
    d = MockDriver()
    await d.start()
    d._update(mode=mode)
    guard = TxGuard(d, allow_ptt=True, max_tx_s=max_tx, heartbeat_timeout_s=1.0)
    audio = FakeAudio(guard)
    cfg = copy.deepcopy(config.DEFAULTS)
    ev = []
    eng = RttyEngine(lambda: cfg, audio, ev.append, lambda: 14080000, guard, lambda cid: cid in clients, d)
    return d, guard, audio, eng, ev, cfg


async def test_engine_decodes_audio_and_keeps_the_text():
    d, guard, audio, eng, ev, cfg = await rig()
    await eng.set_on(True)
    assert audio.rtty_feed is not None and audio.holds == {"rtty"}
    pcm = np.frombuffer(rtty.synth("CQ DE M0DHT K", snr_db=20, lead_s=1.5), "<i2")
    for i in range(0, len(pcm) - 960, 960):
        eng.feed(pcm[i:i + 960].tobytes())
        if eng._waiting > 150:
            await until(lambda: eng._waiting < 20)
    await until(lambda: eng._waiting == 0)
    await asyncio.sleep(0.1)
    assert close(eng.text.strip(), "CQ DE M0DHT K") > 0.9 and eng.state()["text"] == eng.text
    assert any(e.get("k") == "stat" for e in ev)
    eng.clear()
    assert eng.text == ""
    await eng.set_on(False)
    assert audio.rtty_feed is None and audio.holds == set()
    await d.stop()


async def test_engine_settings_are_checked_and_kept():
    d, guard, audio, eng, ev, cfg = await rig()
    eng.set_params(baud=50, shift=425, lo=1500, rev=True)
    assert eng.p == {"baud": 50.0, "shift": 425, "lo": 1500.0, "rev": True, "usos": True}
    for bad in ({"baud": 60}, {"shift": 100}, {"lo": 100}, {"lo": 2900, "shift": 425}):
        with pytest.raises(ValueError):
            eng.set_params(**bad)
    await eng.set_on(True)
    assert eng.rx.baud == 50.0 and eng.rx.rev and eng.rx.fixed_lo == 1500.0
    eng.set_params(lo=None)
    assert eng.rx.fixed_lo is None
    await eng.set_on(False)
    await d.stop()


async def test_send_plays_the_tones_through_the_guard():
    d, guard, audio, eng, ev, cfg = await rig()
    with pytest.raises(ValueError, match="switch RTTY on"):
        await eng.send("c1", "CQ")
    await eng.set_on(True)
    r = await eng.send("c1", "cq de m0dht <k> !")
    assert r["text"] == "CQ DE M0DHT K !" or r["text"].startswith("CQ DE M0DHT K")
    assert "<" in r["dropped"] and r["pieces"] == 1
    await until(lambda: audio.clip is not None and eng.sending is not None)
    assert guard.keyed and guard.owner == "c1"
    peak = int(np.abs(audio.clip).max())
    assert 12000 < peak < 18000                                         # -6 dB: about half of full scale
    await until(lambda: eng._tx_task.done())
    assert not guard.keyed and eng.last_error == ""
    sent = "".join(e["s"] for e in ev if e.get("k") == "text" and e.get("t") == "rtty")
    assert "[TX] CQ DE M0DHT K" in sent
    assert ev[-1]["k"] == "txdone"
    await eng.set_on(False)
    await d.stop()


async def test_what_is_sent_can_be_read_by_the_decoder():
    d, guard, audio, eng, ev, cfg = await rig()
    await eng.set_on(True)
    await eng.send("c1", "CQ CQ DE M0DHT 599")
    await until(lambda: audio.clip is not None and eng.sending is not None)
    clip = audio.clip.copy()
    await until(lambda: eng._tx_task.done())
    up = np.interp(np.arange(0, len(clip) * 3), np.arange(len(clip)) * 3, clip.astype(float)).astype("<i2")          # 16 kHz -> 48 kHz
    out, _ = decode(up.tobytes())
    assert close(out, "CQ CQ DE M0DHT 599") > 0.9
    await eng.set_on(False)
    await d.stop()


async def test_send_refusals():
    d, guard, audio, eng, ev, cfg = await rig(mode="CW-U")
    await eng.set_on(True)
    with pytest.raises(ValueError, match="USB, LSB or a data mode"):
        await eng.send("c1", "CQ")
    d._update(mode="USB")
    audio._fd_tx = object()
    with pytest.raises(ValueError, match="FreeDV"):
        await eng.send("c1", "CQ")
    audio._fd_tx = None
    with pytest.raises(ValueError, match="nothing to send"):
        await eng.send("c1", "<>@")
    await eng.set_on(False)
    await d.stop()


async def test_a_long_text_is_cut_into_pieces_that_fit_the_time_limit():
    d, guard, audio, eng, ev, cfg = await rig(max_tx=12)
    await eng.set_on(True)
    audio.release_after = 0.02
    text = " ".join(["CQ DE M0DHT"] * 12)
    r = await eng.send("c1", text)
    assert r["pieces"] >= 3
    await until(lambda: eng._tx_task.done(), timeout=20)
    assert eng.last_error == "" and not guard.keyed
    await eng.set_on(False)
    await d.stop()


async def test_halt_and_a_missing_operator_stop_the_sending():
    d, guard, audio, eng, ev, cfg = await rig()
    audio.release_after = 30                                           # the clip "plays" for a long time
    await eng.set_on(True)
    await eng.send("c1", "CQ CQ CQ CQ CQ")
    await until(lambda: guard.keyed)
    await eng.halt("test")
    await until(lambda: eng._tx_task.done())
    assert not guard.keyed and eng.last_error == "test" and eng.queue == []
    d2, guard2, audio2, eng2, ev2, _ = await rig(clients=())            # "c1" is not connected
    audio2.release_after = 30
    await eng2.set_on(True)
    await eng2.send("c1", "CQ CQ CQ CQ CQ")
    await until(lambda: eng2._tx_task.done())
    assert "operator left" in eng2.last_error and not guard2.keyed
    await eng.set_on(False)
    await eng2.set_on(False)
    await d.stop()
    await d2.stop()


# ---------------------------------------------------------------- the log takes CW and RTTY contacts
def test_the_log_takes_cw_and_rtty_contacts_and_writes_the_right_adif_mode():
    base = {"call": "G4ABC", "freq_mhz": 14.08, "qso_date": "20261023", "time_on": "201500", "qso_date_off": "20261023", "time_off": "201900", "rst_sent": "599", "rst_rcvd": "579", "name": "Tom"}
    q = qsolog.clean({**base, "mode": "RTTY"})
    assert q["mode"] == "RTTY" and q["band"] == "20m" and q["rst_sent"] == "599"
    rec = qsolog.adif_record({**q, "station_callsign": "M0DHT"})
    assert "<MODE:4>RTTY" in rec and "<RST_SENT:3>599" in rec and "<SUBMODE" not in rec and "<COMMENT:" in rec
    assert "<MODE:2>CW" in qsolog.adif_record(qsolog.clean({**base, "mode": "cw"}))
    with pytest.raises(ValueError, match="mode must be"):
        qsolog.clean({**base, "mode": "SSB"})
    assert qsolog.adif_header("1.0").startswith("Radio Remote log")
