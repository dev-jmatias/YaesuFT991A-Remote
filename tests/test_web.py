import asyncio
import json

from conftest import setup_admin


async def recv_until(ws, pred, timeout=2.0):
    async def go():
        while True:
            m = json.loads((await ws.receive()).data)
            if pred(m):
                return m
    return await asyncio.wait_for(go(), timeout)


async def test_setup_login_flow(client):
    assert (await (await client.get("/api/status")).json())["setup_required"] is True
    assert (await client.get("/api/me")).status == 401
    csrf = await setup_admin(client)
    assert (await (await client.get("/api/status")).json())["setup_required"] is False
    assert (await client.post("/api/setup", json={"username": "x", "password": "y" * 12})).status == 403
    assert (await client.get("/api/me")).status == 200
    # CSRF enforced on state-changing calls
    assert (await client.post("/api/logout")).status == 403
    assert (await client.post("/api/logout", headers={"X-CSRF-Token": csrf})).status == 200
    assert (await client.get("/api/me")).status == 401


async def test_cookie_flags_and_headers(client):
    r = await client.post("/api/setup", json={"username": "admin", "password": "correct horse battery"})
    sc = r.headers["Set-Cookie"]
    assert "HttpOnly" in sc and "SameSite=Strict" in sc
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


async def test_bad_login(client):
    await setup_admin(client)
    await client.post("/api/logout", headers={"X-CSRF-Token": "x"})
    r = await client.post("/api/login", json={"username": "admin", "password": "nope"})
    assert r.status == 401
    r = await client.post("/api/login", data="not json")
    assert r.status == 400


async def test_ws_requires_auth_and_origin(client):
    r = await client.get("/ws")
    assert r.status == 401
    await setup_admin(client)
    r = await client.get("/ws", headers={"Origin": "https://evil.example"})
    assert r.status == 403
    r = await client.get("/ws")            # no Origin header
    assert r.status == 403


def origin(client):
    return {"Origin": f"http://{client.server.host}:{client.server.port}"}


async def test_ws_state_and_commands(client):
    await setup_admin(client)
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    assert hello["t"] == "hello" and hello["caps"]["mock"] is True
    assert hello["state"]["frequency"] == 14_200_000

    await ws.send_json({"id": 1, "type": "set_frequency", "hz": 7_100_000})
    ack = await recv_until(ws, lambda m: m["t"] == "ack")
    assert ack["ok"]
    patch = await recv_until(ws, lambda m: m["t"] == "patch" and "frequency" in m["d"])
    assert patch["d"]["frequency"] == 7_100_000 and patch["d"]["band"] == "40m"

    for bad in [
        {"id": 2, "type": "set_frequency", "hz": 80_000_000},     # outside coverage
        {"id": 3, "type": "set_frequency", "hz": "7"},
        {"id": 4, "type": "set_mode", "mode": "FT8"},
        {"id": 5, "type": "set_level", "name": "af_gain", "value": 999},
        {"id": 6, "type": "set_level", "name": "bogus", "value": 1},
        {"id": 7, "type": "shell", "cmd": "rm -rf /"},
    ]:
        await ws.send_json(bad)
        ack = await recv_until(ws, lambda m: m["t"] == "ack")
        assert ack["ok"] is False, bad
    await ws.close()


async def test_ptt_released_on_disconnect(client):
    await setup_admin(client)
    drv = client.server.app["driver"] if "driver" in client.server.app else None
    from radio_remote.app import K_DRIVER
    drv = client.server.app[K_DRIVER]
    ws = await client.ws_connect("/ws", headers=origin(client))
    await recv_until(ws, lambda m: m["t"] == "hello")
    await ws.send_json({"id": 1, "type": "ptt", "on": True})
    await recv_until(ws, lambda m: m["t"] == "ack" and m["ok"])
    assert drv.state["tx"] is True
    await ws.close()                      # browser vanishes without releasing
    for _ in range(40):
        if not drv.state["tx"]:
            break
        await asyncio.sleep(0.05)
    assert drv.state["tx"] is False


async def test_ptt_heartbeat_timeout(client):
    await setup_admin(client)
    from radio_remote.app import K_DRIVER
    drv = client.server.app[K_DRIVER]
    ws = await client.ws_connect("/ws", headers=origin(client))
    await recv_until(ws, lambda m: m["t"] == "hello")
    await ws.send_json({"id": 1, "type": "ptt", "on": True})
    await recv_until(ws, lambda m: m["t"] == "ack" and m["ok"])
    await asyncio.sleep(1.6)              # connection stays open, but no heartbeats
    assert drv.state["tx"] is False
    await ws.close()


async def test_static_files_revalidate_and_pwa_assets_served(client):
    for path, ctype in [("/", "text/html"), ("/style.css", "text/css"), ("/sw.js", "javascript"),
                        ("/icons/icon-192.png", "image/png"), ("/src/components/vfo-panels.js", "javascript")]:
        r = await client.get(path)
        assert r.status == 200, path
        assert ctype in r.headers["Content-Type"], path
        assert r.headers["Cache-Control"] == "no-cache", path
    r = await client.get("/manifest.webmanifest")
    assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
    manifest = json.loads(await r.text())
    assert manifest["display"] == "standalone" and {i["sizes"] for i in manifest["icons"]} >= {"192x192", "512x512"}
    assert (await client.get("/api/status")).headers["Cache-Control"] == "no-store"


async def test_hello_includes_ui_steps(client):
    await setup_admin(client)
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    assert hello["ui"]["steps"] == [10, 100, 500, 1000, 5000, 10000]
    await ws.close()


# ------------------------------------------------------------------ the offline manual at /docs/
async def test_docs_are_served_when_built(client, tmp_path, monkeypatch):
    # the fixture app was created without a docs-html folder: the routes are only registered when the manual exists
    r = await client.get("/docs/")
    assert r.status in (200, 404)
    if r.status == 200:
        text = await r.text()
        assert "Radio Remote" in text
        assert (await client.get("/docs/INSTALL.html")).status == 200
        assert (await client.get("/docs/../backend/radio_remote/app.py")).status in (400, 403, 404)
        assert "Content-Security-Policy" in r.headers
