#!/usr/bin/env python3
"""Read-only dump of the radio's EX menu values, to find which menu item a front-panel setting (e.g. DG-ID) changes.

  python scripts/ex_dump.py dump before.json          # reads EX001..EX160, writes the file
  (change the setting on the radio)
  python scripts/ex_dump.py dump after.json
  python scripts/ex_dump.py diff before.json after.json

Only sends EX<nnn>; READ commands (no value = read). Never sends a set command, TX, or PS. Stop the radio-remote service first
(it owns the serial port):  sudo systemctl stop radio-remote   ...and start it again afterwards.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from radio_remote.radio.cat import frame  # noqa: E402
from radio_remote.radio.cat.client import CatClient  # noqa: E402
from radio_remote.radio.cat.transport import SerialTransport, detect_port  # noqa: E402

LAST = 160


async def dump(path: str) -> int:
    found = detect_port(38400, frame.RADIO_ID)
    if not found:
        print("No FT-991A answered ID; is the radio on and the radio-remote service stopped?")
        return 1
    port, baud = found
    print(f"using {port} @ {baud}")
    t = SerialTransport(port, baud)
    await t.open()
    c = CatClient(t, lambda _f: None, timeout=1.0)
    await c.start()
    out = {}
    try:
        for n in range(1, LAST + 1):
            cmd = f"EX{n:03d};"
            try:
                out[f"{n:03d}"] = await c.request(cmd)
            except Exception as e:                       # '?;' = menu number not present / not readable
                out[f"{n:03d}"] = f"ERR {type(e).__name__}"
    finally:
        await c.close()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0, sort_keys=True)
    ok = sum(1 for v in out.values() if not v.startswith("ERR"))
    print(f"wrote {path}: {ok} readable of {len(out)}")
    return 0


def diff(a: str, b: str) -> int:
    x, y = json.load(open(a, encoding="utf-8")), json.load(open(b, encoding="utf-8"))
    changed = [(k, x.get(k), y.get(k)) for k in sorted(set(x) | set(y)) if x.get(k) != y.get(k)]
    if not changed:
        print("no EX value changed")
    for k, old, new in changed:
        print(f"EX{k}: {old}  ->  {new}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "dump":
        sys.exit(asyncio.run(dump(sys.argv[2])))
    if len(sys.argv) == 4 and sys.argv[1] == "diff":
        sys.exit(diff(sys.argv[2], sys.argv[3]))
    print(__doc__)
    sys.exit(2)
