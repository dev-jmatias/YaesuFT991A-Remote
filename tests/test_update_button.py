"""The "Update now" button: the page asks (admin + password), a root helper does the update; the page follows the status file."""
import shutil
import subprocess
import sys
import time

import pytest
from test_phase8 import PW, add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote import admin
from radio_remote.common import K_CFG, K_UPDATES

START, STATUS = "/api/admin/update/start", "/api/admin/update/status"


@pytest.fixture
async def ready(make_app, tmp_path, monkeypatch):
    """An app whose data folder is a temp folder, with the helper 'installed' and a newer version known."""
    client = await make_app()
    client.app[K_CFG]["storage"]["data_dir"] = str(tmp_path)
    unit = tmp_path / "radio-remote-update.path"
    unit.write_text("[Path]\n")
    monkeypatch.setattr(admin, "UPDATE_UNIT", unit)
    monkeypatch.setattr(client.app[K_UPDATES], "state", lambda: {"newer": True, "latest": "v9.9.9"})
    root = await admin_user(client)
    yield client, root, tmp_path
    await close_all(root)


async def test_the_whole_flow(ready):
    client, root, data = ready
    st = await (await client.get(STATUS)).json()
    assert st["available"] is True and st["state"] == "idle" and st["log"] == []
    r = await client.post(START, json={"confirm": True, "password": PW}, headers=root.h)
    assert r.status == 202
    assert (data / "update-request").read_text() == "user=root\n"                                 # all the page does: drop a request file
    assert (await (await client.get(STATUS)).json())["state"] == "requested"
    assert (await client.post(START, json={"confirm": True, "password": PW}, headers=root.h)).status == 409    # not twice
    (data / "update-request").unlink()                                                             # what the helper does first
    (data / "update-status").write_text(f"running {int(time.time())} root\n")
    (data / "update.log").write_text("Update requested by root\ndownloading radio-remote-v9.9.9.tar.gz ...\n")
    st = await (await client.get(STATUS)).json()
    assert st["state"] == "running" and st["by"] == "root" and st["log"][-1].startswith("downloading")
    assert (await client.post(START, json={"confirm": True, "password": PW}, headers=root.h)).status == 409
    (data / "update-status").write_text(f"done {int(time.time())} root\n")
    assert (await (await client.get(STATUS)).json())["state"] == "done"
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "update_requested" in events


async def test_rules(ready, monkeypatch):
    client, root, data = ready
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await op.c.post(START, json={"confirm": True, "password": PW}, headers=op.h)).status == 403
    assert (await op.c.get(STATUS)).status == 403
    assert (await client.post(START, json={"confirm": True, "password": "wrong password!"}, headers=root.h)).status == 403
    assert (await client.post(START, json={"password": PW}, headers=root.h)).status == 400                     # no confirmation
    assert (await client.post(START, json={"confirm": True}, headers=root.h)).status == 400                   # no password
    assert (await client.post(START, json={"confirm": True, "password": PW})).status == 403                   # no CSRF token
    assert not (data / "update-request").exists()
    monkeypatch.setattr(client.app[K_UPDATES], "state", lambda: {"newer": False, "latest": "v1.0.0"})
    r = await client.post(START, json={"confirm": True, "password": PW}, headers=root.h)
    assert r.status == 409 and "Check now" in await r.text()
    monkeypatch.setattr(client.app[K_UPDATES], "state", lambda: {"newer": True, "latest": "v9.9.9"})
    monkeypatch.setattr(admin, "UPDATE_UNIT", data / "missing.path")
    r = await client.post(START, json={"confirm": True, "password": PW}, headers=root.h)
    assert r.status == 409 and "helper is not installed" in await r.text()
    assert (await (await client.get(STATUS)).json())["available"] is False
    await close_all(op)


async def test_a_helper_that_died_does_not_block_the_button(ready):
    client, root, data = ready
    (data / "update-status").write_text(f"running {int(time.time()) - 4000} root\n")                 # started over an hour ago, never finished
    assert (await (await client.get(STATUS)).json())["state"] == "failed"
    assert (await client.post(START, json={"confirm": True, "password": PW}, headers=root.h)).status == 202


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs a Linux bash (on Windows it is run in WSL by hand)")
def test_the_helper_script_runs_the_update_and_records_the_outcome(tmp_path):
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "scripts" / "update_from_request.sh"
    if b"\r" in script.read_bytes():
        pytest.skip("script has CRLF line endings on this checkout")
    ok = tmp_path / "ok.sh"
    ok.write_text("#!/bin/bash\necho installing\nexit 0\n")
    bad = tmp_path / "bad.sh"
    bad.write_text("#!/bin/bash\necho boom\nexit 1\n")
    for name, fake, want in (("a", ok, "done"), ("b", bad, "failed")):
        data = tmp_path / name
        data.mkdir()
        (data / "update-request").write_text("user=jorge\n")
        env = {"PATH": "/usr/bin:/bin", "RR_DATA_DIR": str(data), "RR_SELF_UPDATE": str(fake), "RR_OWNER": "nobody"}
        subprocess.run(["bash", str(script)], env=env, check=True, timeout=30)
        assert not (data / "update-request").exists()                                                  # the request is consumed first
        assert (data / "update-status").read_text().split()[0] == want and (data / "update-status").read_text().split()[2] == "jorge"
        assert "Update requested by jorge" in (data / "update.log").read_text()
    again = subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin", "RR_DATA_DIR": str(data), "RR_SELF_UPDATE": str(ok)}, timeout=30)
    assert again.returncode == 0                                                                       # no request: nothing happens
