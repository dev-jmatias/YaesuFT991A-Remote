"""Configuration: TOML file with validated, safe defaults.

Defaults are deliberately safe: mock radio, PTT to real hardware disabled,
listening on loopback only.
"""
from __future__ import annotations

import copy
import os
import re
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, dict[str, Any]] = {
    "server": {"host": "127.0.0.1", "port": 8080, "allowed_origins": []},
    "radio": {
        "model": "mock",
        "serial_port": "auto",
        "baud": 38400,
        "hamlib_model": 0,
    },
    "safety": {
        "allow_ptt": False,
        "tx_timeout_s": 120,
        "ptt_heartbeat_timeout_s": 1.0,
        "control_request_timeout_s": 10,     # a request for control passes automatically after this long unanswered
    },
    "audio": {
        "enabled": True, "backend": "auto", "input_device": "", "output_device": "",
        "rx_gain_db": 0.0, "tx_gain_db": 0.0, "opus_bitrate": 32000, "max_peers": 3,
    },
    "ui": {
        "tuning_steps_hz": [10, 100, 500, 1000, 5000, 10000],
        # Raw CAT meter value (0..255) that corresponds to the radio's own full-scale ALC / compression display. PROVISIONAL:
        # the manual gives no calibration; set from what your radio's meter shows (see docs/05-ui.md).
        "meter_alc_full": 160,
        "meter_comp_full": 255,
        # SWR warning: the SWR meter turns red and a message appears while transmitting at or above this ratio (0 = off). Radios that
        # report a ratio (the newer HF radios) are compared directly; the FT-991A reports only a raw 0..255 value, which is turned into an
        # estimated ratio with a straight line through "1:1 = 0" and "3:1 = swr_raw_at_3". PROVISIONAL: the CAT manual gives no
        # calibration, so set swr_raw_at_3 from what the radio's own meter shows (see docs/05-ui.md).
        "swr_warn": 3.0,
        "swr_raw_at_3": 100,
    },
    "logging": {"level": "INFO"},
    # update check: once a day the server asks GitHub (api.github.com, anonymous, read-only) for the newest release and tells the
    # administrators when it is newer than this program. Nothing is downloaded or installed automatically. check = false switches it off.
    "updates": {"check": True, "repo": "dev-jmatias/YaesuFT991A-Remote"},
    "storage": {"data_dir": "data"},
}

KNOWN_MODELS = {"mock", "ft991a", "ftdx101d", "ftdx101mp", "ftdx10", "ft710"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


class ConfigError(ValueError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for section, values in over.items():
        if section not in out:
            raise ConfigError(f"unknown config section [{section}]")
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] must be a table")
        for k, v in values.items():
            if k not in out[section]:
                raise ConfigError(f"unknown key {section}.{k}")
            if type(v) is not type(out[section][k]) and not (
                isinstance(v, int) and isinstance(out[section][k], float)
            ):
                raise ConfigError(f"{section}.{k} has wrong type ({type(v).__name__})")
            out[section][k] = v
    return out


def validate(cfg: dict) -> dict:
    if cfg["radio"]["model"] not in KNOWN_MODELS:
        raise ConfigError(f"radio.model must be one of {sorted(KNOWN_MODELS)}")
    if not 1 <= cfg["server"]["port"] <= 65535:
        raise ConfigError("server.port out of range")
    if cfg["radio"]["baud"] not in (4800, 9600, 19200, 38400, 115200):
        raise ConfigError("radio.baud must be a standard CAT rate")
    if not 5 <= cfg["safety"]["tx_timeout_s"] <= 600:
        raise ConfigError("safety.tx_timeout_s must be 5..600")
    if not 0.3 <= cfg["safety"]["ptt_heartbeat_timeout_s"] <= 5:
        raise ConfigError("safety.ptt_heartbeat_timeout_s must be 0.3..5")
    if not 3 <= cfg["safety"]["control_request_timeout_s"] <= 120:
        raise ConfigError("safety.control_request_timeout_s must be 3..120")
    for k in ("meter_alc_full", "meter_comp_full"):
        if not 10 <= cfg["ui"][k] <= 255:
            raise ConfigError(f"ui.{k} must be 10..255")
    if not (cfg["ui"]["swr_warn"] == 0 or 1.2 <= cfg["ui"]["swr_warn"] <= 10):
        raise ConfigError("ui.swr_warn must be 0 (off) or 1.2..10")
    if not 10 <= cfg["ui"]["swr_raw_at_3"] <= 255:
        raise ConfigError("ui.swr_raw_at_3 must be 10..255")
    a = cfg["audio"]
    if a["backend"] not in ("auto", "alsa", "test"):
        raise ConfigError("audio.backend must be auto, alsa or test")
    if not 6000 <= a["opus_bitrate"] <= 128000:
        raise ConfigError("audio.opus_bitrate must be 6000..128000")
    if not 1 <= a["max_peers"] <= 8:
        raise ConfigError("audio.max_peers must be 1..8")
    for k in ("rx_gain_db", "tx_gain_db"):
        if not -30 <= a[k] <= 30:
            raise ConfigError(f"audio.{k} must be -30..30")
    for k in ("input_device", "output_device"):
        if a[k] and not all(c.isalnum() or c in ":=,._-" for c in a[k]):
            raise ConfigError(f"audio.{k} contains invalid characters")
        if a[k].isdigit():
            raise ConfigError(f"audio.{k} = {a[k]!r} is not an ALSA device name: leave it empty for auto-detection, or use e.g. plughw:CARD=CODEC,DEV=0 (see `aplay -l`)")
    if cfg["logging"]["level"].upper() not in LOG_LEVELS:
        raise ConfigError("logging.level invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,38}/[A-Za-z0-9._-]{1,100}", cfg["updates"]["repo"]):
        raise ConfigError("updates.repo must look like owner/name")
    return cfg


def load(path: str | os.PathLike | None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path and Path(path).exists():
        with open(path, "rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    return validate(cfg)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise ConfigError(f"cannot serialise {v!r}")


def dumps(cfg: dict) -> str:
    lines = []
    for section, values in cfg.items():
        lines.append(f"[{section}]")
        for k, v in values.items():
            lines.append(f"{k} = {_toml_value(v)}")
        lines.append("")
    return "\n".join(lines)


def save(cfg: dict, path: str | os.PathLike) -> None:
    """Atomic write with a .bak of the previous file."""
    validate(cfg)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(dumps(cfg), encoding="utf-8")
    if p.exists():
        p.replace(p.with_suffix(p.suffix + ".bak"))
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
