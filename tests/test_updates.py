"""Update check: version comparison, the checker, the admin endpoints. Nothing here touches the network."""
import asyncio

import pytest
from test_phase8 import PW, add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote import config, updates
from radio_remote.common import K_UPDATES


def test_version_parsing_and_comparison():
    assert updates.parse_version("v1.0.0.2") == (1, 0, 0, 2) and updates.parse_version("1.2") == (1, 2)
    for bad in ("", "latest", "v1.0.0-beta", "1.0.0 ", "v", "1..2", "x" * 50, None, 5):
        if bad == "1.0.0 ":
            assert updates.parse_version(bad) == (1, 0, 0)                       # surrounding blanks are harmless
        else:
            assert updates.parse_version(bad) is None
    assert updates.is_newer("v1.0.0.2", "1.0.0") and updates.is_newer("v1.1.0", "1.0.0.2") and updates.is_newer("2.0", "1.9.9.9")
    assert not updates.is_newer("v1.0.0", "1.0.0") and not updates.is_newer("v1.0.0.0", "1.0.0")      # equal
    assert not updates.is_newer("v1.0.0", "1.0.0.2") and not updates.is_newer("v0.9", "1.0.0")        # older
    assert not updates.is_newer("garbage", "1.0.0") and not updates.is_newer("v1.0.1", "garbage")     # unusable: no notice


def _checker(release=None, error=None, check=True, current="1.0.0"):
    calls = []

    async def fetch(repo):
        calls.append(repo)
        if error:
            raise RuntimeError(error)
        return release

    settings = {"check": check, "repo": "someone/radio"}
    return updates.UpdateChecker(lambda: settings, current=current, fetch=fetch, clock=lambda: 1000.0), calls, settings


async def test_a_newer_release_is_reported():
    rel = {"tag": "v1.1.0", "name": "Radio Remote 1.1.0", "url": "https://github.com/someone/radio/releases/tag/v1.1.0", "published": "2026-10-04T10:00:00Z"}
    c, calls, _ = _checker(rel)
    assert c.state()["newer"] is False and c.state()["latest"] is None                # nothing asked yet
    st = await c.check()
    assert calls == ["someone/radio"]
    assert st["newer"] is True and st["latest"] == "v1.1.0" and st["current"] == "1.0.0" and st["url"].endswith("v1.1.0")
    assert st["checked_at"] == 1000.0 and st["error"] is None


async def test_the_same_or_an_older_release_is_no_news():
    rel = {"tag": "v1.0.0", "name": "x", "url": "https://github.com/someone/radio/releases/tag/v1.0.0", "published": ""}
    c, _, _ = _checker(rel)
    assert (await c.check())["newer"] is False


async def test_failures_are_kept_as_a_message_and_never_raised():
    c, _, _ = _checker(error="no route to host")
    st = await c.check()
    assert st["newer"] is False and "no route to host" in st["error"] and st["checked_at"] == 1000.0
    c2, _, _ = _checker({"tag": "v1.1.0", "name": "n", "url": "https://github.com/someone/radio/x", "published": ""})
    await c2.check()
    c2._fetch = c._fetch                                                                # then the network goes away: the old answer stays
    st2 = await c2.check()
    assert st2["latest"] == "v1.1.0" and st2["error"]


async def test_switched_off_means_it_never_asks():
    c, calls, settings = _checker({"tag": "v9", "name": "n", "url": "u", "published": ""}, check=False)
    st = await c.check()
    assert calls == [] and st["enabled"] is False and st["latest"] is None
    settings["check"] = True                                                            # live: the next check asks
    await c.check()
    assert calls == ["someone/radio"]


async def test_the_background_loop_starts_and_stops_cleanly(monkeypatch):
    monkeypatch.setattr(updates, "FIRST_CHECK_S", 0.01)
    c, calls, _ = _checker({"tag": "v1.1.0", "name": "n", "url": "https://github.com/someone/radio/x", "published": ""})
    c.start()
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.02)
    assert calls == ["someone/radio"]
    await c.stop()
    assert c._task is None


class _Resp:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_session(resp):
    class S:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, headers=None):
            S.url, S.headers = url, headers
            return resp
    return S


async def test_fetch_validates_what_github_says(monkeypatch):
    good = {"tag_name": "v1.2.0", "name": "Radio Remote 1.2.0", "html_url": "https://github.com/o/r/releases/tag/v1.2.0", "published_at": "2026-10-04T10:00:00Z", "body": "<script>"}
    S = _fake_session(_Resp(200, good))
    monkeypatch.setattr(updates.aiohttp, "ClientSession", S)
    rel = await updates.fetch_latest_release("o/r")
    assert rel == {"tag": "v1.2.0", "name": "Radio Remote 1.2.0", "url": good["html_url"], "published": "2026-10-04T10:00:00Z"}
    assert S.url == "https://api.github.com/repos/o/r/releases/latest" and S.headers["User-Agent"].startswith("radio-remote/")
    # a link that does not belong to the repository is replaced, an unusable tag is refused, errors are named
    monkeypatch.setattr(updates.aiohttp, "ClientSession", _fake_session(_Resp(200, {**good, "html_url": "https://evil.example/x"})))
    assert (await updates.fetch_latest_release("o/r"))["url"] == "https://github.com/o/r/releases"
    monkeypatch.setattr(updates.aiohttp, "ClientSession", _fake_session(_Resp(200, {**good, "tag_name": "nightly"})))
    with pytest.raises(RuntimeError, match="usable version tag"):
        await updates.fetch_latest_release("o/r")
    for status, text in ((404, "no release"), (403, "403")):
        monkeypatch.setattr(updates.aiohttp, "ClientSession", _fake_session(_Resp(status, {})))
        with pytest.raises(RuntimeError, match=text):
            await updates.fetch_latest_release("o/r")


def test_config_has_the_update_section_with_safe_defaults():
    cfg = config.load(None)
    assert cfg["updates"] == {"check": True, "repo": "dev-jmatias/YaesuFT991A-Remote"}
    import copy
    for bad in ("", "noslash", "a/b/c", "../x/y", "a b/c", "a/" + "x" * 101, "/repo"):
        c = copy.deepcopy(cfg)
        c["updates"]["repo"] = bad
        with pytest.raises(config.ConfigError):
            config.validate(c)


async def test_admin_endpoints_and_the_off_switch(make_app, tmp_path):
    client = await make_app()
    root = await admin_user(client)
    checker = client.app[K_UPDATES]
    checker._fetch = _checker({"tag": "v99.0.0", "name": "n", "url": "https://github.com/o/r/releases/tag/v99.0.0", "published": ""})[0]._fetch
    st = await (await client.get("/api/admin/update")).json()
    assert st["enabled"] is True and st["newer"] is False and st["current"]                 # nothing asked yet (the tests never phone home)
    st = await (await client.post("/api/admin/update/check", json={}, headers=root.h)).json()
    assert st["newer"] is True and st["latest"] == "v99.0.0"
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "update_check" in events
    # the switch is editable from the page and applies at once; the repository is not editable there
    r = await client.put("/api/config", json={"updates": {"check": False}}, headers=root.h)
    assert r.status == 200 and (await r.json())["restart_required"] is False
    assert config.load(tmp_path / "config.toml")["updates"]["check"] is False
    assert (await (await client.get("/api/admin/update")).json())["enabled"] is False
    assert (await client.put("/api/config", json={"updates": {"repo": "evil/repo"}}, headers=root.h)).status == 403
    # administrators only
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await op.c.get("/api/admin/update")).status == 403
    assert (await op.c.post("/api/admin/update/check", json={}, headers=op.h)).status == 403
    await close_all(root, op)
