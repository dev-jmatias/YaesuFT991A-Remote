import asyncio
import types
import wave

import numpy as np
import pytest
from test_phase8 import make_app  # noqa: F401

from radio_remote.audio import recorder as rec
from radio_remote.audio.levels import FRAME_BYTES

N = FRAME_BYTES // 2


def tone(hz, seconds, amp=8000.0, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * hz * t)).astype("<i2")


def feed(r, x):
    for i in range(0, len(x) - N + 1, N):
        r.frame(x[i:i + N].tobytes())


def test_a_recording_is_saved_as_a_16k_wav_with_the_tone_intact(tmp_path):
    r = rec.Recorder(tmp_path)
    r.start({"frequency": 14236000, "mode": "USB", "freedv": "RADE"})
    feed(r, tone(1000, 2.0))
    info = r.stop()
    assert info["seconds"] == pytest.approx(2.0, abs=0.1) and info["frequency"] == 14236000 and info["freedv"] == "RADE"
    with wave.open(str(tmp_path / f"{info['id']}.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
    spec = np.abs(np.fft.rfft(x[4000:28000]))
    assert abs(np.argmax(spec) * 16000 / 24000 - 1000) < 10                    # still 1000 Hz
    assert 7000 < np.sqrt((x[4000:] ** 2).mean()) * 1.414 < 9000                # and about the same level


def test_only_the_last_five_clips_are_kept_and_the_oldest_goes(tmp_path, monkeypatch):
    r = rec.Recorder(tmp_path)
    ids = []
    for k in range(7):
        monkeypatch.setattr("radio_remote.audio.recorder.time.time", lambda k=k: 1_700_000_000 + k * 100)
        r.start({"frequency": 7000000 + k})
        feed(r, tone(500, 1.0))
        ids.append(r.stop()["id"])
    items = r.list()
    assert [i["frequency"] for i in items] == [7000002, 7000003, 7000004, 7000005, 7000006]
    assert not (tmp_path / f"{ids[0]}.wav").exists() and (tmp_path / f"{ids[6]}.wav").exists()
    assert len(list(tmp_path.glob("*.wav"))) == 5


def test_the_length_limit_saves_what_there_is(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "MAX_SECONDS", 1.0)
    r = rec.Recorder(tmp_path)
    r.start({})
    feed(r, tone(800, 3.0))
    assert not r.on and len(r.list()) == 1 and r.list()[0]["seconds"] <= 1.1


def test_too_short_is_not_kept_and_delete_and_bad_ids_are_safe(tmp_path):
    r = rec.Recorder(tmp_path)
    r.start({})
    feed(r, tone(800, 0.2))
    assert r.stop() is None and r.list() == []
    r.start({}); feed(r, tone(800, 1.0)); cid = r.stop()["id"]
    assert r.path("../x") is None and r.path("a/b") is None
    assert r.read(cid).dtype == np.dtype("<i2") and r.delete(cid) and r.list() == [] and not r.delete(cid)


def test_resampling_round_trip_keeps_the_tone():
    dec, up = rec.Decimator(), rec.Interpolator()
    x = tone(1500, 1.0).astype(float)
    y = np.concatenate([dec.process(x[i:i + N]) for i in range(0, len(x) - N + 1, N)])
    z = np.concatenate([up.process(y[i:i + 320]) for i in range(0, len(y) - 319, 320)])
    spec = np.abs(np.fft.rfft(z[3000:33000]))
    assert abs(np.argmax(spec) * 48000 / 30000 - 1500) < 10


async def test_a_recording_is_played_in_place_of_the_microphone_and_ends_by_itself(tmp_path):
    from radio_remote.audio import service
    from radio_remote.audio.service import AudioService
    from radio_remote import config
    import copy
    gate = {"open": False}
    svc = AudioService(copy.deepcopy(config.DEFAULTS)["audio"], is_mock=True, tx_gate=lambda cid: gate["open"] and cid == "c1", on_levels=lambda d: None)
    done = []
    svc.on_playback_done = lambda: done.append(1)
    clip = tone(1000, 1.0, rate=16000)                                          # 1 s at 16 kHz
    svc.arm_playback(clip)
    peer = types.SimpleNamespace(conn_id="c1")
    gate["open"] = True
    mic = b"\x00\x00" * N                                                       # the microphone is silent: what goes out must be the clip
    peaks = []
    for _ in range(60):
        svc._mic_frame(peer, mic)
        peaks.append(int(np.abs(np.frombuffer(svc._tx_q[-1], "<i2")).max()))
    assert max(peaks[5:45]) > 5000 and peaks[-1] == 0                           # the clip's tone goes out, then silence
    assert done == [1]                                                          # told the hub once, at the end of the clip
    assert svc._play_active
    gate["open"] = False
    svc._mic_frame(peer, mic)
    svc.arm_playback(None)
    gate["open"] = True
    svc._mic_frame(peer, mic)
    assert not svc._play_active                                                 # the next over is the microphone again


async def test_commands_and_files_over_the_web_api(make_app, tmp_path):
    from test_phase8 import admin_user
    from test_web import recv_until
    from radio_remote.common import K_AUDIO, K_HUB
    client = await make_app()
    root = await admin_user(client)
    audio, hub = client.server.app[K_AUDIO], client.server.app[K_HUB]
    audio.recorder.folder = tmp_path                                           # not the shared data folder
    ws, hello = await root.ws()
    assert hello["state"]["rec"]["on"] is False and hello["state"]["recordings"] == []
    await ws.send_json({"id": 1, "type": "rec", "on": True})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 1))["ok"] is True
    assert audio.recorder.on
    feed(audio.recorder, tone(900, 1.5))
    await ws.send_json({"id": 2, "type": "rec", "on": False})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 2))["ok"] is True
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and m["d"].get("recordings"))
    cid = patch["d"]["recordings"][0]["id"]
    lst = await (await client.get("/api/recordings")).json()
    assert [c["id"] for c in lst["recordings"]] == [cid]
    r = await client.get(f"/api/recordings/{cid}.wav")
    assert r.status == 200 and r.headers["Content-Type"] == "audio/wav" and (await r.read())[:4] == b"RIFF"
    assert (await client.get("/api/recordings/nope.wav")).status == 404
    await ws.send_json({"id": 3, "type": "ptt", "on": True, "play": "nope"})
    assert (await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == 3))["ok"] is False    # no such recording
    r = await client.post(f"/api/recordings/{cid}/delete", headers=root.h)
    assert r.status == 200 and (await (await client.get("/api/recordings")).json())["recordings"] == []
    await ws.close()


async def test_a_clip_is_fed_on_the_servers_own_clock_like_a_voice_keyer():
    """arm_playback with an owner needs no microphone frames: once that connection holds PTT the clip goes out at 20 ms a frame, the microphone is ignored, and the hub is told at the end."""
    import copy
    from radio_remote import config
    from radio_remote.audio.service import AudioService
    gate = {"open": False}
    svc = AudioService(copy.deepcopy(config.DEFAULTS)["audio"], is_mock=True, tx_gate=lambda cid: gate["open"] and cid == "c1", on_levels=lambda d: None)
    done = []
    svc.on_playback_done = lambda: done.append(1)
    svc.arm_playback(tone(1000, 0.4, rate=16000), "c1")                       # 0.4 s = 20 frames
    assert svc._player_running
    peers = []
    real = types.SimpleNamespace(conn_id="c1")
    svc._mic_frame(real, b"\x01\x00" * N)                                      # a microphone frame during playback is dropped
    assert svc.frames_in == 0
    await asyncio.sleep(0.15)
    assert not done and not svc._tx_gate_open                                  # PTT is not keyed yet: it waits
    gate["open"] = True
    peaks = []
    for _ in range(60):
        await asyncio.sleep(0.02)
        if svc._tx_q:
            peaks.append(int(np.abs(np.frombuffer(svc._tx_q[-1], "<i2")).max()))
        if done:
            break
    assert done == [1] and max(peaks) > 5000 and 15 <= svc.frames_in <= 30     # about 20 frames, on its own clock
    await asyncio.sleep(0.1)
    assert not svc._player_running
    svc.arm_playback(None)
    svc.arm_playback(tone(1000, 0.4, rate=16000), "c1")
    svc.arm_playback(None)                                                     # cancelled: the microphone is back
    assert not svc._player_running and peers == []


def test_the_radios_raw_audio_is_kept_beside_the_speech_only_when_it_is_given(tmp_path, monkeypatch):
    r = rec.Recorder(tmp_path)
    r.start({"freedv": "RADE"})
    sp, raw = tone(300, 1.0), tone(1500, 1.0)
    for i in range(0, len(sp) - N + 1, N):
        r.frame(sp[i:i + N].tobytes(), raw[i:i + N].tobytes())
    info = r.stop()
    assert info["raw"] is True and (tmp_path / f"{info['id']}.raw.wav").exists()
    with wave.open(str(tmp_path / f"{info['id']}.raw.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
    assert abs(np.argmax(np.abs(np.fft.rfft(x[2000:14000]))) * 16000 / 12000 - 1500) < 10          # the raw file holds the radio's tone, not the speech
    assert r.path(info["id"], raw=True) is not None
    monkeypatch.setattr("radio_remote.audio.recorder.time.time", lambda: 1_700_000_500)
    r.start({})
    feed(r, sp)                                                                  # no raw frames (FreeDV off)
    info2 = r.stop()
    assert "raw" not in info2 and not (tmp_path / f"{info2['id']}.raw.wav").exists()
    assert r.delete(info["id"]) and not (tmp_path / f"{info['id']}.raw.wav").exists()


def test_the_live_decoders_view_is_summarised_per_second_and_saved_with_the_clip(tmp_path):
    r = rec.Recorder(tmp_path)
    r.start({"freedv": "RADE"})
    sp = tone(300, 3.0)
    for k, i in enumerate(range(0, len(sp) - N + 1, N)):
        sec = k // 50
        r.frame(sp[i:i + N].tobytes(), sp[i:i + N].tobytes(), (1 if sec != 1 else 0, 10.0 + sec, 5.0 * sec, 12 + (40 if k == 60 else 0)))
    info = r.stop()
    d = info["decoder"]
    assert d["sync"] == [1.0, 0.0, 1.0] and d["snr"] == [10.0, 11.0, 12.0] and d["offset"] == [0, 5, 10] and d["ms"] == [12, 52, 12]
    assert r.list()[0]["decoder"] == d                                            # kept in the index (and served as {id}.json)
    r.start({})
    feed(r, tone(300, 1.0))                                                        # no decoder information (FreeDV off): nothing extra
    assert "decoder" not in r.stop()
