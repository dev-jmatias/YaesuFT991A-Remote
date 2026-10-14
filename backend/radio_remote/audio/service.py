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
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import devices, freedv, opus_tuning
from . import dynamics as dynmod
from . import eq as eqmod
from . import recorder as recmod
from .backends import (AlsaSink, AlsaSource, AudioDeviceError, RecordingSink, Sink, Source, ToneSource)
from .levels import FRAME_BYTES, FRAME_SAMPLES, RATE, apply_gain, db_to_gain, level_pct

log = logging.getLogger("audio")

TX_QUEUE_MAX = 6                 # frames (120 ms) of jitter buffer; older audio is dropped to bound latency
TX_QUEUE_FREEDV = 24             # frames (480 ms) while FreeDV is on: its modem hands over whole blocks at once
TX_PREFILL_FRAMES = 10           # FreeDV: frames (200 ms) of modem tones that must be waiting before they start going to the radio (and again after a gap). The modem hands over
                                 # its tones in bursts (RADE: 120 ms at a time) that arrive with the network's jitter; without this cushion a burst that is a few ms late made the
                                 # sender write 20 ms of silence into the middle of the signal, and RADE cannot follow a signal with holes in it
TX_DRAIN_MAX_S = 1.2             # PTT release: the most the page waits for the queued tones to reach the radio (a cushion of 0.2 s plus the last modem frame normally needs about 0.4 s)
TX_TAIL_S = 0.15                 # and a little more: aplay holds about 100 ms that has been handed over but not yet played
TEST_TONE_HZ = 1500.0           # FreeDV level test: a steady tone in the middle of the modem's audio band
TEST_TONE_PEAK = 19000.0        # its peak at a transmit level of 0 dB: what the modem's own peaks reach (the ALC reacts to the peaks)
TEST_TONE_MAX_S = 15.0          # the tone stops by itself after this long
SLOW_FRAME_MS = 20.0             # a FreeDV receive step that takes longer than one frame (20 ms) can make the capture fall behind on a slow PC
LEVEL_INTERVAL_S = 0.1
GATE_IDLE_S = 0.5               # seconds without a microphone frame after which the transmit state ends by itself (over WebSocket the page sends nothing once PTT is released)
RX_HOLD_FRAMES = 15              # frames (300 ms) of silence after a transmission before the FreeDV receiver restarts: the radio is still switching back to receive


class AudioUnavailable(Exception):
    pass


def _decode_timed(chain, pcm: bytes):
    """Runs in the decoding worker thread: the decoded frame and how long the decoder itself took (ms)."""
    t0 = time.perf_counter()
    out = chain.process(pcm)
    return out, (time.perf_counter() - t0) * 1000.0


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
        self._gate_seen = 0.0                            # when the gate last let a microphone frame through
        self._tx_primed = True                           # FreeDV transmit: the cushion (TX_PREFILL_FRAMES) is in place and the tones are flowing
        self._tx_ending = False                          # PTT is being released: the queue running dry is the end of the over, not a hole
        self.tx_dropped = 0                              # FreeDV transmit: tone frames thrown away because the queue was full (the sender stalled for over 480 ms)
        self.recorder: recmod.Recorder | None = None     # set by the app: the last clips of received audio
        self._player_task = None
        self._player_running = False                     # a clip is being fed by the server's own clock: the microphone frames are ignored
        self._play_req = None                            # a clip (16 kHz samples) the next PTT sends instead of the microphone (set by the hub)
        self._play_data = None                           # the clip being sent in this over
        self._play_pos = 0
        self._play_up = None
        self._play_done = False
        self.on_playback_done: Callable[[], None] | None = None
        self.eq_rx = eqmod.Equalizer()                   # the 8-band equalizers: what the listeners hear / what the operator sends (never the modem tones)
        self.eq_tx = eqmod.Equalizer()
        self.rx_gate = dynmod.NoiseGate()                # the receive noise gate (what the listeners hear), before the receive equalizer
        self.tx_dyn = dynmod.TxDynamics()                # the transmit leveler and limiter (on the speech, before the modem or the radio's limiter)
        self._play_active = False
        self._test_req = False                           # the PTT that is about to open the gate asked for the level test tone (set by the hub)
        self._test_active = False                        # this over is a level test: a steady tone instead of the microphone's modem tones
        self._test_n = 0                                 # samples of tone sent so far (phase and duration)
        self._tx_owner = None                            # conn_id of the peer whose frames opened the gate
        self.tx_underruns = 0                            # FreeDV transmit: holes written into the tones because the queue ran dry in mid-over (all overs)
        self._over_underruns = 0                         # the same, for the over in progress
        self._over_frames = 0
        self._over_dropped = 0                           # tone frames of this over thrown away because the queue was full (the sender stalled, or the PTT-release drain ran out of time)
        self.rx_ms_avg = 0.0                             # FreeDV receive: time one 20 ms frame takes to process (average / worst), and how many frames took longer than 20 ms
        self.rx_ms_max = 0.0
        self.rx_work_avg = self.rx_work_max = 0.0                # the same, but only the time INSIDE the decoder (the total above also counts waiting for the worker thread and for the event loop)
        self.rx_slow = 0
        self._tx_proc_ms_max = 0.0
        self._levels = {"audio_rx_level": 0, "audio_tx_level": 0, "audio_tx_frames": 0, "audio_tx_error": "",
                        "freedv_on": False, "freedv_mode": "", "freedv_sync": 0, "freedv_snr": 0.0,
                        "freedv_spec": [], "freedv_offset": 0, "freedv_hint": None, "freedv_afc": "", "freedv_level": -120, "freedv_clip": False}
        # FreeDV (see freedv.py): decode the radio's modem tones to speech for the listeners, encode the operator's speech to modem tones
        self.freedv_ok, self.freedv_reason = freedv.available()
        self._fd_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="freedv-rx")     # one worker: frames are decoded strictly in order
        self._fd_rx: freedv.RxChain | None = None
        self._fd_tx: freedv.TxChain | None = None
        self.on_freedv_state = None                    # callbacks (FreeDV Reporter): (on, mode) and (transmitting)
        self.on_freedv_tx = None
        self._fd_tx_reported = False
        self._rx_hold = 0                                # frames still to skip after a transmission before the FreeDV receiver restarts
        self.freedv_mode = "RADE"                       # the app sets these from [freedv] in the config
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
            "freedv_timing": {"rx_ms_avg": round(self.rx_ms_avg, 2), "rx_ms_max": round(self.rx_ms_max, 1), "rx_work_ms_avg": round(self.rx_work_avg, 2), "rx_work_ms_max": round(self.rx_work_max, 1), "rx_slow_frames": self.rx_slow,
                              "tx_ms_max": round(self._tx_proc_ms_max, 1), "tx_underruns": self.tx_underruns, "tx_dropped": self.tx_dropped},
        }

    # ------------------------------------------------------------- FreeDV
    def freedv_state(self) -> dict:
        from . import rade
        st = freedv.mode_status()
        # "show": the page shows the FreeDV tab also while the RADE library is missing but can be installed, because the Install RADE button lives there
        return {"available": self.freedv_ok, "show": bool(self.freedv_ok or rade.installable()), "reason": self.freedv_reason, "on": self._fd_rx is not None, "mode": self.freedv_mode,
                "modes": [m for m in freedv.ALL_MODES if not st.get(m)], "unavailable": {m: w for m, w in st.items() if w},
                "all_modes": list(freedv.ALL_MODES)}

    def freedv_dial_moved(self, old_hz: int, new_hz: int, mode: str | None) -> None:
        """The radio's dial moved while FreeDV is on: every audio frequency moved with it, so the software shift is adjusted by the same amount and the lock is kept."""
        ch = self._fd_rx
        if ch is None or not hasattr(ch, "afc") or not old_hz or not new_hz:
            return
        d = new_hz - old_hz
        if abs(d) > 600:                                      # a band change or a memory: start the search afresh
            ch.afc.reset()
            return
        ch.afc.nudge(-d if mode in ("LSB", "CW-L", "DATA-L", "RTTY-L") else d)

    def freedv_afc_reset(self) -> None:
        if self._fd_rx is not None and hasattr(self._fd_rx, "afc"):
            self._fd_rx.afc.reset()

    def refresh_freedv(self) -> None:
        """Look again for the FreeDV libraries (after the Install RADE button put one in place)."""
        from . import rade
        rade.reset(force=True)                       # also drops an older library that is already loaded: the newly installed one is used the next time FreeDV is switched on
        self.freedv_ok, self.freedv_reason = freedv.available()

    def set_eq(self, rx_on=None, tx_on=None, rx=None, tx=None, rx_cut=None, tx_cut=None) -> None:
        """rx / tx: 5, 10 or 18 bands [{f, g, q}, ...]. Raises ValueError for bad bands (eq.clean_bands)."""
        self.eq_rx.set(on=rx_on, bands=rx, cut=rx_cut)
        self.eq_tx.set(on=tx_on, bands=tx, cut=tx_cut)

    def set_dyn(self, value) -> None:
        """Noise gate / leveler / limiter settings for the transmit side, e.g. {"leveler": {"on": True, "max_gain": 12}}, and "rx_gate" for the receive noise gate; what is not mentioned
        stays. Raises ValueError (and changes nothing)."""
        if not isinstance(value, dict):
            raise ValueError("expected {gate, leveler, limiter, rx_gate}")
        value = dict(value)
        rx = value.pop("rx_gate", None)
        if rx is not None:
            dynmod._clean_part("gate", self.rx_gate.cfg, rx)                  # check it first, so a bad transmit part cannot leave the receive part half applied
        if value:
            self.tx_dyn.set(value)
        if rx is not None:
            self.rx_gate.set(rx)

    def dyn_save(self) -> dict:
        return {**self.tx_dyn.state(), "rx_gate": self.rx_gate.state()}

    def dyn_state(self) -> dict:
        return {**self.dyn_save(), "limits": {"max_gain": list(dynmod.MAX_GAIN), "target": list(dynmod.TARGET), "decay": list(dynmod.DECAY),
                                              "ceiling": list(dynmod.CEILING), "release": list(dynmod.RELEASE), "threshold": list(dynmod.GATE_THRESHOLD),
                                              "range": list(dynmod.GATE_RANGE), "hold": list(dynmod.GATE_HOLD), "gate_release": list(dynmod.GATE_RELEASE)}}

    def eq_state(self) -> dict:
        return {"rx": self.eq_rx.state(), "tx": self.eq_tx.state(), "layouts": eqmod.LAYOUTS,
                "limits": {"f": [eqmod.MIN_HZ, eqmod.MAX_HZ], "g": eqmod.MAX_DB, "q": [eqmod.MIN_Q, eqmod.MAX_Q],
                           "low": list(eqmod.LOW_CUT), "high": list(eqmod.HIGH_CUT)}}

    def arm_playback(self, samples, owner: str | None = None) -> None:
        """The next PTT sends this recording (16 kHz int16 samples) in place of the microphone; None cancels. With an owner (the connection that is keying) the clip is fed on the
        server's own clock as soon as that connection holds PTT, like a voice keyer: it does not wait for the browser's microphone frames (which are ignored meanwhile)."""
        t, self._player_task = self._player_task, None
        if t is not None and not t.done():
            t.cancel()
        self._player_running = False
        self._play_req = None if samples is None else samples
        if samples is not None and owner is not None:
            self._player_running = True
            self._player_task = asyncio.get_running_loop().create_task(self._player(owner))

    async def _player(self, owner: str) -> None:
        peer = types.SimpleNamespace(conn_id=owner)
        loop = asyncio.get_running_loop()
        try:
            waited = 0.0
            while not self.tx_gate(owner):                               # PTT is being keyed
                await asyncio.sleep(0.02)
                waited += 0.02
                if waited > 3.0:
                    self._play_req = None
                    return
            nxt = loop.time()
            while self.tx_gate(owner):                                   # until the clip has gone out, or PTT is released / lost / times out
                self._mic_frame(peer, bytes(FRAME_BYTES), synthetic=True)
                if self._play_done:
                    return
                nxt += 0.02
                await asyncio.sleep(max(0.0, nxt - loop.time()))
        finally:
            self._player_running = False

    def _play_frame(self) -> bytes:
        """The next 20 ms of the clip as 48 kHz audio (silence once it has ended, after telling the hub so it can release PTT)."""
        n = recmod.FRAME_IN
        if self._play_data is None or self._play_pos >= len(self._play_data):
            if not self._play_done:
                self._play_done = True
                if self.on_playback_done:
                    self.on_playback_done()
            return bytes(FRAME_BYTES)
        chunk = self._play_data[self._play_pos:self._play_pos + n].astype(np.float64)
        self._play_pos += n
        if len(chunk) < n:
            chunk = np.concatenate((chunk, np.zeros(n - len(chunk))))
        return np.clip(np.rint(self._play_up.process(chunk)), -32768, 32767).astype("<i2").tobytes()

    def set_level_test(self, on: bool) -> None:
        """The next PTT (or the one in progress) sends a steady tone at the modem level instead of the microphone: for setting the level by the ALC and power meters."""
        self._test_req = bool(on) and self._fd_tx is not None

    def _test_tone_frame(self) -> bytes:
        n = np.arange(self._test_n, self._test_n + FRAME_SAMPLES)
        self._test_n += FRAME_SAMPLES
        amp = TEST_TONE_PEAK * self.freedv_tx_gain
        return np.clip(np.rint(amp * np.sin(2 * np.pi * TEST_TONE_HZ * n / RATE)), -32768, 32767).astype("<i2").tobytes()

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
        self._tx_primed = True
        self.rx_ms_avg = self.rx_ms_max = self.rx_work_avg = self.rx_work_max = 0.0
        self.rx_slow = 0
        self._levels.update(freedv_on=on, freedv_mode=self.freedv_mode if on else "", freedv_sync=0, freedv_snr=0.0, freedv_spec=[], freedv_offset=0,
                            freedv_hint=None, freedv_afc="", freedv_level=-120, freedv_clip=False)
        self._last_emit = 0.0
        self._level("freedv_on", on)
        log.info("FreeDV %s%s", "on, mode " + self.freedv_mode if on else "off", "")
        self._check_fd_tx()
        if self.on_freedv_state:
            self.on_freedv_state(on, self.freedv_mode if on else "")

    def _check_fd_tx(self) -> None:
        """Tell the listener (FreeDV Reporter) when this station starts or stops transmitting FreeDV."""
        now = self._fd_tx is not None and self._tx_gate_open
        if now != self._fd_tx_reported:
            self._fd_tx_reported = now
            if self.on_freedv_tx:
                self.on_freedv_tx(now)

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
                    if self._tx_gate_open and time.monotonic() - self._gate_seen > GATE_IDLE_S:
                        # BENCH-FOUND on a friend's PC (WebSocket audio): the page sends no microphone frames once PTT is released, so nothing ever told the
                        # server the transmission had ended; the receiver stayed muted and the tuning scope frozen until FreeDV was switched off and on.
                        self._tx_gate_open = False
                    self._check_fd_tx()
                    # a boost must not clip hard, but with FreeDV on the limiter would distort the modem signal the decoder works on (it hard-clips at full scale instead)
                    pcm = apply_gain(await src.read_frame(), self.rx_gain, limit=self.rx_gain > 1.0 and self._fd_rx is None)
                    self._level("audio_rx_level", level_pct(pcm))                # the meter shows what the radio sends (the modem tones)
                    raw_pcm = pcm                                                # the radio's own audio, before FreeDV decodes it (the recorder keeps it for analysis)
                    rec_diag = None
                    if self._fd_rx is not None and self._tx_gate_open:
                        pcm = bytes(FRAME_BYTES)                                 # transmitting: never decode (and replay) our own signal
                        self._rx_hold = RX_HOLD_FRAMES
                    elif self._fd_rx is not None and self._rx_hold > 0:
                        self._rx_hold -= 1                                       # the radio is switching back to receive: nothing to decode yet
                        pcm = bytes(FRAME_BYTES)
                        if self._rx_hold == 0 and hasattr(self._fd_rx, "restart"):
                            try:
                                self._fd_rx.restart()                            # a fresh modem for the first clean receive audio (the tuning is kept)
                            except Exception:
                                log.exception("FreeDV could not restart after a transmission; switching it off")
                                self.set_freedv(False)
                    elif self._fd_rx is not None:                                # FreeDV: listeners get the decoded speech instead
                        try:
                            t0 = time.perf_counter()
                            chain = self._fd_rx
                            # The decoder takes 60-100 ms per modem frame on a small PC (an Atom: 60 ms every 120 ms). Run in the event loop it stalled the whole server (web page, audio to
                            # the listeners, PTT) for that long, eight times a second; in a worker thread (the C library releases the GIL) the loop stays free.
                            out, work = await asyncio.get_running_loop().run_in_executor(self._fd_pool, _decode_timed, chain, pcm)
                            ms = (time.perf_counter() - t0) * 1000.0
                            if self._fd_rx is not chain:                                 # FreeDV was switched off or changed while this frame was being decoded
                                self._rx_q.append(bytes(FRAME_BYTES))
                                self._rx_event.set()
                                continue
                            pcm = out
                            self.rx_ms_avg += (ms - self.rx_ms_avg) * 0.02
                            self.rx_ms_max = max(self.rx_ms_max, ms)
                            self.rx_work_avg += (work - self.rx_work_avg) * 0.02
                            self.rx_work_max = max(self.rx_work_max, work)
                            if ms > SLOW_FRAME_MS:
                                self.rx_slow += 1
                                if self.rx_slow in (1, 10, 100, 1000):
                                    log.warning("FreeDV receive: a 20 ms audio frame took %.0f ms (%.0f ms of it inside the decoder; %d such frames so far; averages: %.1f ms in total, %.1f ms decoding): "
                                                "if the decoding is the long part the PC is too slow for this mode, otherwise something else keeps the server busy", ms, work, self.rx_slow, self.rx_ms_avg, self.rx_work_avg)
                            ch = self._fd_rx
                            rec_diag = (int(ch.sync), float(ch.snr), float(ch.afc.offset) if hasattr(ch, "afc") else 0.0, ms)
                            self._levels.update(freedv_sync=ch.sync, freedv_snr=round(ch.snr, 1))
                            if hasattr(ch, "afc"):                                   # the tuning aid (freedv_tune.py): spectrum, level, where the signal is, what was corrected
                                self._levels.update(freedv_offset=int(round(ch.afc.offset)), freedv_afc=ch.tune_state())
                                if ch.spec_fresh:
                                    self._levels.update(freedv_spec=ch.spec.bands(), freedv_level=int(round(ch.spec.level_db)), freedv_clip=ch.spec.clipping,
                                                        freedv_hint=None if ch.spec.hint is None else int(round(ch.spec.hint)))
                        except Exception:
                            log.exception("FreeDV receive failed; switching it off")
                            self.set_freedv(False)
                    if self.recorder is not None and self.recorder.on and not self._tx_gate_open:
                        self.recorder.frame(pcm, raw_pcm if self._fd_rx is not None else None, rec_diag)      # what the receiver produced, before the equalizer; not while we transmit
                    if self.rx_gate.cfg["on"]:
                        pcm = self.rx_gate.process(pcm)                          # the receive noise gate: the hiss between words
                        self._level("rx_gate_db", round(self.rx_gate.reduction_db, 1))
                    pcm = self.eq_rx.process(pcm)                                # the listeners' equalizer: speech (decoded, when FreeDV is on), never the modem tones
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
                was_fd = False
                while True:
                    fd = self._fd_tx is not None and self._tx_gate_open
                    if was_fd and not fd:
                        self._over_finished()
                    was_fd = fd
                    if fd and not self._tx_primed and len(self._tx_q) >= TX_PREFILL_FRAMES:
                        self._tx_primed = True                                 # the cushion is in place: the tones start (or carry on)
                    if fd and not self._tx_primed:
                        pcm = silence                                          # filling the cushion
                    elif self._tx_q:
                        pcm = self._tx_q.popleft()
                    else:
                        pcm = silence
                        if fd and self._tx_primed and not self._tx_ending:     # ran dry in the middle of an over: a hole in the tones; refill before going on
                            self._tx_primed = False
                            self.tx_underruns += 1
                            self._over_underruns += 1
                            self._level("freedv_tx_underruns", self._over_underruns)
                    if fd and pcm is not silence:
                        self._over_frames += 1
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

    async def drain_freedv_tx(self) -> None:
        """PTT is about to be released. Unkeying at once cut off the end of every FreeDV over: up to a few hundred ms of tones were still queued (the cushion) and the last
        modem frame had not been formed. So: flush the modem (silence completes the last frame), wait until the queue has gone to the radio (at most TX_DRAIN_MAX_S), then
        a short tail for the sound card's own buffer. Nothing happens for ordinary voice or when FreeDV is off."""
        ch = self._fd_tx
        if ch is None or not self._tx_gate_open or self._pump_task is None:
            return
        self._tx_ending = True
        self._tx_primed = True                                   # fewer frames than the cushion (a short over, or a refill in progress) must still go out
        try:
            pending: collections.deque[bytes] = collections.deque()      # the flushed frames wait here and join the queue as it empties: appending them to a full queue would evict the oldest tones
            try:
                pending.extend(ch.flush())
            except Exception:
                log.exception("FreeDV could not flush the last modem frame")
            loop = asyncio.get_running_loop()
            end = loop.time() + TX_DRAIN_MAX_S
            while (self._tx_q or pending) and loop.time() < end:
                while pending and len(self._tx_q) < self._tx_q.maxlen:
                    self._tx_q.append(pending.popleft())
                await asyncio.sleep(0.02)
            if pending:                                                    # out of time: these frames are lost, and the log says so
                self.tx_dropped += len(pending)
                self._over_dropped += len(pending)
            elif not self._tx_q:
                await asyncio.sleep(TX_TAIL_S)
        finally:
            self._tx_ending = False

    def _over_finished(self) -> None:
        """A FreeDV transmission ended: say in the log whether the tones went out without holes (the thing to look at when others cannot decode us)."""
        if self._over_underruns or self._over_dropped:
            log.warning("FreeDV transmit: the tones had %d hole(s) and %d frame(s) thrown away in %d frames (%.1f s); the audio reaching the server was too uneven (network/Wi-Fi?) or the PC too slow (slowest step %.0f ms)",
                        self._over_underruns, self._over_dropped, self._over_frames, self._over_frames * FRAME_SAMPLES / RATE, self._tx_proc_ms_max)
        else:
            log.info("FreeDV transmit: %d frames, no holes (slowest step %.0f ms)", self._over_frames, self._tx_proc_ms_max)

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

    def _mic_frame(self, peer: Peer, pcm: bytes, synthetic: bool = False) -> None:
        """One 20 ms mic frame from a peer (either transport). THE GATE: it only passes while this peer owns an active transmission. (synthetic: the recording player's own frame.)"""
        if self._player_running and not synthetic:
            return                                           # a recording is being sent: the microphone is not used
        self.frames_in += 1
        if self.tx_gate(peer.conn_id):
            self._gate_seen = time.monotonic()
            if not self._tx_gate_open:
                self._tx_q.clear()                       # never send stale audio from before PTT
                self._tx_gate_open = True
                self._tx_owner = peer.conn_id
                self._tx_primed = self._fd_tx is None    # FreeDV: wait for the cushion before the tones start
                self._tx_ending = False
                self._test_active, self._test_req, self._test_n = self._test_req and self._fd_tx is not None, False, 0
                self.eq_tx.reset()
                self.tx_dyn.reset()
                self._play_active = self._play_req is not None
                self._play_data, self._play_req, self._play_pos, self._play_done = self._play_req, None, 0, False
                self._play_up = recmod.Interpolator() if self._play_active else None
                self._over_underruns = self._over_frames = self._over_dropped = 0
                self._tx_proc_ms_max = 0.0
                self._level("freedv_tx_underruns", 0)
                if self._fd_tx is not None:              # a fresh modem for every transmission: nothing left over from the last one
                    self._fd_tx.close()
                    self._fd_tx = freedv.TxChain(self.freedv_mode, self.freedv_tx_gain)
            if self._fd_tx is not None and self._test_active:
                if self._test_n < TEST_TONE_MAX_S * RATE:           # level test: a steady tone at the modem level, one frame for every microphone frame
                    tone = self._test_tone_frame()
                    self._tx_q.append(tone)
                    self._level("audio_tx_level", level_pct(tone))
                return
            pcm = self._play_frame() if self._play_active else self.eq_tx.process(pcm)      # a recording is sent as it is (no equalizer) in place of the microphone
            # the operator's equalizer, on the microphone speech (before the modem, or before the limiter)
            dyn_on = self.tx_dyn.active and not self._play_active           # the leveler and limiter, after the mic gain; a recording is sent as it is
            if self._fd_tx is not None:
                # FreeDV: the microphone speech becomes modem tones. The mic gain still applies; the limiter does not (it would distort the
                # tones), the modem output level (freedv.tx_level_db) takes its place.
                try:
                    t0 = time.perf_counter()
                    tones = self._fd_tx.process(self.tx_dyn.process(apply_gain(pcm, self.tx_gain)) if dyn_on else apply_gain(pcm, self.tx_gain))
                    if dyn_on:
                        self._level("tx_lev_db", self.tx_dyn.lev_db)
                        self._level("tx_lim_db", self.tx_dyn.lim_db)
                        self._level("tx_gate_db", round(self.tx_dyn.gate.reduction_db, 1))
                    self._tx_proc_ms_max = max(self._tx_proc_ms_max, (time.perf_counter() - t0) * 1000.0)
                    for tone in tones:
                        if len(self._tx_q) == self._tx_q.maxlen:
                            self.tx_dropped += 1
                            self._over_dropped += 1
                            if self.tx_dropped == 1 or self.tx_dropped % 100 == 0:
                                log.warning("FreeDV transmit: the tone queue overflowed, %d frame(s) dropped so far (the sending side stalled)", self.tx_dropped)
                        self._tx_q.append(tone)
                        self._level("audio_tx_level", level_pct(tone))
                except Exception:
                    log.exception("FreeDV transmit failed")
                return
            if dyn_on:
                pcm = apply_gain(self.tx_dyn.process(apply_gain(pcm, self.tx_gain)), 1.0, limit=True)
                self._level("tx_lev_db", self.tx_dyn.lev_db)
                self._level("tx_lim_db", self.tx_dyn.lim_db)
                self._level("tx_gate_db", round(self.tx_dyn.gate.reduction_db, 1))
            else:
                pcm = apply_gain(pcm, self.tx_gain, limit=True)
            self._tx_q.append(pcm)
            self._level("audio_tx_level", level_pct(pcm))
        elif not self._tx_gate_open or peer.conn_id == self._tx_owner:
            # Only the peer that opened the gate (or nobody, when it is already closed) may close it: a second browser that is connected but muted still sends
            # silent frames, and those used to close the gate every 20 ms and restart the modem in the middle of the owner's over.
            self._tx_gate_open = False
            self._level("audio_tx_level", 0)
            self._level("tx_lev_db", 0.0)
            self._level("tx_lim_db", 0.0)

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
