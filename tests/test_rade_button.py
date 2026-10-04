"""The Install RADE button (POST /api/admin/rade/install): administrators only, confirmation needed, 64-bit ARM only, picked up without a restart."""
import pytest
from test_phase8 import add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote import admin
from radio_remote.audio import freedv, rade
from radio_remote.common import K_AUDIO

URL = "/api/admin/rade/install"


@pytest.fixture
def arm_pi(monkeypatch):
    """A 64-bit ARM Pi where RADE is missing until the (fake) installer has run."""
    state = {"installed": False, "calls": []}
    monkeypatch.setattr(admin.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr("radio_remote.app.platform.machine", lambda: "aarch64")
    monkeypatch.setattr(rade, "loaded_path", lambda: "")                                   # (a machine that has the real library must not leak into these tests)
    monkeypatch.setattr(freedv, "available", lambda: (True, ""))
    monkeypatch.setattr(freedv, "mode_status",
                        lambda: {"1600": "", "700D": "", "700E": "", "RADE": "" if state["installed"] else "RADE is not installed"})

    async def fake_install(dest, repo):
        state["calls"].append((str(dest), repo))
        if state.get("fail"):
            return 1, "downloading ...\nCHECKSUM MISMATCH for radio-remote-rade-linux-aarch64.tar.xz. Nothing was installed.\n"
        state["installed"] = True
        return 0, f"checksum ok\nINSTALLED {dest}\n"

    monkeypatch.setattr(admin, "_run_rade_install", fake_install)
    return state


async def test_install_makes_rade_available_at_once(make_app, arm_pi):
    client = await make_app()
    root = await admin_user(client)
    info = await (await client.get("/api/freedv")).json()
    assert info["unavailable"] == {"RADE": "RADE is not installed"} and info["rade_installable"] is True and "RADE" not in info["modes"]
    assert info["arch"] == "aarch64" and info["rade_path"] == ""
    r = await client.post(URL, json={"confirm": True}, headers=root.h)
    body = await r.json()
    assert r.status == 200 and body["ok"] is True and "INSTALLED" in body["message"]
    assert arm_pi["calls"][0][1] == "dev-jmatias/YaesuFT991A-Remote" and arm_pi["calls"][0][0].replace("\\", "/").endswith("/lib")
    info = await (await client.get("/api/freedv")).json()
    assert "RADE" in info["modes"] and info["unavailable"] == {}
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "rade_installed" in events
    assert client.app[K_AUDIO].freedv_state()["modes"] == ["1600", "700D", "700E", "RADE"]
    await close_all(root)


async def test_rules(make_app, arm_pi, monkeypatch):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await op.c.post(URL, json={"confirm": True}, headers=op.h)).status == 403          # administrators only
    assert (await client.post(URL, json={}, headers=root.h)).status == 400                       # confirmation needed
    assert (await client.post(URL, json={"confirm": True})).status == 403                        # no CSRF token
    monkeypatch.setattr(admin.platform, "machine", lambda: "x86_64")
    r = await client.post(URL, json={"confirm": True}, headers=root.h)
    assert r.status == 409 and "64-bit ARM" in await r.text() and arm_pi["calls"] == []
    monkeypatch.setattr(admin.platform, "machine", lambda: "aarch64")
    await admin._rade_lock.acquire()                                                              # an installation is already running
    try:
        assert (await client.post(URL, json={"confirm": True}, headers=root.h)).status == 409
    finally:
        admin._rade_lock.release()
    await close_all(root, op)


async def test_a_failed_download_is_reported_and_nothing_changes(make_app, arm_pi):
    client = await make_app()
    root = await admin_user(client)
    arm_pi["fail"] = True
    r = await client.post(URL, json={"confirm": True}, headers=root.h)
    body = await r.json()
    assert r.status == 502 and body["ok"] is False and "CHECKSUM MISMATCH" in body["error"]
    info = await (await client.get("/api/freedv")).json()
    assert "RADE" not in info["modes"]
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "rade_install_failed" in events
    await close_all(root)


def test_a_library_installed_later_is_found_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(rade, "_LIB", None)
    monkeypatch.setattr(rade, "_ERR", "")
    monkeypatch.delenv("RADIO_REMOTE_RADE_LIB", raising=False)
    monkeypatch.setattr(rade, "_EXTRA_DIR", tmp_path)
    monkeypatch.setattr(rade, "_candidates", lambda: [tmp_path / rade.LIB_NAME])
    assert rade.available()[0] is False and "not installed" in rade.available()[1]
    (tmp_path / rade.LIB_NAME).write_bytes(b"not a real library")                                 # now something is there (but unusable)
    assert rade.available()[0] is False and "not installed" in rade.available()[1]               # the failure is cached ...
    rade.reset()                                                                                  # ... until the install resets it
    ok, why = rade.available()
    assert ok is False and "could not be loaded" in why                                           # it looked again and found the file
