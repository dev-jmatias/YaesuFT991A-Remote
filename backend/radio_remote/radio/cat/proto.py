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
    memory_read = staticmethod(frame.memory_read)
    memory_read_notag = staticmethod(frame.memory_read_notag)
    memory_select = staticmethod(frame.memory_select)
    decode_memory = staticmethod(frame.decode_memory)
    decode_memory_notag = staticmethod(frame.decode_memory_notag)

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


# commands whose P1 is the receiver (0 MAIN, 1 SUB) on the FTDX101 and whose SUB answers are decoded with the MAIN parser
SUB_PER_RX = ("NA", "IS", "CO", "BP", "BC", "NR", "RL", "NB", "PA", "RA", "GT", "SH")


def _b(v) -> int:
    return 1 if v else 0


def _signed(v: int) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):04d}"


class HfProto:
    BAND_CODES = fc.BAND_CODES                              # BS 00..10 = 1.8 .. 50 MHz, the same numbering as the FT-991A
    READ_WIDTH = "SH0;"
    READ_WIDTH_SUB = "SH1;"                                  # FTDX101 SUB receiver
    NARROW_REREAD = ["NA0;", "SH0;"]
    NARROW_REREAD_SUB = ["NA1;", "SH1;"]
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
        if model.startswith("ftdx101"):
            self.LEVELS["af_gain_sub"] = ("AG1", 3, 0, 255)             # AG P1=1: the SUB receiver's own volume
            self.LEVELS["rf_gain_sub"] = ("RG1", 3, 0, 255)
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

    # Memory channels (MT / MR read, MC recall: the same layout as the FT-991A; only the mode letters differ). Read and recall only: nothing is ever written.
    memory_read = staticmethod(frame.memory_read)
    memory_read_notag = staticmethod(frame.memory_read_notag)
    memory_select = staticmethod(frame.memory_select)

    def decode_memory(self, answer: str):
        return frame.decode_memory(answer, HF_MODES)

    def decode_memory_notag(self, answer: str):
        return frame.decode_memory_notag(answer, HF_MODES)

    # FTDX101 MAIN / SUB receivers (CAT manual p.13): FR P1 P2 = MAIN / SUB receiver 0 listening, 1 muted; FT set 2 = transmit on MAIN, 3 = on SUB (read FT -> FT0 / FT1)
    def receivers_set(self, main: bool, sub: bool) -> str:
        return f"FR{0 if main else 1}{0 if sub else 1};"

    # The rear / USB audio carries ONE receiver, chosen per mode family by "<mode> OUT SELECT" (0 MAIN, 1 SUB): SSB 01-01-08, AM 01-02-08, FM 01-03-08 (CAT manual p.10).
    # DATA, RTTY and CW have the same item but their numbers are ambiguous in the manual, so they are not touched.
    AUDIO_OUT_MENUS = ("010108", "010208", "010308", "010410", "010510", "020108")     # SSB, AM, FM, DATA, RTTY, CW (the last three read back 0 / 3 digits as the manual says, on a real FTDX101D)

    def audio_out_set(self, which: str) -> list[str]:
        if which not in ("main", "sub"):
            raise FrameError("receiver must be main or sub")
        return [f"EX{m}{0 if which == 'main' else 1};" for m in self.AUDIO_OUT_MENUS]

    def active_receiver_set(self, which: str) -> str:               # VS P1: 0 = MAIN band operation, 1 = SUB band operation (the receiver the radio's dial and keys act on)
        if which not in ("main", "sub"):
            raise FrameError("receiver must be main or sub")
        return "VS0;" if which == "main" else "VS1;"

    def tx_receiver_set(self, which: str) -> str:
        if which not in ("main", "sub"):
            raise FrameError("receiver must be main or sub")
        return "FT2;" if which == "main" else "FT3;"

    def width_set(self, code: int, rx: str = "0") -> str:
        return f"SH{rx}0{code:02d};"                         # SH P1 (receiver) P2 (0 fixed) P3P3

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
        if caps.has("dual_receiver"):
            out += ["FR;", "FT;", "VS;", "AG1;"]
        return out + ["TX;", "AG0;", "RG0;", "MG;", "PC;", "SM0;"]

    def backstop_reads(self, caps) -> list[str]:
        out = ["IF;", "TX;"]
        if caps.has("vfo_b"):
            out += ["FB;", "OI;"]
        if caps.has("split"):
            out.append(self.SPLIT_READ)
        if caps.has("dual_receiver"):
            out += ["FR;", "FT;", "VS;"]
        return out

    def slow_reads(self, caps) -> list[str]:
        out = ["AG0;", "RG0;", "MG;", "PC;"]
        if caps.has("dual_receiver"):
            out += ["AG1;", "RG1;"]
        if caps.has("vfo_b"):
            out.append("FB;")
        if caps.has("split"):
            out.append(self.SPLIT_READ)
        if caps.has("dual_receiver"):
            out += ["FR;", "FT;", "VS;"]
        return out

    def settled_vfo_reads(self, caps) -> list[str]:
        out = ["FA;", "FB;", "OI;"]
        if caps.has("split"):
            out.append(self.SPLIT_READ)
        return out + ["MD0;"]

    # ------------------------------------------------------------------ controls: name -> (read, encode(value) -> [commands])
    def _rx_encoders(self, rx: str) -> dict[str, tuple[str, Callable[[Any], list[str]]]]:
        """The controls that exist once per receiver. rx is the P1 digit: "0" = MAIN (the only one on most radios), "1" = SUB (FTDX101)."""
        return {
            "narrow": (f"NA{rx};", lambda v: [f"NA{rx}{_b(v)};"]),
            "if_shift": (f"IS{rx};", lambda v: [f"IS{rx}0{_signed(v)};"]),                  # IS P1 P2 +/- P4P4P4P4 (0..1200 Hz, 20 Hz steps)
            "contour": (f"CO{rx}0;", lambda v: [f"CO{rx}0{_b(v):04d};"]),
            "contour_freq": (f"CO{rx}1;", lambda v: [f"CO{rx}1{v:04d};"]),
            "notch": (f"BP{rx}0;", lambda v: [f"BP{rx}0{_b(v):03d};"]),
            "notch_freq": (f"BP{rx}1;", lambda v: [f"BP{rx}1{v // 10:03d};"]),
            "auto_notch": (f"BC{rx};", lambda v: [f"BC{rx}{_b(v)};"]),
            "nr": (f"NR{rx};", lambda v: [f"NR{rx}{_b(v)};"]),
            "nr_level": (f"RL{rx};", lambda v: [f"RL{rx}{v:02d};"]),
            "nb": (f"NB{rx};", lambda v: [f"NB{rx}{_b(v)};"]),
            "ipo": (f"PA{rx};", lambda v: [f"PA{rx}{fc.IPO_CODES[v]};"]),
            "att_level": (f"RA{rx};", lambda v: [f"RA{rx}{ATT_CHOICES.index(v)};"]),
            "agc": (f"GT{rx};", lambda v: [f"GT{rx}{AGC_CODES[v]};"]),
        }

    def _encoders(self) -> dict[str, tuple[str, Callable[[Any], list[str]]]]:
        m = self.m
        enc: dict[str, tuple[str, Callable[[Any], list[str]]]] = {
            **self._rx_encoders("0"),
            "apf": ("CO02;", lambda v: [f"CO02{_b(v):04d};"]),
            "apf_freq": ("CO03;", lambda v: [f"CO03{v // 10 + 25:04d};"]),
            "nb_level": ("NL0;", lambda v: [f"NL0{v:03d};"]),                                  # NL P1 is fixed 0: one level for both receivers
            "monitor": ("ML0;", lambda v: [f"ML0{_b(v):03d};"]),
            "monitor_level": ("ML1;", lambda v: [f"ML1{v:03d};"]),
            "tuner": ("AC;", lambda v: [f"AC00{_b(v)};"]),
            "amc_level": ("AO;", lambda v: [f"AO{v:03d};"]),                               # AO P1P1P1 001..100
            "cw_speed": ("KS;", lambda v: [f"KS{v:03d};"]),                                # KS P1P1P1 004..060 WPM
            "cw_pitch": ("KP;", lambda v: [f"KP{(v - 300) // 10:02d};"]),                  # KP P1P1 00..75 = 300..1050 Hz in 10 Hz steps
            "keyer": ("KR;", lambda v: [f"KR{_b(v)};"]),
            "break_in": ("BI;", lambda v: [f"BI{_b(v)};"]),
            "processor": ("PR0;", lambda v: [f"PR0{_b(v)};"]),
            "processor_level": ("PL;", lambda v: [f"PL{v:03d};"]),                         # PL P1P1P1 000..100
            "mic_select": (f"EX{m.mic_menu};", lambda v: [f"EX{m.mic_menu}{m.mic_values[v]};"]),
        }
        if self.model.startswith("ftdx101"):
            enc["audio_out_level"] = ("EX010109;", lambda v: [f"EX{g}{v:03d};" for g in ("010109", "010209", "010309", "010411", "010511", "020109")])    # SSB / AM / FM / DATA / RTTY / CW OUT LEVEL
            enc["audio_in_level"] = ("EX010113;", lambda v: [f"EX{g}{v:03d};" for g in ("010113", "010214", "010313")])     # SSB / AM / FM RPORT GAIN
            enc["rear_select"] = ("EX010112;", lambda v: [f"EX{g}{1 if v == 'USB' else 0};" for g in ("010112", "010213", "010312", "010414")])   # REAR SELECT: SSB / AM / FM / DATA
            enc.update({f"{k}_sub": v for k, v in self._rx_encoders("1").items()})                                           # the same controls for the SUB receiver
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
        if cmd in SUB_PER_RX and p[:1] == "1" and self.model.startswith("ftdx101"):
            inner = self.decode(f"{cmd}0{p[1:]};")                                  # SUB answers: the MAIN parser (P1 forced to 0), keys get a _sub suffix
            return {f"{k}_sub": v for k, v in inner.items()}
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
                    "clarifier_hz": offset, "vfo_memory": {"0": "vfo", "1": "memory"}.get(p[20], "other"), "memory_channel": _int(p[:3])}
        if cmd in ("AG", "RG", "SM"):
            if len(p) != 4:
                raise FrameError(f"{cmd} needs P1 + 3 digits")
            if cmd == "AG" and p[0] == "1" and self.model.startswith("ftdx101"):
                return {"af_gain_sub": _int(p[1:])}
            if cmd == "RG" and p[0] == "1" and self.model.startswith("ftdx101"):
                return {"rf_gain_sub": _int(p[1:])}
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
        if cmd == "FR":
            if len(p) != 2 or p[0] not in "01" or p[1] not in "01":
                raise FrameError("bad FR")
            return {"rx_main": p[0] == "0", "rx_sub": p[1] == "0"}                 # 0 = that receiver is listening, 1 = muted
        if cmd == "VS":
            if p not in ("0", "1"):
                raise FrameError("bad VS")
            return {"active_receiver": "main" if p == "0" else "sub"}
        if cmd == "FT":
            if p not in ("0", "1"):
                raise FrameError("bad FT")
            return {"tx_receiver": "main" if p == "0" else "sub"}
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
            if self.model.startswith("ftdx101"):
                for key, menu in (("audio_out_level", "010109"), ("audio_in_level", "010113")):
                    if p.startswith(menu) and len(p) == len(menu) + 3 and p[len(menu):].isdigit():
                        return {key: int(p[len(menu):])}
                if p.startswith("010112") and len(p) == 7 and p[6] in "01":
                    return {"rear_select": "USB" if p[6] == "1" else "DATA"}
            return {}
        if cmd in ("AO", "KS"):
            if len(p) != 3 or not p.isdigit():
                raise FrameError(f"bad {cmd}")
            return {"amc_level" if cmd == "AO" else "cw_speed": _int(p)}
        if cmd == "KP":
            if len(p) != 2 or not p.isdigit() or int(p) > 75:
                raise FrameError("bad KP")
            return {"cw_pitch": 300 + 10 * int(p)}
        if cmd in ("KR", "BI"):
            if p not in ("0", "1"):
                raise FrameError(f"bad {cmd}")
            return {"keyer" if cmd == "KR" else "break_in": p == "1"}
        if cmd == "PR":
            # BENCH-VERIFIED on a real FTDX101D: PR0 = speech processor, PR1 = parametric mic equalizer, value 0 = OFF / 1 = ON
            # (the manual says 1 = OFF / 2 = ON, and PR02 is rejected with ?;)
            if len(p) != 2 or p[0] not in "01" or p[1] not in "01":
                raise FrameError("bad PR")
            return {("processor" if p[0] == "0" else "mic_eq"): p[1] == "1"}
        parser = fc.PARSERS.get(cmd)
        return parser(p) if parser and cmd not in ("FT", "EX", "SH", "IS", "RA", "AC") else {}


def proto_for(model: str):
    if model == "ft991a":
        return Ft991aProto()
    if model in MODELS:
        return HfProto(model)
    raise KeyError(model)
