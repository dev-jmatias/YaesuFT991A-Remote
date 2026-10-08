"""FT-991A CAT encodings/parsers for adjustable controls. Source: FT-991A CAT manual 1711-D.

Command -> manual page: NA p.13, IS p.10, CO p.5, BP p.5, BC p.4, NR/RL p.13/15, NB/NL p.13,
PA p.14, RA p.15, RT p.16, XT p.18, RC/RU/RD p.15-16, PR/PL p.14, ML p.12, AC p.4, SH p.16, BS p.5, PS p.14.

UNVERIFIED ON HARDWARE (flagged in docs/03-bench-checklist.md): width-table class per mode,
NA wording (printed 'MA' in the manual body), contour/APF/notch step sizes, and which modes the radio
accepts each command in (it may answer '?;').
"""
from __future__ import annotations

from typing import Any, Callable

from .frame import FrameError, _int

# ---- BS band codes (p.5); 24.5 MHz = 12m, 5 MHz = 60m; 14 = AIR (the radio's AIR key: airband receive, AM)
BAND_CODES = {"160m": 0, "80m": 1, "60m": 2, "40m": 3, "30m": 4, "20m": 5, "17m": 6, "15m": 7,
              "12m": 8, "10m": 9, "6m": 10, "AIR": 14, "2m": 15, "70cm": 16}

# ---- SH width tables (p.16), code -> Hz. Code 00 is "default" and duplicates another entry.
_CW_N = {0: 500, 1: 50, 2: 100, 3: 150, 4: 200, 5: 250, 6: 300, 7: 350, 8: 400, 9: 450, 10: 500}
_NARROW_WIDE_HI = {11: 800, 12: 1200, 13: 1400, 14: 1700, 15: 2000, 16: 2400, 17: 3000}
TABLES = {
    ("SSB", True): {0: 1500, 1: 200, 2: 400, 3: 600, 4: 850, 5: 1100, 6: 1350, 7: 1500, 8: 1650, 9: 1800},
    ("SSB", False): {0: 2400, 9: 1800, 10: 1950, 11: 2100, 12: 2200, 13: 2300, 14: 2400, 15: 2500, 16: 2600,
                     17: 2700, 18: 2800, 19: 2900, 20: 3000, 21: 3200},
    ("CW", True): _CW_N,
    ("CW", False): {0: 2400, 10: 500, **_NARROW_WIDE_HI},
    ("RTTY", True): {**_CW_N, 0: 300},
    ("RTTY", False): {0: 500, 10: 500, **_NARROW_WIDE_HI},
}
# DATA-* modes are deliberately absent: the manual's "RTTY/PSK" column is not explicitly tied to them.
MODE_CLASS = {"LSB": "SSB", "USB": "SSB", "CW-U": "CW", "CW-L": "CW", "RTTY-L": "RTTY", "RTTY-U": "RTTY"}


def width_options(mode: str | None, narrow: bool | None) -> list[dict]:
    cls = MODE_CLASS.get(mode or "")
    if not cls or narrow is None:
        return []
    table = TABLES[(cls, narrow)]
    seen, out = set(), []
    for code, hz in sorted(table.items(), key=lambda kv: (kv[1], kv[0])):
        if code == 0 or hz in seen:
            continue
        seen.add(hz)
        out.append({"code": code, "hz": hz})
    return out


def width_hz(mode: str | None, narrow: bool | None, code: int | None) -> int | None:
    cls = MODE_CLASS.get(mode or "")
    if not cls or narrow is None or code is None:
        return None
    return TABLES[(cls, narrow)].get(code)


# ---- encoders: name -> (read command, encode(value) -> [commands])
def _b(v) -> int:
    return 1 if v else 0


def _signed(v: int) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):04d}"


def _clar(v: int) -> list[str]:
    if v == 0:
        return ["RC;"]
    return ["RC;", f"{'RU' if v > 0 else 'RD'}{abs(v):04d};"]


IPO_CODES = {"IPO": 0, "AMP1": 1, "AMP2": 2}
AGC_CODES = {"OFF": 0, "FAST": 1, "MID": 2, "SLOW": 3, "AUTO": 4}                      # GT set P2 (manual: GT P1 P2, P1 = 0 fixed)
AGC_NAMES = {0: "OFF", 1: "FAST", 2: "MID", 3: "SLOW", 4: "AUTO", 5: "AUTO", 6: "AUTO"}  # GT answer P3: 4/5/6 = AUTO-FAST/MID/SLOW

ENCODE: dict[str, tuple[str, Callable[[Any], list[str]]]] = {
    "narrow": ("NA0;", lambda v: [f"NA0{_b(v)};"]),
    "if_shift": ("IS0;", lambda v: [f"IS0{_signed(v)};"]),
    "contour": ("CO00;", lambda v: [f"CO00{_b(v):04d};"]),
    "contour_freq": ("CO01;", lambda v: [f"CO01{v:04d};"]),
    "apf": ("CO02;", lambda v: [f"CO02{_b(v):04d};"]),
    "apf_freq": ("CO03;", lambda v: [f"CO03{v // 10 + 25:04d};"]),     # 0000..0050 = -250..+250 Hz
    "notch": ("BP00;", lambda v: [f"BP00{_b(v):03d};"]),
    "notch_freq": ("BP01;", lambda v: [f"BP01{v // 10:03d};"]),        # x10 Hz, 001..320
    "auto_notch": ("BC0;", lambda v: [f"BC0{_b(v)};"]),
    "nr": ("NR0;", lambda v: [f"NR0{_b(v)};"]),
    "nr_level": ("RL0;", lambda v: [f"RL0{v:02d};"]),
    "nb": ("NB0;", lambda v: [f"NB0{_b(v)};"]),
    "nb_level": ("NL0;", lambda v: [f"NL0{v:03d};"]),
    "ipo": ("PA0;", lambda v: [f"PA0{IPO_CODES[v]};"]),
    "agc": ("GT0;", lambda v: [f"GT0{AGC_CODES[v]};"]),
    "att": ("RA0;", lambda v: [f"RA0{_b(v)};"]),
    "rit": ("RT;", lambda v: [f"RT{_b(v)};"]),
    "xit": ("XT;", lambda v: [f"XT{_b(v)};"]),
    "clarifier_hz": ("IF;", _clar),                                     # offset is read from the IF frame
    "processor": ("PR0;", lambda v: [f"PR0{_b(v)};"]),                  # 0 = OFF, 1 = ON: BENCH-VERIFIED (manual p.14 says 1/2 and is wrong; PR02 gets ?;)
    "processor_level": ("PL;", lambda v: [f"PL{v:03d};"]),
    "monitor": ("ML0;", lambda v: [f"ML0{_b(v):03d};"]),
    "monitor_level": ("ML1;", lambda v: [f"ML1{v:03d};"]),
    "dgid": ("EX153;", lambda v: [f"EX153{0 if v == 'AUTO' else int(v):02d};"]),   # menu 153 WIRES DG-ID: 00 = AUTO, 01..99 (manual p.9)
    # The microphone source has one menu per mode family: 106 SSB MIC SELECT (read, shown), 045 AM MIC SELECT and 074 FM MIC SELECT (manual p.9: 0 = MIC, 1 = REAR). All three are
    # written together, otherwise the page's microphone reaches the radio in LSB/USB (and FreeDV) but not in AM or FM. DATA modes use menu 070 DATA IN SELECT: not touched.
    "mic_select": ("EX106;", lambda v: [f"EX106{1 if v == 'REAR' else 0};", f"EX045{1 if v == 'REAR' else 0};", f"EX074{1 if v == 'REAR' else 0};"]),
    "tuner": ("AC;", lambda v: [f"AC00{_b(v)};"]),                    # P3 0 = off, 1 = on (2 = start tune: NOT used)
}


def width_set(code: int) -> str:
    return f"SH0{code:02d};"


READ_WIDTH = "SH0;"


# ---- parsers: command letters -> fn(payload) -> state fields
def _p_sh(p):
    if len(p) != 3 or p[0] != "0":
        raise FrameError("bad SH")
    return {"width_code": _int(p[1:])}


def _p_na(p):
    if len(p) != 2 or p[0] != "0" or p[1] not in "01":
        raise FrameError("bad NA")
    return {"narrow": p[1] == "1"}


def _p_is(p):
    if len(p) != 6 or p[0] != "0" or p[1] not in "+-":
        raise FrameError("bad IS")
    return {"if_shift": _int(p[2:]) * (-1 if p[1] == "-" else 1)}


def _p_co(p):
    if len(p) != 6 or p[0] != "0":
        raise FrameError("bad CO")
    sub, val = p[1], _int(p[2:])
    if sub == "0":
        return {"contour": val == 1}
    if sub == "1":
        return {"contour_freq": val}
    if sub == "2":
        return {"apf": val == 1}
    if sub == "3":
        return {"apf_freq": (val - 25) * 10}
    raise FrameError("bad CO sub-command")


def _p_bp(p):
    if len(p) != 5 or p[0] != "0":
        raise FrameError("bad BP")
    sub, val = p[1], _int(p[2:])
    if sub == "0":
        return {"notch": val == 1}
    if sub == "1":
        return {"notch_freq": val * 10}
    raise FrameError("bad BP sub-command")


def _p_pr(p):
    # BENCH-VERIFIED on a real FT-991A: PR0; -> PR00; (off), PR01 sets/reads ON, PR02 is rejected with ?;.
    # The manual (p.14) says 1 = OFF / 2 = ON; the radio uses 0 = OFF / 1 = ON.
    if len(p) != 2 or p[0] not in "01" or p[1] not in "01":
        raise FrameError("bad PR")
    return {("processor" if p[0] == "0" else "mic_eq"): p[1] == "1"}


def _p_ml(p):
    if len(p) != 4 or p[0] not in "01":
        raise FrameError("bad ML")
    val = _int(p[1:])
    return {"monitor": val == 1} if p[0] == "0" else {"monitor_level": val}


def _p0(name, digits, conv=lambda v: v):
    def f(p):
        if len(p) != digits + 1 or p[0] != "0":
            raise FrameError(f"bad {name}")
        return {name: conv(_int(p[1:]))}
    return f


def _p_pl(p):
    if len(p) != 3:
        raise FrameError("bad PL")
    return {"processor_level": _int(p)}


def _p_pa(p):
    if len(p) != 2 or p[0] != "0" or p[1] not in "012":
        raise FrameError("bad PA")
    return {"ipo": ("IPO", "AMP1", "AMP2")[int(p[1])]}


def _p_ac(p):
    if len(p) != 3 or p[2] not in "012":
        raise FrameError("bad AC")
    return {"tuner": p[2] == "1"}


def _p_flag0(name):
    def f(p):
        if len(p) != 2 or p[0] != "0" or p[1] not in "01":
            raise FrameError(f"bad {name}")
        return {name: p[1] == "1"}
    return f


def _p_flag(name):
    def f(p):
        if len(p) != 1 or p not in "01":
            raise FrameError(f"bad {name}")
        return {name: p == "1"}
    return f


def _p_ex(p):
    # EX answers carry a 3-digit menu number then the value. Only menu 153 (DG-ID) and 106 (SSB mic select) are interpreted.
    if p.startswith("106"):
        if len(p) != 4 or p[3] not in "01":
            raise FrameError("bad EX106")
        return {"mic_select": "REAR" if p[3] == "1" else "MIC"}
    if p.startswith("153"):
        # the manual says two digits (00..99) but a real FT-991A (seen on a Raspberry Pi bench run) answers 'EX153000;': accept both
        if len(p) not in (5, 6) or not p[3:].isdigit():
            raise FrameError("bad EX153")
        n = int(p[3:])
        return {"dgid": "AUTO" if n == 0 else str(n)}
    return {}


def _p_gt(p):
    if len(p) != 2 or p[0] != "0" or not p[1].isdigit() or int(p[1]) not in AGC_NAMES:
        raise FrameError("bad GT")
    return {"agc": AGC_NAMES[int(p[1])]}


def _p_ft(p):
    # BENCH-VERIFIED on a real FT-991A: FT; -> FT0; (TX on VFO A). FT3; sets TX on B and the radio answers FT1;; FT2; back to A -> FT0;.
    if p not in ("0", "1"):
        raise FrameError("bad FT")
    return {"split": p == "1"}


PARSERS: dict[str, Callable[[str], dict]] = {
    "FT": _p_ft, "GT": _p_gt,
    "EX": _p_ex,
    "SH": _p_sh, "NA": _p_na, "IS": _p_is, "CO": _p_co, "BP": _p_bp, "PR": _p_pr, "ML": _p_ml,
    "BC": _p_flag0("auto_notch"), "NR": _p_flag0("nr"), "NB": _p_flag0("nb"), "RA": _p_flag0("att"),
    "RL": _p0("nr_level", 2), "NL": _p0("nb_level", 3), "PL": _p_pl,
    "PA": _p_pa, "RT": _p_flag("rit"), "XT": _p_flag("xit"), "AC": _p_ac,
}
