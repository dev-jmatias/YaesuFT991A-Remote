#!/usr/bin/env python3
"""Phase 6 SPIKE: can this machine carry the audio chain? Run it on the Raspberry Pi and paste the output.

Needs no radio, no sound card and no network: it measures Opus encode/decode cost and a full in-process
WebRTC loopback (server track -> Opus -> SRTP -> client) using the same code as the app.

  python scripts/audio_spike.py
"""
import asyncio
import os
import platform
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from av import AudioFrame  # noqa: E402

from radio_remote.audio import opus_tuning  # noqa: E402
from radio_remote.audio.backends import RecordingSink, ToneSource  # noqa: E402
from radio_remote.audio.levels import FRAME_SAMPLES, RATE  # noqa: E402
from radio_remote.audio.service import AudioService  # noqa: E402
from radio_remote import config  # noqa: E402


def speechlike(n):
    rng = np.random.default_rng(1)
    t = np.arange(FRAME_SAMPLES * n) / RATE
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)                       # syllable-rate modulation
    x = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate((140, 280, 420, 560, 1100, 2300)))
    x = (x * env * 0.15 + rng.normal(0, 0.01, t.size)).clip(-1, 1)
    return (x * 32767).astype("<i2")


def bench_codec(bitrate):
    from aiortc.codecs import opus
    opus_tuning.apply(bitrate)
    enc, dec = opus.OpusEncoder(), opus.OpusDecoder()
    pcm, n = speechlike(500), 500
    frames = []
    for i in range(n):
        f = AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
        f.planes[0].update(pcm[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes())
        f.sample_rate, f.pts = RATE, i * FRAME_SAMPLES
        frames.append(f)
    t0, c0 = time.perf_counter(), time.process_time()
    packets = []
    for f in frames:
        for p in enc.encode(f)[0]:
            packets.append(p)
    ec = time.process_time() - c0
    from aiortc.jitterbuffer import JitterFrame
    c0 = time.process_time()
    for i, p in enumerate(packets):
        dec.decode(JitterFrame(data=p, timestamp=i * FRAME_SAMPLES))
    dc = time.process_time() - c0
    kbps = sum(len(p) for p in packets) * 8 / (n * 0.02) / 1000
    print(f"  opus {bitrate // 1000:>3} kbps target: encode {ec / n * 1e6:6.0f} us/frame ({ec / n / 0.02 * 100:4.1f}% of 1 core), "
          f"decode {dc / n * 1e6:6.0f} us/frame ({dc / n / 0.02 * 100:4.1f}%), measured {kbps:5.1f} kbps")


async def bench_webrtc(seconds=8, listeners=1):
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    cfg = config.load(None)["audio"]
    svc = AudioService(cfg, is_mock=True, tx_gate=lambda c: False, source_factory=ToneSource, sink_factory=RecordingSink)
    await svc.start()
    clients, counts = [], [0] * listeners
    for i in range(listeners):
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        pc.addTransceiver("audio", direction="recvonly")

        @pc.on("track")
        def on_track(track, i=i):
            async def drain():
                try:
                    while True:
                        await track.recv()
                        counts[i] += 1
                except Exception:
                    pass
            asyncio.ensure_future(drain())

        await pc.setLocalDescription(await pc.createOffer())
        ans = await svc.offer(pc.localDescription.sdp, "offer", user="spike", role="viewer", conn_id=f"c{i}")
        await pc.setRemoteDescription(RTCSessionDescription(ans["sdp"], ans["type"]))
        clients.append(pc)
    await asyncio.sleep(1.5)                                          # settle
    base = list(counts)
    c0, t0 = time.process_time(), time.perf_counter()
    await asyncio.sleep(seconds)
    cpu, wall = time.process_time() - c0, time.perf_counter() - t0
    got = [c - b for c, b in zip(counts, base)]
    print(f"  {listeners} listener(s): CPU {cpu / wall * 100:5.1f}% of one core (server encode + client decode + SRTP, "
          f"both in this process); frames/s per listener {[round(g / wall) for g in got]} (ideal 50)")
    for pc in clients:
        await pc.close()
    await svc.stop()


async def main():
    print(f"Python {sys.version.split()[0]} on {platform.platform()} ({platform.machine()}), {os.cpu_count()} cores")
    try:
        with open("/proc/device-tree/model") as f:
            print("Board:", f.read().strip("\x00"))
    except OSError:
        pass
    print("\n1) Opus codec cost (500 x 20 ms frames, speech-like signal)")
    for br in (96000, 32000, 24000):
        bench_codec(br)
    print("\n2) In-process WebRTC loopback, test-tone source, 32 kbps")
    opus_tuning.apply(32000)
    for n in (1, 3):
        await bench_webrtc(listeners=n)
    print("\nPaste this output back. Rule of thumb: total under ~25% of one core on a Pi 3B+ is comfortable.")


asyncio.run(main())
