from pathlib import Path

from test_phase8 import admin_user, close_all, make_app  # noqa: F401

from radio_remote.app import K_AUDIO

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


async def test_audio_gains_apply_live_without_restart(make_app):
    client = await make_app()
    root = await admin_user(client)
    audio = client.server.app[K_AUDIO]
    base_tx = audio.tx_gain
    r = await client.put("/api/config", json={"audio": {"tx_gain_db": 12, "rx_gain_db": -6}}, headers=root.h)
    body = await r.json()
    assert r.status == 200 and body["restart_required"] is False
    assert abs(audio.tx_gain - 3.98) < 0.02 and abs(audio.rx_gain - 0.501) < 0.01 and audio.tx_gain > base_tx
    r = await client.put("/api/config", json={"audio": {"opus_bitrate": 24000}}, headers=root.h)
    assert (await r.json())["restart_required"] is True          # other audio settings still need a restart
    await close_all(root)


def test_af_gain_slider_is_not_offered():
    text = (SRC / "components" / "controls.js").read_text(encoding="utf-8")
    assert 'k !== "af_gain"' in text and "dual_receiver" in text        # hidden, except on dual-receiver radios
