"""The "Reboot the system" button: the page asks (admin + password), a root helper does it; "Restart the service" is the existing restart hook."""
import shutil
import subprocess
import sys

import pytest
from test_phase8 import PW, add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote import admin
from radio_remote.common import K_CFG, K_DRIVER

REBOOT, POWER = "/api/admin/reboot", "/api/admin/power"


@pytest.fixture
async def ready(make_app, tmp_path, monkeypatch):
    """An app whose data folder is a temp folder, with the reboot helper 'installed'."""
    client = await make_app()
    client.app[K_CFG]["storage"]["data_dir"] = str(tmp_path)
    unit = tmp_path / "radio-remote-power.path"
    unit.write_text("[Path]\n")
    monkeypatch.setattr(admin, "POWER_UNIT", unit)
    root = await admin_user(client)
    yield client, root, tmp_path
    await close_all(root)


async def test_the_whole_flow(ready):
    client, root, data = ready
    st = await (await client.get(POWER)).json()
    assert st["reboot"] is True and "restart" in st
    r = await client.post(REBOOT, json={"confirm": True, "password": PW}, headers=root.h)
    assert r.status == 202
    assert (data / "power-request").read_text() == "action=reboot\nuser=root\n"                    # all the page does: drop a request file
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "reboot_requested" in events


async def test_rules(ready, monkeypatch):
    client, root, data = ready
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await op.c.post(REBOOT, json={"confirm": True, "password": PW}, headers=op.h)).status == 403      # administrators only
    assert (await op.c.get(POWER)).status == 403
    assert (await client.post(REBOOT, json={"confirm": True, "password": "wrong password!"}, headers=root.h)).status == 403
    assert (await client.post(REBOOT, json={"password": PW}, headers=root.h)).status == 400                    # no confirmation
    assert (await client.post(REBOOT, json={"confirm": True}, headers=root.h)).status == 400                  # no password
    assert (await client.post(REBOOT, json={"confirm": True, "password": PW})).status == 403                  # no CSRF token
    assert not (data / "power-request").exists()
    drv = client.app[K_DRIVER]
    monkeypatch.setitem(drv.state, "tx", True)
    r = await client.post(REBOOT, json={"confirm": True, "password": PW}, headers=root.h)
    assert r.status == 409 and "transmitting" in await r.text()                                                # never while the radio transmits
    monkeypatch.setitem(drv.state, "tx", False)
    monkeypatch.setattr(admin, "POWER_UNIT", data / "missing.path")
    r = await client.post(REBOOT, json={"confirm": True, "password": PW}, headers=root.h)
    assert r.status == 409 and "helper is not installed" in await r.text()
    assert (await (await client.get(POWER)).json())["reboot"] is False
    assert not (data / "power-request").exists()
    await close_all(op)


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs a Linux bash (on Windows it is run in WSL by hand)")
def test_the_helper_script_only_ever_reboots(tmp_path):
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "scripts" / "power_from_request.sh"
    if b"\r" in script.read_bytes():
        pytest.skip("script has CRLF line endings on this checkout")
    marker = tmp_path / "rebooted"
    fake = f"touch {marker}"                                                                          # stands in for: systemctl reboot
    env = {"PATH": "/usr/bin:/bin", "RR_DATA_DIR": str(tmp_path), "RR_REBOOT_CMD": fake, "RR_REBOOT_DELAY": "0"}
    (tmp_path / "power-request").write_text("action=reboot\nuser=jorge\n")
    subprocess.run(["bash", str(script)], env=env, check=True, timeout=30)
    assert marker.exists() and not (tmp_path / "power-request").exists()                               # the request is consumed first
    marker.unlink()
    (tmp_path / "power-request").write_text("action=poweroff\nuser=jorge\n")                           # anything else is ignored
    subprocess.run(["bash", str(script)], env=env, check=True, timeout=30)
    assert not marker.exists() and not (tmp_path / "power-request").exists()
    again = subprocess.run(["bash", str(script)], env=env, timeout=30)
    assert again.returncode == 0 and not marker.exists()                                               # no request: nothing happens
