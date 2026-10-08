"""FreeDV (RADE V1 / RADE V2): config, the on/off command, and how the audio service routes receive and transmit audio through the modem.
The real library round trip is in test_freedv_codec.py (skipped where the library is missing); here the modem chains are stand-ins."""
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

    def flush(self):
        return [TONES]                                    # the last modem frame, completed with silence

    def close(self):
        pass


@pytest.fixture
def fake_modem(monkeypatch):
    FakeTx.made = []
    monkeypatch.setattr(freedv, "available", lambda: (True, ""))
    monkeypatch.setattr(freedv, "mode_status", lambda: {"RADE": "", "RADE2": ""})
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
    assert set(config.FREEDV_MODES) == set(freedv.ALL_MODES) == {"RADE", "RADE2"}


def test_config_defaults_and_validation():
    cfg = config.load(None)
    assert cfg["freedv"]["mode"] == "RADE" and "20m|14236000|RADE" in cfg["freedv"]["channels"]
    for bad in (["no pipes"], ["x|abc|RADE"], ["x|14236000|FOO"], ["a|b|c|RADE"], [f"n{i}|7177000|RADE" for i in range(41)]):
        c = copy.deepcopy(cfg)
        c["freedv"]["channels"] = bad
        with pytest.raises(config.ConfigError):
            config.validate(c)
    c = copy.deepcopy(cfg)
    c["freedv"]["mode"] = "FOO"
    with pytest.raises(config.ConfigError):
        config.validate(c)
    c["freedv"]["mode"], c["freedv"]["tx_level_db"] = "RADE2", 3
    with pytest.raises(config.ConfigError):
        config.validate(c)
    c["freedv"]["mode"], c["freedv"]["tx_level_db"] = "RADE2", -6                        # RADE V2 is a mode like any other
    config.validate(c)


def test_a_config_that_still_names_a_removed_mode_is_read_as_rade():
    """1600, 700D and 700E were removed; an existing config file must still start the service."""
    c = copy.deepcopy(config.load(None))
    c["freedv"]["mode"] = "700D"
    c["freedv"]["channels"] = ["20m|14236000|700D", "40m|7177000|1600", "80m|3643000|700E", "17m|18118000|RADE2"]
    config.validate(c)
    assert c["freedv"]["mode"] == "RADE"
    assert c["freedv"]["channels"] == ["20m|14236000|RADE", "40m|7177000|RADE", "80m|3643000|RADE", "17m|18118000|RADE2"]


async def test_receive_audio_is_replaced_by_the_decoded_speech_and_stats_are_published(svc):
    await svc._ensure_capture()
    await until(lambda: svc._rx_q)
    assert svc._rx_q[-1] != SPEECH                                      # off: the radio's own audio (the test tone)
    svc.set_freedv(True, "RADE2")
    assert svc.freedv_state()["on"] and svc.freedv_mode == "RADE2"
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)          # on: listeners get the decoded speech
    await until(lambda: svc._levels["freedv_sync"] == 1 and svc._levels["freedv_snr"] == 8.5)
    svc.set_freedv(False)
    await until(lambda: svc._rx_q and svc._rx_q[-1] != SPEECH)
    assert svc._levels["freedv_on"] is False and svc._levels["freedv_sync"] == 0


async def test_own_transmission_is_never_decoded_and_replayed(svc):
    svc.set_freedv(True, "RADE")
    await svc._ensure_capture()
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)
    svc._tx_gate_open, svc._gate_seen = True, float("inf")              # this connection is transmitting (and never goes idle in this test)
    await until(lambda: svc._rx_q and svc._rx_q[-1] == bytes(FRAME_BYTES))
    svc._tx_gate_open = False
    await until(lambda: svc._rx_q and svc._rx_q[-1] == SPEECH)


async def test_the_receiver_restarts_once_after_every_transmission(svc):
    """A friend had to switch FreeDV off and on after every PTT: the old RADE modem saw a gap and never found the signal again."""
    FakeRx.restarts = 0
    svc.set_freedv(True, "RADE")
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
    svc.set_freedv(True, "RADE")
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
    svc.set_freedv(True, "RADE")
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


async def test_the_tab_is_shown_while_rade_can_still_be_installed(svc, monkeypatch):
    """Without the RADE library the tab used to vanish, and with it the Install RADE button."""
    from radio_remote.audio import rade
    svc.freedv_ok, svc.freedv_reason = False, "RADE is not installed"
    monkeypatch.setattr(rade, "installable", lambda: True)
    st = svc.freedv_state()
    assert st["available"] is False and st["show"] is True
    monkeypatch.setattr(rade, "installable", lambda: False)                      # a 32-bit system: nothing to install, nothing to show
    assert svc.freedv_state()["show"] is False


async def test_unavailable_library_refuses_to_switch_on(svc):
    svc.freedv_ok, svc.freedv_reason = False, "RADE is not installed"
    with pytest.raises(AudioUnavailable, match="not installed"):
        svc.set_freedv(True)
    assert svc.freedv_state()["on"] is False


async def test_command_api_and_settings(make_app, fake_modem, monkeypatch):
    from test_phase8 import admin_user
    monkeypatch.setattr(freedv, "mode_status", lambda: {"RADE": "", "RADE2": "the installed RADE library has no V2"})
    client = await make_app()
    root = await admin_user(client)
    audio = client.server.app[K_AUDIO]
    audio.freedv_ok = True
    ws, hello = await root.ws()
    assert hello["audio"]["freedv"]["available"] is True and hello["audio"]["freedv"]["on"] is False
    h = root.h
    info = await (await client.get("/api/freedv")).json()
    assert {"name": "20m", "hz": 14236000, "mode": "RADE"} in info["channels"] and info["modes"] == ["RADE"] and info["unavailable"] == {"RADE2": "the installed RADE library has no V2"} and info["all_modes"] == ["RADE", "RADE2"]

    await ws.send_json({"id": 1, "type": "freedv", "on": True, "mode": "700D"})
    assert (await recv_until(ws, lambda m: m["t"] == "ack"))["ok"] is False                            # not a mode at all
    await ws.send_json({"id": 11, "type": "freedv", "on": True, "mode": "RADE2"})
    nak = await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 11)
    assert nak["ok"] is False and "no V2" in nak["error"]                                              # a real mode this library cannot do
    await ws.send_json({"id": 2, "type": "freedv", "on": True, "mode": "RADE"})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 2))["ok"] is True
    assert audio.freedv_state()["on"] and audio.freedv_mode == "RADE"
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and m["d"].get("freedv_on") is True)
    assert patch["d"]["freedv_mode"] == "RADE"

    r = await client.put("/api/config", json={"freedv": {"tx_level_db": -12, "channels": ["test|7100000|RADE2"]}}, headers=h)
    assert r.status == 200
    assert abs(audio.freedv_tx_gain - 0.2512) < 0.001 and audio._fd_tx.level == audio.freedv_tx_gain       # applies at once
    assert (await (await client.get("/api/freedv")).json())["channels"] == [{"name": "test", "hz": 7100000, "mode": "RADE2"}]
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



async def test_the_tones_wait_for_a_cushion_and_a_hole_in_the_tones_is_counted(svc):
    """The modem hands its tones over in bursts. Without a cushion a burst a few ms late made the sender write 20 ms of silence into the middle of the signal,
    and RADE cannot follow a signal with holes in it (a friend's transmissions were unreadable at the other station)."""
    svc.set_freedv(True, "RADE")
    peer = types.SimpleNamespace(conn_id="c1")
    mic = b"\x10\x00" * (FRAME_BYTES // 2)
    await svc._ensure_tx_sink()
    svc.gate["open"] = True
    for _ in range(service.TX_PREFILL_FRAMES - 1):
        svc._mic_frame(peer, mic)                                        # one tone frame short of the cushion
    await asyncio.sleep(0.12)
    assert TONES not in svc.sink.frames and svc.tx_underruns == 0         # only silence goes out while the cushion fills; that is not a hole
    svc._mic_frame(peer, mic)                                            # the cushion is complete: the tones start
    await until(lambda: svc.sink.frames.count(TONES) == service.TX_PREFILL_FRAMES)
    await until(lambda: svc.tx_underruns == 1)                           # and then the queue ran dry in mid-over: one hole, counted once
    await asyncio.sleep(0.12)
    assert svc.tx_underruns == 1                                         # (it waits for a new cushion; it does not count again for every frame of silence)
    assert svc._levels["freedv_tx_underruns"] == 1 and svc.status()["freedv_timing"]["tx_underruns"] == 1
    for _ in range(3):
        svc._mic_frame(peer, mic)
    svc.gate["open"] = False
    svc._mic_frame(peer, mic)                                            # PTT released before the cushion was full
    await until(lambda: svc.sink.frames.count(TONES) == service.TX_PREFILL_FRAMES + 3)      # what was already queued still goes out
    await svc._stop_tx_sink()


async def test_an_over_with_no_holes_is_logged_as_such(svc, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="audio")
    svc.set_freedv(True, "RADE")
    peer = types.SimpleNamespace(conn_id="c1")
    mic = b"\x10\x00" * (FRAME_BYTES // 2)
    await svc._ensure_tx_sink()
    svc.gate["open"] = True
    for _ in range(2 * service.TX_PREFILL_FRAMES):
        svc._mic_frame(peer, mic)
        await asyncio.sleep(0.02)                                        # the microphone keeps up with the clock: the cushion is never used up
    svc.gate["open"] = False
    svc._mic_frame(peer, mic)
    await until(lambda: any("no holes" in r.getMessage() for r in caplog.records))
    assert svc.tx_underruns == 0
    await svc._stop_tx_sink()


def test_a_reinstalled_library_is_picked_up_without_a_restart(monkeypatch):
    """The page's Reinstall RADE put a V1+V2 library in place, but the running service kept the older V1-only one until it was restarted."""
    from pathlib import Path
    from radio_remote.audio import rade
    monkeypatch.setattr(rade, "_LIB", object())
    monkeypatch.setattr(rade, "_PATH", Path("/opt/radio-remote/lib/librade-rr.so"))
    monkeypatch.setattr(rade, "_ERR", "")
    rade.reset()
    assert rade._LIB is not None                                         # an ordinary reset only forgets a FAILED load
    rade.reset(force=True)
    assert rade._LIB is None and rade._PATH is None                      # the next look re-resolves which file wins


async def test_the_library_folder_is_known_before_the_audio_service_first_looks(make_app, monkeypatch):
    """The copy installed from the page lives in the data folder and must win over /opt: its folder has to be set BEFORE the first look for the library,
    or the older /opt copy is loaded and kept (seen on a PC where RADE V2 never appeared in the list)."""
    from radio_remote.audio import rade
    rade.set_install_dir(None)
    seen, real = [], rade._load
    monkeypatch.setattr(rade, "_load", lambda: (seen.append(rade.install_dir()), real())[1])
    await make_app()
    assert seen and all(d is not None for d in seen)


async def test_the_tones_already_queued_reach_the_radio_before_ptt_is_released(svc):
    """PiRO (another remote-radio project) holds PTT a while after its last audio because 'handing PCM to ALSA does not mean it has been played'. Here the cushion
    (and the last modem frame) were cut off by unkeying at once; the PTT-off command now waits for the queue."""
    svc.set_freedv(True, "RADE")
    peer = types.SimpleNamespace(conn_id="c1")
    mic = b"\x10\x00" * (FRAME_BYTES // 2)
    await svc._ensure_tx_sink()
    svc.gate["open"] = True
    n = service.TX_PREFILL_FRAMES + 4
    for _ in range(n):
        svc._mic_frame(peer, mic)
    await until(lambda: TONES in svc.sink.frames)                                    # the tones have started
    await svc.drain_freedv_tx()
    assert not svc._tx_q                                                             # everything queued went to the radio
    assert svc.sink.frames.count(TONES) == n + 1                                     # and the flushed last frame with it
    assert svc.tx_underruns == 0 and not svc._tx_ending                              # the queue running dry at the end of an over is not a hole
    await svc._stop_tx_sink()


async def test_drain_does_nothing_for_ordinary_voice_or_when_not_transmitting(svc):
    import time
    t0 = time.monotonic()
    await svc.drain_freedv_tx()                                                      # FreeDV off
    svc.set_freedv(True, "RADE")
    await svc.drain_freedv_tx()                                                      # FreeDV on but nothing is being transmitted
    assert time.monotonic() - t0 < 0.2
