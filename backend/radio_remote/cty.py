"""Country of a callsign, from the AD1C country files (data/cty.json, built by scripts/build_cty.py). Used by the FT8/FT4 tab to show where a decoded station is."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "cty.json"
SUFFIXES = {"P", "M", "MM", "AM", "QRP", "LH", "A", "B", "T", "R", "J", "AA", "AG", "AE", "KT"}          # after the slash: portable, mobile, maritime... (not a country)


# the country files give some entities long official names: the list on the screen is narrow, so a few are shortened
SHORT = {"Fed. Rep. of Germany": "Germany", "Republic of Korea": "South Korea", "Dem. People's Rep. of Korea": "North Korea", "European Russia": "Russia",
         "Asiatic Russia": "Russia (Asia)", "Dem. Rep. of the Congo": "DR Congo", "Republic of the Congo": "Congo", "United Arab Emirates": "UAE",
         "Central African Republic": "C. African Rep.", "Dominican Republic": "Dominican Rep.", "Bosnia-Herzegovina": "Bosnia-Herz.", "Sov Mil Order of Malta": "SMO Malta"}


@lru_cache(maxsize=1)
def _table() -> tuple[list[str], dict[str, int], dict[str, int]]:
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
        return [SHORT.get(n, n) for n in d["entities"]], d["p"], d["x"]
    except (OSError, ValueError, KeyError):
        return [], {}, {}


def _longest(prefixes: dict[str, int], s: str) -> int | None:
    for n in range(min(len(s), 7), 0, -1):
        i = prefixes.get(s[:n])
        if i is not None:
            return i
    return None


def country(call: str) -> str:
    """The country (DXCC entity) name for a callsign, "" when unknown. Handles /P, /QRP, /M and the CALL/PREFIX and PREFIX/CALL forms."""
    names, prefixes, exact = _table()
    c = re.sub(r"[<>\s]", "", call.upper())
    if not names or not c or c == "...":
        return ""
    if c in exact:
        return names[exact[c]]
    parts = [p for p in c.split("/") if p]
    if len(parts) > 1:
        keep = [p for p in parts if p not in SUFFIXES and not (len(p) == 1 and p.isdigit())]
        parts = keep or parts[:1]
        if len(parts) > 1:                                    # DL/K1ABC or K1ABC/KH6: the shorter part is the prefix
            parts = [min(parts, key=len)] if len(min(parts, key=len)) < len(max(parts, key=len)) else parts[:1]
    i = _longest(prefixes, parts[0]) if parts else None
    return names[i] if i is not None else ""


def sender_of(text: str) -> str:
    """The callsign that SENT an FT8/FT4 message: the caller in a CQ, the second callsign otherwise."""
    t = text.upper().split()
    if not t:
        return ""
    if t[0] == "CQ":
        if len(t) >= 4 and re.fullmatch(r"[A-Z]{1,4}", t[1]):
            return t[2]
        return t[1] if len(t) > 1 else ""
    return t[1] if len(t) >= 3 else ""
