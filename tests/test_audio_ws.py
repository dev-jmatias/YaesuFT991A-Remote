"""Audio over a WebSocket (the fallback for networks that block WebRTC/UDP): binary Opus packets both ways, same server-side gate."""
import asyncio
import json

import numpy as np
import pytest
from conftest import setup_admin
from test_audio import until
from test_web import origin, recv_until

from radio_remote.app import K_AUDIO
from radio_remote.audio.levels import FRAME_SAMPLES, RATE
from radio_remote.audio.service import AudioUnavailable, OpusCodec


def tone_frames(n, amp=9000):
    t = np.arange(n * FRAME_SAMPLES) / RATE
    pcm = (np.sin(2 * np.pi * 700 * t) * amp).astype("<i2")
    return [pcm[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes() for i in range(n)]


def test_opus_roundtrip_keeps_the_tone():
    c = OpusCodec(32000)
    out = []
    for f in tone_frames(30):
        for pkt in c.encode(f):
            out += c.decode(pkt)
    assert len(out) >= 28
    peak = max(int(np.abs(np.frombuffer(f, dtype="<i2")).max()) for f in out[5:])
    assert 4000 < peak < 14000                                          # a 9000-peak tone survives, not silence or noise


async def open_audio_ws(client, hello):
    ws = await client.ws_connect(f"/ws/audio?conn={hello['conn']}", headers=origin(client))
    return ws, await ws.receive_json()


async def test_rx_packets_arrive_and_decode(client):
    await setup_admin(client)
    main = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await main.receive()).data)
    aws, ack = await open_audio_ws(client, hello)
    assert ack["ok"] is True and ack["mic"] is True and ack["rate"] == RATE
    codec = OpusCodec(32000)
    frames = []
    while len(frames) < 25:
        m = await asyncio.wait_for(aws.receive(), 5)
        assert m.type.name == "BINARY" and len(m.data) < 1000
        frames += codec.decode(m.data)
    assert max(int(np.abs(np.frombuffer(f, dtype="<i2")).max()) for f in frames[5:]) > 500     # the test tone, not silence
    audio = client.server.app[K_AUDIO]
    assert audio.status()["peers"] == 1 and audio.status()["rx_capture"] is True
    await aws.close()
    await until(lambda: audio.peers == {} and audio.status()["rx_capture"] is False)
    await main.close()


async def test_mic_packets_pass_the_gate_only_for_the_ptt_owner(client):
    await setup_admin(client)
    audio = client.server.app[K_AUDIO]
    main = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await main.receive()).data)
    aws, ack = await open_audio_ws(client, hello)
    enc = OpusCodec(32000)
    packets = [p for f in tone_frames(120) for p in enc.encode(f)]

    async def speak(n, start):
        for p in packets[start:start + n]:
            await aws.send_bytes(p)
            await main.send_json({"type": "ptt_hb"})
            await asyncio.sleep(0.02)

    await speak(30, 0)
    assert audio.frames_in > 10 and audio.frames_to_radio == 0               # arrives, but the gate is shut

    await main.send_json({"id": 1, "type": "ptt", "on": True})
    await recv_until(main, lambda m: m["t"] == "ack" and m["ok"])
    await speak(40, 30)
    assert audio.frames_to_radio > 10                                         # the owner's mic reaches the radio

    await main.send_json({"id": 2, "type": "ptt", "on": False})
    await recv_until(main, lambda m: m["t"] == "ack" and m["ok"])
    await asyncio.sleep(0.15)
    mark = audio.frames_to_radio
    await speak(30, 70)
    assert audio.frames_to_radio == mark                                      # released: nothing more
    await aws.close()
    await main.close()


async def test_rules_conn_must_be_yours_peer_limit_one_session_per_conn(client):
    await setup_admin(client)
    audio = client.server.app[K_AUDIO]
    main = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await main.receive()).data)
    r = await client.get("/ws/audio?conn=nope", headers=origin(client))
    assert r.status == 403
    r = await client.get(f"/ws/audio?conn={hello['conn']}")                    # no Origin header
    assert r.status == 403
    first, _ = await open_audio_ws(client, hello)
    second, _ = await open_audio_ws(client, hello)                             # replaces the first (one session per connection)
    await asyncio.wait_for(first.receive(), 3)                                 # the first one is told to close
    assert len(audio.peers) == 1
    await second.close()
    await main.close()
    await until(lambda: audio.peers == {})


async def test_closing_the_control_socket_ends_the_audio_socket(client):
    await setup_admin(client)
    audio = client.server.app[K_AUDIO]
    main = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await main.receive()).data)
    aws, _ = await open_audio_ws(client, hello)
    await main.close()
    await until(lambda: audio.peers == {})
    await until(lambda: audio.status()["rx_capture"] is False and audio.status()["tx_playback"] is False)     # capture and playback stop too
    await aws.close()


async def test_unavailable_audio_is_refused(client):
    await setup_admin(client)
    audio = client.server.app[K_AUDIO]
    main = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await main.receive()).data)
    audio.available, audio.reason = False, "audio disabled in config"
    r = await client.get(f"/ws/audio?conn={hello['conn']}", headers=origin(client))
    assert r.status == 503
    await main.close()
