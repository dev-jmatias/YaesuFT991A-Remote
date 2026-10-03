#!/usr/bin/env python3
"""Strictly read-only first contact with a newer Yaesu HF radio (FTDX10, FTDX101D/MP, FT-710) using the protocol object of that model.

  python scripts/bench_hf_readonly.py ftdx10                 # auto-detect the CAT port
  python scripts/bench_hf_readonly.py ft710 /dev/ttyUSB0 38400

Stop the radio-remote service first (it owns the port). Only READ commands are sent: no set command, no TX, no PS.
Each line shows the command, the radio's answer and what the driver's decoder makes of it, so a wrong answer format is visible at once.
Paste the whole output into an issue / to the developer: it is the first real test of that radio's profile.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from radio_remote.radio.cat import frame  # noqa: E402
from radio_remote.radio.cat.client import CatClient  # noqa: E402
from radio_remote.radio.cat.proto import MODELS, proto_for  # noqa: E402
from radio_remote.radio.cat.transport import SerialTransport, candidate_ports, detect_port  # noqa: E402


async def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in MODELS:
        print("usage: bench_hf_readonly.py <%s> [port [baud]]" % "|".join(MODELS))
        return 2
    model = sys.argv[1]
    p = proto_for(model)
    if len(sys.argv) >= 3:
        port, baud = sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 38400
    else:
        print("candidate ports (Silicon Labs bridge, the radio has two: the Enhanced one is CAT):", candidate_ports() or "none")
        found = detect_port(38400, p.RADIO_ID)
        if not found:
            print(f"No {p.NAME} answered ID; with ID{p.RADIO_ID}; at 38400 baud. Check the radio's CAT RATE menu and the port.")
            return 1
        port, baud = found
    print(f"using {port} @ {baud} for {p.NAME} (expecting ID{p.RADIO_ID};)")
    t = SerialTransport(port, baud)
    await t.open()
    c = CatClient(t, lambda _f: None, timeout=1.0)
    await c.start()
    reads = ["ID;", "AI;", "IF;", "OI;", "FA;", "FB;", "MD0;", "ST;", "TX;", "AG0;", "RG0;", "MG;", "PC;", "SM0;",
             "RM1;", "RM3;", "RM4;", "RM5;", "RM6;", "GT0;", "RA0;", "PA0;", "NA0;", "NB0;", "NR0;", "BC0;", "IS0;", "SH0;",
             "CO00;", "BP00;", "ML0;", "AC;", "PS;"]
    reads += [v[0] for k, v in p.ENCODE.items() if k in ("mic_select", "rit", "xit")]
    try:
        for cmd in dict.fromkeys(reads):
            try:
                ans = await c.request(cmd)
                try:
                    note = p.decode(ans)
                except frame.FrameError as e:
                    note = f"(decoder: {e})"
                print(f"{cmd:10} -> {ans:34} {note}")
            except Exception as e:
                print(f"{cmd:10} -> {type(e).__name__}: {e}")
        print("stats:", c.stats)
    finally:
        await c.close()
    return 0


sys.exit(asyncio.run(main()))
