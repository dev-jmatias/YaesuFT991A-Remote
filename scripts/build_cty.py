#!/usr/bin/env python3
"""Turn AD1C's cty.dat (the Amateur Radio Country Files, https://www.country-files.com/) into backend/radio_remote/data/cty.json, the compact table the FT8/FT4 tab uses to
show the country of a decoded station.

    python scripts/build_cty.py path/to/cty.dat        (download it from country-files.com first)

The output keeps the entity names, every prefix and every exact callsign. Run it again when a new country file is out (a few times a year).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "backend" / "radio_remote" / "data" / "cty.json"


def parse(text: str) -> dict:
    entities, prefixes, exact = [], {}, {}
    head = None
    body: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):                      # an entity header: name: cq: itu: continent: lat: lon: tz: primary prefix:
            f = [x.strip() for x in line.rstrip().rstrip(":").split(":")]
            head = f[0]
            entities.append(head)
            continue
        body.append(line.strip())
        if line.rstrip().endswith(";"):
            idx = len(entities) - 1
            for tok in "".join(body).rstrip(";").split(","):
                tok = tok.strip()
                if not tok:
                    continue
                is_exact = tok.startswith("=")
                tok = re.sub(r"(\([^)]*\)|\[[^\]]*\]|<[^>]*>|\{[^}]*\}|~[^~]*~)", "", tok.lstrip("="))      # the CQ / ITU zone and position overrides are not needed
                (exact if is_exact else prefixes)[tok.upper()] = idx
            body = []
    return {"source": "AD1C country files (cty.dat), https://www.country-files.com/", "entities": entities, "p": prefixes, "x": exact}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    data = parse(Path(sys.argv[1]).read_text(encoding="latin-1"))
    OUT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    print(f"{len(data['entities'])} countries, {len(data['p'])} prefixes, {len(data['x'])} exact calls -> {OUT} ({OUT.stat().st_size // 1024} KB)")
