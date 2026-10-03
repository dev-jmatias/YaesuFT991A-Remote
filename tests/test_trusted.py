import asyncio
import copy
import json
import sqlite3

from test_phase8 import (PW, User, add_user, admin_user, close_all, login_as, make_app, until,  # noqa: F401
                         wait_closed)
from test_web import recv_until

from radio_remote import config
from radio_remote.app import K_DRIVER
from radio_remote.auth import AuthStore


async def hold_control(root):
    ws, _ = await root.ws()
    await recv_until(ws, lambda m: m["t"] == "lease" and m["d"]["holder"])
    return ws


async def test_untrusted_request_still_asks_but_trusted_takes_over_at_once(make_app):
    client = await make_app(lease_timeout_s=30)                 # long timeout: any grant below is NOT the timeout
    root = await admin_user(client)
    bob_id = await add_user(root, "bob", "operator")
    await add_user(root, "tom", "operator")
    ws_root = await hold_control(root)

    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()
    a = await bob.cmd(ws_bob, 1, type="request_control")
    assert a["ok"] and a["result"] == "pending"                  # bob is not trusted: root is asked
    await recv_until(ws_root, lambda m: m["t"] == "lease_request")
    await bob.cmd(ws_bob, 2, type="release_control")             # tidy: bob withdraws by releasing (he holds nothing)
    await root.cmd(ws_root, 3, type="respond_control", accept=False)

    r = await client.patch(f"/api/users/{bob_id}", json={"trusted": True}, headers=root.h)
    assert r.status == 200
    assert (await client.get("/api/me")).status == 200            # the admin was not signed out by a trust change
    a = await bob.cmd(ws_bob, 4, type="request_control")
    assert a["ok"] and a["result"] == "granted"                  # trusted: immediate, no prompt, no waiting
    lease = await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"]["user"] == "bob")
    assert lease["d"]["pending"] is None
    taken = await recv_until(ws_root, lambda m: m["t"] == "lease_taken")      # sent right after the lease update
    assert taken["by"] == "bob"                                  # the previous holder is told who took over
    assert (await bob.cmd(ws_bob, 5, type="set_frequency", hz=7_100_000))["ok"]
    assert (await root.cmd(ws_root, 6, type="set_frequency", hz=7_050_000))["ok"] is False
    await close_all(bob, root)


async def test_trusted_user_cannot_take_control_while_transmitting(make_app):
    client = await make_app()
    root = await admin_user(client)
    bob_id = await add_user(root, "bob", "operator", )
    await client.patch(f"/api/users/{bob_id}", json={"trusted": True}, headers=root.h)
    drv = client.server.app[K_DRIVER]
    ws_root = await hold_control(root)
    assert (await root.cmd(ws_root, 1, type="ptt", on=True))["ok"] and drv.state["tx"]
    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()
    a = await bob.cmd(ws_bob, 2, type="request_control")
    assert a["ok"] is False and "transmitting" in a["error"]
    assert drv.state["tx"]                                       # nobody was cut off
    await close_all(bob, root)


async def test_trust_is_ignored_for_viewers_and_revocation_is_live(make_app):
    client = await make_app(lease_timeout_s=30)
    root = await admin_user(client)
    vic_id = await add_user(root, "vic", "viewer")
    bob_id = await add_user(root, "bob", "operator")
    await client.patch(f"/api/users/{vic_id}", json={"trusted": True}, headers=root.h)
    await client.patch(f"/api/users/{bob_id}", json={"trusted": True}, headers=root.h)
    ws_root = await hold_control(root)
    vic = await login_as(root, "vic")
    ws_vic, _ = await vic.ws()
    a = await vic.cmd(ws_vic, 1, type="request_control")
    assert a["ok"] is False and "cannot take control" in a["error"]      # a viewer can never hold control

    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()
    # revoke trust WHILE bob is connected: it applies to his very next request, no reconnect needed
    await client.patch(f"/api/users/{bob_id}", json={"trusted": False}, headers=root.h)
    a = await bob.cmd(ws_bob, 2, type="request_control")
    assert a["ok"] and a["result"] == "pending"
    await close_all(vic, bob, root)


async def test_trusted_api_rules_and_audit(make_app):
    client = await make_app()
    root = await admin_user(client)
    r = await client.post("/api/users", json={"username": "ann", "password": PW, "role": "operator", "trusted": True}, headers=root.h)
    assert r.status == 201
    users = {u["username"]: u for u in (await (await client.get("/api/users")).json())["users"]}
    assert users["ann"]["trusted"] is True and users["root"]["trusted"] is False
    uid = users["ann"]["id"]
    for bad in ("yes", 1, None):
        assert (await client.patch(f"/api/users/{uid}", json={"trusted": bad}, headers=root.h)).status == 400
    ann = await login_as(root, "ann")
    assert (await ann.c.patch(f"/api/users/{uid}", json={"trusted": False}, headers=ann.h)).status == 403   # not an admin
    hello = await ann.ws()
    assert hello[1]["user"]["trusted"] is True                     # the UI uses this to label the button
    events = (await (await client.get("/api/audit?limit=50")).json())["events"]
    assert any(e["event"] == "user_updated" for e in events) or any(e["event"] == "user_created" for e in events)
    await close_all(ann, root)


def test_old_database_is_migrated_and_defaults_to_untrusted(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)                                      # schema from before the trusted list existed
    con.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
        pw_hash TEXT NOT NULL, role TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE sessions(sid_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf TEXT NOT NULL,
        created REAL NOT NULL, last_seen REAL NOT NULL);
        CREATE TABLE audit(ts REAL NOT NULL, event TEXT NOT NULL, username TEXT, ip TEXT, detail TEXT);""")
    from radio_remote.auth import hash_password
    con.execute("INSERT INTO users(username,pw_hash,role,created) VALUES('old',?,'operator',1)", (hash_password(PW),))
    con.commit()
    con.close()
    auth = AuthStore(db)
    assert auth.list_users()[0]["trusted"] is False
    sid, session = auth.login("old", PW, "1.1.1.1")
    assert session.trusted is False
    auth.set_trusted(session.user_id, True)
    assert auth.get_session(sid).trusted is True                   # session reads the live value
    AuthStore(db)                                                  # reopening twice does not fail on the existing column


async def test_request_timeout_is_configurable(make_app, tmp_path):
    cfg = copy.deepcopy(config.DEFAULTS)
    assert cfg["safety"]["control_request_timeout_s"] == 10
    for bad in (1, 500):
        c2 = copy.deepcopy(cfg)
        c2["safety"]["control_request_timeout_s"] = bad
        try:
            config.validate(c2)
            raise AssertionError("accepted")
        except config.ConfigError:
            pass
    client = await make_app()
    root = await admin_user(client)
    r = await client.put("/api/config", json={"safety": {"control_request_timeout_s": 20}}, headers=root.h)
    assert r.status == 200 and "safety.control_request_timeout_s" in (await r.json())["changed"]
    assert config.load(tmp_path / "config.toml")["safety"]["control_request_timeout_s"] == 20
    assert (await client.put("/api/config", json={"safety": {"control_request_timeout_s": 1}}, headers=root.h)).status == 400
    await close_all(root)
