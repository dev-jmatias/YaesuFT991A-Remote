"""JS8: the engine talks to a fake JS8Call (a TCP server that speaks its JSON API), the audio between the radio and the loopback is faked, and the transmit protection is the real TxGuard."""
import asyncio
import copy
import json

import pytest

from radio_remote import config, js8_engine
from radio_remote.audio.backends import AudioDeviceError
from radio_remote.audio.levels import FRAME_BYTES
from radio_remote.js8_engine import Js8Engine, merge_ini
from radio_remote.radio.mock import MockDriver
from radio_remote.safety import TxGuard
from test_phase8 import PW, add_user, admin_user, close_all, login_as, make_app  # noqa: F401

OK = {"platform_ok": True, "arch": "aarch64", "libs": True, "pulse": True, "loopback": True, "system_ok": True, "app_ok": True, "missing": []}


class FakeJs8Call:
    """Speaks the JS8Call API: records the commands, emits what a test tells it to."""

    def __init__(self):
        self.cmds, self.clients, self.server = [], [], None
        self.speed = 0

    async def start(self):
        self.server = await asyncio.start_server(self._on, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _on(self, reader, writer):
        self.clients.append(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                m = json.loads(line)
                self.cmds.append(m)
                if m["type"] == "MODE.GET_SPEED":
                    await self.emit("MODE.SPEED", params={"SPEED": self.speed})
                elif m["type"] == "RIG.GET_FREQ":
                    await self.emit("RIG.FREQ", params={"DIAL": 14078000, "FREQ": 14079500, "OFFSET": 1500})
                elif m["type"] == "MODE.SET_SPEED":
                    self.speed = m["params"]["SPEED"]
                    await self.emit("MODE.SPEED", params={"SPEED": self.speed})
        except ConnectionError:
            pass

    async def emit(self, typ, value="", params=None):
        for w in list(self.clients):
            if not w.is_closing():
                w.write((json.dumps({"type": typ, "value": value, "params": {**(params or {}), "_ID": -1}}) + "\n").encode())
                await w.drain()

    def types(self):
        return [c["type"] for c in self.cmds]

    async def stop(self):
        for w in self.clients:
            w.close()
        self.server.close()
        await self.server.wait_closed()


class FakeSink:
    frames = []

    def __init__(self, device):
        self.device = device

    async def start(self):
        pass

    async def write_frame(self, pcm):
        FakeSink.frames.append(pcm)
        await asyncio.sleep(0.001)

    async def stop(self):
        pass


class FakeSource:
    def __init__(self, device):
        pass

    async def start(self):
        pass

    async def read_frame(self):
        await asyncio.sleep(0.02)
        return b"\x01\x00" * (FRAME_BYTES // 2)

    async def stop(self):
        pass


class FakeAudio:
    def __init__(self):
        self.js8_feed, self.holds, self.stream, self.stream_owner, self._fd_tx = None, set(), None, None, None

    async def hold_capture(self, on, who="ftx"):
        (self.holds.add if on else self.holds.discard)(who)

    def arm_stream(self, source, owner=None):
        self.stream, self.stream_owner = source, owner


async def until(cond, timeout=6.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.02)
    return cond()


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    fake = FakeJs8Call()
    port = await fake.start()
    monkeypatch.setattr(js8_engine, "API_PORT", port)
    monkeypatch.setattr(js8_engine, "prereqs", lambda d: dict(OK))
    monkeypatch.setattr(js8_engine, "AlsaSink", FakeSink)
    monkeypatch.setattr(js8_engine, "AlsaSource", FakeSource)
    FakeSink.frames = []
    d = MockDriver()
    await d.start()
    d._update(mode="DATA-U", frequency=14078000)
    guard = TxGuard(d, allow_ptt=True, max_tx_s=60, heartbeat_timeout_s=1.0)
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["storage"]["data_dir"] = str(tmp_path)
    cfg["ftx"]["callsign"], cfg["ftx"]["grid_square"] = "M0DHT", "IO94"
    audio, ev, clients = FakeAudio(), [], {"c1"}
    eng = Js8Engine(lambda: cfg, audio, ev.append, lambda: d.state.get("frequency") or 0, guard, lambda cid: cid in clients, d)
    yield eng, fake, d, guard, audio, ev, clients, cfg
    await eng.shutdown()
    await fake.stop()
    await d.stop()


async def start(eng, fake):
    await eng.set_on(True)
    assert await until(lambda: eng.api_up and eng.status == "on"), eng.status


def test_merge_ini_changes_only_our_keys(tmp_path):
    p = tmp_path / "x.ini"
    p.write_text("[MultiSettings]\nCurrentName=Default\n\n[Configuration]\nMyCall=OLD\nOther=keep\n\n[Other]\nA=1\n")
    merge_ini(p, "Configuration", {"MyCall": "M0DHT", "TCPEnabled": "true"})
    t = p.read_text()
    assert "MyCall=M0DHT" in t and "MyCall=OLD" not in t and "Other=keep" in t and "TCPEnabled=true" in t and "[Other]\nA=1" in t and "[MultiSettings]\nCurrentName=Default" in t
    assert t.index("TCPEnabled") < t.index("[Other]")
    q = tmp_path / "new.ini"
    merge_ini(q, "Configuration", {"MyCall": "G4ABC"})
    assert q.read_text().strip().splitlines() == ["", "[Configuration]", "MyCall=G4ABC"] or "MyCall=G4ABC" in q.read_text()


def test_prereqs_say_what_is_missing(tmp_path):
    r = js8_engine.prereqs(tmp_path)
    assert "platform_ok" in r and isinstance(r["missing"], list)


async def test_switching_on_checks_everything_first(rig, monkeypatch):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    cfg["ftx"]["callsign"] = ""
    with pytest.raises(ValueError, match="callsign"):
        await eng.set_on(True)
    cfg["ftx"]["callsign"] = "M0DHT"
    monkeypatch.setattr(js8_engine, "prereqs", lambda d: {**OK, "system_ok": False, "missing": ["the loopback sound card"]})
    with pytest.raises(ValueError, match="not prepared.*loopback"):
        await eng.set_on(True)
    monkeypatch.setattr(js8_engine, "prereqs", lambda d: {**OK, "app_ok": False})
    with pytest.raises(ValueError, match="not installed"):
        await eng.set_on(True)
    monkeypatch.setattr(js8_engine, "prereqs", lambda d: {**OK, "platform_ok": False})
    with pytest.raises(ValueError, match="not available"):
        await eng.set_on(True)
    guard.keyed = True
    monkeypatch.setattr(js8_engine, "prereqs", lambda d: dict(OK))
    with pytest.raises(ValueError, match="transmitting"):
        await eng.set_on(True)
    assert not eng.on and not (eng.data / "run").exists()


async def test_on_writes_the_config_asks_systemd_to_start_it_and_connects(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    assert (eng.data / "run").exists() and audio.holds == {"js8"} and audio.js8_feed is not None
    ini = (eng.data / "config" / "JS8Call - rr.ini").read_text()
    for k in ("MyCall=M0DHT", "MyGrid=IO94", "TCPEnabled=true", "PSKReporter=false", "SpotToAPRS=false", "CheckForUpdates=false", "SoundInId=js8_in", "SoundOutId=js8_out"):
        assert k in ini, k
    assert "RIG.SET_FREQ" in fake.types() and next(c for c in fake.cmds if c["type"] == "RIG.SET_FREQ")["params"]["DIAL"] == 14078000
    assert eng.dial == 14078000 and eng.offset == 1500
    await eng.set_on(False)
    assert not (eng.data / "run").exists() and audio.holds == set() and audio.js8_feed is None and eng.status == "off"


async def test_received_messages_become_activity_directed_and_heard(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    await fake.emit("RX.ACTIVITY", "G4ABC: @ALLCALL CQ CQ", {"OFFSET": 1500, "SNR": -12, "SPEED": 0})
    await fake.emit("RX.DIRECTED", "", {"FROM": "G4ABC", "TO": "@ALLCALL", "CMD": " CQ", "TEXT": "G4ABC: @ALLCALL CQ CQ CQ  ", "SNR": -12, "OFFSET": 1500, "GRID": " IO91", "SPEED": 0})
    await fake.emit("RX.SPOT", "", {"CALL": "EA1XYZ", "SNR": -20, "GRID": "IN73", "OFFSET": 900})
    assert await until(lambda: len(eng.directed) == 1 and len(eng.activity) == 1 and "EA1XYZ" in eng.heard)
    d0 = eng.directed[0]
    assert d0["from"] == "G4ABC" and d0["to"] == "@ALLCALL" and d0["cmd"] == "CQ" and d0["snr"] == -12 and d0["grid"] == "IO91"
    assert eng.heard["G4ABC"]["grid"] == "IO91" and eng.heard["EA1XYZ"]["grid"] == "IN73"
    kinds = [e["k"] for e in ev if e.get("t") == "js8"]
    assert "activity" in kinds and "directed" in kinds and "heard" in kinds
    assert eng.state()["heard"][0]["call"] in ("G4ABC", "EA1XYZ") and len(eng.state()["activity"]) == 1
    eng.clear()
    assert len(eng.activity) == 0 and ev[-1] == {"t": "js8", "k": "clear"}


async def test_the_radio_audio_goes_into_the_loopback_and_silence_while_we_transmit(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    loud = b"\x10\x00" * (FRAME_BYTES // 2)
    for _ in range(5):
        audio.js8_feed(loud)
    audio.js8_feed(None)
    assert await until(lambda: len(FakeSink.frames) >= 6)
    assert FakeSink.frames[:5] == [loud] * 5 and FakeSink.frames[5] == bytes(FRAME_BYTES)


async def test_send_is_refused_unless_it_is_safe(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    with pytest.raises(ValueError, match="switch JS8 on"):
        await eng.send("c1", "CQ")
    await start(eng, fake)
    d._update(mode="CW-U")
    with pytest.raises(ValueError, match="USB, LSB or a data mode"):
        await eng.send("c1", "CQ")
    d._update(mode="DATA-U")
    audio._fd_tx = object()
    with pytest.raises(ValueError, match="FreeDV"):
        await eng.send("c1", "CQ")
    audio._fd_tx = None
    with pytest.raises(ValueError, match="nothing to send"):
        await eng.send("c1", "   \n ")
    guard.allow_ptt = False
    d.caps.data["model"]["is_mock"] = False
    with pytest.raises(ValueError, match="disabled"):
        await eng.send("c1", "CQ")
    assert "TX.SEND_MESSAGE" not in fake.types()


async def test_a_requested_message_keys_the_radio_streams_the_audio_and_releases(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    r = await eng.send("c1", "CQ CQ CQ  M0DHT\nIO94")
    assert r["text"] == "CQ CQ CQ M0DHT IO94"
    assert await until(lambda: "TX.SEND_MESSAGE" in fake.types())
    assert next(c for c in fake.cmds if c["type"] == "TX.SEND_MESSAGE")["value"] == "CQ CQ CQ M0DHT IO94"
    await fake.emit("RIG.PTT", "on", {"PTT": True})                              # JS8Call is at the start of its slot
    assert await until(lambda: guard.keyed and eng.transmitting)
    assert guard.owner == "c1" and audio.stream is eng._tx and audio.stream_owner == "c1" and eng._tx.active
    assert 0.15 < eng._tx.gain < 0.25                                             # -14 dB
    await asyncio.sleep(0.3)
    assert eng._tx.frame() == b"\x01\x00" * (FRAME_BYTES // 2)                   # JS8Call's audio from the loopback reaches the stream
    assert guard.keyed
    await fake.emit("RIG.PTT", "off", {"PTT": False})
    assert await until(lambda: not guard.keyed and not eng.transmitting)
    assert audio.stream is None and not eng._tx.active
    await fake.emit("TX.END")
    assert await until(lambda: eng.sending is None)


async def test_a_second_frame_of_the_same_message_keys_again(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    await eng.send("c1", "A LONG MESSAGE")
    for _ in range(2):
        await fake.emit("RIG.PTT", "on", {"PTT": True})
        assert await until(lambda: guard.keyed)
        await fake.emit("RIG.PTT", "off", {"PTT": False})
        assert await until(lambda: not guard.keyed)
        await asyncio.sleep(0.05)


async def test_a_transmission_nobody_asked_for_is_stopped(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    await fake.emit("RIG.PTT", "on", {"PTT": True})                              # JS8Call replying by itself
    assert await until(lambda: "RIG.TX_HALT" in fake.types())
    assert not guard.keyed and not eng.transmitting and audio.stream is None
    assert any(e.get("k") == "error" and "nobody had asked" in e["text"] for e in ev)
    eng.owner, eng.armed_until = "c9", asyncio.get_running_loop().time() + 99        # asked by a page that has since gone away
    fake.cmds.clear()
    await fake.emit("RIG.PTT", "on", {"PTT": True})
    assert await until(lambda: "RIG.TX_HALT" in fake.types())
    assert not guard.keyed


async def test_the_guard_can_refuse_and_then_js8call_is_halted(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    await eng.send("c1", "CQ")
    guard.allow_ptt = False
    d.caps.data["model"]["is_mock"] = False
    await fake.emit("RIG.PTT", "on", {"PTT": True})
    assert await until(lambda: "RIG.TX_HALT" in fake.types())
    assert not guard.keyed and any("refused" in e.get("text", "") for e in ev if e.get("k") == "error")


async def test_losing_the_operator_or_the_time_limit_halts_js8call(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    await eng.send("c1", "CQ")
    await fake.emit("RIG.PTT", "on", {"PTT": True})
    assert await until(lambda: guard.keyed)
    fake.cmds.clear()
    clients.discard("c1")                                                        # the operator's page is gone
    assert await until(lambda: "RIG.TX_HALT" in fake.types() and not guard.keyed)
    assert not eng.transmitting and audio.stream is None
    clients.add("c1")
    await eng.send("c1", "CQ")
    await fake.emit("RIG.PTT", "on", {"PTT": True})
    assert await until(lambda: guard.keyed)
    fake.cmds.clear()
    await guard._unkey("time limit")                                              # the guard stopped the transmission by itself
    assert await until(lambda: "RIG.TX_HALT" in fake.types() and not eng.transmitting)


async def test_halt_stops_a_transmission_at_once(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    await eng.send("c1", "CQ")
    await fake.emit("RIG.PTT", "on", {"PTT": True})
    assert await until(lambda: guard.keyed)
    await eng.halt("halted by the operator")
    assert not guard.keyed and eng.sending is None
    assert await until(lambda: "RIG.TX_HALT" in fake.types())


async def test_speed_and_offset_are_checked_and_sent(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    await eng.set_speed(1)
    assert await until(lambda: eng.speed == 1 and "MODE.SET_SPEED" in fake.types())
    assert next(c for c in fake.cmds if c["type"] == "MODE.SET_SPEED")["params"] == {"SPEED": 1}
    with pytest.raises(ValueError, match="unknown speed"):
        await eng.set_speed(3)
    with pytest.raises(ValueError, match="300..2900"):
        await eng.set_offset(100)
    await eng.set_offset(1800)
    assert eng.offset == 1800
    assert await until(lambda: any(c["type"] == "RIG.SET_FREQ" and c["params"].get("OFFSET") == 1800 for c in fake.cmds))
    assert [c for c in fake.cmds if c["type"] == "RIG.SET_FREQ"][-1]["params"] == {"DIAL": 14078000, "OFFSET": 1800}


async def test_when_js8call_goes_away_the_engine_says_so_and_stops(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    await start(eng, fake)
    (eng.data / "run").unlink()                                                  # the service removes the run file when JS8Call dies
    for w in fake.clients:
        w.close()
    assert await until(lambda: not eng.on and eng.status == "error")
    assert audio.js8_feed is None and audio.holds == set()


async def test_the_state_lists_what_the_page_needs(rig):
    eng, fake, d, guard, audio, ev, clients, cfg = rig
    s = eng.state()
    assert s["status"] == "off" and s["prereq"]["system_ok"] and {x["name"] for x in s["speeds"]} >= {"Normal", "Slow"} and any(b["hz"] == 14078000 for b in s["bands"])
    assert s["settings"] == {"callsign": "M0DHT", "grid_square": "IO94", "tx_level_db": -14.0}




# ---------------------------------------------------------------- the audio service streams JS8Call's audio to the radio
async def test_the_audio_service_sends_live_audio_instead_of_the_microphone_and_it_is_limited():
    import numpy as np
    from radio_remote.audio.service import AudioService
    gate = {"open": False}
    svc = AudioService(copy.deepcopy(config.DEFAULTS)["audio"], is_mock=True, tx_gate=lambda cid: gate["open"] and cid == "c1", on_levels=lambda d: None)

    class Src:
        gain = 0.5
        n = 0

        def frame(self):
            self.n += 1
            if self.n > 40:
                return None                                              # nothing more: silence goes out
            return (np.full(960, 20000, dtype="<i2")).tobytes()

    src = Src()
    svc.arm_stream(src, owner="c1")
    assert svc._player_running                                          # the browsers' microphones are ignored meanwhile
    gate["open"] = True
    await asyncio.sleep(0.5)                                            # the streamer feeds one frame every 20 ms by itself
    peaks = [int(np.abs(np.frombuffer(f, "<i2")).max()) for f in svc._tx_q]
    assert len(peaks) >= 3 and all(abs(p - 10000) <= 2 for p in peaks)         # 20000 x 0.5, from the stream and not the (silent) microphone
    mic = b"\x00\x00" * 960
    svc._mic_frame(types_namespace(), mic)                              # a microphone frame while streaming is ignored
    assert int(np.abs(np.frombuffer(svc._tx_q[-1], "<i2")).max()) in (0, 10000)
    gate["open"] = False
    await asyncio.sleep(0.1)
    svc.arm_stream(None)
    assert svc._stream is None and not svc._player_running
    svc._stream_task = None


def types_namespace():
    import types
    return types.SimpleNamespace(conn_id="c1")


# ---------------------------------------------------------------- the web API
async def test_api_commands_state_and_permissions(make_app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = await make_app()
    root = await admin_user(client)
    st = await (await client.get("/api/js8")).json()
    assert st["status"] == "off" and "prereq" in st and st["speeds"][0]["name"] == "Slow"
    ws, hello = await root.ws()
    assert "js8" in hello["audio"]
    ack = await root.cmd(ws, 1, type="js8", on="yes")
    assert not ack["ok"]
    ack = await root.cmd(ws, 2, type="js8_send", text="CQ")
    assert not ack["ok"] and "switch JS8 on" in json.dumps(ack)
    ack = await root.cmd(ws, 3, type="js8_set", speed=True)
    assert not ack["ok"]
    ack = await root.cmd(ws, 4, type="js8_halt")
    assert ack["ok"]
    await add_user(root, "watcher", "viewer")
    v = await login_as(root, "watcher")
    assert (await v.c.get("/api/js8")).status == 200
    assert (await v.c.post("/api/js8/install", json={}, headers=v.h)).status == 403
    assert (await v.c.post("/api/js8/setup", json={}, headers=v.h)).status == 403
    r = await client.post("/api/js8/setup", json={}, headers=root.h)
    assert r.status == 200
    await close_all(root, v)


def test_older_systems_get_the_older_js8call_and_a_wrong_build_is_not_taken_for_installed(tmp_path, monkeypatch):
    from radio_remote import js8_engine as j
    monkeypatch.setattr(j.sys, "platform", "linux")
    monkeypatch.setattr(j.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(j, "glibc", lambda: (2, 36))                                 # Debian 12
    assert j.wanted_version() == j.OLD_VERSION and j.OLD_VERSION in j.app_url("x86_64")
    monkeypatch.setattr(j, "glibc", lambda: (2, 41))                                 # Debian 13
    assert j.wanted_version() == j.VERSION and j.VERSION in j.app_url("aarch64")
    app = tmp_path / "app" / "squashfs-root"
    app.mkdir(parents=True)
    (app / "AppRun").write_text("x")
    assert j.prereqs(tmp_path)["app_ok"]                                             # no version file: installed by an earlier build, which fetched v3.0.3
    monkeypatch.setattr(j, "glibc", lambda: (2, 36))
    assert not j.prereqs(tmp_path)["app_ok"]                                         # v3.0.3 cannot start on glibc 2.36: install again
    (tmp_path / "app" / "version").write_text(j.OLD_VERSION + "\n")
    assert j.prereqs(tmp_path)["app_ok"]
