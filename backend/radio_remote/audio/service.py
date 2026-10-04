"""Remote audio: radio <-> WebRTC/Opus <-> browser.

RX: capture (ALSA or test tone) -> gain -> one shared track -> MediaRelay -> one Opus encoder per peer.
TX: browser mic -> Opus decode -> mono 48k -> *gate* -> gain + limiter -> jitter queue -> playback to the radio.

The gate is the safety-critical part: mic audio only reaches the radio's input while the server-side PTT
guard says THIS peer's connection owns an active transmission. Everything else is dropped here, on the server.
"""
from __future__ import annotations

import asyncio
import collections
import fractions
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import devices, freedv, opus_tuning
from .backends import (AlsaSink, AlsaSource, AudioDeviceError, RecordingSink, Sink, Source, ToneSource)
from .levels import FRAME_BYTES, FRAME_SAMPLES, RATE, apply_gain, db_to_gain, level_pct

log = logging.getLogger("audio")

TX_QUEUE_MAX = 6                 # frames (120 ms) of jitter buffer; older audio is dropped to bound latency
TX_QUEUE_FREEDV = 24             # frames (480 ms) while FreeDV is on: its modem hands over whole blocks at once
LEVEL_INTERVAL_S = 0.1


class AudioUnavailable(Exception):
    pass


class OpusCodec:
    """One Opus encoder + decoder for a WebSocket audio peer (PyAV/libopus; 48 kHz mono, 20 ms packets)."""

    def __init__(self, bitrate: int):
        try:
            import av
            self._av = av
            self.enc = av.CodecContext.create("libopus", "w")
            self.enc.sample_rate, self.enc.layout, self.enc.format, self.enc.bit_rate = RATE, "mono", "s16", bitrate
            self.enc.options = {"application": "voip", "frame_duration": "20"}
            self.enc.open()
            self.dec = av.CodecContext.create("opus", "r")
            self.dec.sample_rate, self.dec.layout = RATE, "mono"
            self.dec.open()
        except Exception as e:                                # codec missing in this PyAV build
            raise AudioUnavailable(f"Opus codec not available for WebSocket audio ({e})") from None
        self._pts = 0
        self._rest = b""

    def encode(self, pcm: bytes) -> list[bytes]:
        f = self._av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
        f.planes[0].update(pcm)
        f.sample_rate, f.pts = RATE, self._pts
        self._pts += FRAME_SAMPLES
        return [bytes(p) for p in self.enc.encode(f)]

    def decode(self, packet: bytes) -> list[bytes]:
        """One Opus packet -> whole 20 ms mono s16 frames (anything left over waits for the next packet)."""
        out = bytearray(self._rest)
        for fr in self.dec.decode(self._av.Packet(packet)):
            a = np.clip(fr.to_ndarray().reshape(-1), -1.0, 1.0)
            out += (a * 32767.0).astype("<i2").tobytes()
        frames = [bytes(out[i:i + FRAME_BYTES]) for i in range(0, len(out) - FRAME_BYTES + 1, FRAME_BYTES)]
        self._rest = bytes(out[len(frames) * FRAME_BYTES:])
        return frames


@dataclass
class Peer:
    id: str
    pc: object
    conn_id: str
    user: str
    role: str
    tx_task: asyncio.Task | None = None
    rx: bool = False
    state: str = "new"
    created: float = field(default_factory=time.time)
    ws: object = None                                          # WebSocket transport (fallback for networks that block WebRTC/UDP)
    codec: OpusCodec | None = None
    out: asyncio.Queue | None = None
    sender: asyncio.Task | None = None
    task: asyncio.Task | None = None                          # the WebSocket session handler


class AudioService:
    def __init__(self, cfg: dict, *, is_mock: bool, tx_gate: Callable[[str], bool],
                 on_levels: Callable[[dict], None] | None = None,
                 source_factory: Callable[[], Source] | None = None,
                 sink_factory: Callable[[], Sink] | None = None):
        self.cfg, self.is_mock, self.tx_gate = cfg, is_mock, tx_gate
        self.on_levels = on_levels or (lambda d: None)
        self._source_factory, self._sink_factory = source_factory, sink_factory
        self.available = False
        self.reason = "not started"
        self.backend = "none"
        self.peers: dict[str, Peer] = {}
        self.rx_gain = db_to_gain(cfg["rx_gain_db"])
        self.tx_gain = db_to_gain(cfg["tx_gain_db"])
        self.capture_error: str | None = None
        self.playback_error: str | None = None
        # RX capture state
        self._source: Source | None = None
        self._cap_task: asyncio.Task | None = None
        self._rx_q: collections.deque[bytes] = collections.deque(maxlen=5)
        self._rx_event: asyncio.Event | None = None
        # TX playback state
        self._sink: Sink | None = None
        self._pump_task: asyncio.Task | None = None
        self._tx_q: collections.deque[bytes] = collections.deque(maxlen=TX_QUEUE_MAX)
        self._tx_gate_open = False
        self._levels = {"audio_rx_level": 0, "audio_tx_level": 0, "audio_tx_frames": 0, "audio_tx_error": "",
                        "freedv_on": False, "freedv_mode": "", "freedv_sync": 0, "freedv_snr": 0.0}
        # FreeDV (see freedv.py): decode the radio's modem tones to speech for the listeners, encode the operator's speech to modem tones
        self.freedv_ok, self.freedv_reason = freedv.available()
        self._fd_rx: freedv.RxChain | None = None
        self._fd_tx: freedv.TxChain | None = None
        self.freedv_mode = "700D"                       # the app sets these from [freedv] in the config
        self.freedv_tx_gain = db_to_gain(-6.0)
        self._last_emit = 0.0
        self.frames_in = self.frames_to_radio = 0
        self._relay = self._rx_track = None

    # ------------------------------------------------------------------ setup
    async def start(self) -> None:
        a = self.cfg
        if not a["enabled"]:
            self.reason = "audio disabled in config"
            return
        try:
            import aiortc  # noqa: F401
            from aiortc.contrib.media import MediaRelay
        except ImportError as e:
            self.reason = f"aiortc not installed ({e})"
            return
        opus_tuning.apply(a["opus_bitrate"])
        backend = a["backend"]
        if backend == "auto":
            backend = "test" if self.is_mock else "alsa"
        self.backend = backend
        if self._source_factory is None:
            if backend == "test":
                self._source_factory, self._sink_factory = ToneSource, RecordingSink
            elif backend == "alsa":
                found = await asyncio.get_running_loop().run_in_executor(None, devices.list_devices)
                cap, why1 = (a["input_device"], "") if a["input_device"] else devices.auto_pick(found["capture"])
                play, why2 = (a["output_device"], "") if a["output_device"] else devices.auto_pick(found["playback"])
                if not cap or not play:
                    self.reason = why1 or why2
                    return
                self._source_factory = lambda: AlsaSource(cap)
                self._sink_factory = lambda: AlsaSink(play)
                log.info("audio devices: capture=%s playback=%s", cap, play)
        self._relay = MediaRelay()
        self._rx_track = self._make_rx_track()
        self.available, self.reason = True, ""
        log.info("audio ready (backend=%s, opus %d bps)", self.backend, a["opus_bitrate"])

    def status(self) -> dict:
        return {
            "available": self.available, "reason": self.reason, "backend": self.backend,
            "peers": len(self.peers), "rx_capture": bool(self._cap_task), "tx_playback": bool(self._pump_task),
            "capture_error": self.capture_error, "playback_error": self.playback_error,
            "frames_to_radio": self.frames_to_radio,
        }

    # ------------------------------------------------------------- FreeDV
    def freedv_state(self) -> dict:
        st = freedv.mode_status()
        return {"available": self.freedv_ok, "reason": self.freedv_reason, "on": self._fd_rx is not None, "mode": self.freedv_mode,
                "modes": [m for m in freedv.ALL_MODES if not st.get(m)], "unavailable": {m: w for m, w in st.items() if w},
                "all_modes": list(freedv.ALL_MODES)}

    def refresh_freedv(self) -> None:
        """Look again for the FreeDV libraries (after the Install RADE button put one in place)."""
        from . import rade
        rade.reset()
        self.freedv_ok, self.freedv_reason = freedv.available()

    def set_freedv_params(self, mode: str, tx_level_db: float) -> None:
        """Defaults from the config. A running session picks up the new transmit level at once; a mode change applies the next time it is switched on."""
        self.freedv_mode = mode if mode in freedv.ALL_MODES else self.freedv_mode
        self.freedv_tx_gain = db_to_gain(tx_level_db)
        if self._fd_tx:
            self._fd_tx.level = self.freedv_tx_gain

    def set_freedv(self, on: bool, mode: str | None = None) -> None:
        """Switch FreeDV on or off. On: the radio's audio is decoded to speech for every listener and the operator's microphone is encoded
        to modem tones while that connection owns PTT. Off: audio passes through untouched, as before."""
        if on and not self.freedv_ok:
            raise AudioUnavailable(self.freedv_reason or "FreeDV is not available")
        if on:
            want = mode if mode in freedv.ALL_MODES else self.freedv_mode
            why = freedv.mode_status().get(want, "")
            if why:
                raise AudioUnavailable(f"FreeDV {want} is not available: {why}")
        for c in (self._fd_rx, self._fd_tx):
            if c:
                c.close()
        self._fd_rx = self._fd_tx = None
        if on:
            self.freedv_mode = mode if mode in freedv.ALL_MODES else self.freedv_mode
            self._fd_rx = freedv.RxChain(self.freedv_mode)
            self._fd_tx = freedv.TxChain(self.freedv_mode, self.freedv_tx_gain)
            self._tx_q = collections.deque(self._tx_q, maxlen=TX_QUEUE_FREEDV)      # the modem delivers its tones in bursts of up to 160 ms
        else:
            self._tx_q = collections.deque(self._tx_q, maxlen=TX_QUEUE_MAX)
        self._tx_gate_open = False
        self._levels.update(freedv_on=on, freedv_mode=self.freedv_mode if on else "", freedv_sync=0, freedv_snr=0.0)
        self._last_emit = 0.0
        self._level("freedv_on", on)
        log.info("FreeDV %s%s", "on, mode " + self.freedv_mode if on else "off", "")

    def set_gains(self, rx_db: float | None = None, tx_db: float | None = None) -> None:
        if rx_db is not None:
            self.rx_gain = db_to_gain(rx_db)
        if tx_db is not None:
            self.tx_gain = db_to_gain(tx_db)

    # -------------------------------------------------------------- levels
    def _level(self, key: str, value: int) -> None:
        self._levels[key] = value
        now = time.monotonic()
        if now - self._last_emit >= LEVEL_INTERVAL_S:
            self._last_emit = now
            self.on_levels(dict(self._levels))

    # ------------------------------------------------------------ RX capture
    def _make_rx_track(self):
        from aiortc import MediaStreamTrack
        from av import AudioFrame

        svc = self

        class RadioRxTrack(MediaStreamTrack):
            kind = "audio"

            def __init__(self):
                super().__init__()
                self._pts = 0

            async def recv(self):
                if self.readyState != "live":
                    from aiortc.mediastreams import MediaStreamError
                    raise MediaStreamError
                pcm = None
                try:
                    await asyncio.wait_for(svc._rx_event.wait(), 0.1)
                    pcm = svc._rx_q.popleft()
                    if not svc._rx_q:
                        svc._rx_event.clear()
                except (asyncio.TimeoutError, IndexError):
                    svc._rx_event.clear()
                n = FRAME_SAMPLES if pcm else FRAME_SAMPLES * 5    # device stalled: 100 ms of silence
                frame = AudioFrame(format="s16", layout="mono", samples=n)
                frame.planes[0].update(pcm if pcm else bytes(n * 2))
                frame.sample_rate = RATE
                frame.pts = self._pts
                frame.time_base = fractions.Fraction(1, RATE)
                self._pts += n
                return frame

        self._rx_event = asyncio.Event()
        return RadioRxTrack()

    async def _ensure_capture(self) -> None:
        if self._cap_task is None:
            self._cap_task = asyncio.create_task(self._capture_loop())

    async def _stop_capture(self) -> None:
        t, self._cap_task = self._cap_task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        self._rx_q.clear()
        self._level("audio_rx_level", 0)

    async def _capture_loop(self) -> None:
        backoff = 0.5
        while True:
            src = self._source_factory()
            try:
                await src.start()
                self.capture_error, backoff = None, 0.5
                while True:
                    pcm = apply_gain(await src.read_frame(), self.rx_gain, limit=self.rx_gain > 1.0)      # a boost must not clip hard
                    self._level("audio_rx_level", level_pct(pcm))                # the meter shows what the radio sends (the modem tones)
                    if self._fd_rx is not None and self._tx_gate_open:
                        pcm = bytes(FRAME_BYTES)                                 # transmitting: never decode (and replay) our own signal
                    elif self._fd_rx is not None:                                # FreeDV: listeners get the decoded speech instead
                        try:
                            pcm = self._fd_rx.process(pcm)
                            self._levels.update(freedv_sync=self._fd_rx.sync, freedv_snr=round(self._fd_rx.snr, 1))
                        except Exception:
                            log.exception("FreeDV receive failed; switching it off")
                            self.set_freedv(False)
                    self._rx_q.append(pcm)
                    self._rx_event.set()
                    self._push_ws_peers(pcm)
            except AudioDeviceError as e:
                self.capture_error = str(e)
                log.warning("RX capture: %s (retry in %.1fs)", e, backoff)
            finally:
                await asyncio.shield(src.stop())
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    # ------------------------------------------------------------ TX playback
    async def _ensure_tx_sink(self) -> None:
        if self._pump_task is None:
            self._tx_q.clear()
            self._pump_task = asyncio.create_task(self._pump_loop())

    async def _stop_tx_sink(self) -> None:
        t, self._pump_task = self._pump_task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        self._tx_q.clear()
        self._level("audio_tx_level", 0)

    async def _pump_loop(self) -> None:
        """Writes one frame every 20 ms to the radio: queued mic audio, else silence (keeps the codec fed)."""
        loop = asyncio.get_running_loop()
        silence = bytes(FRAME_BYTES)
        backoff = 0.5
        while True:
            sink = self._sink_factory()
            try:
                await sink.start()
                self.playback_error, backoff = None, 0.5
                self._level("audio_tx_error", "")
                nxt = loop.time()
                while True:
                    pcm = self._tx_q.popleft() if self._tx_q else silence
                    await sink.write_frame(pcm)
                    if pcm is not silence:
                        self.frames_to_radio += 1
                        self._levels["audio_tx_frames"] = self.frames_to_radio     # shown live in the Audio panel
                    nxt += FRAME_SAMPLES / RATE
                    delay = nxt - loop.time()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    elif delay < -0.2:
                        nxt = loop.time()
            except AudioDeviceError as e:
                self.playback_error = str(e)
                self._level("audio_tx_error", str(e)[:160])                  # shown on the Audio tab: otherwise a bad device fails silently
                log.warning("TX playback: %s (retry in %.1fs)", e, backoff)
            finally:
                await asyncio.shield(sink.stop())
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    def _push_ws_peers(self, pcm: bytes) -> None:
        for p in self.peers.values():
            if p.ws is None or p.out is None:
                continue
            try:
                packets = p.codec.encode(pcm)
            except Exception:
                log.exception("opus encode failed for %s", p.id)
                continue
            for pkt in packets:
                if p.out.full():
                    p.out.get_nowait()                         # a slow link loses its oldest audio instead of falling behind
                p.out.put_nowait(pkt)

    def _mic_frame(self, peer: Peer, pcm: bytes) -> None:
        """One 20 ms mic frame from a peer (either transport). THE GATE: it only passes while this peer owns an active transmission."""
        self.frames_in += 1
        if self.tx_gate(peer.conn_id):
            if not self._tx_gate_open:
                self._tx_q.clear()                       # never send stale audio from before PTT
                self._tx_gate_open = True
                if self._fd_tx is not None:              # a fresh modem for every transmission: nothing left over from the last one
                    self._fd_tx.close()
                    self._fd_tx = freedv.TxChain(self.freedv_mode, self.freedv_tx_gain)
            if self._fd_tx is not None:
                # FreeDV: the microphone speech becomes modem tones. The mic gain still applies; the limiter does not (it would distort the
                # tones), the modem output level (freedv.tx_level_db) takes its place.
                try:
                    for tone in self._fd_tx.process(apply_gain(pcm, self.tx_gain)):
                        self._tx_q.append(tone)
                        self._level("audio_tx_level", level_pct(tone))
                except Exception:
                    log.exception("FreeDV transmit failed")
                return
            pcm = apply_gain(pcm, self.tx_gain, limit=True)
            self._tx_q.append(pcm)
            self._level("audio_tx_level", level_pct(pcm))
        else:
            self._tx_gate_open = False
            self._level("audio_tx_level", 0)

    async def _consume_mic(self, peer: Peer, track) -> None:
        from av import AudioResampler

        resampler = AudioResampler(format="s16", layout="mono", rate=RATE, frame_size=FRAME_SAMPLES)
        try:
            while True:
                frame = await track.recv()
                for f in resampler.resample(frame):
                    self._mic_frame(peer, np.frombuffer(f.planes[0], dtype="<i2", count=f.samples).tobytes())
        except Exception as e:                                # MediaStreamError when the peer leaves
            log.debug("mic consumer for %s ended: %r", peer.id, e)

    # ----------------------------------------------------------------- peers
    async def offer(self, sdp: str, type_: str, *, user: str, role: str, conn_id: str) -> dict:
        if not self.available:
            raise AudioUnavailable(self.reason)
        for p in [p for p in self.peers.values() if p.conn_id == conn_id]:
            await self._close_peer(p.id)                      # one audio session per connection
        if len(self.peers) >= self.cfg["max_peers"]:
            raise AudioUnavailable("too many audio listeners")
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))     # LAN / tailnet: host candidates only
        peer = Peer(secrets.token_hex(6), pc, conn_id, user, role)
        self.peers[peer.id] = peer
        # Any operator connection may carry a mic track: the gate only passes audio from the connection that owns PTT, so a
        # second device (or a stale session that has not timed out yet) can no longer lock the real one out of transmitting.
        can_tx = role in ("operator", "admin") and self._sink_factory is not None

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio" and can_tx and peer.tx_task is None:
                peer.tx_task = asyncio.create_task(self._consume_mic(peer, track))
                asyncio.ensure_future(self._ensure_tx_sink())

        @pc.on("connectionstatechange")
        async def on_state():
            peer.state = pc.connectionState
            log.info("audio peer %s (%s) %s", peer.id, user, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await self._close_peer(peer.id)

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))
            pc.addTrack(self._relay.subscribe(self._rx_track, buffered=False))
            peer.rx = True
            await self._ensure_capture()
            await pc.setLocalDescription(await pc.createAnswer())
        except Exception:
            await self._close_peer(peer.id)
            raise
        log.info("audio peer %s for %s (role=%s, mic=%s)", peer.id, user, role, can_tx)
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "peer": peer.id, "mic": can_tx}

    async def ws_session(self, ws, *, user: str, role: str, conn_id: str) -> None:
        """Audio over a WebSocket (binary Opus packets, 20 ms each, both ways). The fallback for networks where WebRTC/UDP is blocked.
        Runs until the socket closes. Same rules as WebRTC: one session per connection, peer limit, listeners cannot transmit, and
        microphone audio goes through the same server-side gate."""
        from aiohttp import WSMsgType

        if not self.available:
            raise AudioUnavailable(self.reason)
        for p in [p for p in self.peers.values() if p.conn_id == conn_id]:
            await self._close_peer(p.id)
        if len(self.peers) >= self.cfg["max_peers"]:
            raise AudioUnavailable("too many audio listeners")
        codec = OpusCodec(self.cfg["opus_bitrate"])
        peer = Peer(secrets.token_hex(6), None, conn_id, user, role, ws=ws, codec=codec, out=asyncio.Queue(maxsize=8))
        can_tx = role in ("operator", "admin") and self._sink_factory is not None
        self.peers[peer.id] = peer
        peer.state, peer.task = "connected", asyncio.current_task()

        async def sender():
            while True:
                await ws.send_bytes(await peer.out.get())

        peer.sender = asyncio.create_task(sender())
        try:
            await self._ensure_capture()
            if can_tx:
                await self._ensure_tx_sink()
            log.info("audio peer %s for %s over WebSocket (role=%s, mic=%s)", peer.id, user, role, can_tx)
            await ws.send_json({"ok": True, "mic": can_tx, "rate": RATE, "peer": peer.id})
            async for m in ws:
                if m.type == WSMsgType.BINARY and can_tx:
                    try:
                        for pcm in codec.decode(m.data):
                            self._mic_frame(peer, pcm)
                    except Exception as e:                    # a damaged packet: skip it
                        log.debug("bad mic packet from %s: %r", peer.id, e)
        finally:
            await asyncio.shield(self._close_peer(peer.id))      # the web server may cancel this handler when the browser leaves

    async def _close_peer(self, peer_id: str) -> None:
        peer = self.peers.pop(peer_id, None)
        if not peer:
            return
        if peer.sender:
            peer.sender.cancel()
            await asyncio.gather(peer.sender, return_exceptions=True)
        if peer.ws is not None:
            self._tx_gate_open = False
            if peer.task is not None and peer.task is not asyncio.current_task():
                peer.task.cancel()                         # ends the session handler; the browser sees the socket close and reconnects
        if peer.tx_task:
            peer.tx_task.cancel()
            await asyncio.gather(peer.tx_task, return_exceptions=True)
            self._tx_gate_open = False
        if peer.pc is not None:
            await peer.pc.close()
        if not self.peers:
            await self._stop_capture()
        if not any(p.tx_task or (p.ws is not None and p.role in ("operator", "admin")) for p in self.peers.values()):
            await self._stop_tx_sink()

    async def close_conn(self, conn_id: str) -> None:
        for p in [p for p in self.peers.values() if p.conn_id == conn_id]:
            await asyncio.shield(self._close_peer(p.id))     # the control socket's handler may itself be cancelled while it runs this

    async def stop(self) -> None:
        self.set_freedv(False)
        for pid in list(self.peers):
            await self._close_peer(pid)
        await self._stop_capture()
        await self._stop_tx_sink()
