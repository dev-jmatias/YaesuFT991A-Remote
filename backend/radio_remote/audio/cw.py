"""CW (Morse) receive decoder for the CW tab. Pure numpy, no other dependencies; written from the Morse timing rules (dot = 1 unit, dash = 3, gaps of 1 / 3 / 7 units).

Audio comes in as 48 kHz 16-bit mono (20 ms frames, any size works). The chain:
  1. mix the chosen pitch down to 0 Hz and average 1 ms blocks (a first low-pass; other tones 1 kHz away are nulled)
  2. average 6 ms of those blocks (a second low-pass, about 160 Hz wide) and take the size: the envelope, one value per millisecond
  3. an adaptive threshold with hysteresis between the noise floor and the peak turns the envelope into key-down / key-up times
  4. the dot length is learnt from the marks as they arrive, so any speed from about 5 to 50 WPM follows without a setting
  5. marks become dots and dashes, gaps end a letter or a word, letters come out as text
The pitch is found by itself (the strongest steady tone between 300 and 1100 Hz) unless the operator fixes it.
Events returned by feed(): {"k": "text", "s": "CQ "} and, about four times a second, {"k": "stat", "wpm", "hz", "level", "locked", "spec"} (spec = 64 bars, 300-1100 Hz, 0..255 for the page's mini spectrum).
"""
from __future__ import annotations

import numpy as np

FS = 48000
BLOCK = FS // 1000                 # samples per millisecond
SMOOTH = 6                         # ms averaged for the envelope
LO_HZ, HI_HZ = 300, 1100           # the pitch search range
SPEC_BINS = 64
FFT_N = 8192

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M",
    "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", ".----.": "'", "-.-.--": "!", "-..-.": "/", "-.--.": "(", "-.--.-": ")", ".-...": "&", "---...": ":", "-.-.-.": ";",
    "-...-": "=", ".-.-.": "+", "-....-": "-", "..--.-": "_", ".-..-.": '"', "...-..-": "$", ".--.-.": "@",
    "...-.-": "<SK>", "-.-.-": "<KA>", "...-.": "<SN>", "........": "<ERR>",
}
CODE = {v: k for k, v in MORSE.items() if len(v) == 1}


def morse_of(text: str) -> str:
    """The dots and dashes of a text, letters separated by a space and words by ' / ' (used by the tests and the keyer's time estimate)."""
    words = []
    for w in text.upper().split():
        words.append(" ".join(CODE[c] for c in w if c in CODE))
    return " / ".join(x for x in words if x)


def duration_units(text: str) -> int:
    """How many dot lengths the text takes to send (a dot is 1, a dash 3, 1 between marks, 3 between letters, 7 between words)."""
    total = 0
    words = [w for w in text.upper().split() if any(c in CODE for c in w)]
    for wi, w in enumerate(words):
        letters = [CODE[c] for c in w if c in CODE]
        for li, code in enumerate(letters):
            total += sum(1 if s == "." else 3 for s in code) + (len(code) - 1)
            if li < len(letters) - 1:
                total += 3
        if wi < len(words) - 1:
            total += 7
    return total


class CwRx:
    def __init__(self, hz: float | None = None, wpm: float | None = None):
        self.fixed_hz = hz                                   # None: find the pitch by itself
        self.fixed_wpm = wpm                                 # None: learn the speed
        self.hz = float(hz or 700)
        self._phase = 0.0
        self._tail = np.zeros(SMOOTH - 1, dtype=np.complex128)
        self._buf = np.zeros(0, dtype=np.float32)            # audio waiting for a whole millisecond
        self._spec_buf = np.zeros(0, dtype=np.float32)
        self._spec_age = 0
        self._cand = (0.0, 0)                                # a new pitch candidate and how many looks in a row it was seen
        self._last_spec = [0] * SPEC_BINS
        self._locked = False
        # the key
        self._peak = 0.0
        self._floor = 0.0
        self._down = False
        self._since = 0                                      # ms in the current state
        self._dot = 60.0                                     # ms: the learnt dot length
        self._thr = 120.0                                    # ms: marks at least this long are dashes
        self._marks: list[float] = []                        # the last marks, to learn the speed from
        self._seen = 0                                       # marks heard since the last long silence
        self._cur: list[float] = []                          # the marks of the letter being received
        self._log: list[tuple[str, float]] = []              # the first marks and gaps of a transmission (see _leave_early)
        self._early = wpm is None                      # still learning the speed
        self._pend = 0
        self._gap_len = 0
        self._space_done = 0                                 # 0 nothing emitted yet for this gap, 1 letter ended, 2 word space written
        self._ms = 0
        self._stat_at = 0
        self._wrote = False                                  # something was written since the last long silence

    # ------------------------------------------------------------ settings
    def set_pitch(self, hz: float | None) -> None:
        self.fixed_hz = float(hz) if hz else None
        if hz:
            self.hz = float(hz)
            self._locked = True

    def set_wpm(self, wpm: float | None) -> None:
        self.fixed_wpm = float(wpm) if wpm else None
        if wpm:
            self._dot = 1200.0 / float(wpm)
            self._thr = 2 * self._dot
            self._early = False
        else:
            self._early = True

    @property
    def wpm(self) -> float:
        return 1200.0 / (1200.0 / self.fixed_wpm if self.fixed_wpm else self._dot)

    # ------------------------------------------------------------ feed
    def feed(self, pcm: bytes) -> list[dict]:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        events: list[dict] = []
        self._pitch_search(x, events)
        x = np.concatenate([self._buf, x])
        n = (len(x) // BLOCK) * BLOCK
        self._buf = x[n:]
        if n == 0:
            return events
        x = x[:n]
        t = (self._phase + np.arange(n)) * (2 * np.pi * self.hz / FS)
        z = x * np.exp(-1j * t)
        self._phase = (self._phase + n) % (FS / max(self.hz, 1.0) * 1000)      # keep the phase small: it only has to stay continuous
        blocks = z.reshape(-1, BLOCK).mean(axis=1)                              # one complex value per millisecond
        w = np.concatenate([self._tail, blocks])
        self._tail = w[-(SMOOTH - 1):]
        env = np.abs(np.convolve(w, np.ones(SMOOTH) / SMOOTH, mode="valid"))    # len(blocks) values
        for v in env:
            self._step(float(v), events)
        return events

    # ------------------------------------------------------------ pitch
    def _pitch_search(self, x: np.ndarray, events: list) -> None:
        self._spec_buf = np.concatenate([self._spec_buf, x])[-FFT_N:]
        self._spec_age += len(x)
        if self._spec_age < FS // 4 or len(self._spec_buf) < FFT_N:
            return
        self._spec_age = 0
        p = np.abs(np.fft.rfft(self._spec_buf * np.hanning(FFT_N))) ** 2
        f = np.fft.rfftfreq(FFT_N, 1 / FS)
        band = (f >= LO_HZ) & (f <= HI_HZ)
        pb, fb = p[band], f[band]
        # 64 bars for the page: the loudest bin in each slice of the band, on a dB scale against the weakest
        edges = np.linspace(0, len(pb), SPEC_BINS + 1).astype(int)
        bars = np.array([pb[a:b].max() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
        db = 10 * np.log10(bars + 1e-12)
        lo = np.median(db)
        self._last_spec = [int(v) for v in np.clip((db - lo) * (255 / 40.0), 0, 255)]
        i = int(np.argmax(pb))
        peak, med = pb[i], float(np.median(pb)) + 1e-12
        strong = peak / med > 30                                                # a steady tone well above the noise
        if self.fixed_hz is None and strong:
            hz = float(fb[i])
            if abs(hz - self.hz) <= 25:
                self.hz = 0.8 * self.hz + 0.2 * hz
                self._cand = (0.0, 0)
            elif not self._locked:                                              # nothing was being followed: go straight to the first tone found
                self.hz, self._cand = hz, (0.0, 0)
            else:
                c, n = self._cand
                self._cand = (hz, n + 1) if abs(hz - c) < 25 else (hz, 1)
                if self._cand[1] >= 3:                                          # the same new tone three looks in a row: go there
                    self.hz, self._cand = hz, (0.0, 0)
        self._locked = bool(strong)

    # ------------------------------------------------------------ the key
    def _step(self, v: float, events: list) -> None:
        self._ms += 1
        # the loudest recent value decays slowly; the noise level is the average of the key-up values (slowly followed)
        self._peak = max(v, self._peak * 0.9985)
        warm = self._ms < 300                                                    # the first 0.3 s only measure the noise
        if not self._down:
            self._floor += (v - self._floor) * (0.02 if warm else 0.0007)
        have = not warm and self._peak > 4.0 * max(self._floor, 1e-5) and self._peak > 0.002
        hi = self._floor + 0.55 * (self._peak - self._floor)
        lo = self._floor + 0.35 * (self._peak - self._floor)
        if not have:
            self._pend = 0
            if self._down:                                                       # the signal went away in the middle of a mark
                self._down = False
                self._end_mark(self._since, events)
                self._since = 0
        elif not self._down:
            if v > hi:
                self._down = True
                self._pend = 0
                self._gap_len = self._since                                      # the gap that just ended
                self._gap_end(self._since)
                self._since = 0
        else:
            self._pend = self._pend + 1 if v < lo else 0
            if self._pend >= max(8, int(0.3 * self._dot)):                       # a short dip inside a mark is not a gap
                self._down = False
                self._end_mark(self._since - self._pend, events)
                self._since = self._pend
                self._pend = 0
        self._since += 1
        if not self._down:
            self._gap_run(events)
        if self._ms - self._stat_at >= 250:
            self._stat_at = self._ms
            events.append({"k": "stat", "wpm": round(self.wpm, 1), "hz": int(round(self.hz)), "level": round(min(1.0, self._peak * 8), 3), "locked": self._locked and have,
                           "spec": self._last_spec})

    def _unit(self) -> float:
        return 1200.0 / self.fixed_wpm if self.fixed_wpm else self._dot

    def _learn(self) -> None:
        """Dot length and the dot/dash limit from the recent marks: two groups (short and long) found by a small 2-means on the logarithms."""
        m = np.log(np.array(self._marks[-32:]))
        lo, hi = float(m.min()), float(m.max())
        if hi - lo < 0.35:                                       # all about the same length: all dots (or all dashes: cannot tell, dots are the safer guess)
            self._dot = float(np.exp(m.mean()))
            self._thr = 2.0 * self._dot
            return
        for _ in range(6):
            near_lo = np.abs(m - lo) <= np.abs(m - hi)
            if near_lo.all() or (~near_lo).all():
                break
            lo, hi = float(m[near_lo].mean()), float(m[~near_lo].mean())
        self._dot = float(np.median(np.exp(m[near_lo]))) if not near_lo.all() and near_lo.any() else float(np.exp(lo))
        self._thr = float(np.exp((lo + hi) / 2))

    def _end_mark(self, dur: int, events: list) -> None:
        if dur < (20 if self._early else max(20.0, 0.6 * self._unit())):         # a spike, not a mark (even 50 WPM dots are 24 ms; once the speed is known, less than 0.6 of a dot is noise): it counts as part of the gap
            self._since = self._gap_len + dur
            return
        if not self.fixed_wpm:
            self._marks = (self._marks + [float(dur)])[-32:]
            self._seen += 1
            if len(self._marks) >= 3:
                self._learn()
                self._dot = min(max(self._dot, 20.0), 300.0)
        if self._early:
            self._log.append(("m", float(dur)))
            if self._seen >= 8:
                self._leave_early(events)
        else:
            self._cur.append(float(dur))
            if len(self._cur) > 9:
                self._cur = []                                                   # not Morse
        self._space_done = 0

    def _gap_end(self, dur: int) -> None:
        if dur >= 3000 and not self.fixed_wpm:                                   # a new transmission: learn its speed afresh
            self._marks, self._seen, self._early, self._log = [], 0, True, []
        elif self._early and self._log:
            self._log.append(("g", float(dur)))

    def _decode(self, marks: list[float]) -> str:
        thr = 2 * self._unit() if self.fixed_wpm else self._thr
        return MORSE.get("".join("-" if d >= thr else "." for d in marks), "?")

    def _leave_early(self, events: list) -> None:
        """The first marks of a transmission are only logged: the speed is not known until a few have been heard, and the letters are cut out of the log afterwards."""
        u = self._unit()
        cur: list[float] = []
        out: list = []
        log: list[tuple[str, float]] = []                                        # the log without the blips: a mark far shorter than a dot joins the gap around it
        for kind, d in self._log:
            if kind == "m" and d < 0.6 * u:
                if log and log[-1][0] == "g":
                    log[-1] = ("g", log[-1][1] + d)
                else:
                    log.append(("g", d))
            elif kind == "g" and log and log[-1][0] == "g":
                log[-1] = ("g", log[-1][1] + d)
            else:
                log.append((kind, d))
        for kind, d in log:
            if kind == "m":
                cur.append(d)
            elif d >= 2.2 * u:
                if cur:
                    out.append(self._decode(cur))
                    cur = []
                if d >= 5.5 * u:
                    out.append(" ")
        self._log, self._early, self._cur = [], False, cur
        for t in out:
            self._emit(t, events)

    def _gap_run(self, events: list) -> None:
        if self._early:
            if self._since >= 1500 and self._log:
                self._leave_early(events)                                        # the transmission ended before eight marks: decode what there is
            else:
                return
        u = self._unit()
        if self._space_done == 0 and self._since >= 2.2 * u:
            if self._cur:
                self._emit(self._decode(self._cur), events)
                self._cur = []
            self._space_done = 1
        if self._space_done == 1 and self._since >= 5.5 * u:
            if self._wrote:
                self._emit(" ", events)
                self._wrote = False
            self._space_done = 2

    def _emit(self, s: str, events: list) -> None:
        if s != " ":
            self._wrote = True
        if events and events[-1].get("k") == "text":
            events[-1]["s"] += s
        else:
            events.append({"k": "text", "s": s})

    def flush(self) -> list[dict]:
        events: list[dict] = []
        if self._early and self._log:
            self._leave_early(events)
        if self._cur:
            self._emit(self._decode(self._cur), events)
            self._cur = []
        return events


# ---------------------------------------------------------------- a test signal (also handy for trying the screen)
def synth(text: str, wpm: float = 20.0, hz: float = 700.0, snr_db: float | None = None, lead_s: float = 0.5, tail_s: float = 1.5, jitter: float = 0.0, seed: int = 1, amp: float = 0.4) -> bytes:
    """Morse as 16-bit 48 kHz audio: a keyed tone with soft (5 ms) edges, optional noise (snr_db in a 2.4 kHz band) and optional timing jitter (fraction of a dot, random per element)."""
    rng = np.random.default_rng(seed)
    u = 1.2 / wpm                                              # seconds per dot
    segs: list[tuple[bool, float]] = [(False, lead_s)]
    words = text.upper().split()
    for wi, w in enumerate(words):
        for li, c in enumerate(w):
            code = CODE.get(c)
            if not code:
                continue
            for si, s in enumerate(code):
                d = (1 if s == "." else 3) * u * (1 + jitter * rng.uniform(-1, 1))
                segs.append((True, d))
                if si < len(code) - 1:
                    segs.append((False, u * (1 + jitter * rng.uniform(-1, 1))))
            if li < len(w) - 1:
                segs.append((False, 3 * u * (1 + jitter * rng.uniform(-1, 1))))
        if wi < len(words) - 1:
            segs.append((False, 7 * u * (1 + jitter * rng.uniform(-1, 1))))
    segs.append((False, tail_s))
    key = np.concatenate([np.full(int(round(d * FS)), 1.0 if on else 0.0) for on, d in segs])
    edge = int(0.005 * FS)
    key = np.convolve(key, np.ones(edge) / edge, mode="same")
    t = np.arange(len(key)) / FS
    sig = amp * key * np.sin(2 * np.pi * hz * t)
    if snr_db is not None:
        p_sig = (amp ** 2) / 2 * float(np.mean(key > 0.5)) if np.any(key > 0.5) else (amp ** 2) / 2
        sigma = np.sqrt(p_sig / (10 ** (snr_db / 10)) * (FS / 2) / 2400.0)
        sig = sig + rng.normal(0, sigma, len(sig))
    return (np.clip(sig, -1, 1) * 32767).astype("<i2").tobytes()
