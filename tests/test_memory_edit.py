"""Adding and editing a memory channel (FT-991A): the frame, the proof after every write, the safety net, and the admin-only endpoint."""
import pytest
from test_cat import rig  # noqa: F401  (driver + simulated FT-991A)
from test_phase8 import add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote.common import K_GUARD
from radio_remote.radio.base import RadioError
from radio_remote.radio.cat import frame


def test_the_write_frame_is_what_the_radio_itself_reports():
    """The answer a real FT-991A gave for a stored channel (from a service log) must be the write frame, except P7 (0 when writing, 1 when reading)."""
    w = frame.memory_write(11, 145_600_000, "FM", "ctcss_enc", "minus", "S.ESTRELA")
    real = "MT011145600000+0000004120020S.ESTRELA   ;"
    assert w == real.replace("+00000041", "+00000040") and len(w) == 41
    d = frame.decode_memory(real)
    assert d["tone_mode"] == "ctcss_enc" and d["shift"] == "minus" and d["tag"] == "S.ESTRELA" and d["frequency"] == 145_600_000
    assert frame.memory_write(5, 14_200_000, "USB") == "MT005014200000+000000" + "2" + "0" + "0" + "00" + "0" + "0" + " " * 12 + ";"


@pytest.mark.parametrize("args", [
    (0, 14_200_000, "USB"), (100, 14_200_000, "USB"), (5, 10, "USB"), (5, 500_000_000, "USB"), (5, 14_200_000, "XYZ"),
    (5, 14_200_000, "USB", "loud"), (5, 14_200_000, "USB", "off", "sideways"), (5, 14_200_000, "USB", "off", "simplex", "x" * 13),
    (5, 14_200_000, "USB", "off", "simplex", "a;b"), (5, 14_200_000, "USB", "off", "simplex", "café"), (5, True, "USB"), (5, 14.2, "USB"),
])
def test_bad_values_never_reach_the_radio(args):
    with pytest.raises(frame.FrameError):
        frame.memory_write(*args)


async def test_edit_an_existing_channel_and_add_a_new_one(rig):
    d, sim = rig.driver, rig.sim
    before = await d.memory_channels()
    got = await d.memory_write(11, 145_600_000, "FM", "ctcss_enc", "minus", "S.ESTRELA")
    assert got["frequency"] == 145_600_000 and got["tone_mode"] == "ctcss_enc" and got["shift"] == "minus" and got["tag"] == "S.ESTRELA"
    assert sim.mem_writes == 1
    items = {m["channel"]: m for m in await d.memory_channels()}
    assert items[11]["tag"] == "S.ESTRELA" and items[11]["band"] == "2m"
    assert {m["channel"]: m for m in before}[5] == items[5]                        # the others are untouched
    await d.memory_write(50, 438_900_000, "FM", "off", "plus", "UHF NEW")
    listed = await d.memory_channels()
    assert sorted(m["channel"] for m in listed) == [5, 6, 11, 50, 99]
    assert next(m for m in listed if m["channel"] == 50)["shift"] == "plus"


async def test_a_radio_that_writes_to_the_wrong_channel_is_caught_and_editing_switches_off(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    sim.write_misdirect = 6                                                         # a faulty radio: whatever channel is asked, channel 6 is overwritten
    with pytest.raises(RadioError, match="other memory channels"):
        await d.memory_write(11, 145_600_000, "FM", "off", "simplex", "oops")
    with pytest.raises(RadioError, match="switched off"):
        await d.memory_write(12, 145_700_000, "FM")                                 # refused from now on
    assert sim.mem_writes == 1


async def test_a_radio_that_refuses_the_write_is_reported_not_believed(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    sim.refuse_writes = True                                                        # the radio answers '?;' and stores nothing
    with pytest.raises(RadioError, match="rejected"):
        await d.memory_write(50, 438_900_000, "FM", "off", "simplex", "NOPE")
    assert 50 not in [m["channel"] for m in await d.memory_channels()]


async def test_not_while_transmitting_and_not_without_the_feature(rig):
    d = rig.driver
    await d.memory_channels()
    d.state["tx"] = True
    with pytest.raises(RadioError, match="transmitting"):
        await d.memory_write(5, 14_200_000, "USB")
    d.state["tx"] = False
    d.caps.data["features"]["memory_edit"] = False
    with pytest.raises(RadioError, match="not enabled"):
        await d.memory_write(5, 14_200_000, "USB")
    d.caps.data["features"]["memory_edit"] = True
    with pytest.raises(RadioError, match="memory channel"):
        await d.memory_write(0, 14_200_000, "USB")


def test_the_hf_radios_have_no_memory_write():
    from radio_remote.radio.cat.proto import HfProto
    assert not hasattr(HfProto, "memory_write")


URL = "/api/memories/"


async def test_endpoint_is_admin_only_and_needs_confirmation(make_app, monkeypatch):
    client = await make_app()
    root = await admin_user(client)
    body = {"frequency": 145_600_000, "mode": "FM", "tone_mode": "ctcss_enc", "shift": "minus", "name": "S.ESTRELA", "confirm": True}
    r = await client.post(URL + "11", json={**body, "confirm": False}, headers=root.h)
    assert r.status == 400 and "confirmation" in await r.text()
    assert (await client.post(URL + "11", json=body)).status == 403                           # no CSRF token
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await op.c.post(URL + "11", json=body, headers=op.h)).status == 403               # operators may not rewrite the radio's memories
    for bad in ("0", "100", "x"):
        assert (await client.post(URL + bad, json=body, headers=root.h)).status == 400
    assert (await client.post(URL + "11", json={**body, "frequency": "145.6"}, headers=root.h)).status == 400
    assert (await client.post(URL + "11", json={**body, "mode": "XYZ"}, headers=root.h)).status == 409
    r = await client.post(URL + "11", json=body, headers=root.h)
    assert r.status == 200 and (await r.json())["channel"]["tag"] == "S.ESTRELA"
    listed = {m["channel"]: m for m in (await (await client.get("/api/memories")).json())["channels"]}
    assert listed[11]["frequency"] == 145_600_000 and listed[11]["tone_mode"] == "ctcss_enc" and listed[11]["shift"] == "minus"
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "memory_write" in events
    monkeypatch.setattr(client.app[K_GUARD], "keyed", True)
    assert (await client.post(URL + "11", json=body, headers=root.h)).status == 409           # never while the radio transmits
    await close_all(op, root)
