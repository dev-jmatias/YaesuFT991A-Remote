"""PTT permission from the web UI (POST /api/admin/ptt) and callsign user names."""
import pytest
from conftest import setup_admin
from test_phase8 import PW, add_user, admin_user, close_all, login_as, make_app, recv_until  # noqa: F401

from radio_remote import config
from radio_remote.common import K_GUARD


async def post_ptt(user, **body):
    return await user.c.post("/api/admin/ptt", json=body, headers=user.h)


async def test_enabling_needs_the_password_and_is_applied_live_and_saved(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    guard = client.app[K_GUARD]
    ws, hello = await root.ws()
    assert guard.allow_ptt is False
    assert (await (await client.get("/api/config")).json())["ptt"]["enabled"] is False

    assert (await post_ptt(root, enabled=True, confirm=True)).status == 400                     # no password
    assert (await post_ptt(root, enabled=True, confirm=True, password="wrong password!")).status == 403
    assert (await post_ptt(root, enabled=True, password=PW)).status == 400                      # no confirm
    assert guard.allow_ptt is False and config.load(tmp_path / "config.toml")["safety"]["allow_ptt"] is False

    r = await post_ptt(root, enabled=True, confirm=True, password=PW)
    assert r.status == 200 and (await r.json())["enabled"] is True
    assert guard.allow_ptt is True                                                              # live, no restart
    assert config.load(tmp_path / "config.toml")["safety"]["allow_ptt"] is True                 # survives a restart
    assert (await (await client.get("/api/config")).json())["ptt"]["enabled"] is True
    msg = await recv_until(ws, lambda m: m["t"] == "safety")                                    # open pages learn about it
    assert msg["d"] == {"ptt_permitted": True}
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "ptt_enabled" in events and "ptt_permission_denied" in events
    await close_all(root)


async def test_disabling_needs_no_password_and_unkeys(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    guard = client.app[K_GUARD]
    assert (await post_ptt(root, enabled=True, confirm=True, password=PW)).status == 200
    await guard.request("root", True)                                                           # keyed on the mock radio
    assert guard.keyed
    r = await post_ptt(root, enabled=False, confirm=True)
    assert r.status == 200
    assert not guard.keyed and guard.allow_ptt is False
    assert config.load(tmp_path / "config.toml")["safety"]["allow_ptt"] is False
    await close_all(root)


async def test_only_admins_may_change_it_and_the_generic_form_still_cannot(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await post_ptt(op, enabled=True, confirm=True, password=PW)).status == 403
    assert (await client.put("/api/config", json={"safety": {"allow_ptt": True}}, headers=root.h)).status in (400, 403)
    assert client.app[K_GUARD].allow_ptt is False
    await close_all(root, op)


async def test_wrong_passwords_are_throttled(make_app):
    client = await make_app()
    root = await admin_user(client)
    codes = [(await post_ptt(root, enabled=True, confirm=True, password=f"wrong password {i}")).status for i in range(6)]
    assert codes[:4] == [403, 403, 403, 403] and codes[-1] == 429
    assert (await post_ptt(root, enabled=True, confirm=True, password=PW)).status == 429        # even the right one waits
    assert client.app[K_GUARD].allow_ptt is False
    await close_all(root)


async def test_needs_a_config_file(make_app):
    client = await make_app(config_path=None)
    root = await admin_user(client)
    assert (await post_ptt(root, enabled=True, confirm=True, password=PW)).status == 409
    assert client.app[K_GUARD].allow_ptt is False
    await close_all(root)


@pytest.mark.parametrize("name", ["G4XYZ/P", "2E0ABC", "m0dht", "EA1ABC/MM"])
async def test_a_callsign_can_be_the_user_name(client, name):
    csrf = await setup_admin(client, name, "correct horse battery")
    assert csrf
    r = await client.post("/api/login", json={"username": name.lower(), "password": "correct horse battery"})
    assert r.status == 200                                                                      # logins ignore case


def _src(*parts):
    from pathlib import Path
    return Path(__file__).resolve().parents[1].joinpath("frontend", *parts).read_text(encoding="utf-8")


def test_status_lights_and_the_ptt_lock_are_wired_up_in_the_frontend():
    # no browser in the test suite: check that the pieces that were verified by hand in the preview are still connected
    vfo = _src("src", "components", "vfo-panels.js")
    for light in ('["ipo", "IPO"', '["att", "ATT"', '["agc", "AGC"', '["tuner", "TUNER"'):  # ONE preamp and ONE AGC light (named after the setting)
        assert light in vfo
    assert '"ipo:' not in vfo and 'data-chip="ipo:' not in vfo                              # no separate AMP1 / AMP2 lights
    assert "s.ipo || " in vfo and "`AGC ${s.agc}`" in vfo
    assert "data-chips" in vfo and "paintChips(s)" in vfo
    ptt = _src("src", "components", "ptt.js")
    assert "lockbtn" in ptt and "ctx.setPttLock" in ptt and "blocked || (locked && !tx)" in ptt   # a running TX can still be released
    assert "ctx.ui.pttLock" in _src("src", "components", "controls.js")                           # TUNE transmits too: locked as well
    radio = _src("src", "pages", "radio.js")
    assert "rr.pttLock" in radio and "setPttLock" in radio
    css = _src("style.css")
    assert ".schip.on" in css and "button.lockbtn.on" in css


def test_lights_can_be_hidden_per_device_and_the_new_file_is_cached_for_offline_use():
    prefs = _src("src", "prefs.js")
    assert 'rr.lights' in prefs and "rr-lights" in prefs and "export function setLight" in prefs
    assert "getLights()" in _src("src", "components", "vfo-panels.js") and '"rr-lights"' in _src("src", "components", "vfo-panels.js")
    assert "setLight" in _src("src", "pages", "admin.js") and 'id="disp"' in _src("src", "pages", "admin.js")
    sw = _src("sw.js")
    assert "/src/prefs.js" in sw                                   # every module of the page must be in the offline shell list
    import re
    for module in re.findall(r'^import .* from "\./([\w-]+\.js)"|^import .* from "\.\./([\w-]+\.js)"', "".join(
            _src("src", *p) + "\n" for p in (("pages", "radio.js"), ("components", "vfo-panels.js"), ("pages", "admin.js"))), re.M):
        name = next(m for m in module if m)
        assert any(name in line for line in sw.splitlines()) or name in ("util.js", "api.js"), f"{name} missing from sw.js"


def test_login_page_has_a_show_password_toggle_and_the_callsign_wording():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "login.js").read_text(encoding="utf-8")
    assert 'name="showpw"' in js and 'f.password.type = f.showpw.checked ? "text" : "password"' in js
    assert "Callsign" in js


@pytest.mark.parametrize("name", ["", "/", "bad name", "x" * 33, "a;b", "../x"])
async def test_odd_user_names_are_refused(client, name):
    r = await client.post("/api/setup", json={"username": name, "password": "correct horse battery"})
    assert r.status == 400
