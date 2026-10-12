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


def test_off_or_flat_passes_the_audio_untouched():
    e = eq.Equalizer()
    pcm = (np.arange(N) * 7 % 2000 - 1000).astype("<i2").tobytes()
    assert e.process(pcm) == pcm                                     # off
    e.set(on=True)
    assert e.process(pcm) == pcm                                     # on but flat


@pytest.mark.parametrize("hz,band", [(250, 2), (1000, 4), (4000, 6)])
def test_one_band_boosted_by_12_db(hz, band):
    e = eq.Equalizer()
    g = [0] * 8
    g[band] = 12
    e.set(on=True, gains=g)
    assert 9.5 <= tone_gain_db(e, hz) <= 12.5                          # at the band centre (a short filter smears the lowest narrow bands by about 1.5 dB)
    assert abs(tone_gain_db(e, hz / 4)) < 1.5                          # two octaves away: untouched


def test_all_bands_cut_and_the_lowest_and_highest_bands():
    e = eq.Equalizer()
    e.set(on=True, gains=[-6] * 8)
    for hz in (125, 500, 2000, 6000):
        assert -7 <= tone_gain_db(e, hz) <= -5, hz
    e.set(gains=[12, 0, 0, 0, 0, 0, 0, 12])
    assert tone_gain_db(e, 63) > 7                                    # the FIR is long, but still bites at the lowest band
    assert tone_gain_db(e, 8000) > 9
    assert abs(tone_gain_db(e, 1000)) < 1


def test_the_delay_is_fixed_and_there_are_no_frame_seams():
    e = eq.Equalizer()
    e.set(on=True, gains=[3] * 8)
    x = np.zeros(N * 6)
    x[100] = 10000.0
    y = run(e, x)
    assert abs(int(np.argmax(np.abs(y))) - (100 + (eq.TAPS - 1) // 2)) <= 1      # one impulse comes out (TAPS-1)/2 samples later
    t = np.arange(N * 40) / 48000
    s = 6000 * np.sin(2 * np.pi * 700 * t)
    y = run(e, s)[3000:]
    assert np.abs(np.diff(y)).max() < 6000 * 2 * np.pi * 700 / 48000 * 1.5 * 1.6    # a smooth sine, no clicks where frames join


def test_gains_are_checked_and_rounded():
    assert eq.clean_gains([0, 1.26, -2, 3, 4, 5, 6, 12]) == [0, 1.5, -2, 3, 4, 5, 6, 12]
    for bad in ([0] * 7, [0] * 9, [0, 0, 0, 0, 0, 0, 0, 13], [0, 0, 0, 0, 0, 0, 0, "a"], [True] + [0] * 7, "x", None):
        with pytest.raises(ValueError):
            eq.clean_gains(bad)
    assert all(eq.clean_gains(v) for v in eq.PRESETS.values())


async def test_the_equalizer_command_applies_saves_and_reaches_every_page(make_app, tmp_path):
    import json
    from conftest import setup_admin  # noqa: F401
    from test_phase8 import admin_user
    from test_web import recv_until
    from radio_remote.common import K_AUDIO, K_HUB
    client = await make_app()
    root = await admin_user(client)
    audio, hub = client.server.app[K_AUDIO], client.server.app[K_HUB]
    hub.eq_path = tmp_path / "eq.json"                                    # not the shared data folder
    audio.set_eq(rx_on=False, tx_on=False, rx=[0] * 8, tx=[0] * 8)
    hub.extra["eq"] = audio.eq_state()
    ws, hello = await root.ws()
    assert hello["state"]["eq"]["rx"]["on"] is False and hello["state"]["eq"]["bands"] == list(eq.BANDS_HZ)
    await ws.send_json({"id": 1, "type": "set_eq", "rx_on": True, "rx": [0, 0, 0, 0, 3, 3, 0, 0]})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 1))["ok"] is True
    assert audio.eq_rx.on and audio.eq_rx.gains[4] == 3 and not audio.eq_tx.on
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and "eq" in m["d"])
    assert patch["d"]["eq"]["rx"]["on"] is True
    saved = json.loads(hub.eq_path.read_text())
    assert saved["rx"]["gains"][4] == 3 and saved["rx"]["on"] is True
    for i, bad in enumerate([{"rx": [1] * 7}, {"tx": [99] * 8}, {"tx_on": "yes"}], start=2):
        await ws.send_json({"id": i, "type": "set_eq", **bad})
        assert (await recv_until(ws, lambda m, i=i: m["t"] == "ack" and m["id"] == i))["ok"] is False
    hub.audio.set_eq(rx_on=False, rx=[0] * 8)                              # a restart: what was saved comes back
    hub.load_eq()
    assert audio.eq_rx.on and audio.eq_rx.gains[4] == 3
    await ws.close()
