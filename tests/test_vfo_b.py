import json

import pytest
from conftest import setup_admin
from test_cat import FAST, Rig, wait_for
from test_web import origin, recv_until

from radio_remote.radio.base import Capabilities, RadioError
from radio_remote.radio.drivers.ft991a import FT991ADriver


def test_vfo_b_capability_is_declared_for_ft991a():
    caps = Capabilities.load("ft991a")
    assert caps.has("vfo_b") and caps.public()["features"]["vfo_b"] is True


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


async def test_vfo_b_is_read_at_connect(rig):
    s = rig.driver.state
    assert s["frequency_b"] == 7_100_000 and s["band_b"] == "40m"
    assert s["frequency"] == 14_200_000


async def test_swap_copies_and_reads_back(rig):
    d, sim = rig.driver, rig.sim
    await d.vfo_op("swap")
    assert "SV" in sim.log
    assert (sim.freq, sim.freq_b) == (7_100_000, 14_200_000)
    assert d.state["frequency"] == 7_100_000 and d.state["band"] == "40m"
    assert d.state["frequency_b"] == 14_200_000 and d.state["band_b"] == "20m"
    await d.vfo_op("swap")
    assert d.state["frequency"] == 14_200_000 and d.state["frequency_b"] == 7_100_000


async def test_a_to_b_and_b_to_a(rig):
    d, sim = rig.driver, rig.sim
    await d.vfo_op("a_to_b")
    assert "AB" in sim.log and sim.freq_b == 14_200_000 and d.state["frequency_b"] == 14_200_000
    await d.set_frequency_b(21_074_000)
    await d.vfo_op("b_to_a")
    assert "BA" in sim.log and sim.freq == 21_074_000 and d.state["frequency"] == 21_074_000 and d.state["band"] == "15m"


async def test_set_frequency_b_writes_fb_and_reads_back(rig):
    d, sim = rig.driver, rig.sim
    await d.set_frequency_b(144_174_000)
    assert "FB144174000" in sim.log and sim.freq_b == 144_174_000
    assert d.state["frequency_b"] == 144_174_000 and d.state["band_b"] == "2m"
    assert sim.freq == 14_200_000                                  # VFO A untouched
    with pytest.raises(RadioError):
        await d.set_frequency_b(10)                                # below the radio's range: nothing is sent
    assert "FB000000010" not in sim.log


async def test_unknown_vfo_operation_sends_nothing(rig):
    before = list(rig.sim.log)
    with pytest.raises(RadioError):
        await rig.driver.vfo_op("explode")
    assert rig.sim.log == before


async def test_front_panel_change_of_vfo_b_is_picked_up(rig):
    rig.sim.freq_b = 10_136_000                                    # changed on the radio, no push from the simulator
    await wait_for(lambda: rig.driver.state.get("frequency_b") == 10_136_000)
    assert rig.driver.state["band_b"] == "30m"


async def test_swap_with_different_modes_refreshes_width_list(rig):
    """After a swap the active VFO may be in another mode; the existing mode-change logic must refresh the filter list."""
    d, sim = rig.driver, rig.sim
    sim.mode = "CW-U"
    sim.reg["NA0"], sim.reg["SH0"] = "1", "05"
    await sim._send("MD03;")
    await wait_for(lambda: d.state["mode"] == "CW-U" and d.state["width"] == 250)


# ------------------------------------------------------------------- hub / UI contract
async def sock(client):
    await setup_admin(client)
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    await recv_until(ws, lambda m: m["t"] == "lease" and m["d"]["holder"])
    return ws, hello


async def cmd(ws, i, **m):
    await ws.send_json({"id": i, **m})
    return await recv_until(ws, lambda x: x["t"] == "ack" and x["id"] == i)


async def test_hub_vfo_commands_on_the_mock(client):
    ws, hello = await sock(client)
    assert hello["caps"]["features"]["vfo_b"] is True and hello["state"]["frequency_b"] == 7_100_000
    assert (await cmd(ws, 1, type="vfo", op="swap"))["ok"]
    p = await recv_until(ws, lambda m: m["t"] == "patch" and m["d"].get("frequency_b") == 14_200_000)
    assert p["d"]["frequency"] == 7_100_000 and p["d"]["band"] == "40m"
    assert (await cmd(ws, 2, type="vfo", op="a_to_b"))["ok"]
    assert (await cmd(ws, 3, type="set_frequency", hz=50_313_000, vfo="B"))["ok"]
    p = await recv_until(ws, lambda m: m["t"] == "patch" and m["d"].get("frequency_b") == 50_313_000)
    assert p["d"]["band_b"] == "6m"
    for i, bad in enumerate([{"type": "vfo", "op": "x"}, {"type": "vfo"}, {"type": "vfo", "op": 1},
                             {"type": "set_frequency", "hz": 7_000_000, "vfo": "C"},
                             {"type": "set_frequency", "hz": 80_000_000, "vfo": "B"},      # outside coverage
                             {"type": "set_frequency", "hz": "7", "vfo": "B"}], start=10):
        assert (await cmd(ws, i, **bad))["ok"] is False, bad
    await ws.close()


async def test_hub_vfo_commands_need_control(client):
    ws, _ = await sock(client)
    assert (await cmd(ws, 1, type="release_control"))["ok"]
    for i, m in enumerate([{"type": "vfo", "op": "swap"}, {"type": "set_frequency", "hz": 7_000_000, "vfo": "B"}], start=2):
        a = await cmd(ws, i, **m)
        assert a["ok"] is False and "control" in a["error"].lower()
    await ws.close()


# ------------------------------------------------------------------- split (FT) and quick split (QS)
def test_split_answers_seen_on_the_real_radio():
    from radio_remote.radio.cat import frame
    assert frame.decode("FT0;") == {"split": False}            # captured: FT; -> FT0; (TX on VFO A)
    assert frame.decode("FT1;") == {"split": True}             # captured: FT3; -> FT1; (TX on VFO B)
    with pytest.raises(frame.FrameError):
        frame.decode("FT2;")                                   # 2/3 are only ever SET values


async def test_split_on_off_roundtrip_and_initial_state(rig):
    d, sim = rig.driver, rig.sim
    assert d.state["split"] is False                           # read at connect
    await d.set_split(True)
    assert "FT3" in sim.log and sim.ft == 1 and d.state["split"] is True
    await d.set_split(False)
    assert "FT2" in sim.log and sim.ft == 0 and d.state["split"] is False


async def test_split_changed_on_the_radio_is_followed(rig):
    await rig.sim._send("FT1;")                                # radio announces TX on VFO B by itself
    await wait_for(lambda: rig.driver.state["split"] is True)


async def test_quick_split_sends_qs_and_reads_everything_back(rig):
    d, sim = rig.driver, rig.sim
    await d.vfo_op("quick_split")
    assert "QS" in sim.log and sim.ft == 1
    assert d.state["split"] is True and d.state["frequency_b"] == 14_201_000


async def test_driver_refuses_split_and_vfo_changes_while_transmitting(rig):
    d, sim = rig.driver, rig.sim
    await d.set_ptt(True)
    before = list(sim.log)
    for coro in (d.set_split(True), d.vfo_op("swap"), d.vfo_op("quick_split")):
        with pytest.raises(RadioError):
            await coro
    assert sim.log == before                                   # nothing reached the radio
    await d.set_ptt(False)


async def test_hub_split_and_guards_on_the_mock(client):
    from radio_remote.app import K_DRIVER
    ws, hello = await sock(client)
    assert hello["caps"]["features"]["split"] is True and hello["caps"]["features"]["quick_split"] is True
    assert (await cmd(ws, 1, type="split", on=True))["ok"]
    await recv_until(ws, lambda m: m["t"] == "patch" and m["d"].get("split") is True)
    assert (await cmd(ws, 2, type="vfo", op="quick_split"))["ok"]
    assert (await cmd(ws, 3, type="split", on="yes"))["ok"] is False
    assert (await cmd(ws, 4, type="split"))["ok"] is False
    # no changes of TX VFO / VFOs / band while the transmitter is keyed
    assert (await cmd(ws, 5, type="ptt", on=True))["ok"] and client.server.app[K_DRIVER].state["tx"]
    for i, m in enumerate([{"type": "split", "on": False}, {"type": "vfo", "op": "swap"}, {"type": "set_band", "band": "40m"}], start=6):
        a = await cmd(ws, i, **m)
        assert a["ok"] is False and "transmitting" in a["error"], m
    assert (await cmd(ws, 10, type="ptt", on=False))["ok"]
    assert (await cmd(ws, 11, type="split", on=False))["ok"]
    await ws.close()


# ------------------------------------------- regression: the real radio applies VFO operations a moment late
async def test_labels_follow_when_the_radio_applies_a_swap_late():
    """Bench report: after A<->B the radio changed but the VFO B label did not. Reading back immediately returns the OLD value."""
    import asyncio
    from dataclasses import replace
    r = Rig()
    d = FT991ADriver(transport_factory=r.factory, timings=replace(FAST, settle_s=0.25))
    await d.start()
    await wait_for(lambda: d.state["connected"])
    sim = r.sim
    original = sim._handle

    async def laggy(c):
        if c == "SV":
            async def apply_later():
                await asyncio.sleep(0.1)                           # slower than an immediate read-back
                sim.freq, sim.freq_b = sim.freq_b, sim.freq
            asyncio.create_task(apply_later())
            return
        return await original(c)
    sim._handle = laggy
    await d.vfo_op("swap")
    assert (d.state["frequency"], d.state["frequency_b"]) == (7_100_000, 14_200_000)   # settled read saw the NEW values
    await d.stop()
    for s in r.sims:
        await s.stop()


async def test_vfo_b_is_refreshed_every_second_not_every_cycle(rig):
    """FB is in the 1 s backstop poll, so a change made on the radio shows up within about a second."""
    rig.sim.freq_b = 3_573_000
    await wait_for(lambda: rig.driver.state.get("frequency_b") == 3_573_000, timeout=2.5)
