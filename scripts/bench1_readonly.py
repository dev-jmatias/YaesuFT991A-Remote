#!/usr/bin/env python3
"""BENCH TEST 1 - strictly read-only. Sends only read commands; changes nothing on the radio.

  python scripts/bench1_readonly.py            # auto-detect port
  python scripts/bench1_readonly.py COM5 38400 # or give port and baud

Commands sent (all reads, from the FT-991A CAT manual): ID; IF; FA; FB; MD0; TX; AG0; RG0; MG; PC; SM0; RM3..RM6; AI;
It never sends TX1, PS, EX, or any set command.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from radio_remote.radio.cat import frame  # noqa: E402
from radio_remote.radio.cat.client import CatClient  # noqa: E402
from radio_remote.radio.cat.transport import SerialTransport, candidate_ports, detect_port  # noqa: E402

READS = ["ID;", "AI;", "IF;", "FA;", "FB;", "MD0;", "TX;", "AG0;", "RG0;", "MG;", "PC;", "SM0;",
         "RM3;", "RM4;", "RM5;", "RM6;"]


async def main() -> int:
    if len(sys.argv) >= 2:
        port, baud = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 38400
    else:
        print("candidate ports (Silicon Labs bridge):", candidate_ports() or "none")
        found = detect_port(38400, frame.RADIO_ID)
        if not found:
            print("No FT-991A answered ID; on any candidate port at 4800/9600/19200/38400.")
            print("Check: USB cable, radio ON, menu 031 CAT RATE, Silicon Labs driver (Windows).")
            return 1
        port, baud = found
    print(f"using {port} @ {baud}")
    seen = []
    t = SerialTransport(port, baud)
    await t.open()
    c = CatClient(t, seen.append, timeout=1.0)
    await c.start()
    try:
        for cmd in READS:
            try:
                ans = await c.request(cmd)
                note = ""
                try:
                    note = frame.decode(ans)
                except frame.FrameError as e:
                    note = f"(decode error: {e})"
                print(f"{cmd:6} -> {ans:34} {note}")
            except Exception as e:
                print(f"{cmd:6} -> {type(e).__name__}: {e}")
        print("stats:", c.stats)
    finally:
        await c.close()
    return 0


sys.exit(asyncio.run(main()))
