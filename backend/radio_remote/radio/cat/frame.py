"""Yaesu FT-991A CAT frames. Every format here is from the FT-991A CAT Operation
Reference Manual (1711-D); page numbers refer to that manual.

A frame is 2 letters + fixed-width parameters + ';'. The radio answers a bad
command with '?;'.
"""
from __future__ import annotations

import re

# MD0 P2 / IF P6 mode codes (p.11, p.10)
MODES = {
    "1": "LSB", "2": "USB", "3": "CW-U", "4": "FM", "5": "AM", "6": "RTTY-L", "7": "CW-L",
    "8": "DATA-L", "9": "RTTY-U", "A": "DATA-FM", "B": "FM-N", "C": "DATA-U", "D": "AM-N", "E": "C4FM",
}
MODE_CODES = {v: k for k, v in MODES.items()}

# memory channel P8 (tone mode) and P10 (shift direction), MT/MR p.11-12. The tone FREQUENCY is not part of these commands (P9 is fixed at 00).
TONE_MODES = {"0": "off", "1": "ctcss_encdec", "2": "ctcss_enc", "3": "dcs_encdec", "4": "dcs_enc"}
TONE_CODES = {v: k for k, v in TONE_MODES.items()}
SHIFTS = {"0": "simplex", "1": "plus", "2": "minus"}
SHIFT_CODES = {v: k for k, v in SHIFTS.items()}
TAG_LEN = 12

# Table 1 (CTCSS tone chart, tone number 000-049 -> Hz) and Table 2 (DCS code chart, number 000-103 -> code) of the FT-991A CAT manual (CN command).
CTCSS_TONES = (67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1)
DCS_CODES = ("023", "025", "026", "031", "032", "036", "043", "047", "051", "053", "054", "065", "071", "072", "073", "074", "114", "115", "116", "122", "125", "131", "132", "134", "143", "145", "152", "155", "156", "162", "165", "172", "174", "205", "212", "223", "225", "226", "243", "244", "245", "246", "251", "252", "255", "261", "263", "265", "266", "271", "274", "306", "311", "315", "325", "331", "332", "343", "346", "351", "356", "364", "365", "371", "411", "412", "413", "423", "431", "432", "445", "446", "452", "454", "455", "462", "464", "465", "466", "503", "506", "516", "523", "526", "532", "546", "565", "606", "612", "624", "627", "631", "632", "654", "662", "664", "703", "712", "723", "731", "732", "734", "743", "754")


# BENCH-FOUND on a real FT-991A (2026-10-08): the tone FREQUENCY of a memory channel is stored per channel, but the MT write has no field for it: the radio takes the tone number (CN00 / CN01)
# that VFO-A holds at the moment of the write. So: set VFO-A's tone first, then write the channel. Deleting a channel: recall it (MC), then AM; (VFO-A TO MEMORY CHANNEL on a recalled channel empties it).


def tone_set(kind: str, index: int) -> str:
    """CN P1 P2 P3P3P3; (manual p.7): P2 0 = CTCSS tone number 000-049, 1 = DCS code number 000-103."""
    if kind not in ("ctcss", "dcs"):
        raise FrameError(f"unknown tone kind {kind!r}")
    top = len(CTCSS_TONES) if kind == "ctcss" else len(DCS_CODES)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < top:
        raise FrameError(f"{kind} number outside 0..{top - 1}")
    return f"CN0{0 if kind == 'ctcss' else 1}{index:03d};"


def tone_index(hz: float) -> int:
    """The CTCSS tone number for a frequency in Hz (it must be one of the 50 standard tones)."""
    for i, t in enumerate(CTCSS_TONES):
        if abs(t - float(hz)) < 0.05:
            return i
    raise FrameError(f"{hz} Hz is not one of the radio's CTCSS tones")


def dcs_index(code: str) -> int:
    code = str(code).strip().zfill(3)
    if code not in DCS_CODES:
        raise FrameError(f"{code} is not one of the radio's DCS codes")
    return DCS_CODES.index(code)


def tone_mode_set(mode: str) -> str:
    if mode not in TONE_CODES:
        raise FrameError(f"unknown tone mode {mode!r}")
    return f"CT0{TONE_CODES[mode]};"


def shift_set(shift: str) -> str:
    if shift not in SHIFT_CODES:
        raise FrameError(f"unknown shift {shift!r}")
    return f"OS0{SHIFT_CODES[shift]};"


def decode_tone_state(ct: str | None, cn_ctcss: str | None, cn_dcs: str | None, os_: str | None) -> dict:
    """Answers of CT0; CN00; CN01; OS0; (any may be None when the radio refused it) -> {tone_mode, tone_hz, dcs_code, shift}."""
    out: dict = {"tone_mode": None, "tone_hz": None, "dcs_code": None, "shift": None}
    if ct and re.fullmatch(r"CT0[0-4];", ct):
        out["tone_mode"] = TONE_MODES[ct[3]]
    if cn_ctcss and re.fullmatch(r"CN00\d{3};", cn_ctcss) and int(cn_ctcss[4:7]) < len(CTCSS_TONES):
        out["tone_hz"] = CTCSS_TONES[int(cn_ctcss[4:7])]
    if cn_dcs and re.fullmatch(r"CN01\d{3};", cn_dcs) and int(cn_dcs[4:7]) < len(DCS_CODES):
        out["dcs_code"] = DCS_CODES[int(cn_dcs[4:7])]
    if os_ and re.fullmatch(r"OS0[0-2];", os_):
        out["shift"] = SHIFTS[os_[3]]
    return out

FREQ_MIN, FREQ_MAX = 30_000, 470_000_000     # FA/FB range (p.9)
RADIO_ID = "0670"                            # ID; -> ID0670; (p.10)

# level name -> (set/read prefix, digits, min, max)   AG p.4, RG p.15, MG p.11, PC p.14
LEVELS = {
    "af_gain": ("AG0", 3, 0, 255),
    "rf_gain": ("RG0", 3, 0, 255),
    "mic_gain": ("MG", 3, 0, 100),
    "rf_power": ("PC", 3, 5, 100),
}
# RM P1 meter selector (p.15)
RM_NAMES = {"1": "smeter", "3": "comp", "4": "alc", "5": "po_raw", "6": "swr_raw"}

_FRAME_RE = re.compile(r"^[A-Z]{2}[ -~]*;$")


class FrameError(ValueError):
    pass


def split_frames(buf: bytearray) -> list[str]:
    """Remove and return all complete frames from buf (terminated by ';')."""
    out = []
    while True:
        i = buf.find(b";")
        if i < 0:
            return out
        raw = bytes(buf[: i + 1])
        del buf[: i + 1]
        text = raw.decode("ascii", "replace").lstrip("\r\n\x00 ")
        if text:
            out.append(text)


def freq_set(hz: int, vfo: str = "A") -> str:
    if not FREQ_MIN <= hz <= FREQ_MAX:
        raise FrameError(f"frequency {hz} outside {FREQ_MIN}..{FREQ_MAX}")
    return f"F{vfo}{hz:09d};"


def mode_set(mode: str) -> str:
    if mode not in MODE_CODES:
        raise FrameError(f"unknown mode {mode!r}")
    return f"MD0{MODE_CODES[mode]};"


def level_set(name: str, value: int) -> str:
    prefix, digits, lo, hi = LEVELS[name]
    if not lo <= value <= hi:
        raise FrameError(f"{name} {value} outside {lo}..{hi}")
    return f"{prefix}{value:0{digits}d};"


def level_read(name: str) -> str:
    return f"{LEVELS[name][0]};"


def _int(s: str) -> int:
    if not s.isdigit():
        raise FrameError(f"not numeric: {s!r}")
    return int(s)


def decode(frame: str) -> dict:
    """Decode an answer / auto-information frame into state fields.

    Returns {} for frames we deliberately do not interpret. Raises FrameError
    for frames we do interpret but that are malformed.
    """
    if not _FRAME_RE.match(frame):
        raise FrameError(f"malformed frame {frame!r}")
    cmd, p = frame[:2], frame[2:-1]
    if cmd in ("FA", "FB"):
        if len(p) != 9:
            raise FrameError(f"{cmd} needs 9 digits")
        return {"frequency" if cmd == "FA" else "frequency_b": _int(p)}
    if cmd == "MD":
        if len(p) != 2 or p[0] != "0" or p[1] not in MODES:
            raise FrameError("bad MD")
        return {"mode": MODES[p[1]]}
    if cmd == "TX":
        if p not in ("0", "1", "2"):
            raise FrameError("bad TX")
        return {"tx": p != "0", "tx_source": {"0": None, "1": "cat", "2": "radio"}[p]}
    if cmd == "IF":
        # P1 mem(3) P2 freq(9) P3 clar(sign+4) P4 rxclar P5 txclar P6 mode P7 vfo/mem P8 ctcss P9(2) P10 shift  (p.10)
        if len(p) != 25:
            raise FrameError("IF needs 25 chars")
        clar_sign = p[12]
        if clar_sign not in "+-":
            raise FrameError("bad IF clarifier sign")
        offset = _int(p[13:17]) * (-1 if clar_sign == "-" else 1)
        if p[19] not in MODES:
            raise FrameError("bad IF mode")
        return {
            "frequency": _int(p[3:12]), "mode": MODES[p[19]], "rit": p[17] == "1", "xit": p[18] == "1",
            "clarifier_hz": offset, "vfo_memory": {"0": "vfo", "1": "memory"}.get(p[20], "other"),
            "memory_channel": (int(p[:3]) or None) if p[:3].isdigit() else None,        # IF P1: 001-117 while in memory mode
        }
    if cmd == "OI":
        # OPPOSITE BAND INFORMATION (read only, manual p.14): same 25-character layout as IF, but for VFO B. Only its mode is used.
        if len(p) != 25:
            raise FrameError("OI needs 25 chars")
        if p[19] not in MODES:
            raise FrameError("bad OI mode")
        return {"mode_b": MODES[p[19]]}
    if cmd in ("AG", "RG", "SM"):
        if len(p) != 4 or p[0] != "0":
            raise FrameError(f"{cmd} needs P1=0 and 3 digits")
        return {{"AG": "af_gain", "RG": "rf_gain", "SM": "smeter"}[cmd]: _int(p[1:])}
    if cmd in ("MG", "PC"):
        if len(p) != 3:
            raise FrameError(f"{cmd} needs 3 digits")
        return {"mic_gain" if cmd == "MG" else "rf_power": _int(p)}
    if cmd == "RM":
        if len(p) != 4:
            raise FrameError("RM needs P1 + 3 digits")
        name = RM_NAMES.get(p[0])
        return {name: _int(p[1:])} if name else {}
    from .ft991a_controls import PARSERS

    parser = PARSERS.get(cmd)
    return parser(p) if parser else {}


def memory_read(channel: int) -> str:
    """MT P0P0P0; = read one memory channel including its tag (read command of MT, manual p.11 "MT MEMORY CHANNEL WRITE/TAG").
    This builds the READ form only; memory_write() below builds the write."""
    if not 1 <= channel <= 99:
        raise FrameError(f"memory channel {channel} outside 1..99")
    return f"MT{channel:03d};"


def memory_write(channel: int, hz: int, mode: str, tone_mode: str = "off", shift: str = "simplex", tag: str = "") -> str:
    """MT P1..P12; = write one memory channel with its tag (manual p.11 "MT MEMORY CHANNEL WRITE/TAG"): channel(3) frequency(9) clarifier +0000 RX/TX clarifier off (0 0)
    mode(1) P7 0 (fixed on write) tone mode(1) 00 (fixed) shift(1) 0 (fixed) tag (12, padded with spaces). Everything is checked here, so a bad value never reaches the radio."""
    if not 1 <= channel <= 99:
        raise FrameError(f"memory channel {channel} outside 1..99")
    if isinstance(hz, bool) or not isinstance(hz, int) or not FREQ_MIN <= hz <= FREQ_MAX:
        raise FrameError(f"frequency outside {FREQ_MIN}..{FREQ_MAX} Hz")
    if mode not in MODE_CODES:
        raise FrameError(f"unknown mode {mode!r}")
    if tone_mode not in TONE_CODES:
        raise FrameError(f"unknown tone mode {tone_mode!r}")
    if shift not in SHIFT_CODES:
        raise FrameError(f"unknown shift {shift!r}")
    if not isinstance(tag, str) or len(tag) > TAG_LEN or not all(32 <= ord(c) < 127 and c != ";" for c in tag):
        raise FrameError(f"the name must be at most {TAG_LEN} printable ASCII characters (no ';')")
    return f"MT{channel:03d}{hz:09d}+000000{MODE_CODES[mode]}0{TONE_CODES[tone_mode]}00{SHIFT_CODES[shift]}0{tag.ljust(TAG_LEN)};"


def memory_select(channel: int) -> str:
    """MC P1P1P1; selects a memory channel (001-099 regular memories, manual p.11)."""
    if not 1 <= channel <= 99:
        raise FrameError(f"memory channel {channel} outside 1..99")
    return f"MC{channel:03d};"


def memory_read_notag(channel: int) -> str:
    """MR P0P0P0; = read one memory channel WITHOUT its tag (manual p.11 "MR MEMORY CHANNEL READ", read form only). Used when the
    radio does not answer the MT read of a channel: we still get frequency and mode, just no name."""
    if not 1 <= channel <= 99:
        raise FrameError(f"memory channel {channel} outside 1..99")
    return f"MR{channel:03d};"


def decode_memory_notag(answer: str, modes: dict | None = None) -> dict | None:
    """MR answer: channel(3) freq(9) clar(5) rxclar txclar mode vfo/mem ctcss fixed(2) shift = 25 characters, no tag."""
    if not _FRAME_RE.match(answer) or not answer.startswith("MR"):
        raise FrameError(f"not an MR answer: {answer!r}")
    p = answer[2:-1]
    if len(p) != 25 or not p[:3].isdigit() or not p[3:12].isdigit():
        raise FrameError("bad MR answer")
    hz = int(p[3:12])
    if hz == 0:
        return None
    modes = modes or MODES
    if p[19] not in modes:
        raise FrameError("bad MR mode")
    return {"channel": int(p[:3]), "frequency": hz, "mode": modes[p[19]], "tag": "",
            "tone_mode": TONE_MODES.get(p[21], "off"), "shift": SHIFTS.get(p[24], "simplex")}


def decode_memory(answer: str, modes: dict | None = None) -> dict | None:
    """MT answer: channel(3) freq(9) clar(5) rxclar txclar mode vfo/mem ctcss(1) fixed(2) shift fixed tag(up to 12).
    Returns None for an empty channel (frequency 0)."""
    if not _FRAME_RE.match(answer) or not answer.startswith("MT"):
        raise FrameError(f"not an MT answer: {answer!r}")
    p = answer[2:-1]
    if len(p) < 26 or not p[:3].isdigit() or not p[3:12].isdigit():
        raise FrameError("bad MT answer")
    hz = int(p[3:12])
    if hz == 0:
        return None
    modes = modes or MODES
    if p[19] not in modes:
        raise FrameError("bad MT mode")
    return {"channel": int(p[:3]), "frequency": hz, "mode": modes[p[19]], "tag": p[26:].strip(),
            "tone_mode": TONE_MODES.get(p[21], "off"), "shift": SHIFTS.get(p[24], "simplex")}
