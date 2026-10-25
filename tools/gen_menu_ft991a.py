"""Builds backend/radio_remote/radio/menus/ft991a.json from the text of the FT-991A CAT Operation Reference Book (EX MENU table, pages 7-10), saved as text.

    python tools/gen_menu_ft991a.py path/to/cat.txt

The manual's table is the source of the item numbers, names, value descriptions and digit counts. Everything the table cannot say (groups, the items that must stay read-only, the
manual's typing mistakes) is in the OVERRIDES / GROUPS / LOCKED sections below, so the file can be rebuilt and reviewed.
"""
import json
import re
import sys
from pathlib import Path

GROUPS = [(1, 3, "AGC"), (4, 9, "Display"), (10, 11, "Voice memory (DVS)"), (12, 22, "Keyer and CW memories"), (23, 25, "Noise blanker"), (26, 40, "General"),
          (41, 49, "AM mode"), (50, 61, "CW mode"), (62, 73, "Data mode"), (74, 79, "FM mode"), (80, 86, "Repeater"), (87, 91, "C4FM"), (92, 101, "RTTY mode"),
          (102, 110, "SSB mode"), (111, 114, "Filters"), (115, 118, "Scope"), (119, 136, "Equalizers"), (137, 140, "TX maximum power"), (141, 141, "Tuner"),
          (142, 148, "VOX"), (149, 153, "Other")]
# Items the app must not change: they would cut its link to the radio or change how the radio is keyed / which port carries the audio (the app relies on them).
LOCKED = {
    28: "Selects what the radio's serial socket does: changing it can cut the connection.",
    29: "The serial socket's speed.", 30: "The serial socket's time-out.",
    31: "The CAT speed: changing it would cut this connection (the app and the radio would disagree).", 32: "The CAT time-out: changing it can make the link drop.", 33: "Flow control of the CAT link: changing it can cut the connection.",
    47: "Which line keys the transmitter in AM: the app relies on it.", 48: "Which port carries the AM audio: the app relies on it.",
    60: "Which line keys the CW: the app relies on it.", 71: "Which line keys the transmitter in data modes: the app relies on it.", 72: "Which port carries the data audio: the app relies on it.",
    76: "Which line keys the transmitter in FM data: the app relies on it.", 77: "Which port carries the FM data audio: the app relies on it.",
    96: "Which line shifts the RTTY: the app relies on it.", 108: "Which line keys the transmitter in SSB: the app relies on it.", 109: "Which port carries the SSB audio: the app relies on it.",
    87: "The radio's ID: it cannot be set.",
}
UNKNOWN_FORMAT = {27: "The time zone's CAT format is not described in the manual.",
                  101: "The manual's table is inconsistent for this item (its codes are not certain), so it is shown but not changed here."}
CAUTION = {137: "Limits the power the radio can transmit with on HF; the TX Power slider cannot go above it.", 138: "Limits the power the radio can transmit with on 50 MHz.",
           139: "Limits the power the radio can transmit with on 144 MHz.", 140: "Limits the power the radio can transmit with on 430 MHz.",
           141: "Chooses the antenna tuner the radio uses.", 149: "Allows the radio to transmit on the emergency frequencies. Leave it as it is unless you need it.",
           110: "Changes the audio band the transmitter sends: a narrow band can make the signal sound different.",
           46: "Audio level the radio takes from its port in AM: a wrong value overdrives the transmitter.", 54: "Audio level the radio takes in CW.", 73: "Audio level the radio takes from its port in data modes: keep the ALC at zero.",
           75: "Audio level the radio takes in FM.", 78: "Audio level the radio takes in FM data.", 99: "Audio level the radio takes in RTTY.", 107: "Audio level the radio takes from its port in SSB."}
NAME_FIX = {36: "TX TOT", 119: "PRMTRC EQ1 FREQ", 125: "PRMTRC EQ3 FREQ", 128: "P-PRMTRC EQ1 FREQ", 134: "P-PRMTRC EQ3 FREQ", 88: "GM DISPLAY"}
ENUM_NOTE_FIX = {}
# the manual swaps the digit counts of 068 and 069
DIGITS_FIX = {68: 2, 69: 1}
# values the table describes as a text range: (kind, lo, hi, step, unit, scale)
RANGES = {
    1: (20, 4000, 20, "ms"), 2: (20, 4000, 20, "ms"), 3: (20, 4000, 20, "ms"), 5: (0, 5, 1, "s"), 8: (0, 15, 1, ""), 10: (0, 100, 1, ""), 11: (0, 100, 1, ""),
    14: (25, 45, 1, ""), 15: (0, 690, 1, "s"), 17: (0, 9999, 1, ""), 25: (0, 10, 1, ""), 26: (0, 100, 1, ""), 35: (-20, 20, 1, "kHz"), 36: (0, 30, 1, "min"),
    39: (-25, 25, 1, ""), 46: (0, 100, 1, ""), 49: (0, 100, 1, ""), 54: (0, 100, 1, ""), 57: (30, 3000, 10, "ms"), 64: (-3000, 3000, 10, "Hz"), 65: (-3000, 3000, 10, "Hz"),
    73: (0, 100, 1, ""), 75: (0, 100, 1, ""), 78: (0, 100, 1, ""), 80: (0, 1000, 10, "kHz"), 81: (0, 4000, 10, "kHz"), 82: (0, 4000, 10, "kHz"), 83: (0, 10000, 10, "kHz"),
    99: (0, 100, 1, ""), 107: (0, 100, 1, ""), 112: (-40, 20, 1, ""), 113: (1, 11, 1, ""), 120: (-20, 10, 1, ""), 121: (1, 10, 1, ""), 123: (-20, 10, 1, ""), 124: (1, 10, 1, ""),
    126: (-20, 10, 1, ""), 127: (1, 10, 1, ""), 129: (-20, 10, 1, ""), 130: (1, 10, 1, ""), 132: (-20, 10, 1, ""), 133: (1, 10, 1, ""), 135: (-20, 10, 1, ""), 136: (1, 10, 1, ""),
    137: (5, 100, 1, "W"), 138: (5, 100, 1, "W"), 139: (5, 50, 1, "W"), 140: (5, 50, 1, "W"), 143: (0, 100, 1, ""), 144: (30, 3000, 10, "ms"), 145: (0, 100, 1, ""),
    146: (0, 100, 1, ""), 147: (30, 3000, 10, "ms"), 148: (0, 100, 1, ""), 151: (30000, 47000000, 1, "Hz"),
}
SCALE = {14: 0.1}                                  # CW weight is shown as 2.5 .. 4.5
SIGNED = {35, 39, 64, 65, 112, 120, 123, 126, 129, 132, 135}
ZERO_IS = {15: "OFF", 36: "OFF"}
# "00: OFF 01: 100 Hz ~ 19: 1000 Hz": code -> label
def stepped(lo_hz, step, last, off=True):
    out = [("0" * 0 + "0", "OFF")] if off else []
    for n in range(1, last + 1):
        out.append((str(n), f"{lo_hz + step * (n - 1)} Hz"))
    return out
TABLES = {
    92: stepped(100, 50, 19), 94: stepped(700, 50, 67), 102: stepped(100, 50, 19), 104: stepped(700, 50, 67), 66: stepped(100, 50, 19), 68: stepped(700, 50, 67), 41: stepped(100, 50, 19),
    43: stepped(700, 50, 67), 50: stepped(100, 50, 19), 52: stepped(700, 50, 67),
    119: [("0", "OFF")] + [(str(n), f"{100 * n} Hz") for n in range(1, 8)], 128: [("0", "OFF")] + [(str(n), f"{100 * n} Hz") for n in range(1, 8)],
    122: [("0", "OFF")] + [(str(n), f"{700 + 100 * (n - 1)} Hz") for n in range(1, 10)], 131: [("0", "OFF")] + [(str(n), f"{700 + 100 * (n - 1)} Hz") for n in range(1, 10)],
    125: [("0", "OFF")] + [(str(n), f"{1500 + 100 * (n - 1)} Hz") for n in range(1, 19)], 134: [("0", "OFF")] + [(str(n), f"{1500 + 100 * (n - 1)} Hz") for n in range(1, 19)],
    116: [("3", "50 kHz"), ("4", "100 kHz"), ("5", "200 kHz"), ("6", "500 kHz"), ("7", "1000 kHz")],
}


def parse(text: str) -> dict[int, dict]:
    items: dict[int, dict] = {}
    for line in text.splitlines():
        m = re.match(r"^(\d{3}) (.+?) (\d{1,2}|- - - - - - - - - - -)$", line.strip())
        if not m:
            continue
        n = int(m.group(1))
        if not 1 <= n <= 153 or n in items:
            continue
        rest, digits = m.group(2), m.group(3)
        toks = rest.split()
        name_toks = []
        for i, t in enumerate(toks):
            if i > 0 and (re.match(r"^\d+:$", t) or re.match(r"^[–+\-]?\d+(\.\d+)?$", t) and i + 1 < len(toks) and toks[i + 1] in ("~", "Hz", "msec", "sec", "min", "kHz", "~"))\
               or (i > 0 and t in ("OFF", "UTC", "–", "-") and name_toks) or (i > 0 and re.match(r"^\d+$", t) and i + 1 < len(toks) and toks[i + 1] == "~") or (i > 0 and re.match(r"^00:$", t)):
                break
            name_toks.append(t)
        name = " ".join(name_toks)
        name = NAME_FIX.get(n, name)
        desc = rest[len(" ".join(name_toks)):].strip()
        items[n] = {"n": n, "name": name, "desc": desc, "digits": None if digits.startswith("-") else int(digits)}
    return items


def options_of(desc: str):
    """"0: OFF  1: ON" -> [("0", "OFF"), ("1", "ON")]"""
    parts = re.split(r"(?:^|\s{2,}|\s)(?=\d{1,2}:\s)", desc)
    out = []
    for p in parts:
        m = re.match(r"^(\d{1,2}):\s*(.+?)\s*$", p)
        if m:
            out.append((str(int(m.group(1))), m.group(2).strip()))
    return out


def build(items: dict[int, dict]) -> list[dict]:
    out = []
    for n in range(1, 154):
        it = items.get(n)
        if it is None:
            raise SystemExit(f"menu {n:03d} missing from the table")
        digits = DIGITS_FIX.get(n, it["digits"])
        group = next(g for lo, hi, g in GROUPS if lo <= n <= hi)
        d = {"n": n, "name": it["name"], "group": group, "digits": digits, "doc": it["desc"]}
        if n in LOCKED:
            d["locked"] = LOCKED[n]
        if n in UNKNOWN_FORMAT:
            d["locked"] = UNKNOWN_FORMAT[n]
        if n in CAUTION:
            d["caution"] = CAUTION[n]
        if n == 87:
            d["kind"] = "none"
        elif n == 100:
            d["kind"] = "enum"
            d["options"] = [{"v": 0, "label": "170 Hz"}, {"v": 1, "label": "200 Hz"}, {"v": 2, "label": "425 Hz"}, {"v": 3, "label": "850 Hz"}]       # the manual prints "1" twice
        elif n == 153:
            d["kind"] = "int"
            d.update(lo=0, hi=99, step=1, unit="", signed=False, zero="AUTO")
        elif n in TABLES:
            d["kind"] = "enum"
            d["options"] = [{"v": int(c), "label": lbl} for c, lbl in TABLES[n]]
        elif n in RANGES:
            lo, hi, step, unit = RANGES[n]
            d["kind"] = "int"
            d.update(lo=lo, hi=hi, step=step, unit=unit, signed=n in SIGNED)
            if n in SCALE:
                d["scale"] = SCALE[n]
            if n in ZERO_IS:
                d["zero"] = ZERO_IS[n]
        elif n == 27:
            d["kind"] = "raw"
        else:
            opts = options_of(it["desc"])
            if not opts:
                raise SystemExit(f"menu {n:03d} {it['name']}: cannot read the value description {it['desc']!r}")
            d["kind"] = "enum"
            d["options"] = [{"v": int(c), "label": lbl} for c, lbl in opts]
        out.append(d)
    return out


if __name__ == "__main__":
    items = parse(Path(sys.argv[1]).read_text())
    menu = build(items)
    dst = Path(__file__).resolve().parent.parent / "backend" / "radio_remote" / "radio" / "menus" / "ft991a.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps({"model": "ft991a", "source": "FT-991A CAT Operation Reference Book, EX MENU table", "items": menu}, indent=1, ensure_ascii=False) + "\n")
    print("wrote", dst, len(menu), "items")
