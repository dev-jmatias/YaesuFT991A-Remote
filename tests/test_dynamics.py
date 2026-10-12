import numpy as np
import pytest
from test_phase8 import make_app  # noqa: F401

from radio_remote.audio import dynamics as dyn

N = 960


def tone(db_rms, hz=700, seconds=1.0):
    t = np.arange(int(48000 * seconds)) / 48000
    amp = 32768 * 10 ** (db_rms / 20) * np.sqrt(2)
    return (amp * np.sin(2 * np.pi * hz * t)).astype("<i2")


def run(d, x):
    out = []
    for i in range(0, len(x) - N + 1, N):
        out.append(np.frombuffer(d.process(x[i:i + N].tobytes()), "<i2"))
    return np.concatenate(out).astype(float)


def rms_db(x):
    return 20 * np.log10(np.sqrt((x ** 2).mean()) / 32768 + 1e-12)


def test_off_passes_the_audio_untouched():
    d = dyn.TxDynamics()
    pcm = tone(-20)[:N].tobytes()
    assert d.process(pcm) == pcm and not d.active


def test_the_leveler_brings_a_quiet_voice_toward_the_target_but_never_beyond_its_max_gain():
    d = dyn.TxDynamics()
    d.set({"leveler": {"on": True, "max_gain": 12, "target": -18, "decay": 300}})
    y = run(d, tone(-24, seconds=2))
    assert abs(rms_db(y[-24000:]) - (-18)) < 0.7                       # -24 dB asked for +6 dB
    d.reset()
    y = run(d, tone(-40, seconds=2))
    assert abs(rms_db(y[-24000:]) - (-28)) < 0.7                       # -40 dB would need +22 dB: the max gain, +12, is all it gets
    d.reset()
    y = run(d, tone(-10, seconds=1))
    assert abs(rms_db(y[-24000:]) - (-10)) < 0.2                       # a loud voice is left alone: it only adds gain


def test_the_leveler_follows_a_louder_voice_at_once_and_a_quieter_one_slowly_and_holds_in_pauses():
    d = dyn.TxDynamics()
    d.set({"leveler": {"on": True, "max_gain": 18, "target": -18, "decay": 500}})
    run(d, tone(-30, seconds=1))
    g_quiet = d.gain_db
    assert 11 < g_quiet < 13
    run(d, tone(-12, seconds=0.2))                                      # a loud passage: the gain goes at once
    assert d.gain_db < 0.5
    run(d, tone(-30, seconds=0.1))                                      # quiet again: only a little recovered after 100 ms ...
    assert d.gain_db < 4
    run(d, tone(-30, seconds=3))
    assert d.gain_db > 11                                               # ... and all of it after a few seconds
    held = d.gain_db
    run(d, np.zeros(48000, "<i2"))                                      # a pause (below the gate): the gain is held, not raised
    assert d.gain_db == pytest.approx(held)


def test_the_limiter_keeps_the_peaks_under_the_ceiling_and_lets_go_after_the_release():
    d = dyn.TxDynamics()
    d.set({"limiter": {"on": True, "ceiling": -6, "release": 100}})
    ceil = 32768 * 10 ** (-6 / 20)
    loud = tone(-3, seconds=1)                                          # peaks at about 0 dBFS
    y = run(d, loud)
    assert np.abs(y).max() <= ceil + 1 and d.lim_db > 3                 # never over the ceiling; it is working
    quiet = run(d, tone(-30, seconds=1))
    assert abs(rms_db(quiet[-24000:]) - (-30)) < 0.5                    # a second later it has let go: a quiet tone is not reduced
    rng = np.random.default_rng(1)
    noise = (rng.normal(0, 9000, 48000)).clip(-32768, 32767).astype("<i2")
    assert np.abs(run(d, noise)).max() <= ceil + 1                      # whatever comes in


def test_a_signal_under_the_ceiling_is_not_changed_by_the_limiter():
    d = dyn.TxDynamics()
    d.set({"limiter": {"on": True, "ceiling": -3}})
    x = tone(-20, seconds=0.5)
    assert np.array_equal(run(d, x), x[:len(x) // N * N].astype(float))


def test_both_together_raise_the_average_for_the_same_peak():
    d = dyn.TxDynamics()
    d.set({"leveler": {"on": True, "max_gain": 15, "target": -14}, "limiter": {"on": True, "ceiling": -3}})
    quiet_speechlike = np.concatenate([tone(-30, 300, 0.3), tone(-20, 500, 0.3)] * 4)
    y = run(d, quiet_speechlike)
    assert rms_db(y) > rms_db(quiet_speechlike.astype(float)) + 6 and np.abs(y).max() <= 32768 * 10 ** (-3 / 20) + 1


def test_settings_are_checked_and_merged():
    d = dyn.TxDynamics()
    d.set({"leveler": {"on": True, "max_gain": 9}})
    d.set({"limiter": {"on": True}})
    assert d.state() == {"leveler": {"on": True, "max_gain": 9.0, "target": -18.0, "decay": 300.0}, "limiter": {"on": True, "ceiling": -3.0, "release": 100.0}}
    for bad in ({"leveler": {"max_gain": 30}}, {"leveler": {"on": 1}}, {"limiter": {"ceiling": 3}}, {"limiter": {"release": 5}}, {"limiter": {"x": 1}},
                {"mixer": {}}, {"leveler": []}, [], None, {"leveler": {"decay": float("nan")}}, {"leveler": {"target": True}}):
        with pytest.raises(ValueError):
            d.set(bad)
    assert d.state()["leveler"]["max_gain"] == 9.0                      # a refused change leaves the settings as they were


async def test_the_transmit_path_applies_the_leveler_and_limiter_only_when_they_are_on(make_app, tmp_path):
    import copy
    import json
    import types
    from conftest import setup_admin  # noqa: F401
    from test_phase8 import admin_user
    from test_web import recv_until
    from radio_remote import config
    from radio_remote.audio.service import AudioService
    from radio_remote.common import K_AUDIO, K_HUB
    gate = {"open": True}
    svc = AudioService(copy.deepcopy(config.DEFAULTS)["audio"], is_mock=True, tx_gate=lambda cid: gate["open"] and cid == "c1", on_levels=lambda d: None)
    peer = types.SimpleNamespace(conn_id="c1")
    loud = tone(-3, seconds=0.2)[:N].tobytes()
    svc._mic_frame(peer, loud)
    plain = np.abs(np.frombuffer(svc._tx_q[-1], "<i2")).max()               # off: the old path (a soft limiter at the end only)
    svc.set_dyn({"limiter": {"on": True, "ceiling": -12}})
    gate["open"] = False
    svc._mic_frame(peer, loud)
    gate["open"] = True
    svc._mic_frame(peer, loud)                                              # a new over: the dynamics start fresh
    limited = np.abs(np.frombuffer(svc._tx_q[-1], "<i2")).max()
    assert limited <= 32768 * 10 ** (-12 / 20) + 2 < plain
    assert svc.dyn_state()["limits"]["ceiling"] == [-18.0, 0.0] and svc.tx_dyn.lim_db > 5

    client = await make_app()                                               # the command over the websocket: applied, shared, saved, restored
    root = await admin_user(client)
    audio, hub = client.server.app[K_AUDIO], client.server.app[K_HUB]
    hub.eq_path = tmp_path / "eq.json"
    audio.set_dyn({"leveler": {"on": False, "max_gain": 12}, "limiter": {"on": False}})
    ws, hello = await root.ws()
    assert "dyn" in hello["state"] and hello["state"]["dyn"]["leveler"]["on"] is False
    await ws.send_json({"id": 1, "type": "set_dyn", "leveler": {"on": True, "max_gain": 9}})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 1))["ok"] is True
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and "dyn" in m["d"])
    assert patch["d"]["dyn"]["leveler"]["on"] is True and patch["d"]["dyn"]["leveler"]["max_gain"] == 9.0 and patch["d"]["dyn"]["limiter"]["on"] is False
    assert json.loads(hub.eq_path.read_text())["dyn"]["leveler"]["max_gain"] == 9.0
    await ws.send_json({"id": 2, "type": "set_dyn", "limiter": {"ceiling": 5}})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 2))["ok"] is False
    audio.set_dyn({"leveler": {"on": False, "max_gain": 3}})
    hub.load_eq()
    assert audio.tx_dyn.cfg["leveler"]["on"] is True and audio.tx_dyn.cfg["leveler"]["max_gain"] == 9.0
    await ws.close()
