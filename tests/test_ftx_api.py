"""FT8/FT4 over HTTP and WebSocket: state, settings, the contact log and its ADIF export, the upload endpoint, and that no secret ever leaves the Pi."""
import json

from test_phase8 import PW, add_user, admin_user, close_all, login_as, make_app  # noqa: F401

import pytest


@pytest.fixture(autouse=True)
def private_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                       # the default data folder is relative ("data"): the log of these tests must not land in the project or be shared between tests


QSO = {"call": "k1abc", "gridsquare": "fn42", "mode": "FT8", "rst_sent": "-10", "rst_rcvd": "-05", "freq_mhz": 14.0745, "qso_date": "20261017", "time_on": "101500",
       "qso_date_off": "20261017", "time_off": "101600"}


async def test_state_and_the_contact_log(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    st = await (await client.get("/api/ftx")).json()
    assert st["available"] and st["on"] is False and st["mode"] == "FT8" and st["history"] == [] and "FT4" in st["bands"]
    assert st["bands"]["FT8"][5] == {"name": "20m", "hz": 14074000}
    r = await client.post("/api/ftx/log", json={**QSO, "call": "NOT A CALL"}, headers=root.h)
    assert r.status == 400
    r = await client.post("/api/ftx/log", json=QSO, headers=root.h)
    assert r.status == 201
    q = (await r.json())["q"]
    assert q["call"] == "K1ABC" and q["band"] == "20m" and q["station_callsign"] == "" and q["uploaded"] == {}
    log = (await (await client.get("/api/ftx/log")).json())
    assert log["total"] == 1 and log["log"][0]["id"] == q["id"]
    adi = await (await client.get("/api/ftx/log.adi")).text()
    assert "<CALL:5>K1ABC" in adi and "<EOR>" in adi
    r = await client.get("/api/ftx/log.adi")
    assert "attachment" in r.headers["Content-Disposition"]
    assert (await client.post(f"/api/ftx/log/{q['id']}/delete", json={}, headers=root.h)).status == 200
    assert (await client.post(f"/api/ftx/log/{q['id']}/delete", json={}, headers=root.h)).status == 404
    await close_all(root)


async def test_viewers_can_look_but_not_change_the_log(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "watcher", "viewer")
    v = await login_as(root, "watcher")
    assert (await v.c.get("/api/ftx")).status == 200 and (await v.c.get("/api/ftx/log")).status == 200
    assert (await v.c.post("/api/ftx/log", json=QSO, headers=v.h)).status == 403
    assert (await v.c.post("/api/ftx/upload", json={"service": "qrz"}, headers=v.h)).status == 403
    await close_all(root, v)


async def test_upload_without_credentials_says_so_and_a_bad_service_is_refused(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    await client.post("/api/ftx/log", json=QSO, headers=root.h)
    r = await client.post("/api/ftx/upload", json={"service": "qrz"}, headers=root.h)
    body = await r.json()
    assert r.status == 200 and body["sent"] == 0 and body["failed"] == 1 and "not set" in body["message"] and body["left"] == 1
    r = await client.post("/api/ftx/upload", json={"service": "clublog"}, headers=root.h)
    assert "not set" in (await r.json())["message"]
    assert (await client.post("/api/ftx/upload", json={"service": "lotw"}, headers=root.h)).status == 400
    log = (await (await client.get("/api/ftx/log")).json())["log"]
    assert log[0]["uploaded"]["qrz"]["ok"] is False
    await close_all(root)


async def test_secrets_are_masked_kept_on_save_and_left_out_of_the_backup(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    r = await client.put("/api/config", json={"upload": {"qrz_key": "SECRETKEY123", "eqsl_user": "me", "eqsl_password": "pw12345", "clublog_api": "APIKEY"},
                                              "ftx": {"callsign": "ct1xyz", "grid_square": "IN51"}}, headers=root.h)
    assert r.status == 200, await r.text()
    cfg = (await (await client.get("/api/config")).json())["config"]
    assert cfg["upload"]["qrz_key"] == "********" and cfg["upload"]["eqsl_password"] == "********" and cfg["upload"]["clublog_api"] == "********"
    assert cfg["upload"]["eqsl_user"] == "me" and cfg["upload"]["clublog_password"] == ""            # nothing set: nothing masked
    assert "SECRETKEY123" not in json.dumps(cfg) and cfg["ftx"]["callsign"] == "ct1xyz"
    # the page sends the mask back when the person did not touch the field: the saved key must survive
    r = await client.put("/api/config", json={"upload": {"qrz_key": "********", "eqsl_user": "you"}}, headers=root.h)
    assert r.status == 200
    backup = await (await client.get("/api/admin/backup")).text()
    assert "SECRETKEY123" not in backup and "pw12345" not in backup and "APIKEY" not in backup and "qrz_key" not in backup
    r = await client.post("/api/ftx/log", json=QSO, headers=root.h)
    assert (await r.json())["q"]["station_callsign"] == "CT1XYZ"                                    # the station callsign comes from the settings
    st = await (await client.get("/api/ftx")).json()
    assert st["upload"]["qrz"] is True and st["upload"]["eqsl"] is True and st["upload"]["clublog"] is False and st["settings"]["callsign"] == "CT1XYZ"
    await close_all(root)


async def test_bad_settings_are_refused(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    for body in ({"ftx": {"grid_square": "ZZ99"}}, {"ftx": {"tx_level_db": 5}}, {"ftx": {"max_tx_repeats": 99}}, {"ftx": {"mode": "FT2"}}, {"ftx": {"callsign": "bad call!"}},
                 {"upload": {"qrz_key": "x" * 300}}):
        assert (await client.put("/api/config", json=body, headers=root.h)).status == 400, body
    await close_all(root)


async def test_commands_over_the_websocket(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    ws, hello = await root.ws()
    assert hello["audio"]["ftx"]["available"] is True and hello["audio"]["ftx"]["on"] is False
    await root.cmd(ws, 1, type="request_control")
    ack = await root.cmd(ws, 2, type="ftx", on=True, mode="FT4")
    assert ack["ok"], ack
    st = await (await client.get("/api/ftx")).json()
    assert st["on"] is True and st["mode"] == "FT4"
    ack = await root.cmd(ws, 3, type="ftx_tx", text="CQ CT1XYZ IN51", freq=1500, parity=0)
    assert ack["ok"] and ack["result"]["message"] == "CQ CT1XYZ IN51", ack
    ack = await root.cmd(ws, 4, type="ftx_tx", text="THIS IS FAR TOO LONG FOR FREE TEXT", freq=1500, parity=0)
    assert not ack["ok"]
    ack = await root.cmd(ws, 5, type="ftx_tx", text="CQ CT1XYZ IN51", freq=50, parity=0)
    assert not ack["ok"]
    ack = await root.cmd(ws, 6, type="ftx_halt")
    assert ack["ok"]
    assert (await (await client.get("/api/ftx")).json())["pending"] is None
    ack = await root.cmd(ws, 7, type="ftx", on=False)
    assert ack["ok"] and (await (await client.get("/api/ftx")).json())["on"] is False
    ack = await root.cmd(ws, 8, type="ftx", on=True, mode="FT2")
    assert not ack["ok"]
    await close_all(root)


async def test_the_same_contact_logged_twice_is_kept_once(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    r1 = await client.post("/api/ftx/log", json=QSO, headers=root.h)
    r2 = await client.post("/api/ftx/log", json={**QSO, "time_on": "101900", "time_off": "102000"}, headers=root.h)       # four minutes later: the same contact
    assert r1.status == 201 and r2.status == 200
    j2 = await r2.json()
    assert j2["duplicate"] is True and j2["q"]["id"] == (await r1.json())["q"]["id"]
    assert (await (await client.get("/api/ftx/log")).json())["total"] == 1
    later = await client.post("/api/ftx/log", json={**QSO, "time_on": "113000", "time_off": "113100"}, headers=root.h)     # an hour later: a new contact
    other = await client.post("/api/ftx/log", json={**QSO, "freq_mhz": 7.0745}, headers=root.h)                           # another band
    assert later.status == 201 and other.status == 201
    assert (await (await client.get("/api/ftx/log")).json())["total"] == 3
    await close_all(root)


async def test_only_the_page_that_has_control_can_log(make_app, ftx):
    client = await make_app()
    root = await admin_user(client)
    ws, hello = await root.ws()                                              # the first page takes control by itself (nobody had it)
    ws2, hello2 = await root.ws()                                            # a second page of the same user is only watching
    r = await client.post("/api/ftx/log", json={**QSO, "conn": hello["conn"]}, headers=root.h)
    assert r.status == 201 and "conn" not in (await r.json())["q"]
    r = await client.post("/api/ftx/log", json={**QSO, "call": "W1AW", "conn": hello2["conn"]}, headers=root.h)
    assert r.status == 403
    r = await client.post("/api/ftx/log", json={**QSO, "call": "W1AW", "conn": "nonsense"}, headers=root.h)
    assert r.status == 403
    assert (await (await client.get("/api/ftx/log")).json())["total"] == 1
    await close_all(root)
