"""Per-radio CAT protocol objects. The driver (drivers/ft991a.py: YaesuCatDriver) never builds or parses a command itself: it asks
the protocol object of the selected radio, so choosing a radio model applies that radio's commands and menus.

* Ft991aProto: the FT-991A (manual 1711-D), thin wrapper over frame.py / ft991a_controls.py (bench-verified).
* HfProto: the newer Yaesu HF radios FTDX10 (manual 2308-F), FTDX101D / FTDX101MP (2101-I) and FT-710 (2306-C). EXPERIMENTAL:
  written from the manuals only, no such radio has been available for testing. Everything that differs between the radios is data
  in HfModel; every command here is documented in those manuals (page references in the comments).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import frame
from . import ft991a_controls as fc
from .frame import FrameError, _int

# ---------------------------------------------------------------------------------------------------------------- FT-991A


class Ft991aProto:
    NAME = "FT-991A"
    RADIO_ID = frame.RADIO_ID
    BAND_CODES = fc.BAND_CODES
    LEVELS = frame.LEVELS
    ENCODE = fc.ENCODE
    READ_WIDTH = fc.READ_WIDTH
    NARROW_REREAD = ["NA0;", fc.READ_WIDTH]
    SPLIT_READ = "FT;"
    S_METER_READ = "SM0;"
    TX_METER_CYCLE = ("RM5;", "RM6;", "RM4;", "RM3;")        # PO, SWR, ALC, COMP (p.15)
    TUNE_START = "AC002;"
    TUNE_STOP = "AC002;"                                      # START/STOP toggle, only sent while RF is going out
    CLARIFIER_ON = "RT1;"
    DEFAULT_MODES = None

    def __init__(self, model: str = "ft991a"):
        self.model = model

    decode = staticmethod(frame.decode)
    freq_set = staticmethod(frame.freq_set)
    mode_set = staticmethod(frame.mode_set)
    level_read = staticmethod(frame.level_read)
    level_set = staticmethod(frame.level_set)
    width_options = staticmethod(fc.width_options)
    width_hz = staticmethod(fc.width_hz)
    width_set = staticmethod(fc.width_set)

    def split_set(self, on: bool) -> str:
        return "FT3;" if on else "FT2;"                      # bench-verified

    def sync_reads(self, caps) -> list[str]:
        return ["IF;", "FB;", "OI;", "FT;", "TX;", "AG0;", "RG0;", "MG;", "PC;", "SM0;"]

    def backstop_reads(self, caps) -> list[str]:
        return ["IF;", "TX;", "FB;", "OI;", "FT;"]

    def slow_reads(self, caps) -> list[str]:
        return ["AG0;", "RG0;", "MG;", "PC;", "FB;", "FT;"]

    def settled_vfo_reads(self, caps) -> list[str]:
        return ["FA;", "FB;", "OI;", "FT;", "MD0;"]


# ---------------------------------------------------------------------------------------------------------------- newer HF radios

HF_MODES = {                                    # MD P2 / IF P6 (FTDX10 p.15-16; identical in the other two manuals)
    "1": "LSB", "2": "USB", "3": "CW-U", "4": "FM", "5": "AM", "6": "RTTY-L", "7": "CW-L", "8": "DATA-L", "9": "RTTY-U",
    "A": "DATA-FM", "B": "FM-N", "C": "DATA-U", "D": "AM-N", "E": "PSK", "F": "DATA-FM-N",
}
HF_MODE_CODES = {v: k for k, v in HF_MODES.items()}
HF_RM_NAMES = {"1": "smeter", "3": "comp", "4": "alc", "5": "po_raw", "6": "swr_raw"}     # RM P1 (FTDX10 p.20); 7 IDD, 8 VDD not shown
AGC_CODES, AGC_NAMES = fc.AGC_CODES, fc.AGC_NAMES                                     # GT set P2 0..4; answer P3 0..6 (FTDX10 p.13)
ATT_CHOICES = ["OFF", "6 dB", "12 dB", "18 dB"]                                         # RA P2 0..3 (FTDX10 p.19)

# SH width tables (Table 3), code -> Hz. Code 00 is the radio's default and is not listed.
_SSB_FTDX10 = {1: 300, 2: 400, 3: 600, 4: 850, 5: 1100, 6: 1200, 7: 1500, 8: 1650, 9: 1800, 10: 1950, 11: 2100, 12: 2250, 13: 2400,
               14: 2450, 15: 2500, 16: 2600, 17: 2700, 18: 2800, 19: 2900, 20: 3000, 21: 3200, 22: 3500, 23: 4000}
_CW_FTDX10 = {1: 50, 2: 100, 3: 150, 4: 200, 5: 250, 6: 300, 7: 350, 8: 400, 9: 450, 10: 500, 11: 600, 12: 800, 13: 1200, 14: 1400,
              15: 1700, 16: 2000, 17: 2400, 18: 3000, 19: 3200, 20: 3500, 21: 4000}
_SSB_FTDX101 = {1: 300, 2: 400, 3: 600, 4: 850, 5: 1100, 6: 1200, 7: 1500, 8: 1650, 9: 1800, 10: 1950, 11: 2100, 12: 2200, 13: 2300,
                14: 2400, 15: 2500, 16: 2600, 17: 2700, 18: 2800, 19: 2900, 20: 3000, 21: 3200}
_CW_FTDX101 = {k: v for k, v in _CW_FTDX10.items() if k <= 18}
_SSB_FT710 = dict(_SSB_FTDX10)
_CW_FT710 = dict(_CW_FTDX10)

_CLASS_FTDX = {"LSB": "SSB", "USB": "SSB", "CW-U": "CW", "CW-L": "CW", "RTTY-L": "CW", "RTTY-U": "CW", "PSK": "CW"}
_CLASS_FT710 = {"LSB": "SSB", "USB": "SSB", "CW-U": "CW", "CW-L": "CW", "DATA-L": "CW", "DATA-U": "CW", "PSK": "CW"}


@dataclass(frozen=True)
class HfModel:
    key: str                       # radio.model in the config
    name: str
    radio_id: str                  # ID; answer
    power_max: int                 # PC range 5..power_max
    ssb: dict
    cw: dict
    mode_class: dict
    mic_menu: str                  # EX P1P1 P2P2 P3P3 of the SSB "mod source" menu item
    mic_values: dict               # UI value -> menu parameter
    has_rt: bool = True            # RT / RU / RD / RC clarifier commands exist (not in the FT-710 manual)
    tune_start: str = "AC002;"     # AC P1 P2 P3: P3 2 = tuning start/stop (FTDX10, FTDX101); the FT-710 uses P3 3 = start, 0 = stop
    tune_stop: str = "AC002;"
    modes: list = field(default_factory=lambda: ["LSB", "USB", "CW-U", "CW-L", "AM", "FM", "DATA-U", "DATA-L", "RTTY-L", "RTTY-U",
                                                  "PSK", "AM-N", "FM-N", "DATA-FM"])


MODELS: dict[str, HfModel] = {
    "ftdx10": HfModel("ftdx10", "Yaesu FTDX10", "0761", 100, _SSB_FTDX10, _CW_FTDX10, _CLASS_FTDX,
                      "010113", {"MIC": 0, "REAR": 1}),                           # EX 01 01 13 SSB MOD SOURCE (p.11)
    "ftdx101d": HfModel("ftdx101d", "Yaesu FTDX101D", "0681", 100, _SSB_FTDX101, _CW_FTDX101, _CLASS_FTDX,
                        "010111", {"MIC": 0, "REAR": 1}),                         # EX 01 01 11 SSB MOD SOURCE (p.10)
    "ftdx101mp": HfModel("ftdx101mp", "Yaesu FTDX101MP", "0682", 200, _SSB_FTDX101, _CW_FTDX101, _CLASS_FTDX,
                         "010111", {"MIC": 0, "REAR": 1}),
    "ft710": HfModel("ft710", "Yaesu FT-710", "0800", 100, _SSB_FT710, _CW_FT710, _CLASS_FT710,
                     "010114", {"MIC": 0, "REAR": 1}, has_rt=False,               # EX 01 01 14 MOD SOURCE 0 MIC 1 USB 2 REAR 3 AUTO (p.10)
                     tune_start="AC003;", tune_stop="AC000;"),                    # AC P3: 3 = tuning start, 0 = tuning stop (FT-710 p.4)
}


def _b(v) -> int:
    return 1 if v else 0


def _signed(v: int) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):04d}"


class HfProto:
    BAND_CODES = fc.BAND_CODES                              # BS 00..10 = 1.8 .. 50 MHz, the same numbering as the FT-991A
    READ_WIDTH = "SH0;"
    NARROW_REREAD = ["NA0;", "SH0;"]
    SPLIT_READ = "ST;"                                       # ST P1: 0 off, 1 on, 2 on + 5 kHz up
    S_METER_READ = "SM0;"
    TX_METER_CYCLE = ("RM5;", "RM6;", "RM4;", "RM3;")        # PO, SWR, ALC, COMP
    CLARIFIER_ON = "RT1;"

    def __init__(self, model: str):
        self.m = MODELS[model]
        self.model = model
        self.NAME = self.m.name.replace("Yaesu ", "")
        self.RADIO_ID = self.m.radio_id
        self.TUNE_START, self.TUNE_STOP = self.m.tune_start, self.m.tune_stop
        self.LEVELS = {
            "af_gain": ("AG0", 3, 0, 255), "rf_gain": ("RG0", 3, 0, 255), "mic_gain": ("MG", 3, 0, 100),
            "rf_power": ("PC", 3, 5, self.m.power_max),
        }
        self.ENCODE = self._encoders()
        self._mic_by_value = {v: k for k, v in self.m.mic_values.items()}

    # ------------------------------------------------------------------ building commands
    def freq_set(self, hz: int, vfo: str = "A") -> str:
        if not 30_000 <= hz <= 75_000_000:
            raise FrameError(f"frequency {hz} outside 30000..75000000")
        return f"F{vfo}{hz:09d};"

    def mode_set(self, mode: str) -> str:
        if mode not in HF_MODE_CODES:
            raise FrameError(f"unknown mode {mode!r}")
        return f"MD0{HF_MODE_CODES[mode]};"

    def level_read(self, name: str) -> str:
        return f"{self.LEVELS[name][0]};"

    def level_set(self, name: str, value: int) -> str:
        prefix, digits, lo, hi = self.LEVELS[name]
        if not lo <= value <= hi:
            raise FrameError(f"{name} {value} outside {lo}..{hi}")
        return f"{prefix}{value:0{digits}d};"

    def split_set(self, on: bool) -> str:
        return "ST1;" if on else "ST0;"

    def width_set(self, code: int) -> str:
        return f"SH00{code:02d};"                            # SH P1 P2 P3P3, P1 = P2 = 0 fixed

    def _table(self, mode):
        cls = self.m.mode_class.get(mode or "")
        return {"SSB": self.m.ssb, "CW": self.m.cw}.get(cls) if cls else None

    def width_options(self, mode, narrow) -> list[dict]:
        t = self._table(mode)
        return [{"code": c, "hz": hz} for c, hz in sorted(t.items(), key=lambda kv: kv[1])] if t else []

    def width_hz(self, mode, narrow, code):
        t = self._table(mode)
        return t.get(code) if t and code is not None else None

    def sync_reads(self, caps) -> list[str]:
        out = ["IF;"]
        if caps.has("vfo_b"):
            out += ["FB;", "OI;"]
        if caps.has("split"):
            out.append(self.SPLIT_READ)
        return out + ["TX;", "AG0;", "RG0;", "MG;", "PC;", "SM0;"]

    def backstop_reads(self, caps) -> list[str]:
        out = ["IF;", "TX;"]
        if caps.has("vfo_b"):
            out += ["FB;", "OI;"]
        if caps.has("split"):
            out.append(self.SPLIT_READ)
        return out

    def slow_reads(self, caps) -> list[str]:
        out = ["AG0;", "RG0;", "MG;", "PC;"]
        if caps.has("vfo_b"):
            out.append("FB;")
        if caps.has("split"):
            out.append(self.SPLIT_READ)
        return out

    def settled_vfo_reads(self, caps) -> list[str]:
        out = ["FA;", "FB;", "OI;"]
        if caps.has("split"):
            out.append(self.SPLIT_READ)
        return out + ["MD0;"]

    # ------------------------------------------------------------------ controls: name -> (read, encode(value) -> [commands])
    def _encoders(self) -> dict[str, tuple[str, Callable[[Any], list[str]]]]:
        m = self.m
        enc: dict[str, tuple[str, Callable[[Any], list[str]]]] = {
            "narrow": ("NA0;", lambda v: [f"NA0{_b(v)};"]),
            "if_shift": ("IS0;", lambda v: [f"IS00{_signed(v)};"]),                    # IS P1 P2 +/- P4P4P4P4 (0..1200 Hz, 20 Hz steps)
            "contour": ("CO00;", lambda v: [f"CO00{_b(v):04d};"]),
            "contour_freq": ("CO01;", lambda v: [f"CO01{v:04d};"]),
            "apf": ("CO02;", lambda v: [f"CO02{_b(v):04d};"]),
            "apf_freq": ("CO03;", lambda v: [f"CO03{v // 10 + 25:04d};"]),
            "notch": ("BP00;", lambda v: [f"BP00{_b(v):03d};"]),
            "notch_freq": ("BP01;", lambda v: [f"BP01{v // 10:03d};"]),
            "auto_notch": ("BC0;", lambda v: [f"BC0{_b(v)};"]),
            "nr": ("NR0;", lambda v: [f"NR0{_b(v)};"]),
            "nr_level": ("RL0;", lambda v: [f"RL0{v:02d};"]),
            "nb": ("NB0;", lambda v: [f"NB0{_b(v)};"]),
            "nb_level": ("NL0;", lambda v: [f"NL0{v:03d};"]),
            "ipo": ("PA0;", lambda v: [f"PA0{fc.IPO_CODES[v]};"]),
            "att_level": ("RA0;", lambda v: [f"RA0{ATT_CHOICES.index(v)};"]),
            "agc": ("GT0;", lambda v: [f"GT0{AGC_CODES[v]};"]),
            "monitor": ("ML0;", lambda v: [f"ML0{_b(v):03d};"]),
            "monitor_level": ("ML1;", lambda v: [f"ML1{v:03d};"]),
            "tuner": ("AC;", lambda v: [f"AC00{_b(v)};"]),
            "mic_select": (f"EX{m.mic_menu};", lambda v: [f"EX{m.mic_menu}{m.mic_values[v]};"]),
        }
        if m.has_rt:
            enc.update({
                "rit": ("RT;", lambda v: [f"RT{_b(v)};"]),
                "xit": ("XT;", lambda v: [f"XT{_b(v)};"]),
                "clarifier_hz": ("IF;", fc._clar),                                   # RC / RU / RD, 0000..9990 Hz
            })
        return enc

    # ------------------------------------------------------------------ decoding answers / auto-information
    def decode(self, f: str) -> dict:
        if not frame._FRAME_RE.match(f):
            raise FrameError(f"malformed frame {f!r}")
        cmd, p = f[:2], f[2:-1]
        if cmd in ("FA", "FB"):
            if len(p) != 9:
                raise FrameError(f"{cmd} needs 9 digits")
            return {"frequency" if cmd == "FA" else "frequency_b": _int(p)}
        if cmd == "MD":
            if len(p) != 2 or p[1] not in HF_MODES:
                raise FrameError("bad MD")
            return {"mode": HF_MODES[p[1]]} if p[0] == "0" else {}              # P1 1 = SUB band (FTDX101): not used
        if cmd == "TX":
            if p not in ("0", "1", "2"):
                raise FrameError("bad TX")
            return {"tx": p != "0", "tx_source": {"0": None, "1": "cat", "2": "radio"}[p]}
        if cmd in ("IF", "OI"):
            # P0 mem(3) P2 freq(9) P3 clar(sign+4) P4 rxclar P5 txclar P6 mode P7 vfo/mem P8 ctcss P9(2) P10 shift (IF p.14, OI p.18)
            if len(p) != 25:
                raise FrameError(f"{cmd} needs 25 chars")
            if p[19] not in HF_MODES:
                raise FrameError(f"bad {cmd} mode")
            if cmd == "OI":
                return {"mode_b": HF_MODES[p[19]]}
            if p[12] not in "+-":
                raise FrameError("bad IF clarifier sign")
            offset = _int(p[13:17]) * (-1 if p[12] == "-" else 1)
            return {"frequency": _int(p[3:12]), "mode": HF_MODES[p[19]], "rit": p[17] == "1", "xit": p[18] == "1",
                    "clarifier_hz": offset, "vfo_memory": {"0": "vfo", "1": "memory"}.get(p[20], "other")}
        if cmd in ("AG", "RG", "SM"):
            if len(p) != 4:
                raise FrameError(f"{cmd} needs P1 + 3 digits")
            return {{"AG": "af_gain", "RG": "rf_gain", "SM": "smeter"}[cmd]: _int(p[1:])} if p[0] == "0" else {}
        if cmd in ("MG", "PC"):
            if len(p) != 3:
                raise FrameError(f"{cmd} needs 3 digits")
            return {"mic_gain" if cmd == "MG" else "rf_power": _int(p)}
        if cmd == "RM":
            if len(p) != 7:
                raise FrameError("RM needs P1 + 3 + 3 digits")
            name = HF_RM_NAMES.get(p[0])
            return {name: _int(p[1:4])} if name else {}
        if cmd == "ST":
            if p not in ("0", "1", "2"):
                raise FrameError("bad ST")
            return {"split": p != "0"}
        if cmd == "GT":
            if len(p) != 2 or p[0] != "0" or not p[1].isdigit() or int(p[1]) not in AGC_NAMES:
                raise FrameError("bad GT")
            return {"agc": AGC_NAMES[int(p[1])]}
        if cmd == "IS":
            if len(p) != 7 or p[:2] != "00" or p[2] not in "+-":
                raise FrameError("bad IS")
            return {"if_shift": _int(p[3:]) * (-1 if p[2] == "-" else 1)}
        if cmd == "RA":
            if len(p) != 2 or p[0] != "0" or p[1] not in "0123":
                raise FrameError("bad RA")
            return {"att_level": ATT_CHOICES[int(p[1])]}
        if cmd == "SH":
            if len(p) != 4 or p[:2] != "00":
                raise FrameError("bad SH")
            return {"width_code": _int(p[2:])}
        if cmd == "AC":
            if len(p) != 3 or not p.isdigit():
                raise FrameError("bad AC")
            return {"tuner": p[1] == "0" and p[2] == "1"}
        if cmd == "EX":
            pre = self.m.mic_menu
            if p.startswith(pre):
                v = p[len(pre):]
                if len(v) != 1 or not v.isdigit():
                    raise FrameError("bad EX mic menu")
                return {"mic_select": self._mic_by_value.get(int(v), "MIC")}
            return {}
        if cmd == "PR":
            return {}                                                                 # the processor is not offered (see the profile)
        parser = fc.PARSERS.get(cmd)
        return parser(p) if parser and cmd not in ("FT", "EX", "SH", "IS", "RA", "AC", "PR") else {}


def proto_for(model: str):
    if model == "ft991a":
        return Ft991aProto()
    if model in MODELS:
        return HfProto(model)
    raise KeyError(model)
