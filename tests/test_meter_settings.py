import copy
import json

import pytest
from conftest import setup_admin
from test_phase8 import admin_user, close_all, make_app  # noqa: F401
from test_web import origin

from radio_remote import config


def test_meter_calibration_defaults_and_validation():
    cfg = config.load(None)
    assert cfg["ui"]["meter_alc_full"] == 160 and cfg["ui"]["meter_comp_full"] == 255
    for key in ("meter_alc_full", "meter_comp_full"):
        for bad in (0, 9, 256, 1000):
            c = copy.deepcopy(cfg)
            c["ui"][key] = bad
            with pytest.raises(config.ConfigError):
                config.validate(c)


async def test_hello_carries_meter_calibration(client):
    await setup_admin(client)
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    assert hello["ui"]["meter"] == {"alc_full": 160, "comp_full": 255, "swr_warn": 3.0, "swr_raw_at_3": 100}
    assert hello["ui"]["steps"]                                   # existing contract unchanged
    await ws.close()


def test_swr_warning_settings_are_validated():
    cfg = config.load(None)
    assert cfg["ui"]["swr_warn"] == 3.0 and cfg["ui"]["swr_raw_at_3"] == 100
    for ok in (0, 1.2, 2.5, 3, 10):
        c = copy.deepcopy(cfg)
        c["ui"]["swr_warn"] = ok
        config.validate(c)                                                         # 0 switches the warning off
    for bad in (-1, 0.5, 1.0, 11, 100):
        c = copy.deepcopy(cfg)
        c["ui"]["swr_warn"] = bad
        with pytest.raises(config.ConfigError):
            config.validate(c)
    for bad in (0, 9, 256):
        c = copy.deepcopy(cfg)
        c["ui"]["swr_raw_at_3"] = bad
        with pytest.raises(config.ConfigError):
            config.validate(c)


async def test_swr_warning_is_editable_and_reaches_the_page(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    r = await client.put("/api/config", json={"ui": {"swr_warn": 2.5, "swr_raw_at_3": 120}}, headers=root.h)
    assert r.status == 200 and sorted((await r.json())["changed"]) == ["ui.swr_raw_at_3", "ui.swr_warn"]
    saved = config.load(tmp_path / "config.toml")
    assert saved["ui"]["swr_warn"] == 2.5 and saved["ui"]["swr_raw_at_3"] == 120
    assert (await client.put("/api/config", json={"ui": {"swr_warn": 0.5}}, headers=root.h)).status == 400
    await close_all(root)


def test_the_swr_meter_code_has_the_alarm():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "meters.js").read_text(encoding="utf-8")
    assert "swr.setAlarm(show)" in js and "cal.swr_warn" in js and "swr_raw_at_3" in js and "High SWR" in js


async def test_meter_calibration_is_editable_and_validated(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    r = await client.put("/api/config", json={"ui": {"meter_alc_full": 60, "meter_comp_full": 120}}, headers=root.h)
    assert r.status == 200 and sorted((await r.json())["changed"]) == ["ui.meter_alc_full", "ui.meter_comp_full"]
    saved = config.load(tmp_path / "config.toml")
    assert saved["ui"]["meter_alc_full"] == 60 and saved["ui"]["meter_comp_full"] == 120
    assert (await client.put("/api/config", json={"ui": {"meter_alc_full": 5}}, headers=root.h)).status == 400
    await close_all(root)
