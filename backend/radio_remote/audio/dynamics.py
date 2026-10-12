"""Transmit dynamics: a leveler and a limiter for the operator's speech (never for the FreeDV modem tones, which they are applied before).

Leveler: a slow gain control that brings the speech toward a target level. It only ever adds gain (0 dB up to "max gain"), follows a louder voice at once and a quieter one slowly (the
"decay" time), and holds its gain during pauses instead of boosting the room noise (a gate, GATE_DB). This evens out a voice that moves around the microphone, so the average power is higher
for the same peaks.
Limiter: keeps the peaks under a ceiling. It reacts within 5 ms blocks (an attack of about 1 ms) and recovers with the "release" time; what still gets over the ceiling is clipped.
Both work on 20 ms frames of 48 kHz 16-bit mono audio and add no delay.
"""
from __future__ import annotations

import numpy as np

RATE = 48000
FULL = 32768.0
GATE_DB = -55.0            # below this frame level (dBFS RMS) the leveler holds its gain
BLOCK = 240                # the limiter's block: 5 ms
ATTACK = 48                # samples (1 ms) over which the gain comes down at the start of a block
MAX_GAIN = (0.0, 24.0)     # leveler max gain, dB
TARGET = (-30.0, -6.0)     # leveler target level, dBFS RMS
DECAY = (50.0, 2000.0)     # leveler decay, ms
CEILING = (-18.0, 0.0)     # limiter ceiling, dBFS
RELEASE = (10.0, 500.0)    # limiter release, ms

DEFAULT = {
    "leveler": {"on": False, "max_gain": 12.0, "target": -18.0, "decay": 300.0},
    "limiter": {"on": False, "ceiling": -3.0, "release": 100.0},
}


def clean(value) -> dict:
    """Validated settings {"leveler": {...}, "limiter": {...}} (missing parts keep the defaults). Raises ValueError."""
    if not isinstance(value, dict):
        raise ValueError("expected {leveler, limiter}")
    out = {k: dict(v) for k, v in DEFAULT.items()}
    ranges = {"leveler": {"max_gain": MAX_GAIN, "target": TARGET, "decay": DECAY}, "limiter": {"ceiling": CEILING, "release": RELEASE}}
    for part, fields in ranges.items():
        given = value.get(part, {})
        if not isinstance(given, dict):
            raise ValueError(f"{part} must be an object")
        for key, v in given.items():
            if key == "on":
                if not isinstance(v, bool):
                    raise ValueError(f"{part}.on must be true or false")
                out[part]["on"] = v
            elif key in fields:
                lo, hi = fields[key]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
                    raise ValueError(f"{part}.{key} must be {lo:g}..{hi:g}")
                out[part][key] = round(float(v), 1)
            else:
                raise ValueError(f"unknown setting {part}.{key}")
    return out


class TxDynamics:
    def __init__(self) -> None:
        self.cfg = clean({})
        self.gain_db = 0.0          # the leveler's current gain
        self._primed = False
        self.lim_gain = 1.0         # the limiter's current gain (linear)
        self.lev_db = 0.0           # for the meters: the leveler's gain and the limiter's reduction in the last frame
        self.lim_db = 0.0

    @property
    def active(self) -> bool:
        return self.cfg["leveler"]["on"] or self.cfg["limiter"]["on"]

    def set(self, value) -> None:
        """Change some settings, keeping the rest: {"leveler": {"on": True}} alone switches the leveler on and leaves its numbers as they are. Raises ValueError."""
        if not isinstance(value, dict) or any(p not in self.cfg for p in value):
            raise ValueError("expected {leveler, limiter}")
        merged = {p: dict(self.cfg[p]) for p in self.cfg}
        for p, v in value.items():
            if not isinstance(v, dict):
                raise ValueError(f"{p} must be an object")
            merged[p].update(v)
        self.cfg = clean(merged)

    def reset(self) -> None:
        self.gain_db, self._primed, self.lim_gain, self.lev_db, self.lim_db = 0.0, False, 1.0, 0.0, 0.0

    def state(self) -> dict:
        return {k: dict(v) for k, v in self.cfg.items()}

    def meters(self) -> dict:
        return {"lev": round(self.lev_db, 1), "lim": round(self.lim_db, 1)}

    # ------------------------------------------------------------------ frames
    def process(self, pcm: bytes) -> bytes:
        if not self.active:
            return pcm
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        n = len(x)
        if n == 0:
            return pcm
        dt = n / RATE
        lv, lm = self.cfg["leveler"], self.cfg["limiter"]
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
