"""Radio abstraction: capabilities + RadioDriver interface + state container."""
from __future__ import annotations

import tomllib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from . import controls

CAP_DIR = Path(__file__).parent / "capabilities"

# Fields pushed on the high-rate "meters" channel instead of "patch".
METER_FIELDS = {"smeter", "swr", "alc", "comp", "rf_power_out", "po_raw", "swr_raw",
                "audio_rx_level", "audio_tx_level", "audio_tx_frames"}


class RadioError(Exception):
    """A radio operation failed in a way worth showing to the operator."""


class Capabilities:
    def __init__(self, data: dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, model_id: str) -> "Capabilities":
        path = CAP_DIR / f"{model_id}.toml"
        if not path.exists():
            raise FileNotFoundError(f"no capability profile for '{model_id}'")
        with open(path, "rb") as f:
            return cls(tomllib.load(f))

    def has(self, feature: str) -> bool:
        """True only for an explicit `true`; "unverified" and absent are False."""
        return self.data.get("features", {}).get(feature) is True

    @property
    def is_mock(self) -> bool:
        return bool(self.data["model"].get("is_mock"))

    def level_range(self, name: str) -> tuple[int, int] | None:
        lv = self.data.get("levels", {}).get(name)
        return (lv["min"], lv["max"]) if lv else None

    def frequency_ok(self, hz: int) -> bool:
        return any(lo <= hz <= hi for lo, hi in self.data["frequency"]["ranges"])

    def modes(self) -> list[str]:
        return list(self.data["modes"]["list"])

    def public(self) -> dict[str, Any]:
        """Snapshot sent to browsers; the UI builds itself from this."""
        feats = {k: v is True for k, v in self.data.get("features", {}).items()}
        return {
            "model": {k: self.data["model"][k] for k in ("id", "name")},
            "experimental": not self.data["model"].get("bench_tested", False) and not self.is_mock,
            "bands": self.data.get("bands", {}).get("list", []),
            "controls": controls.available(feats),
            "mock": self.is_mock,
            "features": feats,
            "modes": self.modes(),
            "levels": self.data.get("levels", {}),
            "frequency_ranges": self.data["frequency"]["ranges"],
        }


class RadioDriver(ABC):
    """One physical (or simulated) radio. Drivers own the CAT conversation; callers use intents."""

    def __init__(self, caps: Capabilities):
        self.caps = caps
        self.state: dict[str, Any] = {"connected": False}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    @property
    def is_mock(self) -> bool:
        return self.caps.is_mock

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def _update(self, **fields: Any) -> None:
        changed = {k: v for k, v in fields.items() if self.state.get(k) != v}
        if not changed:
            return
        self.state.update(changed)
        for fn in self._listeners:
            fn(changed)

    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def stop(self) -> None: ...
    @abstractmethod
    async def set_frequency(self, hz: int) -> None: ...
    @abstractmethod
    async def set_mode(self, mode: str) -> None: ...
    @abstractmethod
    async def set_level(self, name: str, value: int) -> None: ...
    @abstractmethod
    async def set_ptt(self, on: bool) -> None:
        """Low-level keying. ONLY TxGuard may call this."""

    def publish(self, **fields: Any) -> None:
        """Let collaborators (e.g. the TX guard) put non-CAT state such as `tuning` into the shared radio state."""
        self._update(**fields)

    async def vfo_op(self, op: str) -> None:
        raise RadioError("VFO operations are not supported")

    async def set_frequency_b(self, hz: int) -> None:
        raise RadioError("VFO B is not supported")

    async def set_split(self, on: bool) -> None:
        raise RadioError("split is not supported")

    async def set_receivers(self, main: bool, sub: bool) -> None:
        raise RadioError("this radio has no sub receiver")

    async def set_tx_receiver(self, which: str) -> None:
        raise RadioError("this radio has no sub receiver")

    async def set_active_receiver(self, which: str) -> None:
        raise RadioError("this radio has no sub receiver")

    async def tune_start(self) -> None:
        raise RadioError("antenna tuner tune is not supported")

    async def tune_stop(self) -> None:
        return None

    def diagnostics(self) -> dict:
        """Driver-specific facts for the diagnostics page (never secrets)."""
        return {}

    # ---- optional operations; drivers override what the radio really supports
    async def set_control(self, name: str, value: Any, receiver: str = "main") -> None:
        raise RadioError(f"control {name} not supported")

    async def set_band(self, band: str) -> None:
        raise RadioError("band selection not supported")

    async def power_on(self) -> None:
        raise RadioError("power on not supported")

    async def memory_channels(self, refresh: bool = False) -> list[dict]:
        raise RadioError("memory channels are not supported by this radio profile")

    async def memory_select(self, channel: int) -> None:
        raise RadioError("memory channels are not supported by this radio profile")

    async def memory_to_vfo(self) -> None:
        raise RadioError("memory channels are not supported by this radio profile")

    async def memory_write(self, channel: int, frequency: int, mode: str, tone_mode: str = "off", shift: str = "simplex", name: str = "") -> dict:
        raise RadioError("editing memory channels is not supported by this radio profile")

    async def power_off(self) -> None:
        raise RadioError("power off not supported")
