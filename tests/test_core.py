import pytest

from radio_remote import config
from radio_remote.auth import AuthStore, hash_password, verify_password
from radio_remote.radio.bands import band_for
from radio_remote.radio.base import Capabilities
from radio_remote.radio.mock import MockDriver
from radio_remote.safety import TxGuard, TxRefused


# ---- config
def test_defaults_are_safe():
    c = config.load(None)
    assert c["safety"]["allow_ptt"] is False
    assert c["radio"]["model"] == "mock"
    assert c["server"]["host"] == "127.0.0.1"


def test_config_rejects_unknown_and_bad_types(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[safety]\nallow_ptt = \"yes\"\n")
    with pytest.raises(config.ConfigError):
        config.load(p)
    p.write_text("[safety]\nbogus = 1\n")
    with pytest.raises(config.ConfigError):
        config.load(p)


def test_config_roundtrip(tmp_path):
    p = tmp_path / "c.toml"
    c = config.load(None)
    c["radio"]["baud"] = 9600
    config.save(c, p)
    assert config.load(p)["radio"]["baud"] == 9600
    config.save(c, p)
    assert (tmp_path / "c.toml.bak").exists()


# ---- capabilities
def test_unverified_is_not_exposed():
    caps = Capabilities.load("ft991a")
    assert caps.has("vhf") and caps.has("power_on_cat") and not caps.has("apf")      # APF is hidden on the FT-991A (CW only, did nothing on the bench)
    assert caps.has("antenna_switching") is False
    assert caps.public()["features"]["apf"] is False


def test_band_lookup():
    assert band_for(14_200_000) == "20m"
    assert band_for(145_500_000) == "2m"
    assert band_for(100_000) is None


# ---- auth
def test_password_hash():
    h = hash_password("a long enough password")
    assert verify_password("a long enough password", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_login_throttle_and_sessions():
    t = [1000.0]
    a = AuthStore(":memory:", clock=lambda: t[0])
    a.create_user("bob", "long enough pw!", "operator")
    for _ in range(5):
        assert a.login("bob", "bad", "1.2.3.4") is None
    assert a.retry_after("1.2.3.4", "bob") > 0
    t[0] += 1000
    sid, s = a.login("bob", "long enough pw!", "1.2.3.4")
    assert a.get_session(sid).username == "bob"
    t[0] += 13 * 3600
    assert a.get_session(sid) is None   # idle expiry


def test_weak_password_rejected():
    a = AuthStore(":memory:")
    with pytest.raises(ValueError):
        a.create_user("bob", "short", "admin")


# ---- TX guard
class Clock:
    t = 0.0
    def __call__(self):
        return self.t


class FakeRadio(MockDriver):
    pass


async def _guard(allow=True, mock=True, **kw):
    d = MockDriver()
    await d.start()
    if not mock:
        d.caps.data["model"]["is_mock"] = False
    clk = Clock()
    g = TxGuard(d, allow_ptt=allow, max_tx_s=kw.get("max", 10), heartbeat_timeout_s=1.0, clock=clk)
    return d, g, clk


async def test_real_radio_refused_when_ptt_disabled():
    d, g, _ = await _guard(allow=False, mock=False)
    with pytest.raises(TxRefused):
        await g.request("a", True)
    assert d.ptt_calls == []
    await d.stop()


async def test_mock_can_key_without_allow():
    d, g, _ = await _guard(allow=False, mock=True)
    await g.request("a", True)
    assert d.state["tx"]
    await d.stop()


async def test_single_owner_and_release():
    d, g, clk = await _guard()
    await g.request("a", True)
    clk.t += 1
    with pytest.raises(TxRefused):
        await g.request("b", True)
    with pytest.raises(TxRefused):
        await g.request("b", False)
    await g.request("a", False)
    assert not d.state["tx"]
    await d.stop()


async def test_heartbeat_loss_unkeys():
    d, g, clk = await _guard()
    await g.request("a", True)
    clk.t += 0.5
    await g.heartbeat("a")
    await g.tick()
    assert d.state["tx"]
    clk.t += 1.2
    await g.tick()
    assert not d.state["tx"] and not g.keyed
    await d.stop()


async def test_timeout_unkeys_and_locks():
    d, g, clk = await _guard(max=10)
    await g.request("a", True)
    for _ in range(12):
        clk.t += 1
        await g.heartbeat("a")
        await g.tick()
    assert not d.state["tx"]
    clk.t += 1
    with pytest.raises(TxRefused):
        await g.request("a", True)   # locked until released
    await g.request("a", False)
    clk.t += 1
    await g.request("a", True)
    await d.stop()


async def test_disconnect_unkeys():
    d, g, _ = await _guard()
    await g.request("a", True)
    await g.release_owner("a")
    assert not d.state["tx"]
    await d.stop()


async def test_rapid_repeat_refused():
    d, g, clk = await _guard()
    await g.request("a", True)
    await g.request("a", False)
    clk.t += 0.01
    with pytest.raises(TxRefused):
        await g.request("a", True)
    await d.stop()


async def test_unkey_failure_is_retried():
    d, g, clk = await _guard()
    await g.request("a", True)
    orig = d.set_ptt
    fail = {"n": 1}

    async def flaky(on):
        if not on and fail["n"]:
            fail["n"] -= 1
            raise OSError("cat down")
        await orig(on)

    d.set_ptt = flaky
    await g.request("a", False)
    assert g.keyed and d.state["tx"]      # still believed keyed
    await g.tick()
    assert not g.keyed and not d.state["tx"]
    await d.stop()


async def test_mock_simulation_updates_meters():
    d = MockDriver(seed=3, tick_s=0.01)
    seen = []
    d.subscribe(seen.append)
    await d.start()
    import asyncio
    await asyncio.sleep(0.1)
    await d.stop()
    assert any("smeter" in c for c in seen)
