"""SSTV (slow-scan television) for the SSTV tab: receive pictures from the radio's audio and make the audio of a picture to transmit.

Everything here is plain numpy, written from the published mode descriptions (the JL Barber N7CXI "Dayton paper" timings and the widely documented VIS header), no third-party
SSTV code. The modes are the common ones: Robot 36 and 72, Martin M1 and M2, Scottie S1, S2 and DX, PD50, PD90, PD120, PD160, PD180 and PD240.

An SSTV picture is frequency modulation of the audio: 1200 Hz is the line sync, 1500 Hz black and 2300 Hz white, everything between is the grey / colour level. A transmission starts
with a calibration header (a 1900 Hz leader, a 1200 Hz break, the leader again, then the VIS code that names the mode, as 30 ms bits of 1100 Hz = 1 and 1300 Hz = 0).

`encode(rgb, mode, fs)` -> float audio.  `SstvRx.feed(pcm, now)` -> events (start / rows / done) as the pictures arrive.  `png_bytes(rgb)` -> a PNG file.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

FS = 48000                       # the audio service's rate: the receiver works at it directly (the narrow pixels of PD120 need the resolution)
FREQ_BIT1, FREQ_SYNC, FREQ_BIT0, FREQ_BLACK, FREQ_LEADER, FREQ_WHITE = 1100.0, 1200.0, 1300.0, 1500.0, 1900.0, 2300.0
FC = 1700.0                      # the receiver mixes the signal down by this much (the middle of 1100..2300)


# ------------------------------------------------------------------------------ the modes
@dataclass
class Seg:
    kind: str                    # "sync", "porch" (a fixed tone), "scan" (picture data)
    ms: float
    freq: float = 0.0            # tone of sync / porch
    chan: str = ""               # scan: R G B Y Cr Cb Y2
    npix: int = 0                # scan: pixels carried (the chroma of Robot 36 / 72 has half the picture width)


@dataclass
class Mode:
    key: str
    name: str
    vis: int
    w: int
    h: int
    family: str                  # "rgb" (Martin, Scottie), "r36", "r72", "pd"
    segs: list = field(default_factory=list)            # the line (Robot 36: even line) in order
    segs_odd: list = field(default_factory=list)        # Robot 36: the odd line
    rows: int = 1                # picture rows one transmitted line (PD: line pair) delivers
    lead_ms: float = 0.0         # Scottie: one extra sync pulse between the header and the first line

    @property
    def line_ms(self) -> float:
        return sum(s.ms for s in self.segs)

    @property
    def lines(self) -> int:
        return self.h // self.rows

    @property
    def seconds(self) -> float:
        """Length of the whole transmission (header included)."""
        return (910.0 + self.lead_ms + self.line_ms * self.lines) / 1000.0

    def sync_at(self, odd: bool = False) -> tuple[float, float, float]:
        """(start in ms from the line start, length, length of the 1500 Hz porch after it) of the sync pulse of a line."""
        segs, t = (self.segs_odd if odd and self.segs_odd else self.segs), 0.0
        for i, s in enumerate(segs):
            if s.kind == "sync":
                nxt = segs[i + 1].ms if i + 1 < len(segs) and segs[i + 1].kind == "porch" else 0.0
                return t, s.ms, nxt
            t += s.ms
        raise ValueError("no sync in the line")


def _rgb_mode(key, name, vis, scan, order, sync, sep, scottie=False, w=320, h=256):
    sy, po = Seg("sync", sync, FREQ_SYNC), lambda ms: Seg("porch", ms, FREQ_BLACK)
    sc = lambda c: Seg("scan", scan, chan=c, npix=w)
    if scottie:                  # separator, G, separator, B, sync, porch, R: the sync is in the middle of the line
        segs = [po(sep), sc("G"), po(sep), sc("B"), sy, po(sep), sc("R")]
    else:                        # sync, porch, G, gap, B, gap, R, gap
        segs = [sy, po(sep)]
        for c in order:
            segs += [sc(c), po(sep)]
    return Mode(key, name, vis, w, h, "rgb", segs, lead_ms=sync if scottie else 0.0)


def _pd_mode(key, name, vis, pixel_ms, w, h):
    scan = lambda c: Seg("scan", pixel_ms * w, chan=c, npix=w)
    return Mode(key, name, vis, w, h, "pd", [Seg("sync", 20.0, FREQ_SYNC), Seg("porch", 2.08, FREQ_BLACK), scan("Y"), scan("Cr"), scan("Cb"), scan("Y2")], rows=2)


def _robot_modes():
    sy, po = Seg("sync", 9.0, FREQ_SYNC), lambda ms, f=FREQ_BLACK: Seg("porch", ms, f)
    y36, c36 = Seg("scan", 88.0, chan="Y", npix=320), lambda c: Seg("scan", 44.0, chan=c, npix=160)
    r36 = Mode("robot36", "Robot 36", 8, 320, 240, "r36",
               [sy, po(3.0), y36, po(4.5, FREQ_BLACK), po(1.5, FREQ_LEADER), c36("Cr")],
               [sy, po(3.0), y36, po(4.5, FREQ_WHITE), po(1.5, FREQ_LEADER), c36("Cb")])
    y72, c72 = Seg("scan", 138.0, chan="Y", npix=320), lambda c: Seg("scan", 69.0, chan=c, npix=160)
    r72 = Mode("robot72", "Robot 72", 12, 320, 240, "r72",
               [sy, po(3.0), y72, po(4.5, FREQ_BLACK), po(1.5, FREQ_LEADER), c72("Cr"), po(4.5, FREQ_WHITE), po(1.5, FREQ_LEADER), c72("Cb")])
    return [r36, r72]


_MODE_LIST = _robot_modes() + [
    _rgb_mode("martin1", "Martin M1", 44, 146.432, "GBR", 4.862, 0.572),
    _rgb_mode("martin2", "Martin M2", 40, 73.216, "GBR", 4.862, 0.572),
    _rgb_mode("scottie1", "Scottie S1", 60, 138.24, "GBR", 9.0, 1.5, scottie=True),
    _rgb_mode("scottie2", "Scottie S2", 56, 88.064, "GBR", 9.0, 1.5, scottie=True),
    _rgb_mode("scottiedx", "Scottie DX", 76, 345.6, "GBR", 9.0, 1.5, scottie=True),
    _pd_mode("pd50", "PD50", 93, 0.286, 320, 256),
    _pd_mode("pd90", "PD90", 99, 0.532, 320, 256),
    _pd_mode("pd120", "PD120", 95, 0.19, 640, 496),
    _pd_mode("pd160", "PD160", 98, 0.382, 512, 400),
    _pd_mode("pd180", "PD180", 96, 0.286, 640, 496),
    _pd_mode("pd240", "PD240", 97, 0.382, 640, 496),
]
MODES = {m.key: m for m in _MODE_LIST}
BY_VIS = {m.vis: m for m in _MODE_LIST}


def mode_list() -> list[dict]:
    return [{"key": m.key, "name": m.name, "w": m.w, "h": m.h, "seconds": round(m.seconds, 1)} for m in _MODE_LIST]


# ------------------------------------------------------------------------------ colour
def rgb_to_ycc(rgb: np.ndarray) -> np.ndarray:
    """(h, w, 3) uint8 RGB -> float (h, w, 3) Y, Cb, Cr in 0..255 (the full-range JPEG conversion)."""
    r, g, b = (rgb[..., i].astype(np.float64) for i in range(3))
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return np.stack([y, 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b, 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b], axis=-1)


def ycc_to_rgb(y, cb, cr) -> np.ndarray:
    r, g, b = y + 1.402 * (cr - 128.0), y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0), y + 1.772 * (cb - 128.0)
    return np.clip(np.rint(np.stack([r, g, b], axis=-1)), 0, 255).astype(np.uint8)


# ------------------------------------------------------------------------------ encoder
def _val_to_freq(v) -> np.ndarray:
    return FREQ_BLACK + np.asarray(v, dtype=np.float64) * ((FREQ_WHITE - FREQ_BLACK) / 255.0)


def _tone(freq: float, ms: float, fs: int, t_end_samples: float) -> tuple[np.ndarray, float]:
    n = int(round(t_end_samples + ms * fs / 1000.0)) - int(round(t_end_samples))
    return np.full(max(n, 0), freq), t_end_samples + ms * fs / 1000.0


def _scan_freqs(vals: np.ndarray, ms: float, fs: int, t0: float) -> tuple[np.ndarray, float]:
    """One scan: len(vals) pixels spread over `ms`; each sample takes the pixel it falls in. t0 is the (fractional) sample position where the scan starts."""
    n = len(vals)
    a, b = int(round(t0)), int(round(t0 + ms * fs / 1000.0))
    idx = np.minimum(((np.arange(a, b) + 0.5 - t0) * n / (ms * fs / 1000.0)).astype(int), n - 1)
    return _val_to_freq(vals)[idx], t0 + ms * fs / 1000.0


def _chan_values(mode: Mode, ycc: np.ndarray, rgb: np.ndarray, line: int):
    """The scan values of a transmitted line: dict chan -> array. Chroma is averaged over the pixel pairs / line pairs the mode shares it between."""
    out = {}
    if mode.family == "rgb":
        for i, c in enumerate("RGB"):
            out[c] = rgb[line, :, i].astype(np.float64)
    elif mode.family == "pd":
        r0, r1 = 2 * line, 2 * line + 1
        out["Y"], out["Y2"] = ycc[r0, :, 0], ycc[r1, :, 0]
        out["Cb"], out["Cr"] = (ycc[r0, :, 1] + ycc[r1, :, 1]) / 2.0, (ycc[r0, :, 2] + ycc[r1, :, 2]) / 2.0
    else:                         # Robot: chroma at half the width; Robot 36 sends Cr on even lines and Cb on odd ones, each shared by the line pair
        half = lambda a: (a[0::2] + a[1::2]) / 2.0
        out["Y"] = ycc[line, :, 0]
        if mode.family == "r72":
            out["Cr"], out["Cb"] = half(ycc[line, :, 2]), half(ycc[line, :, 1])
        elif line % 2 == 0:
            out["Cr"] = half((ycc[line, :, 2] + ycc[min(line + 1, mode.h - 1), :, 2]) / 2.0)
        else:
            out["Cb"] = half((ycc[line - 1, :, 1] + ycc[line, :, 1]) / 2.0)
    return out


def encode(rgb: np.ndarray, mode: Mode | str, fs: int = 16000, amplitude: float = 1.0) -> np.ndarray:
    """The audio (float, +-amplitude) of a whole transmission of an (h, w, 3) uint8 picture: calibration header, VIS code, every line."""
    mode = MODES[mode] if isinstance(mode, str) else mode
    rgb = np.asarray(rgb, dtype=np.uint8)
    if rgb.shape != (mode.h, mode.w, 3):
        raise ValueError(f"{mode.name} needs a {mode.w} x {mode.h} picture, not {rgb.shape[1]} x {rgb.shape[0]}")
    ycc = rgb_to_ycc(rgb)
    out: list[np.ndarray] = []
    parts: list[np.ndarray] = []                                 # frequency per sample, waiting to be turned into audio (done in chunks: a long mode would otherwise need a lot of memory)
    pending = 0
    phase = 0.0
    pos = 0.0                                                    # position in samples (fractional: the sum of the segment times is kept exact, so nothing drifts)

    def flush() -> None:
        nonlocal parts, pending, phase
        if not parts:
            return
        freq = np.concatenate(parts)
        ph = phase + 2.0 * np.pi * np.cumsum(freq) / fs
        phase = float(ph[-1] % (2.0 * np.pi))
        out.append((amplitude * np.sin(ph)).astype(np.float32))
        parts, pending = [], 0

    def add(f: np.ndarray) -> None:
        nonlocal pending
        parts.append(f)
        pending += len(f)
        if pending >= fs:
            flush()

    def tone(freq, ms):
        nonlocal pos
        f, pos = _tone(freq, ms, fs, pos)
        add(f)

    tone(FREQ_LEADER, 300), tone(FREQ_SYNC, 10), tone(FREQ_LEADER, 300), tone(FREQ_SYNC, 30)           # the calibration header
    bits = [(mode.vis >> i) & 1 for i in range(7)]
    bits.append(sum(bits) & 1)                                                                            # even parity
    for b in bits:
        tone(FREQ_BIT1 if b else FREQ_BIT0, 30)
    tone(FREQ_SYNC, 30)
    if mode.lead_ms:
        tone(FREQ_SYNC, mode.lead_ms)
    for line in range(mode.lines):
        vals = _chan_values(mode, ycc, rgb, line)
        for s in (mode.segs_odd if (line % 2 and mode.segs_odd) else mode.segs):
            if s.kind == "scan":
                f, pos = _scan_freqs(vals[s.chan], s.ms, fs, pos)
                add(f)
            else:
                tone(s.freq, s.ms)
    flush()
    return np.concatenate(out)


# ------------------------------------------------------------------------------ PNG
def png_bytes(rgb: np.ndarray) -> bytes:
    """A PNG file of an (h, w, 3) uint8 picture (no library needed)."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


# ------------------------------------------------------------------------------ receiver
def _lowpass(fs: int, cutoff: float, taps: int) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2.0
    h = np.sinc(2.0 * cutoff / fs * n) * np.kaiser(taps, 7.0)
    return h / h.sum()


_LP_TAPS = 161
_LP = _lowpass(FS, 1900.0, _LP_TAPS)           # keeps -600..+600 Hz around the mixing frequency, rejects the image of the mix (2800 Hz and up)
MS = FS // 1000                                # samples per millisecond
CHECK_EVERY = FS // 10                         # look for a header every 100 ms of new audio
IDLE_KEEP = 2 * FS                             # audio kept while no picture is being received
SYNC_GAIN = 0.0                                # how fast the sync pulses correct the tuning offset. 0: only the header's leader measures it (the 1200 Hz pulse reads a few Hz off through the receive filter, which cost more than the drift of a radio over a few minutes)
MAX_MISSES = 8                                 # lines in a row without a sync pulse before the picture is given up
GAP_S = 1.2                                    # a break in the audio longer than this ends the picture (our own transmission, a lost link)


def _freq_of(s: complex | np.ndarray):
    return FC + np.angle(s) * FS / (2.0 * np.pi)


class SstvRx:
    """Streaming receiver. feed(pcm_bytes, now) takes the radio's 48 kHz frames and returns a list of events:
        {"k": "start", "mode": key, "name", "w", "h"}                       a header was found
        {"k": "rows", "y0": int, "rgb": bytes}                              picture rows (rgb: n rows of w x 3 bytes)
        {"k": "done", "complete": bool, "rows": int, "rgb": ndarray}        the picture ended (or was given up)
        {"k": "vis", "code": int}                                           a header for a mode this program does not know
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.p = np.zeros(0, dtype=np.complex64)       # lag-1 products of the mixed signal: the phase of each is the frequency
        self.p0 = 0                                    # absolute sample index of p[0]
        self.n = 0                                     # samples fed so far
        self.hist = np.zeros(_LP_TAPS - 1, dtype=np.complex128)
        self.zlast = 0j
        self.since_check = 0
        self.last_t = 0.0
        self.mode: Mode | None = None
        self.img: np.ndarray | None = None
        self.line = 0                                  # next transmitted line to decode
        self.L0 = 0.0                                  # absolute sample index where that line starts
        self._pulse_off = 0.0
        self.misses = 0
        self.fofs = 0.0                                # how far the tuning of the radio is off: every tone arrives this many Hz high (+) or low (-); measured from the header and the sync pulses
        self.state = "idle"
        self.pend: dict = {}                           # Robot 36: the line of an even/odd pair that is waiting for its partner

    # ---- signal chain
    def _demod(self, x: np.ndarray) -> None:
        n0 = self.n
        ph = -2.0 * np.pi * ((np.arange(n0, n0 + len(x)) * FC / FS) % 1.0)
        z = np.concatenate([self.hist, x * np.exp(1j * ph)])
        self.hist = z[-(_LP_TAPS - 1):]
        y = np.convolve(z, _LP, "valid")                                  # len(x) samples, a constant delay of (taps-1)/2 that every position measured here shares
        prev = np.concatenate([[self.zlast], y[:-1]])
        self.zlast = y[-1]
        self.p = np.concatenate([self.p, (y * np.conj(prev)).astype(np.complex64)])
        self.n += len(x)

    @property
    def p_end(self) -> int:
        return self.p0 + len(self.p)

    def _trim(self, keep_from: int) -> None:
        cut = int(keep_from) - self.p0
        if cut > 0:
            self.p = self.p[cut:]
            self.p0 += cut

    # ---- header
    def _find_vis(self):
        """Look for a calibration header in the recent audio. Returns (vis code, absolute sample index of the end of the header, tuning offset in Hz) or None.
        The header is found with wide tolerances (a radio that is tuned a little off shifts every tone), and the offset is then measured from its leader, which must be 1900 Hz."""
        n = len(self.p) // MS
        if n < 700:
            return None
        P1 = self.p[len(self.p) - n * MS:].reshape(n, MS).astype(np.complex128).sum(axis=1)       # one value per millisecond, aligned to the END of the buffer
        base = self.p_end - n * MS
        F, A = _freq_of(P1), np.abs(P1)
        good = A > 0.15 * np.percentile(A, 97)
        L = (np.abs(F - FREQ_LEADER) < 190) & good
        S = (np.abs(F - FREQ_SYNC) < 190) & good
        frac = lambda m, a, b: m[max(a, 0):max(b, 0)].mean() if b > a else 0.0
        for i in range(min(n - 311, n - 1) - 1, 620, -1):            # candidates for the start of the VIS start bit, latest first
            if not (S[i] and S[i + 1] and (L[i - 1] or L[i - 2]) and not S[i - 1]):          # the first millisecond of 1200 Hz after the leader (the bin of the change itself is neither)
                continue
            if frac(L, i - 290, i - 6) < 0.80 or frac(S, i - 307, i - 303) < 0.5 or frac(L, i - 600, i - 316) < 0.75:
                continue
            lead = F[i - 290:i - 6][L[i - 290:i - 6]]
            off = float(np.median(lead)) - FREQ_LEADER                # the tuning offset, as the leader shows it
            brk = F[i - 309:i - 301][S[i - 309:i - 301]]
            if len(brk) and abs(float(np.median(brk)) - off - FREQ_SYNC) > 110:                # the break must be 700 Hz under the leader, with that same offset
                continue
            bits, ok = [], True
            for k in range(8):
                a = i + 30 + 30 * k + 7
                m = F[a:a + 16].mean() - off
                if abs(m - 1200) < 25:
                    ok = False
                    break
                bits.append(1 if m < 1200 else 0)
            if not ok or i + 300 > n or frac(S, i + 276, i + 296) < 0.6:
                continue
            if sum(bits) % 2:                                          # parity (7 data bits + the parity bit make an even number of ones)
                continue
            code = sum(b << k for k, b in enumerate(bits[:7]))
            return code, base + (i + 300) * MS, off
        return None

    # ---- lines
    def _sync_end(self, expect_end: float, sync_ms: float, porch_ms: float, rad_ms: float):
        """Where the sync pulse of a line ends, found as the place where 1200 Hz (the pulse) turns into 1500 Hz (the porch); None when nothing like a pulse is near the expected place."""
        ls, lp, r = max(int(sync_ms * MS), 8), max(int(porch_ms * MS), 4), int(rad_ms * MS)
        a = int(expect_end) - r - ls - self.p0
        b = int(expect_end) + r + lp - self.p0
        if a < 0 or b > len(self.p):
            return None
        C = np.concatenate([[0], np.cumsum(self.p[a:b].astype(np.complex128))])
        ks = np.arange(ls, 2 * r + ls + 1)                              # candidate END positions, relative to a
        pre = C[ks] - C[ks - ls]
        post = C[np.minimum(ks + lp, len(C) - 1)] - C[ks]
        f_pre = _freq_of(pre) - self.fofs
        e_pre, e_post = np.abs(f_pre - FREQ_SYNC), np.abs(_freq_of(post) - self.fofs - FREQ_BLACK)
        score = e_pre + (0.5 * e_post if porch_ms > 0 else 0.0)
        i = int(np.argmin(score))
        if e_pre[i] > 140 or np.abs(pre[i]) < 0.3 * np.median(np.abs(self.p[a:b])) * ls:
            return None
        self._pulse_off = float(f_pre[i] - FREQ_SYNC)                  # how far off the pulse itself was
        return self.p0 + a + int(ks[i])

    def _scan_values(self, start_ms: float, ms: float, npix: int, L0: float) -> np.ndarray:
        """The 0..1 value of each pixel of a scan that starts start_ms after the line start L0."""
        t0 = L0 + start_ms * MS
        edges = np.rint(t0 + np.arange(npix + 1) * (ms * MS / npix)).astype(np.int64) - self.p0
        seg = self.p[max(int(edges[0]), 0):int(edges[-1]) + 1].astype(np.complex128)
        C = np.concatenate([[0], np.cumsum(seg)])
        e = edges - max(int(edges[0]), 0)
        s = C[e[1:]] - C[e[:-1]]
        return np.clip((_freq_of(s) - self.fofs - FREQ_BLACK) / (FREQ_WHITE - FREQ_BLACK), 0.0, 1.0)

    def _decode_line(self):
        """Decode the next line when the audio for it has arrived. Returns a list of events, or None when more audio is needed."""
        m = self.mode
        odd = bool(self.line % 2) and bool(m.segs_odd)
        segs = m.segs_odd if odd else m.segs
        line_ms = sum(s.ms for s in segs)
        need = self.L0 + (line_ms + 12.0) * MS
        if self.p_end < need:
            return None
        sy_t, sy_len, porch = m.sync_at(odd)
        first = self.line == 0
        end = self._sync_end(self.L0 + (sy_t + sy_len) * MS, sy_len, porch, (13.0 if m.lead_ms else 6.0) if first else 2.5)
        if end is not None:
            self.L0 = end - (sy_t + sy_len) * MS
            self.misses = 0
            self.fofs = float(np.clip(self.fofs + SYNC_GAIN * self._pulse_off, -220.0, 220.0))          # every sync pulse refines the tuning offset (and follows a radio that drifts)
        else:
            self.misses += 1
        vals, t = {}, 0.0
        for s in segs:
            if s.kind == "scan":
                vals[s.chan] = self._scan_values(t, s.ms, s.npix, self.L0)
            t += s.ms
        ev = self._store(vals)
        self.line += 1
        self.L0 += line_ms * MS
        self._trim(self.L0 - 20 * MS)
        if self.misses >= MAX_MISSES:
            ev.append(self._finish(False))
        elif self.line >= m.lines:
            ev.append(self._finish(True))
        return ev

    def _store(self, v: dict) -> list:
        m, img, w = self.mode, self.img, self.mode.w
        px = lambda a: np.rint(a * 255.0)
        if m.family == "rgb":
            row = np.stack([px(v["R"]), px(v["G"]), px(v["B"])], axis=-1).astype(np.uint8)
            img[self.line] = row
            return [self._rows(self.line, 1)]
        if m.family == "pd":
            y0, y1, cr, cb = px(v["Y"]), px(v["Y2"]), px(v["Cr"]), px(v["Cb"])
            img[2 * self.line] = ycc_to_rgb(y0, cb, cr)
            img[2 * self.line + 1] = ycc_to_rgb(y1, cb, cr)
            return [self._rows(2 * self.line, 2)]
        up = lambda a: np.repeat(px(a), 2)[:w]
        if m.family == "r72":
            img[self.line] = ycc_to_rgb(px(v["Y"]), up(v["Cb"]), up(v["Cr"]))
            return [self._rows(self.line, 1)]
        # Robot 36: the chroma of an even line (Cr) and of the next, odd one (Cb) belong to both rows of the pair
        if self.line % 2 == 0:
            self.pend = {"Y": px(v["Y"]), "Cr": up(v["Cr"])}
            return []
        a, cr = self.pend.get("Y"), self.pend.get("Cr")
        if a is None:
            a, cr = px(v["Y"]), np.full(w, 128.0)
        cb = up(v["Cb"])
        img[self.line - 1] = ycc_to_rgb(a, cb, cr)
        img[self.line] = ycc_to_rgb(px(v["Y"]), cb, cr)
        return [self._rows(self.line - 1, 2)]

    def _rows(self, y0: int, n: int) -> dict:
        return {"k": "rows", "y0": y0, "rgb": self.img[y0:y0 + n].tobytes()}

    def _finish(self, complete: bool) -> dict:
        m, img = self.mode, self.img
        rows = self.line if m.family != "pd" else self.line * 2
        if m.family == "r36" and self.line % 2:
            rows -= 1
        ev = {"k": "done", "complete": complete, "rows": int(min(rows, m.h)), "mode": m.key, "rgb": img}
        keep = self.p_end
        self.mode, self.img, self.state, self.misses, self.pend = None, None, "idle", 0, {}
        self._trim(keep - IDLE_KEEP // 4)
        return ev

    # ---- public
    def feed(self, pcm: bytes, now: float) -> list:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
        events: list = []
        if self.state == "image" and self.last_t and now - self.last_t > GAP_S + len(x) / FS:          # audio stopped for a while: the picture ends where it is
            events.append(self._finish(False))
        self.last_t = now
        self._demod(x)
        self.since_check += len(x)
        if self.state == "idle":
            if len(self.p) > IDLE_KEEP:
                self._trim(self.p_end - IDLE_KEEP)
            if self.since_check >= CHECK_EVERY:
                self.since_check = 0
                found = self._find_vis()
                if found:
                    code, at, off = found
                    m = BY_VIS.get(code)
                    if m is None:
                        events.append({"k": "vis", "code": code})
                        self._trim(int(at))
                    else:
                        self.mode, self.state = m, "image"
                        self.img = np.zeros((m.h, m.w, 3), dtype=np.uint8)
                        self.line, self.L0, self.misses, self.pend = 0, float(at) + m.lead_ms * MS, 0, {}
                        self.fofs = float(np.clip(off, -220.0, 220.0))
                        self._trim(int(at) - 10 * MS)
                        events.append({"k": "start", "mode": m.key, "name": m.name, "w": m.w, "h": m.h, "seconds": round(m.seconds, 1)})
        if self.state == "image":
            while self.state == "image":
                ev = self._decode_line()
                if ev is None:
                    break
                events.extend(ev)
        return events

    def flush(self) -> list:
        """The receiver is being switched off: a picture in progress ends where it is."""
        return [self._finish(False)] if self.state == "image" else []

    @property
    def progress(self) -> dict:
        m = self.mode
        if m is None:
            return {}
        rows = self.line * m.rows
        return {"mode": m.key, "rows": min(rows, m.h), "h": m.h}
