"""Recordings of the received audio: the last few clips, kept on the Pi as 16 kHz mono WAV files, that can be played in the browser and sent back over the air.

What is recorded is what the receiver produced (the decoded speech when FreeDV is on, otherwise the radio's audio), before the equalizer, so that an audio report is faithful.
A new recording replaces the oldest one once MAX_CLIPS exist.

When FreeDV is on, the radio's own audio (the modem signal, before decoding) is saved too, as "<id>.raw.wav" (16 kHz): the file to give to the reference RADE decoder to find out
why a station is badly decoded (sync, SNR, frequency offset, level, gaps).
"""
from __future__ import annotations

import json
import logging
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("radio_remote.recorder")

MAX_CLIPS = 5
MAX_SECONDS = 120.0
RATE_IN = 48000            # the audio service's rate
RATE_CLIP = 16000          # what is stored: plenty for speech, a third of the size
RATIO = RATE_IN // RATE_CLIP
FRAME_IN = RATE_CLIP // 50       # clip samples in one 20 ms frame (320)
_FIR_TAPS = 95


def _lowpass(cutoff_hz: float, rate: float, taps: int = _FIR_TAPS) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff_hz / rate * n) * np.kaiser(taps, 6.0)
    return h / h.sum()


_H = _lowpass(7000.0, RATE_IN)


class Decimator:
    """48 kHz -> 16 kHz, streaming (frames of any length)."""

    def __init__(self) -> None:
        self._tail = np.zeros(_FIR_TAPS - 1)
        self._skip = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate((self._tail, x.astype(np.float64)))
        y = np.convolve(buf, _H, mode="valid")
        self._tail = buf[-(_FIR_TAPS - 1):]
        out = y[self._skip::RATIO]
        self._skip = (self._skip - len(y)) % RATIO
        return out


class Interpolator:
    """16 kHz -> 48 kHz, streaming."""

    def __init__(self) -> None:
        self._tail = np.zeros(_FIR_TAPS - 1)

    def process(self, x: np.ndarray) -> np.ndarray:
        up = np.zeros(len(x) * RATIO)
        up[::RATIO] = x * RATIO
        buf = np.concatenate((self._tail, up))
        self._tail = buf[-(_FIR_TAPS - 1):]
        return np.convolve(buf, _H, mode="valid")


def _to_i16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x), -32768, 32767).astype("<i2")


class Recorder:
    def __init__(self, folder: Path, on_change=None) -> None:
        self.folder = Path(folder)
        self.on_change = on_change or (lambda d: None)
        self._dec: Decimator | None = None
        self._raw_dec: Decimator | None = None
        self._raw_chunks: list[np.ndarray] = []
        self._chunks: list[np.ndarray] = []
        self._n = 0
        self._meta: dict = {}
        self.on = False

    # ------------------------------------------------------------------ index
    @property
    def _index(self) -> Path:
        return self.folder / "index.json"

    def list(self) -> list[dict]:
        try:
            items = json.loads(self._index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [i for i in items if (self.folder / f"{i['id']}.wav").exists()]

    def _write_index(self, items: list[dict]) -> None:
        tmp = self._index.with_suffix(".tmp")
        tmp.write_text(json.dumps(items), encoding="utf-8")
        tmp.replace(self._index)

    def path(self, clip_id: str, raw: bool = False) -> Path | None:
        if not clip_id.isalnum():
            return None
        p = self.folder / f"{clip_id}{'.raw' if raw else ''}.wav"
        return p if p.exists() else None

    def delete(self, clip_id: str) -> bool:
        p = self.path(clip_id)
        if p is None:
            return False
        p.unlink(missing_ok=True)
        (self.folder / f"{clip_id}.raw.wav").unlink(missing_ok=True)
        self._write_index([i for i in self.list() if i["id"] != clip_id])
        return True

    def read(self, clip_id: str) -> np.ndarray | None:
        """The clip's 16 kHz samples (int16), or None."""
        p = self.path(clip_id)
        if p is None:
            return None
        with wave.open(str(p), "rb") as w:
            if w.getframerate() != RATE_CLIP or w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")

    # ---------------------------------------------------------------- recording
    def start(self, meta: dict) -> None:
        if self.on:
            return
        self._dec, self._chunks, self._n, self._meta, self.on = Decimator(), [], 0, dict(meta), True
        self._raw_dec, self._raw_chunks = Decimator(), []
        self._meta["t0"] = time.time()
        self._publish()

    def frame(self, pcm: bytes, raw: bytes | None = None) -> None:
        """One 20 ms frame (48 kHz, 16-bit mono) of what the receiver produced; raw: the radio's audio for the same 20 ms before FreeDV decoding (only while FreeDV is on)."""
        if not self.on:
            return
        if raw is not None:
            self._raw_chunks.append(_to_i16(self._raw_dec.process(np.frombuffer(raw, dtype="<i2"))))
        y = _to_i16(self._dec.process(np.frombuffer(pcm, dtype="<i2")))
        self._chunks.append(y)
        self._n += len(y)
        if self._n >= MAX_SECONDS * RATE_CLIP:
            self.stop()                                                   # the limit: save what there is

    def stop(self) -> dict | None:
        """Ends the recording and saves it as the newest clip (dropping the oldest beyond MAX_CLIPS). Returns the clip's description, or None if nothing was recorded."""
        if not self.on:
            return None
        self.on = False
        chunks, self._chunks, self._dec = self._chunks, [], None
        raw_chunks, self._raw_chunks, self._raw_dec = self._raw_chunks, [], None
        data = np.concatenate(chunks) if chunks else np.zeros(0, "<i2")
        raw = np.concatenate(raw_chunks) if raw_chunks else None
        info = None
        if len(data) >= RATE_CLIP // 2:                                   # less than half a second is not a recording
            info = self._save(data, raw)
        self._publish()
        return info

    @staticmethod
    def _write_wav(path: Path, data: np.ndarray) -> None:
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE_CLIP)
            w.writeframes(data.tobytes())

    def _save(self, data: np.ndarray, raw: np.ndarray | None = None) -> dict:
        self.folder.mkdir(parents=True, exist_ok=True)
        t0 = self._meta.get("t0", time.time())
        cid = time.strftime("%Y%m%d%H%M%S", time.localtime(t0))
        items = self.list()
        while any(i["id"] == cid for i in items):
            cid += "x"
        info = {"id": cid, "t0": t0, "seconds": round(len(data) / RATE_CLIP, 1), **{k: v for k, v in self._meta.items() if k != "t0"}}
        self._write_wav(self.folder / f"{cid}.wav", data)
        if raw is not None and len(raw) >= RATE_CLIP // 2:
            self._write_wav(self.folder / f"{cid}.raw.wav", raw)
            info["raw"] = True
        items.append(info)
        items.sort(key=lambda i: i["t0"])
        while len(items) > MAX_CLIPS:                                     # the oldest makes room
            old = items.pop(0)
            (self.folder / f"{old['id']}.wav").unlink(missing_ok=True)
            (self.folder / f"{old['id']}.raw.wav").unlink(missing_ok=True)
        self._write_index(items)
        log.info("recording saved: %s (%.1f s)", cid, info["seconds"])
        return info

    def _publish(self) -> None:
        self.on_change({"rec": {"on": self.on, "t0": self._meta.get("t0") if self.on else None, "max": MAX_SECONDS},
                        "recordings": self.list()})

    def state(self) -> dict:
        return {"on": self.on, "t0": self._meta.get("t0") if self.on else None, "max": MAX_SECONDS}
