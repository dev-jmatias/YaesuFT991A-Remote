import asyncio
import copy
import fractions
import json
import sys

import numpy as np
import pytest
from conftest import setup_admin
from test_web import origin, recv_until

from radio_remote import config
from radio_remote.audio import devices
from radio_remote.audio.backends import (AlsaSink, AlsaSource, AudioDeviceError, RecordingSink, ToneSource,
                                          alsa_capture_argv, alsa_playback_argv)
from radio_remote.audio.levels import FRAME_BYTES, FRAME_SAMPLES, RATE, apply_gain, db_to_gain, level_pct
from radio_remote.audio.service import AudioService, AudioUnavailable


# ---------------------------------------------------------------- devices
ARECORD = """**** List of CAPTURE Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
  Subdevices: 8/8
card 2: CODEC [USB Audio CODEC], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""


def test_parse_and_autopick():
    devs = devices.parse_alsa_list(ARECORD)
    assert [d["id"] for d in devs] == ["Headphones", "CODEC"]
    codec = devs[1]
    assert codec["plughw"] == "plughw:CARD=CODEC,DEV=0" and codec["hw"] == "hw:CARD=CODEC,DEV=0"
    assert codec["likely_radio"] and not devs[0]["likely_radio"]
    assert devices.auto_pick(devs) == ("plughw:CARD=CODEC,DEV=0", "")


def test_autopick_refuses_ambiguity():
    two = devices.parse_alsa_list(ARECORD + "card 3: CODEC_1 [USB Audio CODEC], device 0: USB Audio [USB Audio]\n")
    dev, why = devices.auto_pick(two)
    assert dev is None and "several" in why
    assert devices.auto_pick([])[0] is None


# ----------------------------------------------------------------- levels
def pcm(x):
    return (np.asarray(x) * 32767).astype("<i2").tobytes()


def test_levels_and_gain():
    n = np.arange(FRAME_SAMPLES)
    sine = np.sin(2 * np.pi * 1000 * n / RATE)
    assert level_pct(bytes(FRAME_BYTES)) == 0
    assert level_pct(b"") == 0
    quiet, loud = level_pct(pcm(sine * 0.01)), level_pct(pcm(sine * 0.5))
    assert 0 < quiet < loud <= 100
    assert abs(level_pct(pcm(sine * 0.99)) - 100) <= 6          # near full scale
    assert abs(db_to_gain(6) - 1.995) < 0.01
    half = np.frombuffer(apply_gain(pcm(sine * 0.5), 0.5), dtype="<i2")
    assert abs(int(half.max()) - 8191) <= 2


def test_limiter_bounds_output_and_is_transparent_when_quiet():
    n = np.arange(FRAME_SAMPLES)
    sine = np.sin(2 * np.pi * 1000 * n / RATE)
    hot = np.frombuffer(apply_gain(pcm(sine * 0.9), db_to_gain(20), limit=True), dtype="<i2")
    assert 26000 < int(np.abs(hot).max()) <= int(32768 * 0.89) + 1
    quiet_in = pcm(sine * 0.3)
    assert apply_gain(quiet_in, 1.0, limit=True) == quiet_in      # below the knee: untouched


# --------------------------------------------------------------- backends
def test_alsa_argv_is_fixed_and_safe():
    cap = alsa_capture_argv("plughw:CARD=CODEC,DEV=0")
    assert cap[:5] == ["arecord", "-q", "-D", "plughw:CARD=CODEC,DEV=0", "-t"] and "-c" in cap and "1" in cap
    assert alsa_playback_argv("x")[0] == "aplay"
    evil = alsa_capture_argv("hw:0; rm -rf /")                    # stays one argv element, never a shell
    assert evil[3] == "hw:0; rm -rf /" and len(evil) == len(alsa_capture_argv("hw:0"))


async def test_alsa_source_reads_frames_from_process():
    code = "import sys,time\nfor i in range(5):\n    sys.stdout.buffer.write(bytes([i])*%d); sys.stdout.buffer.flush()\n" % FRAME_BYTES
    src = AlsaSource("dev", lambda d: [sys.executable, "-c", code])
    await src.start()
    first = await src.read_frame()
    assert len(first) == FRAME_BYTES and first[0] == 0
    for _ in range(4):
        await src.read_frame()
    with pytest.raises(AudioDeviceError):
        await src.read_frame()                                    # process exited => device-gone error
    await src.stop()


async def test_alsa_sink_writes_to_process(tmp_path):
    out = tmp_path / "out.raw"
    code = ("import os\nf = open(%r, \"wb\", buffering=0)\nwhile True:\n    d = os.read(0, 4096)\n    if not d: break\n"
            "    f.write(d)\n") % str(out)
    sink = AlsaSink("dev", lambda d: [sys.executable, "-c", code])
    await sink.start()
    want = b"\x01\x00" * FRAME_SAMPLES + b"\x02\x00" * FRAME_SAMPLES
    await sink.write_frame(b"\x01\x00" * FRAME_SAMPLES)
    await sink.write_frame(b"\x02\x00" * FRAME_SAMPLES)
    await until(lambda: out.exists() and out.stat().st_size >= len(want))
    await sink.stop()
    assert out.read_bytes() == want


async def test_missing_alsa_binary_is_a_clean_error():
    src = AlsaSource("dev", lambda d: ["definitely-not-a-real-binary-xyz"])
    with pytest.raises(AudioDeviceError):
        await src.start()


async def test_tone_source_is_paced_in_real_time():
    s = ToneSource()
    await s.start()
    t0 = asyncio.get_running_loop().time()
    for _ in range(10):
        assert len(await s.read_frame()) == FRAME_BYTES
    assert 0.17 < asyncio.get_running_loop().time() - t0 < 0.4    # 10 x 20 ms


# ----------------------------------------------------------------- config
def test_audio_config_validation():
    base = copy.deepcopy(config.DEFAULTS)
    config.validate(base)
    for key, val in [("backend", "pulse"), ("opus_bitrate", 100), ("max_peers", 0), ("rx_gain_db", 99),
                     ("input_device", "hw:0; rm -rf /"), ("output_device", "6"), ("input_device", "0")]:    # a bare number is not an ALSA name
        bad = copy.deepcopy(base)
        bad["audio"][key] = val
        with pytest.raises(config.ConfigError):
            config.validate(bad)


# ------------------------------------------- real WebRTC session, in-process
def _mic_track(level=0.3):
    from aiortc import MediaStreamTrack
    from av import AudioFrame

    class Mic(MediaStreamTrack):
        kind = "audio"

        def __init__(self):
            super().__init__()
            self.n = 0
            self.t0 = None

        async def recv(self):
            loop = asyncio.get_running_loop()
            self.t0 = self.t0 or loop.time()
            await asyncio.sleep(max(0, self.t0 + (self.n + 1) * 0.02 - loop.time()))
            t = (np.arange(FRAME_SAMPLES) + self.n * FRAME_SAMPLES) / RATE
            f = AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
            f.planes[0].update(pcm(np.sin(2 * np.pi * 300 * t) * level))
            f.sample_rate, f.pts, f.time_base = RATE, self.n * FRAME_SAMPLES, fractions.Fraction(1, RATE)
            self.n += 1
            return f

    return Mic()


async def until(pred, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


class Client:
    """Stands in for the browser: sendrecv audio, counts received frames."""

    def __init__(self, with_mic=True):
        from aiortc import RTCConfiguration, RTCPeerConnection
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.received = 0
        self.rx_peak = 0
        self.with_mic = with_mic

    async def connect(self, svc, role="operator", conn="c1"):
        from aiortc import RTCSessionDescription
        pc = self.pc
        if self.with_mic:
            pc.addTrack(_mic_track())
        else:
            pc.addTransceiver("audio", direction="recvonly")

        @pc.on("track")
        def on_track(track):
            async def drain():
                try:
                    while True:
                        f = await track.recv()
                        self.received += 1
                        self.rx_peak = max(self.rx_peak, int(np.abs(f.to_ndarray()).max()))
                except Exception:
                    pass
            asyncio.ensure_future(drain())

        await pc.setLocalDescription(await pc.createOffer())
        ans = await svc.offer(pc.localDescription.sdp, "offer", user="u", role=role, conn_id=conn)
        await pc.setRemoteDescription(RTCSessionDescription(ans["sdp"], ans["type"]))
        return ans


@pytest.fixture
async def svc():
    cfg = copy.deepcopy(config.DEFAULTS)["audio"]
    gate = {"open": False}
    levels = []
    sink = RecordingSink()
    s = AudioService(cfg, is_mock=True, tx_gate=lambda cid: gate["open"] and cid == "c1",
                     on_levels=levels.append, source_factory=ToneSource, sink_factory=lambda: sink)
    s.gate, s.levels, s.sink = gate, levels, sink
    await s.start()
    assert s.available, s.reason
    yield s
    await s.stop()


async def test_rx_audio_reaches_browser_and_meter_moves(svc):
    c = Client(with_mic=False)
    await c.connect(svc, role="viewer")
    await until(lambda: c.received > 30)
    assert c.rx_peak > 500                                         # the 440 Hz tone, not silence
    await until(lambda: any(l["audio_rx_level"] > 10 for l in svc.levels))
    await c.pc.close()
    await until(lambda: not svc.peers and svc.status()["rx_capture"] is False)   # capture stops when nobody listens


async def test_mic_audio_is_dropped_until_server_gate_opens(svc):
    c = Client()
    ans = await c.connect(svc, role="operator")
    assert ans["mic"] is True
    await until(lambda: svc.frames_in > 20)                        # mic audio is arriving server-side...
    await asyncio.sleep(0.2)
    assert svc.frames_to_radio == 0                                # ...but none reaches the radio
    assert all(f == bytes(FRAME_BYTES) for f in svc.sink.frames)  # only silence is fed to the sink

    svc.gate["open"] = True                                        # PTT owner keyed
    await until(lambda: svc.frames_to_radio > 10)
    audible = [f for f in svc.sink.frames if f != bytes(FRAME_BYTES)]
    assert audible and level_pct(audible[-1]) > 20
    assert any(l["audio_tx_level"] > 10 for l in svc.levels)
    assert any(l["audio_tx_frames"] > 0 for l in svc.levels)       # the live "To radio: N frames" counter in the Audio panel

    svc.gate["open"] = False                                       # PTT released
    await asyncio.sleep(0.15)
    mark = svc.frames_to_radio
    await asyncio.sleep(0.4)
    assert svc.frames_to_radio == mark                             # nothing more is sent
    await c.pc.close()


async def test_gate_only_opens_for_the_ptt_owning_connection(svc):
    svc.gate["open"] = True                                        # gate allows conn "c1" only
    c = Client()
    await c.connect(svc, role="operator", conn="other")
    await until(lambda: svc.frames_in > 20)
    await asyncio.sleep(0.2)
    assert svc.frames_to_radio == 0
    await c.pc.close()


async def test_viewer_cannot_transmit_audio(svc):
    svc.gate["open"] = True
    c = Client()                                                   # tries to send a mic track anyway
    ans = await c.connect(svc, role="viewer")
    assert ans["mic"] is False
    await asyncio.sleep(0.6)
    assert svc.frames_in == 0 and svc.frames_to_radio == 0
    await c.pc.close()


async def test_peer_limit_and_one_session_per_connection(svc):
    svc.cfg["max_peers"] = 2
    a, b = Client(False), Client(False)
    await a.connect(svc, role="viewer", conn="a")
    await b.connect(svc, role="viewer", conn="b")
    with pytest.raises(AudioUnavailable):
        await Client(False).connect(svc, role="viewer", conn="c")
    await Client(False).connect(svc, role="viewer", conn="a")      # same connection replaces its old session
    assert len(svc.peers) == 2
    await a.pc.close(); await b.pc.close()


async def test_close_conn_stops_audio_and_tx_playback(svc):
    c = Client()
    await c.connect(svc, role="operator", conn="c1")
    await until(lambda: svc.status()["tx_playback"])
    await svc.close_conn("c1")
    assert not svc.peers and not svc.status()["tx_playback"] and not svc.status()["rx_capture"]
    await c.pc.close()


async def test_unavailable_when_disabled_or_no_devices():
    cfg = copy.deepcopy(config.DEFAULTS)["audio"]
    cfg["enabled"] = False
    s = AudioService(cfg, is_mock=True, tx_gate=lambda c: False)
    await s.start()
    assert not s.available and "disabled" in s.reason
    with pytest.raises(AudioUnavailable):
        await s.offer("x", "offer", user="u", role="admin", conn_id="c")


async def test_capture_device_loss_is_retried():
    cfg = copy.deepcopy(config.DEFAULTS)["audio"]
    attempts = {"n": 0}

    class Flaky(ToneSource):
        async def read_frame(self):
            attempts["n"] += 1
            if attempts["n"] == 3:
                raise AudioDeviceError("USB unplugged")
            return await super().read_frame()

    s = AudioService(cfg, is_mock=True, tx_gate=lambda c: False, source_factory=Flaky,
                     sink_factory=RecordingSink)
    await s.start()
    c = Client(False)
    await c.connect(s, role="viewer")
    await until(lambda: s.capture_error == "USB unplugged")
    await until(lambda: s.capture_error is None and c.received > 40, timeout=10)     # recovered by itself
    await c.pc.close()
    await s.stop()


# ---------------------------------------------------------------- HTTP/WS
async def test_offer_endpoint_rules(client):
    from radio_remote.app import K_AUDIO
    csrf = await setup_admin(client)
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    assert hello["audio"]["available"] is True and hello["audio"]["mic"] is True and hello["conn"]
    h = {"X-CSRF-Token": csrf}

    from aiortc import RTCConfiguration, RTCPeerConnection
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pc.addTransceiver("audio", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    body = {"sdp": pc.localDescription.sdp, "type": "offer", "conn": hello["conn"]}

    assert (await client.post("/api/audio/offer", json=body)).status == 403                      # no CSRF
    assert (await client.post("/api/audio/offer", json={**body, "conn": "nope"}, headers=h)).status == 403
    assert (await client.post("/api/audio/offer", json={**body, "type": "answer"}, headers=h)).status == 400
    r = await client.post("/api/audio/offer", json=body, headers=h)
    assert r.status == 200 and (await r.json())["type"] == "answer"
    st = await (await client.get("/api/audio/status")).json()
    assert st["available"] and st["peers"] == 1
    await ws.close()                                                                             # closes the audio peer too
    await until(lambda: client.server.app[K_AUDIO].peers == {})
    await pc.close()
    assert (await client.get("/api/audio/devices")).status == 200


async def test_ptt_owner_mic_flows_over_real_stack(client):
    """Full chain: WS PTT + WebRTC mic -> gate -> sink. Released PTT stops audio."""
    csrf = await setup_admin(client)
    from radio_remote.app import K_AUDIO
    audio = client.server.app[K_AUDIO]
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    c = Client()
    from aiortc import RTCSessionDescription
    await c.pc.setLocalDescription(await (c.pc.createOffer() if c.pc.getTransceivers() else _prep(c)))
    r = await client.post("/api/audio/offer", headers={"X-CSRF-Token": csrf},
                          json={"sdp": c.pc.localDescription.sdp, "type": "offer", "conn": hello["conn"]})
    ans = await r.json()
    await c.pc.setRemoteDescription(RTCSessionDescription(ans["sdp"], ans["type"]))
    await until(lambda: audio.frames_in > 10)
    assert audio.frames_to_radio == 0                                                            # not keyed yet

    await ws.send_json({"id": 1, "type": "ptt", "on": True})
    await recv_until(ws, lambda m: m["t"] == "ack" and m["ok"])
    for _ in range(25):                                                                          # keep the heartbeat alive
        await ws.send_json({"type": "ptt_hb"})
        await asyncio.sleep(0.04)
    assert audio.frames_to_radio > 10
    await ws.send_json({"id": 2, "type": "ptt", "on": False})
    await recv_until(ws, lambda m: m["t"] == "ack" and m["ok"])
    await asyncio.sleep(0.15)
    mark = audio.frames_to_radio
    await asyncio.sleep(0.4)
    assert audio.frames_to_radio == mark
    await c.pc.close()
    await ws.close()


async def _prep(c):
    """Offer with a mic track (Client.connect builds this itself; here we need the raw offer)."""
    c.pc.addTrack(_mic_track())

    @c.pc.on("track")
    def _t(track):
        async def drain():
            try:
                while True:
                    await track.recv()
            except Exception:
                pass
        asyncio.ensure_future(drain())
    return await c.pc.createOffer()


async def test_a_second_operator_connection_still_gets_a_mic_but_only_the_ptt_owner_is_heard(svc):
    svc.gate["open"] = True                                        # gate allows conn "c1" only
    first, second = Client(), Client()
    a = await first.connect(svc, role="operator", conn="c1")
    b = await second.connect(svc, role="operator", conn="other")
    assert a["mic"] is True and b["mic"] is True                   # a stale or second session must not lock the real one out
    await until(lambda: svc.frames_to_radio > 10)                  # c1's audio reaches the radio
    await first.pc.close(); await second.pc.close()


async def test_a_failing_tx_sound_card_is_reported_to_the_ui(svc):
    from radio_remote.audio.backends import AudioDeviceError

    class BadSink:
        async def start(self):
            raise AudioDeviceError("playback ended: Unknown PCM 6")
        async def stop(self):
            pass
        async def write_frame(self, pcm):
            pass

    svc._sink_factory = BadSink
    await svc._ensure_tx_sink()
    await until(lambda: any(l.get("audio_tx_error") for l in svc.levels))
    assert "Unknown PCM 6" in [l for l in svc.levels if l.get("audio_tx_error")][-1]["audio_tx_error"]
