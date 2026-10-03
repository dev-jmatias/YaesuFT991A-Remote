"""Host diagnostics without extra dependencies. Everything degrades to None off Linux (e.g. on a dev PC)."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from importlib import metadata

_prev_cpu: tuple[float, float] | None = None


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def cpu_percent() -> float | None:
    """Whole-machine CPU use since the previous call (first call returns None)."""
    global _prev_cpu
    raw = _read("/proc/stat")
    if not raw:
        return None
    parts = [float(x) for x in raw.splitlines()[0].split()[1:]]
    idle, total = parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts)
    prev, _prev_cpu = _prev_cpu, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def memory() -> dict | None:
    raw = _read("/proc/meminfo")
    if not raw:
        return None
    kv = {l.split(":")[0]: int(l.split()[1]) for l in raw.splitlines() if ":" in l and l.split()[1:2]}
    total, avail = kv.get("MemTotal"), kv.get("MemAvailable")
    if not total or avail is None:
        return None
    return {"total_mb": total // 1024, "available_mb": avail // 1024, "used_pct": round(100 * (1 - avail / total), 1)}


def temperature_c() -> float | None:
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw) / 1000, 1) if raw else None
    except ValueError:
        return None


def board_model() -> str | None:
    raw = _read("/proc/device-tree/model")
    return raw.strip("\x00\n ") if raw else None


def system() -> dict:
    return {
        "board": board_model(), "platform": platform.platform(), "machine": platform.machine(),
        "cores": os.cpu_count(), "cpu_pct": cpu_percent(), "memory": memory(), "temp_c": temperature_c(),
        "load": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "boot_uptime_s": _boot_uptime(),
    }


def _boot_uptime() -> int | None:
    raw = _read("/proc/uptime")
    return int(float(raw.split()[0])) if raw else None


def _ver(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def hamlib_version() -> str | None:
    """`rigctl --version`, fixed argv, short timeout. None when Hamlib is not installed."""
    exe = shutil.which("rigctl")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=3).stdout.strip()
        return out.splitlines()[0] if out else None
    except (OSError, subprocess.SubprocessError):
        return None


def versions() -> dict:
    return {"python": platform.python_version(), "aiohttp": _ver("aiohttp"), "aiortc": _ver("aiortc"),
            "pyserial": _ver("pyserial"), "numpy": _ver("numpy"), "hamlib": hamlib_version()}
