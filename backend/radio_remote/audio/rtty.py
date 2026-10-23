"""RTTY (radio teletype) receive decoder and AFSK transmit signal for the RTTY tab. Pure numpy; written from the published mode: two tones (mark and space) a fixed shift apart,
5-bit Baudot (ITA2) characters sent as start bit (space), 5 data bits (least significant first), stop bit(s) (mark, 1.5 bit long), the shift characters LTRS and FIGS changing what
the codes mean. The usual amateur settings are 45.45 baud and 170 Hz shift, with the mark tone below the space tone on the audio of an upper-sideband receiver (2125 / 2295 Hz).

Receive: two matched filters (one bit long) at the mark and the space tone give an energy ratio d (+1 mark ... -1 space) eight times per bit. A character starts where d falls through zero
after a stretch of mark; the start, data and stop bits are read at the middle of each bit from that moment, so every character finds its own timing and a transmitter that is a little fast or
slow does not matter. The two tones are found by themselves (the pair of steady tones one shift apart) unless the operator fixes them.
Events returned by feed(): {"k": "text", "s": "CQ "} and, about four times a second, {"k": "stat", "lo", "hi", "mark", "baud", "shift", "level", "locked", "spec"} (spec = 96 bars, 300-3000 Hz).
"""
from __future__ import annotations

import numpy as np

FS = 48000
LO_HZ, HI_HZ = 300, 3000
SPEC_BINS = 96
FFT_N = 8192
SHIFTS = (170, 200, 425, 850)
BAUDS = (45.45, 50.0, 75.0, 100.0)

LTRS = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\x00MXV\x00"                # code 0..31; 0x1B = FIGS and 0x1F = LTRS are handled as shifts
FIGS = "\x003\n- '87\r$4\a,!:(5\")2#6019?&\x00./;\x00"                    # the US teleprinter figures (code 0x0B, a bell, has no character)
SHIFT_FIGS, SHIFT_LTRS = 0x1B, 0x1F
_L = {c: i for i, c in enumerate(LTRS) if c not in "\0" and i not in (SHIFT_FIGS, SHIFT_LTRS)}
_F = {c: i for i, c in enumerate(FIGS) if c not in "\0\a" and i not in (SHIFT_FIGS, SHIFT_LTRS)}
ALLOWED = set(_L) | set(_F) | {"\n", "\r"}


def baudot_encode(text: str) -> list[int]:
    """The 5-bit codes for a text, with shift characters where the kind of character changes. After a space the shift is sent again before the next letter or figure, so that a receiver that
    returns to letters on a space (USOS) and one that does not both read the text correctly."""
    out: list[int] = [SHIFT_LTRS, SHIFT_LTRS]                    # a diddle first: the receiver's shift is known
    shift = "L"
    unknown = False
    for ch in text.upper():
        if ch in ("\n", "\r"):
            out.append(0x08 if ch == "\r" else 0x02)
            continue
        if ch == " ":
            out.append(0x04)
            unknown = True
            continue
        in_l, in_f = ch in _L, ch in _F
        if not in_l and not in_f:
            continue
        want = "L" if in_l else "F"
        if in_l and in_f:                                        # (a character in both tables: none today)
            want = shift
        if want != shift or unknown:
            out.append(SHIFT_LTRS if want == "L" else SHIFT_FIGS)
            shift = want
        unknown = False
        out.append(_L[ch] if want == "L" else _F[ch])
    return out


def duration_s(text: str, baud: float = 45.45) -> float:
    """Seconds the codes of a text take (7.5 bit times per character), without the lead-in and tail."""
    return len(baudot_encode(text)) * 7.5 / baud


def encode(text: str, baud: float = 45.45, shift: int = 170, lo: float = 2125.0, rate: int = 16000, amp: float = 0.5, rev: bool = False, lead_s: float = 0.4, tail_s: float = 0.3) -> np.ndarray:
    """The AFSK audio of a text (float32 in -1..1 at `rate`): a continuous-phase sine switching between the mark and the space tone. `lo` is the lower tone; mark = lower unless rev."""
    mark, space = (lo + shift, lo) if rev else (lo, lo + shift)
    bit = rate / baud
    bits: list[int] = []                                         # 1 = mark, 0 = space, one entry per half bit (the stop bit is 1.5 bit)
    for code in baudot_encode(text):
        bits += [0, 0]
        for i in range(5):
            b = (code >> i) & 1
            bits += [b, b]
        bits += [1, 1, 1]
    n_half = bit / 2
    n = int(round(len(bits) * n_half))
    idx = np.minimum((np.arange(n) / n_half).astype(int), max(len(bits) - 1, 0))
    tone = np.where(np.array(bits, dtype=np.int8)[idx] == 1, mark, space) if bits else np.zeros(0)
    phase = 2 * np.pi * np.cumsum(tone) / rate
    body = np.sin(phase) * amp
    lead = np.sin(2 * np.pi * mark * np.arange(int(lead_s * rate)) / rate) * amp
    # keep the phase continuous from the lead-in into the body
    if len(lead):
        lead_phase = 2 * np.pi * mark * len(lead) / rate
        body = np.sin(phase + lead_phase) * amp
    tail_n = int(tail_s * rate)
    tail_ph = (phase[-1] if len(phase) else 0.0) + (2 * np.pi * mark * len(lead) / rate if len(lead) else 0.0)
    tail = np.sin(tail_ph + 2 * np.pi * mark * np.arange(tail_n) / rate) * amp
    return np.concatenate([lead, body, tail]).astype(np.float32)


class RttyRx:
    def __init__(self, baud: float = 45.45, shift: int = 170, lo: float | None = None, rev: bool = False, usos: bool = True):
        self.baud, self.shift, self.rev, self.usos = float(baud), int(shift), bool(rev), bool(usos)
        self.fixed_lo = lo
        self.lo = float(lo or 2125.0)
        self._setup()
        self._buf = np.zeros(0, dtype=np.float32)
        self._spec_buf = np.zeros(0, dtype=np.float32)
        self._spec_age = 0
        self._cand = (0.0, 0)
        self._last_spec = [0] * SPEC_BINS
        self._locked = False
        self._lock_until = 0                                     # sample count until which a signal still counts as present after the last time the two tones were seen
        self._level = 0.0
        self._n = 0                                              # samples fed
        self._stat_at = 0
        # the decision stream
        self._d: list[float] = []                                # d per hop (the last few hundred)
        self._k0 = 0                                             # hop index of self._d[0]
        self._mark_run = 0
        self._busy_until = -1.0                                  # hop index before which no new start is looked for
        self._pending: list[float] = []                          # start times (in hops, fractional) of characters waiting for their bits
        self._shift_state = "L"
        self._wrote = False

    def _setup(self) -> None:
        self._T = FS / self.baud                                 # samples per bit
        self._H = max(8, int(round(self._T / 8)))                # samples per hop (an eighth of a bit)
        self._hp = self._T / self._H                             # hops per bit (about 8)
        self._ph_m = 0.0
        self._ph_s = 0.0
        self._blocks_m = np.zeros(8, dtype=np.complex128)
        self._blocks_s = np.zeros(8, dtype=np.complex128)
        self._nb = 0
        self._hopbuf = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------ settings
    def set_params(self, baud: float | None = None, shift: int | None = None, lo: float | None = "keep", rev: bool | None = None, usos: bool | None = None) -> None:
        if baud is not None and float(baud) != self.baud:
            self.baud = float(baud)
            self._setup()
            self._d, self._pending = [], []
        if shift is not None:
            self.shift = int(shift)
        if rev is not None:
            self.rev = bool(rev)
        if usos is not None:
            self.usos = bool(usos)
        if lo != "keep":
            self.fixed_lo = float(lo) if lo else None
            if lo:
                self.lo = float(lo)
                self._locked = True

    @property
    def mark_hz(self) -> float:
        return self.lo + self.shift if self.rev else self.lo

    @property
    def space_hz(self) -> float:
        return self.lo if self.rev else self.lo + self.shift

    # ------------------------------------------------------------ feed
    def feed(self, pcm: bytes) -> list[dict]:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        events: list[dict] = []
        self._n += len(x)
        self._look(x)
        x = np.concatenate([self._hopbuf, x])
        n = (len(x) // self._H) * self._H
        self._hopbuf = x[n:]
        if n == 0:
            return events
        x = x[:n].astype(np.float64)
        t = np.arange(n)
        zm = x * np.exp(-1j * (self._ph_m + 2 * np.pi * self.mark_hz * t / FS))
        zs = x * np.exp(-1j * (self._ph_s + 2 * np.pi * self.space_hz * t / FS))
        self._ph_m = (self._ph_m + 2 * np.pi * self.mark_hz * n / FS) % (2 * np.pi)
        self._ph_s = (self._ph_s + 2 * np.pi * self.space_hz * n / FS) % (2 * np.pi)
        bm = zm.reshape(-1, self._H).sum(axis=1)
        bs = zs.reshape(-1, self._H).sum(axis=1)
        for a, b in zip(bm, bs):
            self._blocks_m = np.roll(self._blocks_m, -1)
            self._blocks_s = np.roll(self._blocks_s, -1)
            self._blocks_m[-1], self._blocks_s[-1] = a, b
            em = abs(self._blocks_m.sum()) ** 2
            es = abs(self._blocks_s.sum()) ** 2
            tot = em + es
            self._level = 0.97 * self._level + 0.03 * tot
            d = (em - es) / (tot + 1e-12) if tot > 1e-9 else 0.0
            self._hop(d, events)
        if self._n - self._stat_at >= FS // 4:
            self._stat_at = self._n
            events.append({"k": "stat", "lo": int(round(self.lo)), "hi": int(round(self.lo + self.shift)), "mark": int(round(self.mark_hz)), "baud": self.baud, "shift": self.shift,
                           "level": round(float(min(1.0, (self._level ** 0.5) * 20)), 3), "locked": self._locked, "spec": self._last_spec})
        return events

    # ------------------------------------------------------------ spectrum and tuning
    def _look(self, x: np.ndarray) -> None:
        self._spec_buf = np.concatenate([self._spec_buf, x])[-FFT_N:]
        self._spec_age += len(x)
        if self._spec_age < FS // 4 or len(self._spec_buf) < FFT_N:
            return
        self._spec_age = 0
        p = np.abs(np.fft.rfft(self._spec_buf * np.hanning(FFT_N))) ** 2
        f = np.fft.rfftfreq(FFT_N, 1 / FS)
        band = (f >= LO_HZ) & (f <= HI_HZ)
        pb, fb = p[band], f[band]
        edges = np.linspace(0, len(pb), SPEC_BINS + 1).astype(int)
        bars = np.array([pb[a:b].max() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
        db = 10 * np.log10(bars + 1e-12)
        self._last_spec = [int(v) for v in np.clip((db - np.median(db)) * (255 / 40.0), 0, 255)]
        med = float(np.median(pb)) + 1e-12
        df = fb[1] - fb[0]
        k = int(round(self.shift / df))
        n = len(pb)
        if n <= k + 2:
            return
        sm = np.maximum.reduce([pb[1:-1], pb[:-2], pb[2:]])                        # a tone may sit between two bins
        a, b = sm[: n - 2 - k], sm[k: n - 2]                                      # the same bins and the bins one shift higher
        both = np.minimum(a, b)
        i = int(np.argmax(both))
        pair_ok = both[i] / med > 40
        if pair_ok:
            self._lock_until = self._n + 4 * FS                                  # the characters are not all in view in every look: a signal stays "present" for a few seconds
            # the tone frequencies to a fraction of a bin: the centre of the power of each tone's peak
            def centre(j: int) -> float:
                a0, b0 = max(0, j - 6), min(n, j + 7)
                w = pb[a0:b0]
                return float((fb[a0:b0] * w).sum() / (w.sum() + 1e-12))
            lo = 0.5 * (centre(1 + i) + centre(1 + i + k) - self.shift)
        self._locked = self._n < self._lock_until or self.fixed_lo is not None
        if self.fixed_lo is None and pair_ok:
            if abs(lo - self.lo) <= 12:
                self.lo = 0.8 * self.lo + 0.2 * lo
                self._cand = (0.0, 0)
            else:
                c, m = self._cand
                self._cand = (lo, m + 1) if abs(lo - c) < 12 else (lo, 1)
                if self._cand[1] >= 2:
                    self.lo, self._cand = lo, (0.0, 0)

    # ------------------------------------------------------------ the bits
    def _hop(self, d: float, events: list) -> None:
        self._d.append(d)
        k = self._k0 + len(self._d) - 1                                          # index of this hop
        if len(self._d) > 400:
            del self._d[:100]
            self._k0 += 100
        prev = self._d[-2] if len(self._d) >= 2 else d
        if d > 0.3:
            self._mark_run += 1                                                  # a stretch of mark: the line is idle or in a stop bit
        elif d < 0:
            if prev >= 0 and k >= self._busy_until and self._mark_run >= 4 and self._locked:
                frac = prev / (prev - d)                                         # where between the two hops the zero is
                self._pending.append(k - 1 + frac)
                self._busy_until = k - 1 + frac + 7.0 * self._hp                 # the character is 6 bits and 1.5 bit of stop: nothing new starts before about 7 bits
            self._mark_run = 0
        # decode the characters whose bits are all in
        done = []
        for t0 in self._pending:
            if k >= t0 + 6.6 * self._hp + 1:
                done.append(t0)
        for t0 in done:
            self._pending.remove(t0)
            self._char(t0, events)

    def _at(self, pos: float) -> float:
        """d at a (fractional) hop index."""
        i = pos - self._k0
        i0 = int(np.floor(i))
        if i0 < 0 or i0 + 1 >= len(self._d):
            return 0.0
        f = i - i0
        return self._d[i0] * (1 - f) + self._d[i0 + 1] * f

    def _char(self, t0: float, events: list) -> None:
        hp = self._hp
        # the zero crossing is half a window (half a bit) after the real start edge: the bit i is read at t0 + (i + 0.5) bits
        if self._at(t0 + 0.5 * hp) >= 0:
            return                                                               # not a start bit
        code = 0
        for i in range(5):
            if self._at(t0 + (i + 1.5) * hp) > 0:
                code |= 1 << i
        if self._at(t0 + 6.5 * hp) <= 0:
            return                                                               # no stop bit: noise, not a character
        self._baudot(code, events)

    def _baudot(self, code: int, events: list) -> None:
        if code == SHIFT_FIGS:
            self._shift_state = "F"
            return
        if code == SHIFT_LTRS:
            self._shift_state = "L"
            return
        ch = (LTRS if self._shift_state == "L" else FIGS)[code]
        if ch in ("\0", "\a", "\x00"):
            return
        if ch == " " and self.usos:
            self._shift_state = "L"                                              # unshift on space
        if ch == "\r":
            return
        if events and events[-1].get("k") == "text":
            events[-1]["s"] += ch
        else:
            events.append({"k": "text", "s": ch})

    def flush(self) -> list[dict]:
        return []


def synth(text: str, baud: float = 45.45, shift: int = 170, lo: float = 2125.0, snr_db: float | None = None, rev: bool = False, lead_s: float = 0.6, tail_s: float = 1.0, seed: int = 1,
          amp: float = 0.4, baud_error: float = 0.0, interleave_idle: bool = False) -> bytes:
    """RTTY as 16-bit 48 kHz audio for tests (noise: snr_db in a 2.4 kHz band; baud_error: a transmitter that is that fraction fast or slow)."""
    sig = encode(text, baud * (1 + baud_error), shift, lo, rate=FS, amp=amp, rev=rev, lead_s=lead_s, tail_s=tail_s)
    if snr_db is not None:
        rng = np.random.default_rng(seed)
        sigma = np.sqrt(((amp ** 2) / 2) / (10 ** (snr_db / 10)) * (FS / 2) / 2400.0)
        sig = sig + rng.normal(0, sigma, len(sig))
    return (np.clip(sig, -1, 1) * 32767).astype("<i2").tobytes()
