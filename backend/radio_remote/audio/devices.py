"""ALSA device discovery (Linux). Parsing is separate from running `arecord -l` / `aplay -l` so it can be tested.

Devices are referenced by card ID (e.g. plughw:CARD=CODEC,DEV=0), never by index: indexes change when
USB devices are re-plugged or enumerate in a different order after boot.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys

_LINE = re.compile(r"^card (\d+): (\S+) \[(.*?)\], device (\d+): (.*?) \[(.*?)\]")


def parse_alsa_list(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        idx, cid, name, dev, dev_name, _ = m.groups()
        out.append({
            "card": int(idx), "id": cid, "name": name, "device": int(dev), "dev_name": dev_name,
            "hw": f"hw:CARD={cid},DEV={dev}", "plughw": f"plughw:CARD={cid},DEV={dev}",
            # The radios present a generic "USB Audio CODEC"; this is a hint only, never proof.
            "likely_radio": any(k in (name + " " + dev_name).lower() for k in ("codec", "yaesu")),
        })
    return out


def _run(argv: list[str]) -> str:
    if sys.platform != "linux" or not shutil.which(argv[0]):
        return ""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def list_devices() -> dict[str, list[dict]]:
    return {"capture": parse_alsa_list(_run(["arecord", "-l"])), "playback": parse_alsa_list(_run(["aplay", "-l"]))}


def auto_pick(devs: list[dict]) -> tuple[str | None, str]:
    """Pick the radio's codec only when it is unambiguous. Returns (device, reason-if-none)."""
    likely = [d for d in devs if d["likely_radio"]]
    if len(likely) == 1:
        return likely[0]["plughw"], ""
    if not likely:
        return None, "no USB audio CODEC found"
    return None, "several USB audio CODECs found; set audio.input_device / audio.output_device"
