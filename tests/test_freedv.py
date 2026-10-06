"""FreeDV (700D/700E): config, the on/off command, and how the audio service routes receive and transmit audio through the modem.
The real libcodec2 round trip is in test_freedv_codec.py (skipped where the library is missing); here the modem chains are stand-ins."""
import asyncio
import copy
import json
import types

import pytest
from conftest import setup_admin
from test_audio import until
from test_phase8 import make_app  # noqa: F401
from test_web import origin, recv_until

from radio_remote import config
from radio_remote.audio import freedv, service
from radio_remote.audio.backends import RecordingSink, ToneSource
from radio_remote.audio.levels import FRAME_BYTES
from radio_remote.audio.service import AudioService, AudioUnavailable
from radio_remote.common import K_AUDIO

SPEECH = b"\x07\x00" * (FRAME_BYTES // 2)            # what the fake receive modem "decodes"
TONES = b"\x09\x00" * (FRAME_BYTES // 2)             # what the fake transmit modem "modulates"


class FakeRx:
    sync, snr = 1, 8.5
    restarts = 0

    def __init__(self, mode):
        self.mode = mode

    def restart(self):
        FakeRx.restarts += 1

    def process(self, pcm):
        return SPEECH

    def close(self):
        pass


class FakeTx:
    made = []

    def __init__(self, mode, level=0.5):
        self.mode, self.level, self.fed = mode, level, []
        FakeTx.made.append(self)

    def process(self, pcm):
        self.fed.append(pcm)
        return [TONES]

    def close(self):
        pass


@pytest.fixture
def fake_modem(monkeypatch):
    FakeTx.made = []
    monkeypatch.setattr(freedv, "available", lambda: (True, ""))
    monkeypatch.setattr(freedv, "mode_status", lambda: {"1600": "", "700D": "", "700E": "", "RADE": "RADE is not installed"})
    monkeypatch.setattr(service.freedv, "RxChain", FakeRx)
    monkeypatch.setattr(service.freedv, "TxChain", FakeTx)


@pytest.fixture
async def svc(fake_modem):
    cfg = copy.deepcopy(config.DEFAULTS)["audio"]
    gate, sink = {"open": False}, RecordingSink()
    s = AudioService(cfg, is_mock=True, tx_gate=lambda cid: gate["open"] and cid == "c1", on_levels=lambda d: None,
                     source_factory=ToneSource, sink_factory=lambda: sink)
    s.gate, s.sink, s.freedv_ok = gate, sink, True
    await s.start()
    assert s.available, s.reason
    yield s
    await s.stop()


def test_config_and_codec_mode_lists_agree():
    assert set(config.FREEDV_MODES) == set(freedv.ALL_MODES) == set(freedv.MODES) | {"RADE"}


def test_config_defaults_and_validation():
    cfg = config.load(None)
    assert cfg["freedv"]["mode"] == "700D" and "20m|14236000|700D" in cfg["freedv"]["channels"]
    for bad in (["no pipes"], ["x|abc|700D"], ["x|14236000|RADE2"], ["a|b|c|700D"], [f"n{i}|7177000|700D" for i in range(41)]):
        c = copy.deepcopy(cfg)
        c["freedv"]["channels"] = bad
        with pytest.raises(config.ConfigError):
            config.validate(c)
    c = copy.deepcopy(cfg)
    c["freedv"]["mode"] = "RADE2"
    with pytest.raises(config.ConfigError):
        config.validate(c)
    c["freedv"]["mode"], c["freedv"]["tx_level_db"] = "700E", 3
    with pytest.raises(config.ConfigError):
        config.validate(c)


async def test_receive_audio_is_replaced_by_the_decoded_speech_and_stats_are_published(svc):
    await svc._ensure_capture()
    await until(lambda: svc._rx_q)
    assert svc._rx_q[-1] != SPEECH                                      # off: the radio's own audio (the test tone)
    svc.set_freedv(True, "700E")
    assert svc.freedv_state()["on"] and svc.freedv_mode == "700E"
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)          # on: listeners get the decoded speech
    await until(lambda: svc._levels["freedv_sync"] == 1 and svc._levels["freedv_snr"] == 8.5)
    svc.set_freedv(False)
    await until(lambda: svc._rx_q and svc._rx_q[-1] != SPEECH)
    assert svc._levels["freedv_on"] is False and svc._levels["freedv_sync"] == 0


async def test_own_transmission_is_never_decoded_and_replayed(svc):
    svc.set_freedv(True, "700D")
    await svc._ensure_capture()
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)
    svc._tx_gate_open, svc._gate_seen = True, float("inf")              # this connection is transmitting (and never goes idle in this test)
    await until(lambda: svc._rx_q and svc._rx_q[-1] == bytes(FRAME_BYTES))
    svc._tx_gate_open = False
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)


async def test_the_receiver_restarts_once_after_every_transmission(svc):
    """A friend had to switch FreeDV off and on after every PTT: the old RADE modem saw a gap and never found the signal again."""
    FakeRx.restarts = 0
    svc.set_freedv(True, "700D")
    await svc._ensure_capture()
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)
    assert FakeRx.restarts == 0
    svc._tx_gate_open, svc._gate_seen = True, float("inf")
    await until(lambda: svc._rx_q and svc._rx_q[-1] == bytes(FRAME_BYTES))
    svc._tx_gate_open = False
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)           # decoding again after the hold-off
    assert FakeRx.restarts == 1                                          # one fresh modem for the whole transmission, not one per frame
    svc._tx_gate_open, svc._gate_seen = True, float("inf")
    await until(lambda: svc._rx_q and svc._rx_q[-1] == bytes(FRAME_BYTES))
    svc._tx_gate_open = False
    await until(lambda: FakeRx.restarts == 2 and svc._rx_q and svc._rx_q[-1] == SPEECH)


async def test_the_receiver_comes_back_when_the_page_stops_sending_microphone_frames_after_ptt(svc, monkeypatch):
    """Over WebSocket the page sends NO microphone frames once PTT is released, so the old code never learned that the transmission had ended:
    the receiver stayed muted and the FreeDV scope frozen until FreeDV was switched off and on (seen on a friend's PC)."""
    from radio_remote.audio import service as service_mod

    monkeypatch.setattr(service_mod, "GATE_IDLE_S", 0.15)
    FakeRx.restarts = 0
    svc.set_freedv(True, "700D")
    await svc._ensure_capture()
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)
    svc.gate["open"] = True                                              # PTT held: the page sends microphone frames
    peer = type("P", (), {"conn_id": "c1"})()
    for _ in range(10):
        svc._mic_frame(peer, bytes(FRAME_BYTES))
        await asyncio.sleep(0.02)
    assert svc._tx_gate_open
    await until(lambda: svc._rx_q and svc._rx_q[-1] == bytes(FRAME_BYTES))     # the receiver is muted while transmitting
    svc.gate["open"] = False                                             # PTT released: and no more microphone frames arrive at all
    await until(lambda: not svc._tx_gate_open, timeout=3)               # the gate closes by itself
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH, timeout=3)      # and decoding comes back
    assert FakeRx.restarts == 1


async def test_transmit_only_passes_modem_tones_while_the_ptt_owner_is_keyed(svc):
    svc.set_freedv(True, "700D")
    peer = types.SimpleNamespace(conn_id="c1")
    mic = b"\x10\x00" * (FRAME_BYTES // 2)
    svc._mic_frame(peer, mic)
    assert not svc._tx_q and svc._fd_tx.fed == []                        # not keyed: nothing is modulated, nothing is queued
    svc.gate["open"] = True
    svc._mic_frame(peer, mic)
    assert list(svc._tx_q) == [TONES]                                    # keyed: the microphone became modem tones
    assert len(FakeTx.made) == 2                                         # a fresh modem for this transmission
    assert svc._tx_q.maxlen == service.TX_QUEUE_FREEDV
    svc._mic_frame(types.SimpleNamespace(conn_id="other"), mic)           # a different connection is never passed
    assert list(svc._tx_q) == [TONES]
    svc.gate["open"] = False
    svc._mic_frame(peer, mic)
    assert list(svc._tx_q) == [TONES] and not svc._tx_gate_open


async def test_speech_passes_through_the_normal_path_when_freedv_is_off(svc):
    svc.gate["open"] = True
    svc._mic_frame(types.SimpleNamespace(conn_id="c1"), b"\x10\x00" * (FRAME_BYTES // 2))
    assert len(svc._tx_q) == 1 and svc._tx_q[0] != TONES


async def test_unavailable_library_refuses_to_switch_on(svc):
    svc.freedv_ok, svc.freedv_reason = False, "libcodec2 is not installed"
    with pytest.raises(AudioUnavailable, match="libcodec2"):
        svc.set_freedv(True)
    assert svc.freedv_state()["on"] is False


async def test_command_api_and_settings(make_app, fake_modem):
    from test_phase8 import admin_user
    client = await make_app()
    root = await admin_user(client)
    audio = client.server.app[K_AUDIO]
    audio.freedv_ok = True
    ws, hello = await root.ws()
    assert hello["audio"]["freedv"]["available"] is True and hello["audio"]["freedv"]["on"] is False
    h = root.h
    info = await (await client.get("/api/freedv")).json()
    assert {"name": "20m", "hz": 14236000, "mode": "700D"} in info["channels"] and info["modes"] == ["1600", "700D", "700E"] and info["unavailable"] == {"RADE": "RADE is not installed"} and "RADE" in info["all_modes"]

    await ws.send_json({"id": 1, "type": "freedv", "on": True, "mode": "RADE2"})
    assert (await recv_until(ws, lambda m: m["t"] == "ack"))["ok"] is False                            # not a mode at all
    await ws.send_json({"id": 11, "type": "freedv", "on": True, "mode": "RADE"})
    nak = await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 11)
    assert nak["ok"] is False and "not installed" in nak["error"]                                      # a real mode whose library is missing
    await ws.send_json({"id": 2, "type": "freedv", "on": True, "mode": "700E"})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 2))["ok"] is True
    assert audio.freedv_state()["on"] and audio.freedv_mode == "700E"
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and m["d"].get("freedv_on") is True)
    assert patch["d"]["freedv_mode"] == "700E"

    r = await client.put("/api/config", json={"freedv": {"tx_level_db": -12, "channels": ["test|7100000|700E"]}}, headers=h)
    assert r.status == 200
    assert abs(audio.freedv_tx_gain - 0.2512) < 0.001 and audio._fd_tx.level == audio.freedv_tx_gain       # applies at once
    assert (await (await client.get("/api/freedv")).json())["channels"] == [{"name": "test", "hz": 7100000, "mode": "700E"}]
    r = await client.put("/api/config", json={"freedv": {"channels": ["broken"]}}, headers=h)
    assert r.status == 400

    await ws.send_json({"id": 3, "type": "freedv", "on": False})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 3))["ok"] is True
    assert audio.freedv_state()["on"] is False
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "freedv_on" in events and "freedv_off" in events
    await ws.close()


async def test_a_viewer_or_a_connection_without_control_cannot_switch_it(make_app, fake_modem):
    client = await make_app()
    from test_phase8 import add_user, admin_user, close_all, login_as  # noqa: F401
    root = await admin_user(client)
    client.server.app[K_AUDIO].freedv_ok = True
    await add_user(root, "watcher", "viewer")
    v = await login_as(root, "watcher")
    ws, _ = await v.ws()
    ack = await v.cmd(ws, 1, type="freedv", on=True)
    assert ack["ok"] is False
    assert client.server.app[K_AUDIO].freedv_state()["on"] is False
    await close_all(root, v)

