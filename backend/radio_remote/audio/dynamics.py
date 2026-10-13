"""Transmit dynamics (noise gate, leveler, limiter) and a receive noise gate.

Noise gate: closes (turns the audio down by "range" dB) when the level stays under the threshold, and opens again at once when it comes back over it. It waits "hold" ms before it starts to
close and closes over the "release" time, so word endings are not chopped; 3 dB of hysteresis keeps it from chattering. It works on 5 ms blocks. Receive: it takes the hiss out of the
pauses between words (also the decoded FreeDV speech). Transmit: it keeps the microphone's room noise out of the signal, and out of the leveler, which would otherwise raise it.
Leveler: a slow gain control that brings the speech toward a target level. It only ever adds gain (0 dB up to "max gain"), follows a louder voice at once and a quieter one slowly (the
"decay" time), and holds its gain during pauses instead of boosting the room noise (a gate, GATE_DB). This evens out a voice that moves around the microphone, so the average power is higher
for the same peaks.
Limiter: keeps the peaks under a ceiling. It reacts within 5 ms blocks (an attack of about 1 ms) and recovers with the "release" time; what still gets over the ceiling is clipped.
All work on 20 ms frames of 48 kHz 16-bit mono audio and add no delay. The transmit stages are applied before the FreeDV modem or the radio's limiter, never to the modem tones.
"""
from __future__ import annotations

import numpy as np

RATE = 48000
FULL = 32768.0
GATE_DB = -55.0            # below this frame level (dBFS RMS) the leveler holds its gain
BLOCK = 240                # the limiter's and the gate's block: 5 ms
BLOCK_MS = 1000.0 * BLOCK / RATE
ATTACK = 48                # samples (1 ms) over which the limiter's gain comes down at the start of a block
MAX_GAIN = (0.0, 24.0)     # leveler max gain, dB
TARGET = (-30.0, -6.0)     # leveler target level, dBFS RMS
DECAY = (50.0, 2000.0)     # leveler decay, ms
CEILING = (-18.0, 0.0)     # limiter ceiling, dBFS
RELEASE = (10.0, 500.0)    # limiter release, ms
GATE_THRESHOLD = (-80.0, -20.0)   # noise gate threshold, dBFS (RMS of 5 ms)
GATE_RANGE = (3.0, 60.0)          # how far the closed gate turns the audio down, dB
GATE_HOLD = (0.0, 1000.0)         # ms the gate stays open after the level fell under the threshold
GATE_RELEASE = (20.0, 2000.0)     # ms the gate takes to close completely
HYSTERESIS_DB = 3.0

DEFAULT_GATE = {"on": False, "threshold": -50.0, "range": 30.0, "hold": 150.0, "release": 250.0}
DEFAULT = {
    "gate": dict(DEFAULT_GATE),
    "leveler": {"on": False, "max_gain": 12.0, "target": -18.0, "decay": 300.0},
    "limiter": {"on": False, "ceiling": -3.0, "release": 100.0},
}
RANGES = {
    "gate": {"threshold": GATE_THRESHOLD, "range": GATE_RANGE, "hold": GATE_HOLD, "release": GATE_RELEASE},
    "leveler": {"max_gain": MAX_GAIN, "target": TARGET, "decay": DECAY},
    "limiter": {"ceiling": CEILING, "release": RELEASE},
}


def _clean_part(part: str, base: dict, given) -> dict:
    if not isinstance(given, dict):
        raise ValueError(f"{part} must be an object")
    out = dict(base)
    fields = RANGES[part]
    for key, v in given.items():
        if key == "on":
            if not isinstance(v, bool):
                raise ValueError(f"{part}.on must be true or false")
            out["on"] = v
        elif key in fields:
            lo, hi = fields[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
                raise ValueError(f"{part}.{key} must be {lo:g}..{hi:g}")
            out[key] = round(float(v), 1)
        else:
            raise ValueError(f"unknown setting {part}.{key}")
    return out


def clean(value) -> dict:
    """Validated settings {"gate": {...}, "leveler": {...}, "limiter": {...}} (missing parts keep the defaults). Raises ValueError."""
    if not isinstance(value, dict):
        raise ValueError("expected {gate, leveler, limiter}")
    if any(p not in DEFAULT for p in value):
        raise ValueError("expected {gate, leveler, limiter}")
    return {p: _clean_part(p, DEFAULT[p], value.get(p, {})) for p in DEFAULT}


class NoiseGate:
    def __init__(self) -> None:
        self.cfg = dict(DEFAULT_GATE)
        self.reset()

    def reset(self) -> None:
        self.gain_db = 0.0          # the gate's current gain: 0 = open, -range = closed
        self._open = True
        self._hold_left = 0.0

    @property
    def reduction_db(self) -> float:
        return -self.gain_db

    def set(self, value) -> None:
        """Change some settings, keeping the rest. Raises ValueError (and changes nothing)."""
        self.cfg = _clean_part("gate", self.cfg, value)

    def process(self, pcm: bytes) -> bytes:
        c = self.cfg
        if not c["on"]:
            return pcm
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        n = len(x)
        if n == 0:
            return pcm
        g_prev = 10.0 ** (self.gain_db / 20.0)
        for s in range(0, n, BLOCK):
            blk = x[s:s + BLOCK]
            level = 20.0 * np.log10(float(np.sqrt(np.mean(blk * blk))) / FULL + 1e-12)
            if level >= c["threshold"]:
                self._open, self._hold_left = True, c["hold"]
            elif self._open and level < c["threshold"] - HYSTERESIS_DB:
                self._hold_left -= BLOCK_MS
                if self._hold_left <= 0:
                    self._open = False
            target = 0.0 if self._open else -c["range"]
            if target > self.gain_db:
                new = target                                                  # opening: within this block (about 5 ms)
            else:
                new = max(target, self.gain_db - c["range"] * BLOCK_MS / c["release"])      # closing: down the whole range over the release time
            g_new = 10.0 ** (new / 20.0)
            x[s:s + BLOCK] = blk * np.linspace(g_prev, g_new, len(blk))
            g_prev, self.gain_db = g_new, new
        return np.clip(np.rint(x), -32768, 32767).astype("<i2").tobytes()

    def state(self) -> dict:
        return dict(self.cfg)


class TxDynamics:
    def __init__(self) -> None:
        self.cfg = clean({})
        self.gate = NoiseGate()
        self.gain_db = 0.0          # the leveler's current gain
        self._primed = False
        self.lim_gain = 1.0         # the limiter's current gain (linear)
        self.lev_db = 0.0           # for the meters: the leveler's gain and the limiter's reduction in the last frame
        self.lim_db = 0.0

    @property
    def active(self) -> bool:
        return self.cfg["gate"]["on"] or self.cfg["leveler"]["on"] or self.cfg["limiter"]["on"]

    def set(self, value) -> None:
        """Change some settings, keeping the rest: {"leveler": {"on": True}} alone switches the leveler on and leaves its numbers as they are. Raises ValueError (and changes nothing)."""
        if not isinstance(value, dict) or any(p not in self.cfg for p in value):
            raise ValueError("expected {gate, leveler, limiter}")
        merged = {p: _clean_part(p, self.cfg[p], value[p]) if p in value else dict(self.cfg[p]) for p in self.cfg}
        self.cfg = merged
        self.gate.cfg = dict(merged["gate"])

    def reset(self) -> None:
        self.gain_db, self._primed, self.lim_gain, self.lev_db, self.lim_db = 0.0, False, 1.0, 0.0, 0.0
        self.gate.reset()

    def state(self) -> dict:
        return {k: dict(v) for k, v in self.cfg.items()}

    def meters(self) -> dict:
        return {"lev": round(self.lev_db, 1), "lim": round(self.lim_db, 1), "gate": round(self.gate.reduction_db, 1)}

    # ------------------------------------------------------------------ frames
    def process(self, pcm: bytes) -> bytes:
        if not self.active:
            return pcm
        lv, lm = self.cfg["leveler"], self.cfg["limiter"]
        if self.cfg["gate"]["on"]:
            pcm = self.gate.process(pcm)
        if not lv["on"] and not lm["on"]:
            return pcm
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        n = len(x)
        if n == 0:
            return pcm
        dt = n / RATE
        if lv["on"]:
            rms = float(np.sqrt(np.mean(x * x)))
            level_db = 20.0 * np.log10(rms / FULL + 1e-12)
            if level_db > GATE_DB:
                want = float(np.clip(lv["target"] - level_db, 0.0, lv["max_gain"]))
                if not self._primed:
                    self.gain_db, self._primed = want, True                # the first words of an over are brought to level at once
                elif want < self.gain_db:
                    self.gain_db = want                                    # a louder voice: at once
                else:
                    self.gain_db += (want - self.gain_db) * (1.0 - np.exp(-dt / (lv["decay"] / 1000.0)))      # a quieter one: slowly
            self.gain_db = float(np.clip(self.gain_db, 0.0, lv["max_gain"]))
            g_new, g_old = 10.0 ** (self.gain_db / 20.0), 10.0 ** (self.lev_db / 20.0)
            x = x * np.linspace(g_old, g_new, n)                           # a ramp over the frame: no steps in the gain
            self.lev_db = self.gain_db
        if lm["on"]:
            ceil = FULL * 10.0 ** (lm["ceiling"] / 20.0)
            rel = 1.0 - np.exp(-(BLOCK / RATE) / (lm["release"] / 1000.0))
            g = self.lim_gain
            worst = 1.0
            for s in range(0, n, BLOCK):
                blk = x[s:s + BLOCK]
                need = min(1.0, ceil / (float(np.abs(blk).max()) + 1e-9))
                if need < g:
                    new = need                                              # attack: down at once (over the first millisecond)
                    ramp = np.concatenate((np.linspace(g, new, min(ATTACK, len(blk))), np.full(max(0, len(blk) - ATTACK), new)))[:len(blk)]
                else:
                    new = min(need, g + (1.0 - g) * rel)                    # release: back up slowly
                    ramp = np.linspace(g, new, len(blk))
                x[s:s + BLOCK] = blk * ramp
                g = new
                worst = min(worst, g)
            self.lim_gain = g
            self.lim_db = float(-20.0 * np.log10(worst))
            x = np.clip(x, -ceil, ceil)                                      # what still gets over (inside the first millisecond) is clipped
        return np.clip(np.rint(x), -32768, 32767).astype("<i2").tobytes()
