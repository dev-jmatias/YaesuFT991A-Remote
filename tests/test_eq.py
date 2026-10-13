import json

import numpy as np
import pytest
from test_phase8 import make_app  # noqa: F401

from radio_remote.audio import eq

N = 960


def run(e, x):
    out = []
    for i in range(0, len(x) - N + 1, N):
        out.append(np.frombuffer(e.process(x[i:i + N].astype("<i2").tobytes()), "<i2"))
    return np.concatenate(out).astype(float)


def tone_gain_db(e, hz):
    t = np.arange(48000 * 2) / 48000
    x = 8000 * np.sin(2 * np.pi * hz * t)
    y = run(e, x)
    a, b = x[20000:90000], y[20000:90000]
    return 20 * np.log10(np.sqrt((b ** 2).mean()) / np.sqrt((a ** 2).mean()))


def one_band(f, g, q, n=10):
    """A flat equalizer of n bands with the first band moved to (f, g, q)."""
    b = eq.default_bands(n)
    b[0] = {"f": f, "g": g, "q": q}
    return b


def test_off_or_flat_passes_the_audio_untouched():
    e = eq.Equalizer()
    pcm = (np.arange(N) * 7 % 2000 - 1000).astype("<i2").tobytes()
    assert e.process(pcm) == pcm                                     # off
    e.set(on=True)
    assert e.process(pcm) == pcm                                     # on but flat


@pytest.mark.parametrize("hz,q", [(250, 1.4), (1000, 1.4), (3000, 2.0), (4000, 4.0)])
def test_one_band_boosted_by_12_db_at_its_own_frequency(hz, q):
    e = eq.Equalizer()
    e.set(on=True, bands=one_band(hz, 12, q))
    assert 10.5 <= tone_gain_db(e, hz) <= 12.5                         # at the band centre
    assert abs(tone_gain_db(e, hz / 5)) < 2.0                          # far below: untouched
    assert abs(tone_gain_db(e, hz * 4)) < 2.5                          # and far above


def test_a_cut_and_the_q_sets_the_width():
    e = eq.Equalizer()
    e.set(on=True, bands=one_band(2000, -9, 1.4))
    assert -10.5 <= tone_gain_db(e, 2000) <= -7.5
    wide, narrow = eq.Equalizer(), eq.Equalizer()
    wide.set(on=True, bands=one_band(1000, 12, 0.5))
    narrow.set(on=True, bands=one_band(1000, 12, 8.0))
    assert tone_gain_db(wide, 2000) > 6                                # a low Q reaches an octave away
    assert tone_gain_db(narrow, 2000) < 1.5                            # a high Q does not
    assert tone_gain_db(narrow, 1000) > 10


def test_the_lowest_band_still_bites_and_the_response_is_the_sum_of_the_bands():
    e = eq.Equalizer()
    e.set(on=True, bands=one_band(60, 12, 1.4))
    assert tone_gain_db(e, 60) > 6                                     # the FIR is long, but still bites at 60 Hz
    f = np.array([100.0, 1000.0, 3000.0])
    b = eq.default_bands(10)
    b[3]["g"], b[5]["g"] = 6, -4
    both = eq.response_db(b, f)
    only3 = eq.response_db([dict(x, g=6 if i == 3 else 0) for i, x in enumerate(b)], f)
    only5 = eq.response_db([dict(x, g=-4 if i == 5 else 0) for i, x in enumerate(b)], f)
    assert np.allclose(both, only3 + only5)


def test_the_delay_is_fixed_and_there_are_no_frame_seams():
    e = eq.Equalizer()
    b = eq.default_bands(5)
    for x in b:
        x["g"] = 3
    e.set(on=True, bands=b)
    x = np.zeros(N * 6)
    x[100] = 10000.0
    y = run(e, x)
    assert abs(int(np.argmax(np.abs(y))) - (100 + (eq.TAPS - 1) // 2)) <= 1      # one impulse comes out (TAPS-1)/2 samples later
    t = np.arange(N * 40) / 48000
    s = 6000 * np.sin(2 * np.pi * 700 * t)
    y = run(e, s)[3000:]
    assert np.abs(np.diff(y)).max() < 6000 * 2 * np.pi * 700 / 48000 * 1.5 * 1.8    # a smooth sine, no clicks where frames join


def test_bands_are_checked_and_rounded():
    ok = eq.clean_bands([{"f": 100.4, "g": 1.26, "q": 1.43}] * 5)
    assert ok[0] == {"f": 100.0, "g": 1.3, "q": 1.45}
    for n in (5, 10, 18):
        assert eq.clean_bands(eq.default_bands(n)) == eq.default_bands(n)
    good = {"f": 1000, "g": 0, "q": 1}
    for bad in ([good] * 7, [good] * 6, "x", None, [good] * 4 + [{"f": 1000}], [good] * 4 + [{"f": 10, "g": 0, "q": 1}],
                [good] * 4 + [{"f": 1000, "g": 13, "q": 1}], [good] * 4 + [{"f": 1000, "g": 0, "q": 11}], [good] * 4 + [{"f": 1000, "g": 0, "q": 0.1}],
                [good] * 4 + [{"f": True, "g": 0, "q": 1}], [good] * 4 + [{"f": float("nan"), "g": 0, "q": 1}], [good] * 4 + ["a"]):
        with pytest.raises(ValueError):
            eq.clean_bands(bad)


def test_settings_saved_by_the_first_version_are_converted():
    bands = eq.from_old_gains([-12, -8, -3, 0, 3, 5, 3, -3])         # its "voice" preset on 63 Hz .. 8 kHz
    assert len(bands) == 10 and [b["f"] for b in bands] == [b["f"] for b in eq.default_bands(10)]
    by_f = {b["f"]: b["g"] for b in bands}
    assert by_f[60] < -10 and by_f[1000] == 3 and by_f[2000] == 5 and by_f[8000] == -3     # the same curve, read at the new centres


async def test_the_equalizer_command_applies_saves_and_reaches_every_page(make_app, tmp_path):
    from conftest import setup_admin  # noqa: F401
    from test_phase8 import admin_user
    from test_web import recv_until
    from radio_remote.common import K_AUDIO, K_HUB
    client = await make_app()
    root = await admin_user(client)
    audio, hub = client.server.app[K_AUDIO], client.server.app[K_HUB]
    hub.eq_path = tmp_path / "eq.json"                                    # not the shared data folder
    audio.set_eq(rx_on=False, tx_on=False, rx=eq.default_bands(10), tx=eq.default_bands(10))
    hub.extra["eq"] = audio.eq_state()
    ws, hello = await root.ws()
    st = hello["state"]["eq"]
    assert st["rx"]["on"] is False and len(st["rx"]["bands"]) == 10 and set(st["layouts"]) == {"5", "10", "18"} and st["limits"]["g"] == 12
    bands = eq.default_bands(5)
    bands[2]["g"] = 4
    await ws.send_json({"id": 1, "type": "set_eq", "rx_on": True, "rx": bands})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 1))["ok"] is True
    assert audio.eq_rx.on and len(audio.eq_rx.bands) == 5 and audio.eq_rx.bands[2]["g"] == 4 and not audio.eq_tx.on
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and "eq" in m["d"])
    assert patch["d"]["eq"]["rx"]["on"] is True and len(patch["d"]["eq"]["rx"]["bands"]) == 5
    saved = json.loads(hub.eq_path.read_text())
    assert saved["rx"]["bands"][2]["g"] == 4 and saved["rx"]["on"] is True
    for i, bad in enumerate([{"rx": bands[:3]}, {"tx": [{"f": 1, "g": 0, "q": 1}] * 5}, {"tx_on": "yes"}, {"rx": "x"}], start=2):
        await ws.send_json({"id": i, "type": "set_eq", **bad})
        assert (await recv_until(ws, lambda m, i=i: m["t"] == "ack" and m["id"] == i))["ok"] is False
    audio.set_eq(rx_on=False, rx=eq.default_bands(10))                      # a restart: what was saved comes back
    hub.load_eq()
    assert audio.eq_rx.on and len(audio.eq_rx.bands) == 5 and audio.eq_rx.bands[2]["g"] == 4
    hub.eq_path.write_text(json.dumps({"rx": {"on": True, "gains": [0, 0, 0, 0, 6, 0, 0, 0]}, "tx": {"on": False, "gains": [0] * 8}}))    # the first version's file
    hub.load_eq()
    assert audio.eq_rx.on and len(audio.eq_rx.bands) == 10 and max(b["g"] for b in audio.eq_rx.bands) == pytest.approx(6, abs=0.6)
    await ws.close()


def test_the_low_cut_and_high_cut_shape_the_audio():
    e = eq.Equalizer()
    e.set(on=True, cut={"low": 300, "high": 2800})
    assert tone_gain_db(e, 100) < -15                                  # well below the low-cut
    assert -6 < tone_gain_db(e, 300) < -0.5                            # about -3 dB at the corner (a long filter smooths the corner)
    assert abs(tone_gain_db(e, 1000)) < 1.0                            # the passband is untouched
    assert tone_gain_db(e, 6000) < -15                                 # well above the high-cut
    assert -6 < tone_gain_db(e, 2800) < -0.5
    e.set(cut={"low": 0, "high": 0})                                   # both off: flat again, and free
    assert e.flat and abs(tone_gain_db(e, 100)) < 0.01


def test_the_cuts_are_checked():
    assert eq.clean_cut({"low": 150, "high": 2800.4}) == {"low": 150.0, "high": 2800.0}
    assert eq.clean_cut({}) == {"low": 0.0, "high": 0.0}
    for bad in ({"low": 10}, {"low": 2000}, {"high": 500}, {"high": 20000}, {"low": 1000, "high": 1000}, {"low": True}, {"low": "x"}, {"low": float("nan")}, [], None):
        with pytest.raises(ValueError):
            eq.clean_cut(bad)


async def test_saved_equalizer_sets_and_the_gates_over_the_websocket(make_app, tmp_path):
    from test_phase8 import admin_user
    from test_web import recv_until
    from radio_remote.common import K_AUDIO, K_HUB
    client = await make_app()
    root = await admin_user(client)
    audio, hub = client.server.app[K_AUDIO], client.server.app[K_HUB]
    hub.eq_path = tmp_path / "eq.json"
    hub.eq_presets.clear()
    ws, hello = await root.ws()
    assert hello["state"]["eq_presets"] == {} and "rx_gate" in hello["state"]["dyn"] and "gate" in hello["state"]["dyn"]
    bands = eq.default_bands(5)
    bands[1]["g"] = 5

    async def cmd(i, **kw):
        await ws.send_json({"id": i, "type": kw.pop("type"), **kw})
        return (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == i))["ok"]

    assert await cmd(1, type="eq_preset_save", name="  My voice ", bands=bands, cut={"low": 150, "high": 0}) is True
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and "eq_presets" in m["d"])
    assert list(patch["d"]["eq_presets"]) == ["My voice"] and patch["d"]["eq_presets"]["My voice"]["cut"]["low"] == 150.0
    saved = json.loads(hub.eq_path.read_text())
    assert saved["presets"]["My voice"]["bands"][1]["g"] == 5
    assert await cmd(2, type="eq_preset_save", name="My voice", bands=eq.default_bands(10)) is True            # the same name: replaced
    assert len(hub.eq_presets["My voice"]["bands"]) == 10
    for i, bad in enumerate([dict(name=""), dict(name="x" * 25), dict(name="a\nb"), dict(name=5), dict(name="ok", bands=bands[:3]), dict(name="ok", bands=bands, cut={"low": 5})], start=3):
        kw = {"bands": bands, **bad}
        assert await cmd(i, type="eq_preset_save", **kw) is False
    for k in range(25):
        ok = await cmd(100 + k, type="eq_preset_save", name=f"set {k}", bands=bands)
        assert ok is (k < 19)                                                                                 # "My voice" plus 19 more makes twenty; then it refuses
    assert len(hub.eq_presets) == 20
    hub.eq_presets.clear(); hub.eq_presets["a"] = {"bands": bands, "cut": {"low": 0, "high": 0}}
    hub._save_eq()
    hub.eq_presets.clear()
    hub.load_eq()
    assert list(hub.eq_presets) == ["a"]                                                                      # restored after a restart
    assert await cmd(200, type="eq_preset_delete", name="a") is True and hub.eq_presets == {}
    assert await cmd(201, type="eq_preset_delete", name="a") is False

    assert await cmd(300, type="set_dyn", rx_gate={"on": True, "threshold": -45}, gate={"on": True, "range": 40}) is True
    assert audio.rx_gate.cfg["on"] and audio.rx_gate.cfg["threshold"] == -45 and audio.tx_dyn.cfg["gate"]["range"] == 40 and audio.tx_dyn.gate.cfg["on"]
    assert await cmd(301, type="set_dyn", rx_gate={"range": 99}) is False and audio.rx_gate.cfg["threshold"] == -45
    assert await cmd(302, type="set_dyn", rx_gate={"threshold": -60}, limiter={"ceiling": 5}) is False
    assert audio.rx_gate.cfg["threshold"] == -45                                                              # a bad part changes nothing at all
    audio.set_dyn({"rx_gate": {"on": False}, "gate": {"on": False}})
    await ws.close()
