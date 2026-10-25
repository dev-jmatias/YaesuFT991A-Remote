"""Builds backend/radio_remote/radio/menus/<model>.json for the radios whose CAT manual has the hierarchical menu table (FTDX10, FTDX101D/MP, FT-710):

    python tools/gen_menu_hier.py ftdx10  path/to/ftdx10.txt
    python tools/gen_menu_hier.py ftdx101d path/to/ftdx101.txt     (the FTDX101MP has the same table: ftdx101mp.json is a copy made with --copy-of)
    python tools/gen_menu_hier.py ft710   path/to/ft710.txt

The text is the pypdf text of the manual's "EX MENU" command and its Table 2 (MENU Chart): sections (P1), groups (P2) and items (P3) with the value description and the digit count.
The address of an item is the six digits P1 P2 P3 (EX P1P1 P2P2 P3P3 P4...). What the table cannot say (the items that must stay read only, the manual's typing mistakes) is in the
LOCK_WORDS / OVERRIDES below. The script prints every item it could not understand: those are kept as read-only "raw" items until somebody adds a rule for them.
"""
import json
import re
import sys
from pathlib import Path

DASH = {"–": "-", "−": "-", "—": "-"}
# an item whose name contains one of these cannot be changed from the app: it would cut the link or change how the radio is keyed / which port or network it uses
LOCK_WORDS = [
    (r"\bCAT\b.*(RATE|TIME ?OUT|RTS|STOP BIT)|^CAT[- ]?\d|\b232C\b|TUNER/232C", "The CAT or serial link's speed, time-out or flow control: changing it could cut the connection."),
    (r"\bPTT SELECT\b|\bRPTT\b|\bPC KEYING\b|\bDAKY\b|KEYING SELECT|\bKEY SELECT\b|\bPORT SELECT\b|\bREAR SELECT\b|\bSHIFT PORT\b|PTT PORT", "Which line keys the radio or which port carries the audio: the app relies on it."),
    (r"\bLAN\b|\bIP ADDRESS|\bSUBNET|\bGATEWAY|\bDHCP|\bNETWORK|\bMAC ADDRESS|\bUSER NAME\b|\bPASSWORD|\bPORT NUMBER|\bTIME ZONE\b|\bUTC\b", "A network or clock setting: it is left to the radio itself."),
    (r"\bRADIO ID\b|\bSERIAL\b|\bFIRMWARE\b", "Cannot be set."),
]
CAUTION_WORDS = [
    (r"\bMAX POWER\b|\bTX MAX\b|POWER LIMIT", "Limits the power the radio can transmit with; the TX Power slider cannot go above it."),
    (r"\bOUT LEVEL\b|\bMOD GAIN\b|\bDATA GAIN\b|\bTX GAIN\b|\bRPORT GAIN\b|\bTX LEVEL\b", "Audio level of the radio's transmit input: a wrong value overdrives the transmitter. Keep the ALC at zero."),
    (r"\bEMERGENCY\b", "Allows transmitting on the emergency frequencies."),
    (r"\bTUNER SELECT\b", "Chooses the antenna tuner the radio uses."),
    (r"\bMOD SOURCE\b|\bSSB MOD SOURCE\b|\bMIC SELECT\b", "Chooses which input feeds the transmitter (microphone, USB or the rear jack): the app's Mic control uses it."),
    (r"\bTX BPF\b", "Changes the audio band the transmitter sends."),
    (r"\b(SSB|AM|FM|CW) OUT SELECT\b", "Which receiver (MAIN or SUB) feeds the USB audio: the app sets it when you choose the SUB receiver."),
]


def clean(line: str) -> str:
    for k, v in DASH.items():
        line = line.replace(k, v)
    line = re.sub(r"\b(\d{1,2}) :", r"\1:", line)                     # "00 : OFF" -> "00: OFF"
    return line.strip()


def lines_of(text: str) -> list[str]:
    a = text.index("Table 2 (MENU Chart)")
    m = re.search(r"\nFA FREQUENCY", text[a:])
    body = text[a:a + m.start()] if m else text[a:]
    out, skip = [], False
    for ln in body.splitlines():
        s = clean(ln)
        if s.startswith("====="):
            skip = True                                      # the line after a page marker is the page number
            continue
        if skip:
            skip = False
            continue
        if not s or s.startswith(("CAT (Computer", "Table 2", "P1 P2 P3", "FTDX", "FT-")):
            continue
        out.append(s)
    return out


def parse(text: str) -> list[dict]:
    """[{p1, p1name, p2, p2name, p3, name, desc, digits}] in table order."""
    L = lines_of(text)
    i = 0
    p1 = p1n = p2 = p2n = None
    shared = []
    items, pending, shared = [], [], []
    while i < len(L):
        s = L[i]
        if re.fullmatch(r"\d\d", s) and i + 1 < len(L) and re.fullmatch(r"\(.+\)", L[i + 1]):
            pending.append((s, L[i + 1][1:-1]))
            i += 2
            continue
        if pending:
            if len(pending) >= 3:                                  # a section and several groups that share the items that follow (the extension presets)
                (p1, p1n) = pending[0]
                shared = pending[1:]
                p2, p2n = shared[-1]
            elif len(pending) == 2:
                (p1, p1n), (p2, p2n) = pending[0], pending[-1]
                shared = [pending[-1]]
            else:
                p2, p2n = pending[0]
                shared = [pending[0]]
            pending = []
        m = re.match(r"^(\d\d) (.+)$", s)
        if not m:
            i += 1
            continue
        p3, rest = m.group(1), m.group(2)
        # an item that goes on over several lines has no digit count at the end of its first line: it ends with a line that is a lone digit
        digits = None
        mm = re.match(r"^(.+?) (\d{1,2}|- -|- - -)$", rest)
        j = i
        if mm:
            desc_all, digits = mm.group(1), mm.group(2)
        else:
            parts = [rest]
            j = i + 1
            while j < len(L):
                if re.fullmatch(r"\d", L[j]):
                    digits = L[j]
                    break
                if re.fullmatch(r"\d\d", L[j]) and j + 1 < len(L) and L[j + 1].startswith("("):
                    break
                if re.match(r"^\d\d [A-Z]", L[j]) and not re.match(r"^\d\d: ", L[j]):
                    break                                          # the next item: this one had no digit count
                parts.append(L[j])
                j += 1
            desc_all = " ".join(parts)
        i = j + 1
        name, desc = split_name(desc_all)
        name = name.rstrip(".")
        desc = re.sub(r"^D (?=\d)", "", desc)
        for a, b in DESC_FIX.get(f"{p1}{p2}{p3}", []):
            desc = desc.replace(a, b)
        for g2, g2n in shared:
            items.append({"p1": p1, "p1name": p1n, "p2": g2, "p2name": g2n, "p3": p3, "name": name, "desc": desc, "digits": None if digits is None or digits.startswith("-") else int(digits)})
    return items


def split_name(text: str) -> tuple[str, str]:
    toks = text.split()
    name = []
    for k, t in enumerate(toks):
        if k > 0 and (re.match(r"\d{1,4}:", t) or re.fullmatch(r"[+\-]?\d+(\.\d+)?", t) and k + 1 < len(toks) and toks[k + 1] in ("-", "~", "Hz", "msec", "sec", "min", "kHz", "W", "dB", "%")
                      or t in ("OFF", "UTC", "ON") and name or re.fullmatch(r"\(P\d.*", t) or t == "0:"
                      or t == "Up" and k + 1 < len(toks) and toks[k + 1] == "to" or re.fullmatch(r"\d+", t) and k + 1 < len(toks) and toks[k + 1].startswith("(")):
            break
        name.append(t)
    n = " ".join(name)
    return n, text[len(n):].strip()


def options_of(desc: str) -> list[tuple[int, str]]:
    d = re.sub(r"\s+", " ", desc)
    d = re.sub(r"(\d{1,2}):(?=\S)", r"\1: ", d)                # "01:100" -> "01: 100"
    parts = re.split(r"(?:^|\s)(?=\d{1,2}: )", d)
    out = []
    for p in parts:
        m = re.match(r"^(\d{1,2}): (.+?)\s*$", p)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def stepped(desc: str):
    """"00: OFF 01: 100 Hz - 19: 1000 Hz (50 Hz steps)" -> options"""
    m = re.search(r"(\d+): OFF\s+(\d+): ([\d.]+) ?Hz [-~] (\d+): ([\d.]+) ?Hz ?\((\d+) ?Hz steps\)", desc)
    if not m:
        return None
    first, lo, last, hi, step = int(m.group(2)), float(m.group(3)), int(m.group(4)), float(m.group(5)), float(m.group(6))
    opts = [(0, "OFF")]
    for n in range(first, last + 1):
        v = lo + step * (n - first)
        opts.append((n, f"{v:g} Hz"))
    return opts if abs(lo + step * (last - first) - hi) < 1e-6 else None


def numeric(desc: str, digits: int):
    """-> dict(lo, hi, step, unit, signed, scale?, zero?) or None"""
    d = re.sub(r"\s+", " ", desc)
    unit = ""
    um = re.search(r"\d ?(msec|sec|min|kHz|Hz|MHz|W|dB)\b", d)
    if um:
        unit = {"msec": "ms"}.get(um.group(1), um.group(1))
    step = 1
    sm = re.search(r"(\d+) ?(?:msec|kHz|Hz|sec)/step|, (\d+) ?Hz steps|(\d+) ?Hz steps", d)
    if sm:
        step = int(next(g for g in sm.groups() if g))
    # the parameter written out: "(P4 = 0020 - 4000, ...)" or "(P4 = -20 - -00 or +00 - +10)"
    pm = re.search(r"\(P\d ?= ?([+-]?\d+) [-~] ([+-]?\d+)(?: or ([+-]?\d+) [-~] ([+-]?\d+))?", d)
    if pm:
        a, b = pm.group(1), pm.group(2)
        if pm.group(4):
            return dict(lo=int(a), hi=int(pm.group(4)), step=step, unit=unit, signed=True)
        signed = a.startswith(("-", "+")) or b.startswith(("-", "+"))
        return dict(lo=int(a), hi=int(b), step=step, unit=unit, signed=signed)
    rm = re.match(r"^([+-]?\d+(?:\.\d+)?)((?: ?[A-Za-z]+)?(?: \(\w+\))?(?: [-~] [+-]?\d+(?:\.\d+)?(?: ?[A-Za-z]+)?)+)", d)
    if rm and ":" not in d:
        nums = re.findall(r"[+-]?\d+(?:\.\d+)?", rm.group(0))
        lo, hi = nums[0], nums[-1]
        signed = lo.startswith("-") or hi.startswith("+") or (digits == len(lo.lstrip("+-")) + 1 and lo.startswith("-"))
        if "." in lo or "." in hi:
            return None
        return dict(lo=int(lo), hi=int(hi), step=step, unit=unit, signed=signed)
    return None


ACRONYMS = {"SSB", "AM", "FM", "PSK", "RTTY", "CW", "DSP", "TX", "RX", "TFT", "LED", "NB", "AGC", "EQ", "SD", "LAN", "CAT", "RX-DSP", "VFO", "IND", "ENC/DEC", "ENC", "DEC", "GNRL", "OK"}


def pretty(name: str) -> str:
    return " ".join(w if w in ACRONYMS else w.capitalize() for w in name.replace("&", " & ").split())


def build(model: str, items: list[dict], overrides: dict) -> tuple[list[dict], list[str]]:
    out, odd = [], []
    seen = set()
    sections = {}
    for it in items:
        if it["digits"] is None:
            continue                                         # actions (SD card, reset, date) without a value
        addr = f"{it['p1']}{it['p2']}{it['p3']}"
        if addr in seen:
            continue
        seen.add(addr)
        n = len(out) + 1
        d = {"n": n, "addr": addr, "num": f"{it['p1']}-{it['p2']}-{it['p3']}", "name": it["name"], "group": f"{pretty(it['p1name'])}: {pretty(it['p2name'])}",
             "digits": it["digits"], "doc": it["desc"]}
        for pat, why in LOCK_WORDS:
            if re.search(pat, it["name"], re.I) or re.search(pat, it["p2name"], re.I) and "KEYING" in pat:
                d["locked"] = why
                break
        for pat, why in CAUTION_WORDS:
            if re.search(pat, it["name"], re.I) and "locked" not in d:
                d["caution"] = why
                break
        ov = overrides.get(addr)
        ctcss = re.search(r"67\.0 .*254\.1", it["desc"]) is not None
        opts = options_of(it["desc"])
        st = stepped(it["desc"])
        num = numeric(it["desc"], it["digits"])
        if ov == "raw":
            d["kind"] = "raw"
        elif re.match(r"^Up to \d+ characters", it["desc"]):
            d["kind"] = "raw"
            d["locked"] = "A text item: edit it on the radio."
        elif ctcss:
            d["kind"] = "enum"
            d["options"] = [{"v": n, "label": f"{hz} Hz"} for n, hz in enumerate(CTCSS)]
        elif re.search(r"SHIFT FREQUENCY", it["name"]) and [v for v, _ in opts] == [1, 1, 2, 3]:
            d["kind"] = "enum"
            d["options"] = [{"v": n, "label": lbl} for n, lbl in enumerate(["170 Hz", "200 Hz", "425 Hz", "850 Hz"])]       # the manual prints "1" twice
        elif st:
            d["kind"] = "enum"
            d["options"] = [{"v": v, "label": lbl} for v, lbl in st]
        elif len(opts) >= 2 and not re.search(r"\(P\d", it["desc"]):
            d["kind"] = "enum"
            d["options"] = [{"v": v, "label": lbl} for v, lbl in opts]
            if len({v for v, _ in opts}) != len(opts):
                d["kind"] = "raw"
                odd.append(f"{d['num']} {d['name']}: duplicate codes in {it['desc'][:80]!r}")
        elif num:
            d["kind"] = "int"
            d.update(num)
            if re.search(r"\b0 ?\(OFF\)", it["desc"]):
                d["zero"] = "OFF"
            need = len(str(max(abs(num["lo"]), abs(num["hi"])))) + (1 if num["signed"] else 0)
            if it["digits"] < need:
                d["kind"] = "raw"
                odd.append(f"{d['num']} {d['name']}: digits {it['digits']} < {need} for {it['desc'][:80]!r}")
        else:
            d["kind"] = "raw"
            odd.append(f"{d['num']} {d['name']}: not understood: {it['desc'][:90]!r} digits {it['digits']}")
        if d["kind"] == "int" and it["name"].strip() == "CW WEIGHT":
            d["scale"] = 0.1
        if re.search(r"MARK FREQUENCY", it["name"]):
            d["locked"] = "The manual's table is inconsistent for this item (its codes are not certain), so it is shown but not changed here."
        if d["kind"] == "raw" and "locked" not in d:
            d["locked"] = "Shown as the radio writes it: this item's values are not understood well enough to change here."
        if d["kind"] == "enum":
            need = max(len(str(o["v"])) for o in d["options"])
            if it["digits"] < need:
                d["digits"] = need                          # the manual's digit count is a typing mistake
                odd.append(f"{d['num']} {d['name']}: digits {it['digits']} -> {need}")
        out.append(d)
    return out, odd


CTCSS = ["67.0", "69.3", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4", "88.5", "91.5", "94.8", "97.4", "100.0", "103.5", "107.2", "110.9", "114.8", "118.8", "123.0", "127.3", "131.8",
         "136.5", "141.3", "146.2", "151.4", "156.7", "159.8", "162.2", "165.5", "167.9", "171.3", "173.8", "177.3", "179.9", "183.5", "186.2", "189.9", "192.8", "196.6", "199.5",
         "203.5", "206.5", "210.7", "218.1", "225.7", "229.1", "233.6", "241.8", "250.3", "254.1"]
# typing mistakes of the manuals, by address: (text in the description, replacement)
DESC_FIX = {"030120": [("17 ATT", "17:ATT")]}
OVERRIDES: dict[str, dict] = {"ftdx10": {}, "ftdx101d": {}, "ft710": {}}

if __name__ == "__main__":
    model, src = sys.argv[1], Path(sys.argv[2])
    items = parse(src.read_text())
    menu, odd = build(model, items, OVERRIDES.get(model, {}))
    dst = Path(__file__).resolve().parent.parent / "backend" / "radio_remote" / "radio" / "menus" / f"{model}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps({"model": model, "source": f"{model} CAT Operation Reference Book, EX MENU table", "items": menu}, indent=1, ensure_ascii=False) + "\n")
    kinds = {}
    for d in menu:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    print("wrote", dst, len(menu), "items", kinds, "locked", sum(1 for d in menu if d.get("locked")))
    for o in odd:
        print("  !", o)
