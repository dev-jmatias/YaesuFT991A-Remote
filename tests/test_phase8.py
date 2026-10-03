import asyncio
import copy
import json
import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer
from conftest import setup_admin
from test_web import origin, recv_until

from radio_remote import config
from radio_remote.app import K_AUTH, K_DRIVER, K_GUARD, K_HUB, create_app
from radio_remote.auth import AuthStore
from radio_remote.logs import scrub
from radio_remote.radio.mock import MockDriver
from radio_remote.ratelimit import TokenBucket

PW = "correct horse battery"


# ------------------------------------------------------------------ fixtures
@pytest.fixture
async def make_app(tmp_path):
    made = []

    async def make(lease_timeout_s=10.0, config_path="default", restart_hook=None):
        cfg = copy.deepcopy(config.DEFAULTS)
        if config_path == "default":
            config_path = tmp_path / "config.toml"
            config.save(cfg, config_path)
        app = create_app(cfg, driver=MockDriver(seed=1), auth=AuthStore(":memory:"), config_path=config_path,
                         restart_hook=restart_hook, lease_timeout_s=lease_timeout_s)
        c = TestClient(TestServer(app))
        await c.start_server()
        made.append(c)
        return c

    yield make
    for c in made:
        await c.close()


class User:
    """A logged-in browser: own cookie jar, own CSRF token, can open websockets."""

    def __init__(self, client, name, csrf):
        self.c, self.name, self.csrf = client, name, csrf
        self.h = {"X-CSRF-Token": csrf}
        self.sockets = []

    async def ws(self):
        ws = await self.c.ws_connect("/ws", headers=origin(self.c))
        hello = json.loads((await ws.receive()).data)
        self.sockets.append(ws)
        return ws, hello

    async def cmd(self, ws, i, **msg):
        await ws.send_json({"id": i, "type": msg.pop("type"), **msg})
        return await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == i)


async def admin_user(client):
    csrf = await setup_admin(client, "root", PW)
    return User(client, "root", csrf)


async def add_user(admin, name, role, pw=PW):
    r = await admin.c.post("/api/users", json={"username": name, "password": pw, "role": role}, headers=admin.h)
    assert r.status == 201, await r.text()
    return (await r.json())["id"]


async def login_as(admin, name, pw=PW):
    c2 = TestClient(admin.c.server)                    # same server, separate cookie jar
    r = await c2.post("/api/login", json={"username": name, "password": pw})
    assert r.status == 200, await r.text()
    return User(c2, name, (await r.json())["csrf"])


async def close_all(*users):
    for u in users:
        for ws in u.sockets:
            await ws.close()
        if u.c is not None and u.name != "root":
            await u.c.session.close()


async def wait_closed(ws, timeout=3.0):
    async def go():
        while True:
            m = await ws.receive()
            if m.type.name in ("CLOSE", "CLOSING", "CLOSED"):
                return m
    return await asyncio.wait_for(go(), timeout)


async def until(pred, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------- unit bits
def test_token_bucket():
    t = [0.0]
    b = TokenBucket(rate=10, burst=5, clock=lambda: t[0])
    assert [b.allow() for _ in range(6)] == [True] * 5 + [False]
    t[0] += 0.3
    assert [b.allow() for _ in range(4)] == [True, True, True, False]


def test_log_scrubbing():
    assert "hunter2" not in scrub("login password=hunter2 ok")
    assert "abc123" not in scrub("Cookie: rr_session=abc123; other=1")
    assert "abc123" not in scrub("rr_session=abc123")
    assert "tok" not in scrub("X-CSRF-Token: tok")
    assert "secret-value" not in scrub('{"password": "secret-value"}')
    assert scrub("normal message about frequency 14200000") == "normal message about frequency 14200000"


# --------------------------------------------------------------- lease flows
async def test_first_operator_gets_control_viewer_and_second_do_not(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    await add_user(root, "vic", "viewer")
    ws_root, hello = await root.ws()
    assert hello["lease"]["holder"] is None                            # not yet: granted right after hello
    lease = await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"])
    assert lease["d"]["holder"]["user"] == "root"

    bob = await login_as(root, "bob")
    ws_bob, hello_b = await bob.ws()
    assert hello_b["lease"]["holder"]["user"] == "root"
    a = await bob.cmd(ws_bob, 1, type="set_frequency", hz=7_100_000)
    assert a["ok"] is False and "root has control" in a["error"]

    vic = await login_as(root, "vic")
    ws_vic, _ = await vic.ws()
    a = await vic.cmd(ws_vic, 1, type="request_control")
    assert a["ok"] is False and "cannot take control" in a["error"]
    a = await vic.cmd(ws_vic, 2, type="set_level", name="af_gain", value=5)
    assert a["ok"] is False
    assert (await root.cmd(ws_root, 5, type="set_frequency", hz=7_100_000))["ok"]
    await close_all(bob, vic, root)


async def test_request_accept_transfers_control(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    ws_root, _ = await root.ws()
    await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"])
    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()

    a = await bob.cmd(ws_bob, 1, type="request_control")
    assert a["ok"] and a["result"] == "pending"
    req = await recv_until(ws_root, lambda m: m["t"] == "lease_request")
    assert req["from"] == "bob"
    assert (await root.cmd(ws_root, 2, type="respond_control", accept=True))["ok"]
    lease = await recv_until(ws_bob, lambda m: m["t"] == "lease" and m["d"]["holder"]["user"] == "bob")
    assert lease["d"]["pending"] is None
    assert (await bob.cmd(ws_bob, 3, type="set_frequency", hz=7_100_000))["ok"]
    a = await root.cmd(ws_root, 4, type="set_frequency", hz=7_050_000)
    assert a["ok"] is False and "bob has control" in a["error"]
    await close_all(bob, root)


async def test_request_deny_keeps_holder(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    ws_root, _ = await root.ws()
    await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"])
    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()
    await bob.cmd(ws_bob, 1, type="request_control")
    await recv_until(ws_root, lambda m: m["t"] == "lease_request")
    await root.cmd(ws_root, 2, type="respond_control", accept=False)
    denied = await recv_until(ws_bob, lambda m: m["t"] == "lease_denied")
    assert denied["by"] == "root"
    assert (await root.cmd(ws_root, 3, type="set_frequency", hz=7_100_000))["ok"]
    a = await bob.cmd(ws_bob, 4, type="respond_control", accept=True)       # only the holder may answer
    assert a["ok"] is False
    await close_all(bob, root)


async def test_unanswered_request_is_granted_after_timeout(make_app):
    client = await make_app(lease_timeout_s=0.3)
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    ws_root, _ = await root.ws()
    await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"])
    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()
    await bob.cmd(ws_bob, 1, type="request_control")
    lease = await recv_until(ws_bob, lambda m: m["t"] == "lease" and m["d"]["holder"]["user"] == "bob", timeout=3)
    assert lease["d"]["pending"] is None
    await close_all(bob, root)


async def test_no_handover_while_transmitting_but_admin_force_unkeys(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    drv = client.server.app[K_DRIVER]
    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()                                         # bob connects first => bob holds control
    await recv_until(ws_bob, lambda m: m["t"] == "lease" and m["d"]["holder"])
    ws_root, _ = await root.ws()
    assert (await bob.cmd(ws_bob, 1, type="ptt", on=True))["ok"] and drv.state["tx"]

    a = await root.cmd(ws_root, 2, type="request_control")
    assert a["ok"] is False and "transmitting" in a["error"]
    assert drv.state["tx"]

    a = await bob.cmd(ws_bob, 3, type="force_control")                 # not an admin
    assert a["ok"] is False and "admin" in a["error"]
    assert (await root.cmd(ws_root, 4, type="force_control"))["ok"]    # admin override
    assert drv.state["tx"] is False                                     # transmitter was un-keyed first
    lease = await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"]["user"] == "root")
    assert lease
    a = await bob.cmd(ws_bob, 5, type="ptt", on=True)
    assert a["ok"] is False
    await close_all(bob, root)


async def test_ptt_requires_lease_and_foreign_heartbeat_is_ignored(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    drv = client.server.app[K_DRIVER]
    ws_root, _ = await root.ws()
    await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"])
    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()
    a = await bob.cmd(ws_bob, 1, type="ptt", on=True)
    assert a["ok"] is False and not drv.state["tx"]
    assert (await root.cmd(ws_root, 2, type="ptt", on=True))["ok"]
    for _ in range(8):                                                  # bob "keeps it alive": must not count
        await ws_bob.send_json({"type": "ptt_hb"})
        await asyncio.sleep(0.2)
    assert drv.state["tx"] is False                                     # root sent no heartbeats => un-keyed
    await close_all(bob, root)


async def test_holder_disconnect_frees_lease_and_unkeys(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    drv = client.server.app[K_DRIVER]
    ws_root, _ = await root.ws()
    await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"])
    await root.cmd(ws_root, 1, type="ptt", on=True)
    bob = await login_as(root, "bob")
    ws_bob, _ = await bob.ws()
    await ws_root.close()
    await until(lambda: not drv.state["tx"])
    lease = await recv_until(ws_bob, lambda m: m["t"] == "lease" and m["d"]["holder"] is None)
    assert lease
    a = await bob.cmd(ws_bob, 2, type="request_control")
    assert a["ok"] and a["result"] == "granted"
    await close_all(bob)


async def test_same_user_other_device_takes_over_immediately(make_app):
    client = await make_app()
    root = await admin_user(client)
    ws1, _ = await root.ws()
    await recv_until(ws1, lambda m: m["t"] == "lease" and m["d"]["holder"])
    ws2, h2 = await root.ws()
    a = await root.cmd(ws2, 1, type="request_control")
    assert a["ok"] and a["result"] == "granted"
    assert (await root.cmd(ws2, 2, type="set_frequency", hz=7_100_000))["ok"]
    assert (await root.cmd(ws1, 3, type="set_frequency", hz=7_050_000))["ok"] is False
    await close_all(root)


async def test_release_control(make_app):
    client = await make_app()
    root = await admin_user(client)
    ws, _ = await root.ws()
    await recv_until(ws, lambda m: m["t"] == "lease" and m["d"]["holder"])
    assert (await root.cmd(ws, 1, type="release_control"))["ok"]
    lease = await recv_until(ws, lambda m: m["t"] == "lease" and m["d"]["holder"] is None)
    assert lease
    a = await root.cmd(ws, 2, type="set_frequency", hz=7_100_000)
    assert a["ok"] is False and "Nobody has control" in a["error"]
    assert (await root.cmd(ws, 3, type="release_control"))["ok"] is False
    await close_all(root)


async def test_command_rate_limit_but_heartbeats_exempt(make_app):
    client = await make_app()
    root = await admin_user(client)
    ws, _ = await root.ws()
    await recv_until(ws, lambda m: m["t"] == "lease" and m["d"]["holder"])
    for i in range(1, 160):
        await ws.send_json({"id": i, "type": "set_level", "name": "af_gain", "value": 10})
    acks = []
    while len(acks) < 159:
        acks.append(json.loads((await asyncio.wait_for(ws.receive(), 5)).data))
        acks = [a for a in acks if a["t"] == "ack"]
    limited = [a for a in acks if not a["ok"] and "rate limited" in a["error"]]
    assert limited and any(a["ok"] for a in acks)
    for _ in range(100):
        await ws.send_json({"type": "ptt_hb"})                           # never rate limited, never an error
    await asyncio.sleep(0.1)
    await close_all(root)


# ---------------------------------------------------------- user management
async def test_user_management_rules(make_app):
    client = await make_app()
    root = await admin_user(client)
    bob_id = await add_user(root, "bob", "operator")
    r = await client.post("/api/users", json={"username": "bob", "password": PW, "role": "viewer"}, headers=root.h)
    assert r.status == 409
    for bad in [{"username": "x y", "password": PW, "role": "viewer"}, {"username": "ok", "password": "short", "role": "viewer"},
                {"username": "ok", "password": PW, "role": "god"}, {"username": "ok"}]:
        assert (await client.post("/api/users", json=bad, headers=root.h)).status == 400, bad
    users = (await (await client.get("/api/users")).json())["users"]
    assert {u["username"]: u["role"] for u in users} == {"root": "admin", "bob": "operator"}
    assert all("pw_hash" not in u and "password" not in u for u in users)

    bob = await login_as(root, "bob")
    assert (await bob.c.get("/api/users")).status == 403                  # operator cannot administer
    assert (await bob.c.post("/api/users", json={"username": "z", "password": PW, "role": "admin"}, headers=bob.h)).status == 403
    assert (await bob.c.get("/api/diagnostics")).status == 403
    assert (await bob.c.get("/api/audit")).status == 403
    assert (await bob.c.put("/api/config", json={"logging": {"level": "DEBUG"}}, headers=bob.h)).status == 403

    root_id = next(u["id"] for u in users if u["username"] == "root")
    assert (await client.patch(f"/api/users/{root_id}", json={"role": "viewer"}, headers=root.h)).status == 400
    assert (await client.delete(f"/api/users/{root_id}", headers=root.h)).status == 400
    assert (await client.patch(f"/api/users/{bob_id}", json={}, headers=root.h)).status == 400
    assert (await client.patch("/api/users/9999", json={"role": "viewer"}, headers=root.h)).status == 404
    assert (await client.patch(f"/api/users/{bob_id}", json={"role": "admin"}, headers=root.h)).status == 200
    assert (await client.patch(f"/api/users/{root_id}", json={"role": "viewer"}, headers=root.h)).status == 400   # self
    await close_all(bob)


async def test_last_admin_cannot_be_removed(make_app):
    auth = AuthStore(":memory:")
    a = auth.create_user("a", PW, "admin")
    with pytest.raises(ValueError):
        auth.set_role(a, "viewer")
    with pytest.raises(ValueError):
        auth.delete_user(a)
    b = auth.create_user("b", PW, "admin")
    auth.set_role(a, "viewer")
    with pytest.raises(ValueError):
        auth.delete_user(b)


async def test_role_change_and_reset_kick_sessions(make_app):
    client = await make_app()
    root = await admin_user(client)
    bob_id = await add_user(root, "bob", "operator")
    bob = await login_as(root, "bob")
    ws, _ = await bob.ws()
    assert (await client.patch(f"/api/users/{bob_id}", json={"role": "viewer"}, headers=root.h)).status == 200
    await wait_closed(ws)
    assert ws.close_code == 4401

    bob2 = await login_as(root, "bob")                                      # fresh login sees the new role
    ws2, hello = await bob2.ws()
    assert hello["user"]["role"] == "viewer"
    assert (await client.patch(f"/api/users/{bob_id}", json={"password": "a brand new password"}, headers=root.h)).status == 200
    assert (await bob2.c.get("/api/me")).status == 401                      # old session revoked
    r = await bob2.c.post("/api/login", json={"username": "bob", "password": PW})
    assert r.status == 401
    r = await bob2.c.post("/api/login", json={"username": "bob", "password": "a brand new password"})
    assert r.status == 200
    await close_all(bob, bob2)


async def test_delete_user_kicks_and_removes(make_app):
    client = await make_app()
    root = await admin_user(client)
    bob_id = await add_user(root, "bob", "operator")
    bob = await login_as(root, "bob")
    ws, _ = await bob.ws()
    assert (await client.delete(f"/api/users/{bob_id}", headers=root.h)).status == 200
    await wait_closed(ws)
    assert ws.close_code == 4401
    assert (await bob.c.get("/api/me")).status == 401
    assert (await bob.c.post("/api/login", json={"username": "bob", "password": PW})).status == 401
    await close_all(bob)


async def test_change_own_password(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    b1 = await login_as(root, "bob")
    b2 = await login_as(root, "bob")                                        # a second device
    r = await b1.c.post("/api/me/password", json={"current": "wrong", "new": "another long password"}, headers=b1.h)
    assert r.status == 403
    r = await b1.c.post("/api/me/password", json={"current": PW, "new": "short"}, headers=b1.h)
    assert r.status == 400
    r = await b1.c.post("/api/me/password", json={"current": PW, "new": "another long password"}, headers=b1.h)
    assert r.status == 200
    assert (await b1.c.get("/api/me")).status == 200                        # this session survives
    assert (await b2.c.get("/api/me")).status == 401                        # the other one is revoked
    assert (await b1.c.post("/api/login", json={"username": "bob", "password": PW})).status == 401
    await close_all(b1, b2)


# ------------------------------------------------------------ clients / kick
async def test_clients_listing_and_kick(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    ws_root, _ = await root.ws()
    await recv_until(ws_root, lambda m: m["t"] == "lease" and m["d"]["holder"])
    bob = await login_as(root, "bob")
    ws_bob, hb = await bob.ws()
    info = await (await client.get("/api/clients")).json()
    users = {c["user"]: c for c in info["clients"]}
    assert users["root"]["holder"] is True and users["bob"]["holder"] is False
    assert info["lease"]["holder"]["user"] == "root"
    assert (await client.post("/api/clients/nope/kick", headers=root.h)).status == 404
    assert (await client.post(f"/api/clients/{hb['conn']}/kick", headers=root.h)).status == 200
    await wait_closed(ws_bob)
    assert ws_bob.close_code == 4403
    await close_all(bob, root)


# ------------------------------------------------------------------ config
async def test_config_get_and_put(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    info = await (await client.get("/api/config")).json()
    assert info["writable"] and "safety.allow_ptt" in info["locked"] and "mock" in info["models"]
    assert "allow_ptt" not in info["editable"]["safety"]

    r = await client.put("/api/config", json={"audio": {"opus_bitrate": 24000}, "radio": {"baud": 9600}}, headers=root.h)
    body = await r.json()
    assert r.status == 200 and sorted(body["changed"]) == ["audio.opus_bitrate", "radio.baud"] and body["restart_required"]
    on_disk = config.load(tmp_path / "config.toml")
    assert on_disk["audio"]["opus_bitrate"] == 24000 and on_disk["radio"]["baud"] == 9600
    assert on_disk["safety"]["allow_ptt"] is False                            # untouched

    r = await client.put("/api/config", json={"logging": {"level": "WARNING"}}, headers=root.h)
    assert (await r.json())["restart_required"] is False                      # applies live
    assert logging.getLogger().level == logging.WARNING
    logging.getLogger().setLevel(logging.INFO)


async def test_config_refuses_dangerous_or_invalid_changes(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    before = (tmp_path / "config.toml").read_text()
    for body, code in [
        ({"safety": {"allow_ptt": True}}, 403),                  # PTT can only be enabled by editing the file on the Pi
        ({"server": {"port": 9999}}, 400), ({"server": {"host": "0.0.0.0"}}, 400),
        ({"storage": {"data_dir": "/etc"}}, 400),
        ({"radio": {"model": "nope"}}, 400), ({"radio": {"baud": 12345}}, 400), ({"radio": {"baud": "fast"}}, 400),
        ({"audio": {"input_device": "hw:0; rm -rf /"}}, 400), ({"safety": {"tx_timeout_s": 5000}}, 400),
        ({"radio": {"nonexistent": 1}}, 403), ({"bogus": {"a": 1}}, 400), ({"radio": "x"}, 400),
    ]:
        r = await client.put("/api/config", json=body, headers=root.h)
        assert r.status == code, (body, r.status, await r.text())
    assert (tmp_path / "config.toml").read_text() == before                   # nothing was written
    assert client.server.app[K_GUARD].allow_ptt is False


async def test_config_put_needs_a_file(make_app):
    client = await make_app(config_path=None)
    root = await admin_user(client)
    assert (await (await client.get("/api/config")).json())["writable"] is False
    assert (await client.put("/api/config", json={"logging": {"level": "INFO"}}, headers=root.h)).status == 409


async def test_restart_endpoint(make_app):
    calls = []
    client = await make_app(restart_hook=lambda: calls.append(1))
    root = await admin_user(client)
    assert (await client.post("/api/admin/restart", json={}, headers=root.h)).status == 400
    assert calls == []
    assert (await client.post("/api/admin/restart", json={"confirm": True}, headers=root.h)).status == 200
    assert calls == [1]
    client2 = await make_app()
    root2 = await admin_user(client2)
    assert (await client2.post("/api/admin/restart", json={"confirm": True}, headers=root2.h)).status == 501


# ----------------------------------------------------- audit / diagnostics
async def test_audit_trail_records_security_events(make_app):
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "bob", "operator")
    ws, _ = await root.ws()
    await recv_until(ws, lambda m: m["t"] == "lease" and m["d"]["holder"])
    await root.cmd(ws, 1, type="ptt", on=True)
    await root.cmd(ws, 2, type="ptt", on=False)
    bad = TestClient(client.server)
    await bad.post("/api/login", json={"username": "bob", "password": "wrong password"})
    await bad.session.close()
    await client.put("/api/config", json={"audio": {"max_peers": 2}}, headers=root.h)
    events = (await (await client.get("/api/audit?limit=100")).json())["events"]
    names = [e["event"] for e in events]
    for expected in ("admin_created", "login", "user_created", "control_granted", "ptt_on", "ptt_off",
                     "login_failed", "config_changed"):
        assert expected in names, expected
    assert "wrong password" not in json.dumps(events) and PW not in json.dumps(events)
    assert (await client.get("/api/audit?limit=abc")).status == 400
    await close_all(root)


async def test_diagnostics_content_and_no_secrets(make_app):
    client = await make_app()
    root = await admin_user(client)
    logging.getLogger("test").warning("login with password=hunter2 and Cookie: rr_session=zzz")
    d = await (await client.get("/api/diagnostics")).json()
    for key in ("app", "system", "versions", "radio", "ptt", "audio", "clients", "lease", "log"):
        assert key in d, key
    assert d["versions"]["aiohttp"] and d["versions"]["python"] and d["radio"]["mock"] is True
    assert d["ptt"]["permitted"] is True and d["ptt"]["keyed"] is False
    blob = json.dumps(d)
    assert "hunter2" not in blob and "rr_session=zzz" not in blob
    assert any("[redacted]" in l for l in d["log"])
    await close_all(root)


# ------------------------------------------------------------ transport hygiene
async def test_headers_and_hsts_only_over_https(make_app):
    client = await make_app()
    r = await client.get("/api/status")
    assert "Strict-Transport-Security" not in r.headers
    assert "microphone=(self)" in r.headers["Permissions-Policy"] and "base-uri 'none'" in r.headers["Content-Security-Policy"]
    r = await client.get("/api/status", headers={"X-Forwarded-Proto": "https"})     # we are the local proxy's peer
    assert "max-age" in r.headers["Strict-Transport-Security"]
    r = await client.post("/api/setup", json={"username": "root", "password": PW}, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"] and "SameSite=Strict" in r.headers["Set-Cookie"]


async def test_login_throttling_uses_forwarded_ip_from_proxy(make_app):
    client = await make_app()
    root = await admin_user(client)
    for _ in range(6):
        await client.post("/api/login", json={"username": "root", "password": "nope nope nope"},
                          headers={"X-Forwarded-For": "203.0.113.9"})
    r = await client.post("/api/login", json={"username": "root", "password": PW}, headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status == 429                                                    # that client is locked out...
    r = await client.post("/api/login", json={"username": "root", "password": PW}, headers={"X-Forwarded-For": "203.0.113.10"})
    assert r.status == 200                                                    # ...other clients are not


# ----------------------------------------------------------------------- recovery CLI
def test_cli_reset_password_and_create_admin(tmp_path, monkeypatch, capsys):
    from radio_remote import cli
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg['storage']['data_dir'] = str(tmp_path / 'data')
    cpath = tmp_path / 'c.toml'
    config.save(cfg, cpath)
    answers = iter(['first admin password', 'first admin password'])
    monkeypatch.setattr(cli.getpass, 'getpass', lambda *_a: next(answers))
    assert cli.main(['--config', str(cpath), 'create-admin', 'boss']) == 0
    auth = AuthStore(tmp_path / 'data' / 'radio-remote.db')
    assert auth.login('boss', 'first admin password', '1.1.1.1')
    assert auth.list_users()[0]['role'] == 'admin'
    sid, _s = auth.login('boss', 'first admin password', '1.1.1.1')
    answers = iter(['a different long password', 'a different long password'])
    assert cli.main(['--config', str(cpath), 'reset-password', 'BOSS']) == 0
    assert auth.get_session(sid) is None
    assert auth.login('boss', 'a different long password', '1.1.1.1')
    answers = iter(['short', 'short'])
    assert cli.main(['--config', str(cpath), 'reset-password', 'boss']) == 1
    assert cli.main(['--config', str(cpath), 'reset-password', 'nobody']) == 1
    assert cli.main(['--config', str(cpath), 'list-users']) == 0
    assert 'boss' in capsys.readouterr().out
