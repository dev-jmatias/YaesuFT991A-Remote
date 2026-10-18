"""FT8/FT4 engine (receive through the slot cutter, transmit scheduling), the contact log with its ADIF export, and the uploaders (no network: the call is injected)."""
import asyncio
import types

import numpy as np
import pytest

from conftest import ftx_slot
from radio_remote import qsolog, uploaders
from radio_remote.ftx_engine import FtxEngine


class FakeAudio:
    def __init__(self):
        self.ftx_feed, self.clip, self.held = None, None, 0

    async def hold_capture(self, on):
        self.held += 1 if on else -1

    def arm_playback(self, samples, owner=None):
        self.clip = samples


class FakeGuard:
    def __init__(self):
        self.keyed, self.owner, self.calls, self.beats = False, None, [], 0

    async def request(self, owner, on):
        self.calls.append((owner, on))
        self.keyed, self.owner = on, (owner if on else None)

    async def heartbeat(self, owner):
        self.beats += 1


async def until(cond, timeout=3.0):
    """wait for the engine's own background task (it polls every 0.25 s) instead of guessing a delay: a loaded test machine made a fixed sleep flaky"""
    end = asyncio.get_running_loop().time() + timeout
    while not cond() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.05)


def make(cfg):
    ev = []
    audio, guard = FakeAudio(), FakeGuard()
    eng = FtxEngine(lambda: cfg, audio, ev.append, lambda: 14074000, guard, lambda cid: cid == "c1")
    return eng, audio, guard, ev


async def test_receive_through_the_slot_cutter(ftx, cfg, monkeypatch):
    eng, audio, guard, ev = make(cfg)
    await eng.set_on(True, "FT8")
    assert audio.ftx_feed is not None and audio.held == 1
    sig = ftx_slot(ftx, "FT8", [("CQ K1FM FN30", 1000.0), ("K1FM WV1W -10", 1700.0)])
    pcm = (np.clip(np.repeat(sig, 4), -1, 1) * 30000).astype("<i2")
    t0 = (int(1_000_000 // 15) + 1) * 15.0 - 4.0                          # 4 s of silence, then the slot starts on a slot boundary
    seq = np.concatenate([np.zeros(4 * 48000, "<i2"), pcm, np.zeros(20 * 48000, "<i2")])
    clock = types.SimpleNamespace(now=t0)
    monkeypatch.setattr("radio_remote.ftx_engine.time", types.SimpleNamespace(time=lambda: clock.now, perf_counter=__import__("time").perf_counter, strftime=__import__("time").strftime, gmtime=__import__("time").gmtime))
    for i in range(0, len(seq) - 960, 960):
        clock.now = t0 + (i + 960) / 48000
        eng.feed(seq[i:i + 960].tobytes())
    await asyncio.sleep(1.0)                                              # the decode runs in a worker thread
    decs = [e for e in ev if e.get("k") == "dec"]
    assert decs, [e.get("k") for e in ev]
    texts = {it["text"] for d in decs for it in d["items"]}
    assert {"CQ K1FM FN30", "K1FM WV1W -10"} <= texts
    heard = [it["text"] for d in decs for it in d["items"]]
    assert len(heard) == len(set(heard))                                   # the final pass does not repeat what the early pass showed
    assert decs[0]["hz"] == 14074000 and decs[0]["parity"] in (0, 1)
    assert any(e.get("k") == "wf" for e in ev)
    assert eng.history and eng.state()["history"]
    await eng.set_on(False)
    assert audio.ftx_feed is None


async def test_transmit_keys_ptt_through_the_guard_and_sends_the_clip(ftx, cfg, monkeypatch):
    eng, audio, guard, ev = make(cfg)
    await eng.set_on(True, "FT8")
    out = eng.set_tx("c1", "cq k1fm fn30", 1500, 0)
    assert out["message"] == "CQ K1FM FN30"
    with pytest.raises(ValueError):
        eng.set_tx("c1", "THIS IS FAR TOO LONG FOR FREE TEXT", 1500, 0)
    with pytest.raises(ValueError):
        eng.set_tx("c1", "CQ K1FM FN30", 100, 0)
    p = eng.pending
    await eng._transmit(p, 100)
    assert guard.calls == [("c1", True)]
    assert audio.clip is not None and audio.clip.dtype == np.dtype("<i2")
    assert abs(len(audio.clip) / 16000 - (0.2 + 79 * 0.16 + 0.1)) < 0.05
    assert int(np.abs(audio.clip).max()) > 8000                           # -6 dB default level: about half of full scale
    assert any(e.get("k") == "tx" and e["text"] == "CQ K1FM FN30" for e in ev)
    guard.keyed = False                                                    # the hub released PTT after the clip ended
    await until(lambda: any(e.get("k") == "txdone" for e in ev))
    assert any(e.get("k") == "txdone" for e in ev)
    assert eng.pending.repeats == cfg["ftx"]["max_tx_repeats"] - 1 and not eng.transmitting
    await eng.set_on(False)


async def test_the_operator_leaving_cancels_the_message_and_repeats_run_out(ftx, cfg):
    eng, audio, guard, ev = make(cfg)
    await eng.set_on(True, "FT4")
    eng.set_tx("c1", "CQ K1FM FN30", 1200, 1, repeats=1)
    await eng._transmit(eng.pending, 7)
    guard.keyed = False
    await until(lambda: eng.pending is None)
    assert eng.pending is None                                            # the one allowed repetition has been used
    eng.set_tx("c1", "CQ K1FM FN30", 1200, 1)
    eng.conn_gone("c1")
    assert eng.pending is None
    await eng.set_on(False)


def test_the_contact_log_and_its_adif_export(tmp_path):
    lg = qsolog.QsoLog(tmp_path / "log.jsonl")
    q = lg.add({"call": "k1abc", "gridsquare": "fn42", "mode": "FT8", "rst_sent": "-10", "rst_rcvd": "-05", "freq_mhz": 14.0745, "qso_date": "20261017", "time_on": "101500",
                "qso_date_off": "20261017", "time_off": "101600", "station_callsign": "CT1XYZ", "my_gridsquare": "IN51"})
    q4 = lg.add({"call": "W1AW", "mode": "FT4", "freq_mhz": 7.0475, "qso_date": "20261017", "time_on": "102000", "qso_date_off": "20261017", "time_off": "102030"})
    assert q["band"] == "20m" and q4["band"] == "40m"
    text = lg.adif("1.2.3")
    assert text.count("<EOR>") == 2 and "<EOH>" in text
    assert "<MODE:3>FT8" in text and "<MODE:4>MFSK" in text and "<SUBMODE:3>FT4" in text
    assert "<CALL:5>K1ABC" in text and "<BAND:3>20m" in text and "<FREQ:9>14.074500" in text
    lg2 = qsolog.QsoLog(tmp_path / "log.jsonl")                           # survives a restart
    assert [x["call"] for x in lg2.items] == ["K1ABC", "W1AW"]
    lg2.mark(q["id"], "qrz", True, "ok")
    assert [x["call"] for x in lg2.pending("qrz")] == ["W1AW"] and len(lg2.pending("eqsl")) == 2
    assert lg2.delete(q["id"]) and not lg2.delete(q["id"])
    for bad in ({"call": "NOT A CALL"}, {"call": "K1ABC", "freq_mhz": "x"}, {"call": "K1ABC", "freq_mhz": 14.07, "qso_date": "2026", "time_on": "1", "qso_date_off": "x", "time_off": "y"},
                {"call": "K1ABC", "gridsquare": "ZZ99", "freq_mhz": 14.07, "qso_date": "20261017", "time_on": "101500", "qso_date_off": "20261017", "time_off": "101600"}):
        with pytest.raises(ValueError):
            qsolog.clean(bad)


async def test_uploaders_build_the_right_requests_and_read_the_answers(cfg):
    q = {"call": "K1ABC", "mode": "FT8", "freq_mhz": 14.0745, "qso_date": "20261017", "time_on": "101500", "qso_date_off": "20261017", "time_off": "101600", "band": "20m"}
    sent = []

    def poster(status, body):
        async def post(url, data=None, files=None):
            sent.append((url, data, files))
            return status, body
        return post

    ok, msg = await uploaders.qrz("KEY123", q, poster(200, "RESULT=OK&LOGID=42&COUNT=1"))
    assert ok and "42" in msg and sent[-1][0] == "https://logbook.qrz.com/api"
    assert sent[-1][1]["KEY"] == "KEY123" and sent[-1][1]["ACTION"] == "INSERT" and "<CALL:5>K1ABC" in sent[-1][1]["ADIF"]
    assert await uploaders.qrz("K", q, poster(200, "RESULT=FAIL&REASON=wrong station callsign")) == (False, "QRZ refused it: wrong station callsign")
    assert (await uploaders.qrz("K", q, poster(200, "RESULT=FAIL&REASON=Unable to add QSO to database: duplicate")))[0] is True
    assert (await uploaders.qrz("", q, poster(200, "")))[0] is False

    ok, msg = await uploaders.eqsl("me", "pw", q, poster(200, "<html>Result: 1 out of 1 records added</html>"))
    assert ok and sent[-1][0].endswith("ImportADIF.cfm")
    adif = sent[-1][2]["Filename"][1]
    assert "<EQSL_USER:2>me" in adif and "<EQSL_PSWD:2>pw" in adif and "<CALL:5>K1ABC" in adif
    assert (await uploaders.eqsl("me", "pw", q, poster(200, "Error: No match on eQSL_User/EQSL_Pswd")))[0] is False
    assert (await uploaders.eqsl("me", "pw", q, poster(200, "Warning: Duplicate")))[0] is True

    ok, msg = await uploaders.clublog("a@b.c", "pw", "APIKEY", "CT1XYZ", q, poster(200, "OK"))
    assert ok and sent[-1][1]["api"] == "APIKEY" and sent[-1][1]["callsign"] == "CT1XYZ" and "<CALL:5>K1ABC" in sent[-1][1]["adif"]
    assert (await uploaders.clublog("a@b.c", "pw", "", "CT1XYZ", q, poster(200, "OK")))[0] is False       # no application key
    assert (await uploaders.clublog("a@b.c", "pw", "K", "CT1XYZ", q, poster(403, "Forbidden")))[0] is False

    async def boom(url, data=None, files=None):
        raise OSError("network down")
    ok, msg = await uploaders.qrz("K", q, boom)
    assert not ok and "network down" in msg


def test_clock_info_reads_timedatectl_and_chrony(monkeypatch):
    """The clock state shown on the tab: NTP synchronised or not, and the offset from the time server (systemd-timesyncd on Raspberry Pi OS, chrony elsewhere)."""
    import subprocess
    from radio_remote import ftx_engine

    outputs = {}

    def fake_run(cmd, **kw):
        out = outputs.get(" ".join(cmd))
        if out is None:
            raise FileNotFoundError(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    outputs.update({"timedatectl show -p NTPSynchronized --value": "yes\n",
                    "timedatectl timesync-status": "       Server: x\n      Offset: +334us\n       Delay: 13.7ms\n"})
    assert ftx_engine.clock_info() == {"ok": True, "offset_ms": pytest.approx(0.334), "source": "systemd-timesyncd"}
    outputs["timedatectl timesync-status"] = "      Offset: -2.5ms\n"
    assert ftx_engine.clock_info()["offset_ms"] == pytest.approx(-2.5)
    outputs.update({"timedatectl show -p NTPSynchronized --value": "no\n", "timedatectl timesync-status": ""})
    outputs["chronyc tracking"] = "System time     : 0.250000000 seconds slow of NTP time\nLeap status     : Normal\n"
    got = ftx_engine.clock_info()
    assert got["ok"] is False and got["offset_ms"] == pytest.approx(-250.0) and got["source"] == "chrony"
    outputs.clear()                                                         # a machine with neither: nothing is claimed
    assert ftx_engine.clock_info() == {"ok": None, "offset_ms": None, "source": ""}


def test_every_logged_contact_carries_the_reports_in_its_comment(tmp_path):
    """The comment QRZ and the logging programs show: the mode and the reports as exchanged, e.g. "FT8 Sent: +06 Rcvd: +01"."""
    lg = qsolog.QsoLog(tmp_path / "log.jsonl")
    base = {"freq_mhz": 14.0745, "qso_date": "20261017", "time_on": "101500", "qso_date_off": "20261017", "time_off": "101600"}
    lg.add({**base, "call": "K1ABC", "mode": "FT8", "rst_sent": "+06", "rst_rcvd": "+01"})
    lg.add({**base, "call": "W1AW", "mode": "FT4", "rst_sent": "-12", "rst_rcvd": "-05"})
    lg.add({**base, "call": "DL1ABC", "mode": "FT8", "rst_sent": "-10"})                        # only one report known
    lg.add({**base, "call": "EA4XYZ", "mode": "FT8"})                                           # none: no comment
    lg.add({**base, "call": "JA1ABC", "mode": "FT8", "rst_sent": "-03", "rst_rcvd": "-04", "comment": "Hello"})        # a comment of its own wins
    text = lg.adif("1.0")
    assert "<COMMENT:23>FT8 Sent: +06 Rcvd: +01" in text
    assert "<COMMENT:23>FT4 Sent: -12 Rcvd: -05" in text
    assert "<COMMENT:13>FT8 Sent: -10 <" in text
    assert "<COMMENT:5>Hello" in text
    recs = text.split("<EOR>")
    assert "COMMENT" not in recs[3]


async def test_ft8_will_not_transmit_while_freedv_is_on(ftx, cfg):
    eng, audio, guard, ev = make(cfg)
    await eng.set_on(True, "FT8")
    audio._fd_tx = object()                                                 # FreeDV is on
    with pytest.raises(ValueError, match="FreeDV is on"):
        eng.set_tx("c1", "CQ K1FM FN30", 1500, 0)
    audio._fd_tx = None
    assert eng.set_tx("c1", "CQ K1FM FN30", 1500, 0)["message"] == "CQ K1FM FN30"
    await eng.set_on(False)
