import asyncio
import json

import pytest
from conftest import setup_admin
from test_cat import FAST, Rig, wait_for
from test_web import origin, recv_until

from radio_remote.radio.base import RadioError
from radio_remote.radio.cat import frame
from radio_remote.radio.drivers.ft991a import FT991ADriver
from radio_remote.radio.mock import MockDriver
from radio_remote.safety import TUNE_MAX_S, TUNE_SETTLE_S, TxGuard, TxRefused


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


async def mock_guard(allow=True, mock=True, max_tx=120):
    d = MockDriver()
    await d.start()
    if not mock:
        d.caps.data["model"]["is_mock"] = False
    clk = Clock()
    return d, TxGuard(d, allow_ptt=allow, max_tx_s=max_tx, heartbeat_timeout_s=1.0, clock=clk), clk


# ------------------------------------------------------------------- guard: tune
async def test_tune_refused_when_transmitting_is_disabled_on_a_real_radio():
    d, g, _ = await mock_guard(allow=False, mock=False)
    with pytest.raises(TxRefused):
        await g.start_tune("a")
    assert d.tune_calls == []
    await d.stop()


async def test_tune_runs_then_finishes_by_itself():
    d, g, clk = await mock_guard()
    d.tune_duration_s = 0.05
    await g.start_tune("a")
    assert g.keyed and g.tuning and g.owner == "a" and d.state["tuning"] is True and d.state["tx"] is True
    await asyncio.sleep(0.1)                                 # the radio finished and stopped transmitting
    clk.t += TUNE_SETTLE_S + 0.5
    await g.tick()
    assert not g.keyed and not g.tuning and d.state["tuning"] is False
    assert d.tune_calls == ["start"]                          # nothing was "stopped": it had already finished
    await d.stop()


async def test_tune_never_sends_stop_to_an_idle_radio():
    """AC002 is a start/stop TOGGLE: a stop sent to an idle radio would start a new tune."""
    d, g, clk = await mock_guard()
    await d.tune_stop()                                       # radio idle
    assert d.tune_calls == []
    await g.start_tune("a")
    d._update(tx=False)                                       # radio finished on its own
    clk.t += 1
    await g.stop_tune("a")                                    # user taps "stop" a moment too late
    assert d.tune_calls == ["start"]                          # no stray stop
    await d.stop()


async def test_tune_stopped_by_owner_while_transmitting():
    d, g, clk = await mock_guard()
    d.tune_duration_s = 30
    await g.start_tune("a")
    with pytest.raises(TxRefused):
        await g.stop_tune("b")                                # someone else cannot stop it
    await g.stop_tune("a")
    assert d.tune_calls == ["start", "stop"] and not d.state["tx"] and not g.keyed
    await d.stop()


async def test_tune_hard_time_limit():
    d, g, clk = await mock_guard()
    d.tune_duration_s = 60                                    # radio "gets stuck" transmitting
    await g.start_tune("a")
    clk.t += TUNE_MAX_S - 1
    await g.tick()
    assert d.state["tx"]
    clk.t += 2
    await g.tick()
    assert not d.state["tx"] and not g.keyed and d.tune_calls[-1] == "stop"
    await d.stop()


async def test_tune_needs_no_heartbeat_but_stops_when_owner_disconnects():
    d, g, clk = await mock_guard()
    d.tune_duration_s = 30
    await g.start_tune("a")
    clk.t += 5                                                # far beyond the PTT heartbeat timeout
    await g.tick()
    assert g.keyed and d.state["tx"]                          # a click-to-tune is not a hold, so no heartbeat needed
    await g.release_owner("a")
    assert not g.keyed and not d.state["tx"]
    await d.stop()


async def test_no_ptt_or_second_tune_during_a_tune_and_no_tune_during_ptt():
    d, g, clk = await mock_guard()
    d.tune_duration_s = 30
    await g.start_tune("a")
    for owner in ("a", "b"):
        with pytest.raises(TxRefused):
            await g.request(owner, True)
    with pytest.raises(TxRefused):
        await g.start_tune("b")
    await g.stop_tune("a")
    clk.t += 5
    await g.request("a", True)
    with pytest.raises(TxRefused):
        await g.start_tune("a")
    await d.stop()


async def test_tune_failure_leaves_nothing_keyed():
    d, g, _ = await mock_guard()

    async def refuse():
        raise RadioError("radio rejected AC002")
    d.tune_start = refuse
    with pytest.raises(RadioError):
        await g.start_tune("a")
    assert not g.keyed and not g.tuning
    await d.stop()


# --------------------------------------------------------- FT-991A driver + sim
@pytest.fixture
async def rig():
    r = Rig()
    d = FT991ADriver(transport_factory=r.factory, timings=FAST)
    r.driver = d
    await d.start()
    await wait_for(lambda: d.state["connected"])
    yield r
    await d.stop()
    for s in r.sims:
        await s.stop()


async def test_real_driver_tune_sends_ac002_and_stops_only_while_transmitting(rig):
    d, sim = rig.driver, rig.sim
    sim.tune_seconds = 30
    g = TxGuard(d, allow_ptt=True, max_tx_s=60, heartbeat_timeout_s=1.0)
    await g.start_tune("a")
    assert "AC002" in sim.log and sim.tuning
    await wait_for(lambda: d.state["tx"] or (d.state.get("po_raw") or 0) > 0)          # radio reports RF going out
    await g.stop_tune("a")
    await wait_for(lambda: not sim.tuning)
    assert sim.log.count("AC002") == 2 and sim.tx == 0       # exactly start + stop


async def test_real_driver_never_sends_a_stray_ac002(rig):
    d, sim = rig.driver, rig.sim
    await d.tune_stop()                                       # idle radio: must send nothing
    assert "AC002" not in sim.log
    sim.tune_seconds = 0.2
    g = TxGuard(d, allow_ptt=True, max_tx_s=60, heartbeat_timeout_s=1.0)
    await g.start_tune("a")
    await wait_for(lambda: not sim.tuning)                    # finished on its own
    await wait_for(lambda: not d.state["tx"] and (d.state.get("po_raw") or 0) == 0)
    await g.stop_tune("a")                                    # tap "stop" after it ended
    assert sim.log.count("AC002") == 1


async def test_real_driver_refuses_tune_when_radio_rejects(rig):
    async def reject(c):
        return await rig.sim._send("?;")
    orig = rig.sim._handle
    rig.sim._handle = lambda c: reject(c) if c == "AC002" else orig(c)
    g = TxGuard(rig.driver, allow_ptt=True, max_tx_s=60)
    with pytest.raises(RadioError):
        await g.start_tune("a")
    assert not g.keyed


# ------------------------------------------------------------------ DG-ID
def test_dgid_frames():
    assert frame.decode("EX15300;") == {"dgid": "AUTO"}
    assert frame.decode("EX15312;") == {"dgid": "12"}
    assert frame.decode("EX15399;") == {"dgid": "99"}
    assert frame.decode("EX153000;") == {"dgid": "AUTO"}       # a real radio answers with three digits (was logged as a bad frame)
    assert frame.decode("EX153012;") == {"dgid": "12"}
    assert frame.decode("EX03100;") == {}                      # other menus are not interpreted
    with pytest.raises(frame.FrameError):
        frame.decode("EX1531;")


async def test_dgid_roundtrip_and_validation(rig):
    d, sim = rig.driver, rig.sim
    assert d.state["dgid"] == "AUTO"                           # read at connect
    await d.set_control("dgid", "25")
    assert sim.reg["EX153"] == "25" and d.state["dgid"] == "25"
    await d.set_control("dgid", "AUTO")
    assert sim.reg["EX153"] == "00" and d.state["dgid"] == "AUTO"
    for bad in ("0", "100", "-1", "ABC", 5):
        with pytest.raises(RadioError):
            await d.set_control("dgid", bad)
    assert sim.log.count("EX15325") == 1                       # nothing else was written to the menu


# --------------------------------------------------------------- hub / UI contract
async def ws(client):
    await setup_admin(client)
    sock = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await sock.receive()).data)
    await recv_until(sock, lambda m: m["t"] == "lease" and m["d"]["holder"])
    return sock, hello


async def cmd(sock, i, **m):
    await sock.send_json({"id": i, **m})
    return await recv_until(sock, lambda x: x["t"] == "ack" and x["id"] == i)


async def test_hub_tune_and_stop_on_the_mock(client):
    from radio_remote.app import K_DRIVER
    sock, hello = await ws(client)
    assert hello["caps"]["features"]["tuner_tune"] is True
    assert any(c["name"] == "dgid" and c.get("quick") and c["modes"] == ["C4FM"] for c in hello["caps"]["controls"])
    drv = client.server.app[K_DRIVER]
    drv.tune_duration_s = 30
    assert (await cmd(sock, 1, type="tune"))["ok"]
    patch = await recv_until(sock, lambda m: m["t"] == "patch" and m["d"].get("tuning") is True)
    assert patch
    a = await cmd(sock, 2, type="ptt", on=True)
    assert a["ok"] is False and "tuning" in a["error"]
    assert (await cmd(sock, 3, type="tune_stop"))["ok"]
    await recv_until(sock, lambda m: m["t"] == "patch" and m["d"].get("tuning") is False)
    assert drv.tune_calls == ["start", "stop"]
    await sock.close()


async def test_hub_tune_requires_control(client):
    from conftest import setup_admin  # noqa: F401
    sock, _ = await ws(client)
    assert (await cmd(sock, 1, type="release_control"))["ok"]
    a = await cmd(sock, 2, type="tune")
    assert a["ok"] is False and "control" in a["error"].lower()
    await sock.close()


async def test_hub_dgid_validation(client):
    sock, _ = await ws(client)
    assert (await cmd(sock, 1, type="set_control", name="dgid", value="42"))["ok"]
    for i, bad in enumerate(["0", "100", "x", 3], start=2):
        assert (await cmd(sock, i, type="set_control", name="dgid", value=bad))["ok"] is False
    await sock.close()


# ------------------------------------------------------------------ mic select (menu 106)
def test_mic_select_frames_and_encoder():
    assert frame.decode("EX1061;") == {"mic_select": "REAR"}
    assert frame.decode("EX1060;") == {"mic_select": "MIC"}
    with pytest.raises(frame.FrameError):
        frame.decode("EX1062;")
    from radio_remote.radio.cat import ft991a_controls as fc
    assert fc.ENCODE["mic_select"][1]("REAR") == ["EX1061;", "EX0451;", "EX0741;"]            # SSB, AM and FM menus together
    assert fc.ENCODE["mic_select"][1]("MIC") == ["EX1060;", "EX0450;", "EX0740;"]
    assert fc.ENCODE["mic_select"][0] == "EX106;"


async def test_mic_select_roundtrip_and_validation(rig):
    d, sim = rig.driver, rig.sim
    assert d.state["mic_select"] == "REAR"                       # read at connect
    await d.set_control("mic_select", "MIC")
    assert sim.reg["EX106"] == "0" and sim.reg["EX045"] == "0" and sim.reg["EX074"] == "0" and d.state["mic_select"] == "MIC"
    await d.set_control("mic_select", "REAR")
    assert sim.reg["EX106"] == "1" and sim.reg["EX045"] == "1" and sim.reg["EX074"] == "1"      # AM and FM follow the SSB menu
    await d.set_control("mic_select", "MIC")
    with pytest.raises(Exception):
        await d.set_control("mic_select", "LINE")
