"""Hamlib NET rigctl link for a logbook: read and set frequency/mode only; never transmit; allow list; live on/off."""
import asyncio
import socket

import pytest
from test_phase8 import admin_user, close_all, make_app  # noqa: F401

from radio_remote import config, rigctl
from radio_remote.common import K_DRIVER, K_GUARD, K_RIGCTL


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def talk(port, *lines, wait=0.3):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    out = []
    for ln in lines:
        w.write((ln + "\n").encode())
        await w.drain()
        await asyncio.sleep(wait)
        out.append((await asyncio.wait_for(r.read(4096), 2)).decode())
    w.close()
    return out


async def enable(root, port, **extra):
    r = await root.c.put("/api/config", json={"rigctl": {"enabled": True, "port": port, **extra}}, headers=root.h)
    assert r.status == 200, await r.text()


def test_config_defaults_and_validation(tmp_path):
    cfg = config.load(None)
    assert cfg["rigctl"] == {"enabled": False, "port": 4532, "allow": "private", "set": True}
    cfg["rigctl"]["allow"] = "not-an-address"
    with pytest.raises(config.ConfigError):
        config.validate(cfg)
    cfg["rigctl"]["allow"] = "192.168.1.0/24, 100.64.0.0/10"
    config.validate(cfg)
    cfg["rigctl"]["port"] = cfg["server"]["port"]
    with pytest.raises(config.ConfigError):
        config.validate(cfg)


def test_allow_list():
    nets = rigctl.parse_allow("private")
    assert rigctl._allowed("192.168.1.20", nets) and rigctl._allowed("127.0.0.1", nets) and rigctl._allowed("::ffff:10.1.2.3", nets)
    assert not rigctl._allowed("8.8.8.8", nets) and not rigctl._allowed("100.64.0.5", nets)
    assert rigctl._allowed("100.64.0.5", rigctl.parse_allow("100.64.0.0/10"))


async def test_off_by_default_then_live_on_off_and_logbook_commands(make_app):
    client = await make_app()
    root = await admin_user(client)
    rc = client.app[K_RIGCTL]
    assert not rc.listening
    port = free_port()
    await enable(root, port)
    assert rc.listening
    drv = client.app[K_DRIVER]

    out = await talk(port, "f", "m", "t", "\\chk_vfo", "\\get_powerstat", "+f", "bogus")
    assert out[0].strip() == str(drv.state["frequency"])
    assert out[1].split()[0] == rigctl.TO_HAMLIB[drv.state["mode"]]
    assert out[2].strip() == "0" and out[3].strip() == "0" and out[4].strip() == "1"
    assert out[5].startswith("get_freq:\nFrequency: ") and out[5].endswith("RPRT 0\n")
    assert out[6].strip() == "RPRT -11"

    out = await talk(port, "F 7074000", "M LSB 2400", "f", "m")
    assert out[0].strip() == "RPRT 0" and out[1].strip() == "RPRT 0"
    assert drv.state["frequency"] == 7074000 and drv.state["mode"] == "LSB"
    assert "-1" in (await talk(port, "F 1"))[0]                                   # outside the radio's coverage
    assert "RPRT -1" in (await talk(port, "M NOPE 0"))[0]

    r = await root.c.put("/api/config", json={"rigctl": {"enabled": False}}, headers=root.h)
    assert r.status == 200 and not rc.listening
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)
    await close_all(root)


async def test_logbook_can_never_transmit_or_power_off(make_app):
    client = await make_app()
    root = await admin_user(client)
    port = free_port()
    await enable(root, port)
    guard = client.app[K_GUARD]
    guard.allow_ptt = True                                                        # even with transmitting switched on
    out = await talk(port, "T 1", "\\set_ptt 1", "\\set_powerstat 0", "T 0", "t")
    assert [o.strip() for o in out] == ["RPRT -9", "RPRT -9", "RPRT -9", "RPRT 0", "0"]
    assert not guard.keyed
    await close_all(root)


async def test_read_only_mode_and_no_retune_while_transmitting(make_app):
    client = await make_app()
    root = await admin_user(client)
    port = free_port()
    await enable(root, port, set=False)
    drv = client.app[K_DRIVER]
    before = drv.state["frequency"]
    assert (await talk(port, "F 7074000"))[0].strip() == "RPRT -9" and drv.state["frequency"] == before
    await root.c.put("/api/config", json={"rigctl": {"set": True}}, headers=root.h)
    drv._update(tx=True)
    assert (await talk(port, "F 7074000"))[0].strip() == "RPRT -9" and drv.state["frequency"] == before
    drv._update(tx=False)
    assert (await talk(port, "F 7074000"))[0].strip() == "RPRT 0"
    await close_all(root)


async def test_address_not_in_allow_list_is_dropped(make_app):
    client = await make_app()
    root = await admin_user(client)
    port = free_port()
    await enable(root, port, allow="10.99.0.0/16")                                # this test connects from 127.0.0.1
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(b"f\n")
    await w.drain()
    try:
        assert await asyncio.wait_for(r.read(100), 2) == b""                      # closed without an answer
    except ConnectionError:
        pass                                                                      # (Windows reports the close as a reset)
    assert client.app[K_RIGCTL].refused == 1
    st = await (await client.get("/api/admin/rigctl")).json()
    assert st["listening"] and st["refused"] == 1
    await close_all(root)


async def test_dump_state_has_the_layout_hamlib_expects(make_app):
    client = await make_app()
    text = client.app[K_RIGCTL]._dump_state()
    lines = text.splitlines()
    assert lines[0] == "1" and lines[-1] == "done" and "0 0 0 0 0 0 0" in lines
