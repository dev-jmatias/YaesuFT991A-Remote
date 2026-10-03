"""Memory channels of the FT-991A: list (read only), recall, back to VFO. Nothing is ever written to the radio's memories."""
import json

import pytest

from radio_remote.radio.base import RadioError
from radio_remote.radio.cat import frame
from test_cat import rig, wait_for  # noqa: F401  (the 'rig' fixture: driver + simulated FT-991A)
from test_tune_dgid import cmd, ws


def test_memory_frames():
    assert frame.memory_read(5) == "MT005;" and frame.memory_select(99) == "MC099;"
    for bad in (0, 100, -1):
        with pytest.raises(frame.FrameError):
            frame.memory_read(bad)
        with pytest.raises(frame.FrameError):
            frame.memory_select(bad)
    ok = "MT005" + "014200000" + "+0000" + "0" + "0" + "2" + "1" + "0" + "00" + "0" + "0" + "20m DX".ljust(12) + ";"
    assert frame.decode_memory(ok) == {"channel": 5, "frequency": 14_200_000, "mode": "USB", "tag": "20m DX"}
    empty = "MT006" + "000000000" + "+0000" + "00" + "1" + "1" + "0" + "00" + "0" + "0" + " " * 12 + ";"
    assert frame.decode_memory(empty) is None                       # an empty channel
    with pytest.raises(frame.FrameError):
        frame.decode_memory("MT005123;")
    with pytest.raises(frame.FrameError):
        frame.decode_memory("FA014200000;")
    # IF reports the memory channel and whether the radio is in memory mode
    f = "IF" + "007" + "007100000" + "+0000" + "0" + "0" + "1" + "1" + "0" + "00" + "0" + ";"
    d = frame.decode(f)
    assert d["vfo_memory"] == "memory" and d["memory_channel"] == 7 and d["frequency"] == 7_100_000


async def test_list_reads_every_channel_with_the_read_form_only(rig):
    d, sim = rig.driver, rig.sim
    items = await d.memory_channels()
    assert [m["channel"] for m in items] == [5, 6, 11, 99]
    first = items[0]
    assert first["frequency"] == 14_200_000 and first["mode"] == "USB" and first["tag"] == "20m DX" and first["band"] == "20m"
    assert items[2]["band"] == "2m" and items[3]["tag"] == ""
    reads = [c for c in sim.log if c.startswith("MT")]
    assert len(reads) == 99 and all(len(c) == 5 for c in reads)                    # MT001 .. MT099: no write form (which is longer)
    assert not any(c.startswith(("MW", "AM", "BM")) for c in sim.log)             # nothing that could store a memory
    n = len(sim.log)
    assert await d.memory_channels() == items and len([c for c in sim.log[n:] if c.startswith("MT")]) == 0   # cached
    sim.memories[7] = (21_074_000, "DATA-U", "FT8 15m")
    again = await d.memory_channels(refresh=True)
    assert [m["channel"] for m in again] == [5, 6, 7, 11, 99]


async def test_recall_a_channel_and_go_back_to_the_vfo(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    assert d.state["vfo_memory"] == "vfo"
    await d.memory_to_vfo()
    assert "VM" not in sim.log                                                     # V/M is a toggle: never sent while already in VFO mode
    await d.memory_select(11)
    assert d.state["vfo_memory"] == "memory" and d.state["memory_channel"] == 11
    assert d.state["frequency"] == 145_500_000 and d.state["mode"] == "FM" and d.state["band"] == "2m" and "MC011" in sim.log
    await d.memory_to_vfo()
    assert "VM" in sim.log and d.state["vfo_memory"] == "vfo" and d.state["frequency"] == 14_200_000 and d.state["mode"] == "USB"
    with pytest.raises(RadioError):
        await d.memory_select(7)                                                   # not a stored channel
    with pytest.raises(RadioError):
        await d.memory_select(100)


async def test_no_channel_change_while_transmitting(rig):
    d = rig.driver
    await d.memory_channels()
    d.state["tx"] = True
    with pytest.raises(RadioError):
        await d.memory_select(5)
    with pytest.raises(RadioError):
        await d.memory_channels(refresh=True)


# ------------------------------------------------------------------ hub / HTTP on the mock
async def test_http_list_and_ws_recall_on_the_mock(client):
    sock, hello = await ws(client)
    assert hello["caps"]["features"]["memories"] is True
    r = await client.get("/api/memories")
    assert r.status == 200
    items = (await r.json())["channels"]
    assert items[0]["channel"] == 1 and items[0]["tag"] == "FT8 80m" and len(items) == 8
    a = await cmd(sock, 1, type="memory_select", channel=5)
    assert a["ok"], a
    patch = await recv_patch(sock)
    assert patch["memory_channel"] == 5
    from radio_remote.app import K_DRIVER
    assert client.server.app[K_DRIVER].state["frequency"] == 14_200_000
    assert (await cmd(sock, 2, type="memory_select", channel=0))["ok"] is False
    assert (await cmd(sock, 3, type="memory_select", channel="5"))["ok"] is False
    assert (await cmd(sock, 4, type="memory_select", channel=4))["ok"] is False     # empty channel
    assert (await cmd(sock, 5, type="memory_vfo"))["ok"]
    await sock.close()


async def recv_patch(sock):
    from test_web import recv_until
    m = await recv_until(sock, lambda x: x["t"] == "patch" and x["d"].get("vfo_memory") == "memory")
    return m["d"]


async def test_memory_commands_need_control_and_login(client):
    r = await client.get("/api/memories")
    assert r.status == 401
    sock, _ = await ws(client)
    assert (await cmd(sock, 1, type="release_control"))["ok"]
    a = await cmd(sock, 2, type="memory_select", channel=5)
    assert a["ok"] is False and "control" in a["error"].lower()
    await sock.close()
