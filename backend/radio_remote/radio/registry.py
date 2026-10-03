from __future__ import annotations

from .base import Capabilities, RadioDriver
from .mock import MockDriver


def create_driver(cfg: dict) -> RadioDriver:
    model = cfg["radio"]["model"]
    if model == "mock":
        return MockDriver()
    from .drivers.ft991a import DRIVERS
    if model in DRIVERS:
        return DRIVERS[model](cfg)
    raise NotImplementedError(f"no driver for '{model}'; use radio.model = \"mock\"")
