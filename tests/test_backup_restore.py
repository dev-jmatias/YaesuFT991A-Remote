"""Settings backup (download) and restore from the Admin page."""
import tomllib

from test_phase8 import PW, add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote import config


async def restore(user, text):
    return await user.c.post("/api/admin/restore", json={"toml": text}, headers=user.h)


async def test_download_is_a_valid_settings_file_for_administrators_only(make_app):
    client = await make_app()
    root = await admin_user(client)
    r = await client.get("/api/admin/backup")
    assert r.status == 200
    assert 'attachment; filename="radio-remote-settings-' in r.headers["Content-Disposition"] and r.headers["Cache-Control"] == "no-store"
    text = await r.text()
    assert text.startswith("# Radio Remote settings backup")
    data = tomllib.loads(text)                                                      # real TOML, every section present
    assert set(data) == set(config.DEFAULTS) and data["radio"]["model"] == "mock"
    assert "password" not in text.lower() and "pw_hash" not in text                 # no accounts, no secrets
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await op.c.get("/api/admin/backup")).status == 403
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "config_backup" in events
    await close_all(root, op)


async def test_restore_applies_only_what_the_web_ui_may_change(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    backup = """
[radio]
baud = 19200
[audio]
opus_bitrate = 24000
[ui]
swr_warn = 2.5
[server]
port = 9999
host = "0.0.0.0"
[safety]
allow_ptt = true
tx_timeout_s = 90
[storage]
data_dir = "/somewhere/else"
"""
    r = await restore(root, backup)
    assert r.status == 200, await r.text()
    body = await r.json()
    assert sorted(body["changed"]) == ["audio.opus_bitrate", "radio.baud", "safety.tx_timeout_s", "ui.swr_warn"]
    assert sorted(body["ignored"]) == ["safety.allow_ptt", "server.host", "server.port", "storage.data_dir"]
    assert body["restart_required"] is True
    saved = config.load(tmp_path / "config.toml")
    assert saved["radio"]["baud"] == 19200 and saved["audio"]["opus_bitrate"] == 24000 and saved["ui"]["swr_warn"] == 2.5
    assert saved["safety"]["tx_timeout_s"] == 90
    assert saved["safety"]["allow_ptt"] is False                                    # a backup can never switch transmitting on
    assert saved["server"]["port"] == 8080 and saved["server"]["host"] == "127.0.0.1" and saved["storage"]["data_dir"] == "data"
    assert (tmp_path / "config.toml.bak").exists()                                  # the previous file is kept
    shown = (await (await client.get("/api/config")).json())["config"]
    assert shown["radio"]["baud"] == 19200                                          # the page shows the restored values
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "config_restored" in events
    await close_all(root)


async def test_download_then_restore_is_a_no_op(make_app):
    client = await make_app()
    root = await admin_user(client)
    text = await (await client.get("/api/admin/backup")).text()
    r = await restore(root, text)
    assert r.status == 200
    body = await r.json()
    assert body["changed"] == [] and body["restart_required"] is False
    await close_all(root)


async def test_bad_backups_are_refused_and_change_nothing(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    before = (tmp_path / "config.toml").read_text()
    for text, why in (("this is = = not toml", "invalid toml"), ("", "empty"), ("[server]\nport = 1\n", "nothing restorable"),
                      ("[radio]\nbaud = 1234\n", "invalid value"), ("[nosuchsection]\nx = 1\n", "unknown section"),
                      ("[radio]\nnosuchkey = 1\n", "unknown key"), ("[audio]\nopus_bitrate = \"high\"\n", "wrong type")):
        r = await restore(root, text)
        assert r.status == 400, (why, r.status, await r.text())
    big = "# " + "x" * 70000 + "\n[radio]\nbaud = 9600\n"
    assert (await restore(root, big)).status == 413
    assert (await root.c.post("/api/admin/restore", json={"toml": 5}, headers=root.h)).status == 400
    assert (tmp_path / "config.toml").read_text() == before                         # the file is untouched
    await close_all(root)


async def test_restore_needs_an_administrator_and_a_config_file(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await restore(op, "[audio]\nopus_bitrate = 24000\n")).status == 403
    await close_all(root, op)
    client2 = await make_app(config_path=None)
    root2 = await admin_user(client2)
    assert (await restore(root2, "[audio]\nopus_bitrate = 24000\n")).status == 409
    await close_all(root2)


def test_the_admin_page_has_the_backup_buttons():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "admin.js").read_text(encoding="utf-8")
    assert "/api/admin/backup" in js and "/api/admin/restore" in js and 'id="bk-dl"' in js and 'id="bk-up"' in js
