"""Tone frequency of a memory channel (stored per channel via VFO-A, bench-found on a real FT-991A), reading it back, and emptying a channel (recall + AM)."""
import pytest
from test_cat import rig  # noqa: F401  (driver + simulated FT-991A)
from test_phase8 import add_user, admin_user, close_all, login_as, make_app  # noqa: F401

from radio_remote.common import K_GUARD
from radio_remote.radio.base import RadioError
from radio_remote.radio.cat import frame


def test_the_tone_tables_are_the_manuals():
    assert len(frame.CTCSS_TONES) == 50 and frame.CTCSS_TONES[0] == 67.0 and frame.CTCSS_TONES[2] == 71.9 and frame.CTCSS_TONES[10] == 94.8 and frame.CTCSS_TONES[49] == 254.1
    assert len(frame.DCS_CODES) == 104 and frame.DCS_CODES[0] == "023" and frame.DCS_CODES[15] == "074" and frame.DCS_CODES[103] == "754"
    assert frame.tone_set("ctcss", 2) == "CN00002;" and frame.tone_set("dcs", 103) == "CN01103;"
    assert frame.tone_index(88.5) == 8 and frame.dcs_index("023") == 0 and frame.dcs_index("754") == 103
    for bad in (lambda: frame.tone_index(70.0), lambda: frame.dcs_index("999"), lambda: frame.tone_set("ctcss", 50), lambda: frame.tone_set("dcs", 104), lambda: frame.tone_set("x", 1)):
        with pytest.raises(frame.FrameError):
            bad()
    assert frame.decode_tone_state("CT02;", "CN00010;", "CN01000;", "OS02;") == {"tone_mode": "ctcss_enc", "tone_hz": 94.8, "dcs_code": "023", "shift": "minus"}
    assert frame.decode_tone_state(None, "?;", None, None) == {"tone_mode": None, "tone_hz": None, "dcs_code": None, "shift": None}


async def test_the_tone_frequency_is_stored_per_channel_and_vfo_a_is_put_back(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    sim.freq, sim.mode = 14_200_000, "USB"
    sim.vfo_tone.update(CT="0", OS="0", CN0="008", CN1="000")                            # VFO-A: no tone, tone number 008 (88.5 Hz) left over from earlier
    await d.memory_write(11, 145_600_000, "FM", "ctcss_enc", "minus", "A", tone_hz=71.9)
    await d.memory_write(12, 145_625_000, "FM", "ctcss_encdec", "minus", "B", tone_hz=94.8)
    assert sim.mem_tone[11]["CN0"] == "002" and sim.mem_tone[12]["CN0"] == "010"          # each channel has its own number, whatever VFO-A held
    assert sim.mem_tone[11]["CT"] == "2" and sim.mem_tone[12]["CT"] == "1" and sim.mem_tone[11]["OS"] == "2"
    assert (sim.freq, sim.mode, sim.mem_ch) == (14_200_000, "USB", 0)                    # VFO-A frequency, mode and the VFO state are back
    assert sim.vfo_tone == {"CT": "0", "OS": "0", "CN0": "008", "CN1": "000"}              # and so are its own shift and tone settings


async def test_the_tone_is_read_back_by_recalling_the_channel_and_the_radio_is_put_back(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    await d.memory_write(11, 145_600_000, "FM", "ctcss_enc", "minus", "A", tone_hz=88.5)
    sim.vfo_tone["CN0"] = "020"
    assert await d.memory_tone(11) == {"tone_mode": "ctcss_enc", "tone_hz": 88.5, "dcs_code": "023", "shift": "minus"}
    assert sim.mem_ch == 0                                                                # it was in VFO mode: back in VFO mode
    await d.memory_select(6)                                                              # in memory mode on channel 6: stays there
    await d.memory_tone(11)
    assert sim.mem_ch == 6


async def test_editing_a_name_keeps_the_tone_frequency(rig):
    """The editor fills the form from memory_tone() and sends the tone back, so a name change does not reset the tone to whatever VFO-A holds."""
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    await d.memory_write(11, 145_600_000, "FM", "ctcss_enc", "minus", "OLD NAME", tone_hz=123.0)
    sim.vfo_tone["CN0"] = "000"                                                           # VFO-A holds 67.0 Hz now
    t = await d.memory_tone(11)
    await d.memory_write(11, 145_600_000, "FM", t["tone_mode"], t["shift"], "NEW NAME", tone_hz=t["tone_hz"])
    assert sim.mem_tone[11]["CN0"] == "018" and sim.memories[11][2] == "NEW NAME"           # 123.0 Hz kept


async def test_a_tone_mode_without_a_tone_is_refused_instead_of_guessed(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    with pytest.raises(RadioError, match="tone frequency is needed"):
        await d.memory_write(11, 145_600_000, "FM", "ctcss_enc", "minus", "X")
    with pytest.raises(RadioError, match="DCS code is needed"):
        await d.memory_write(11, 145_600_000, "FM", "dcs_enc", "minus", "X")
    with pytest.raises(RadioError, match="not one of"):
        await d.memory_write(11, 145_600_000, "FM", "ctcss_enc", "minus", "X", tone_hz=70.0)
    assert sim.mem_writes == 0
    await d.memory_write(11, 145_600_000, "FM", "dcs_enc", "plus", "DCS", dcs_code="754")
    assert sim.mem_tone[11]["CN1"] == "103" and sim.mem_tone[11]["CT"] == "4"


async def test_delete_empties_only_that_channel_and_puts_the_radio_back(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    await d.memory_delete(11)
    assert sorted(m["channel"] for m in await d.memory_channels()) == [5, 6, 99]
    assert sim.mem_ch == 0                                                                # back in VFO mode (the real radio lands on the first channel after AM)
    with pytest.raises(RadioError, match="already empty"):
        await d.memory_delete(11)
    await d.memory_select(5)
    await d.memory_delete(6)                                                              # radio was on channel 5: back to channel 5
    assert sim.mem_ch == 5
    await d.memory_delete(5)                                                              # deleting the channel the radio was on: ends in VFO mode
    assert sim.mem_ch == 0


async def test_delete_never_sends_am_when_the_recall_did_not_take(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    sim.mem_check = 2                                                                     # a radio stuck in a special memory state: MC does nothing
    with pytest.raises(RadioError, match="V/M"):
        await d.memory_delete(11)
    assert "AM" not in sim.log and 11 in sim.memories                                     # AM could write or erase the wrong thing: it is not sent


async def test_a_radio_whose_am_empties_another_channel_is_caught(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    sim.am_deletes = 6
    with pytest.raises(RadioError, match="switched off"):
        await d.memory_delete(11)
    with pytest.raises(RadioError, match="switched off"):
        await d.memory_write(12, 145_700_000, "FM")


URL = "/api/memories/"


async def test_endpoints_tone_and_delete(make_app, monkeypatch):
    client = await make_app()
    root = await admin_user(client)
    body = {"frequency": 145_600_000, "mode": "FM", "tone_mode": "ctcss_enc", "tone_hz": 88.5, "shift": "minus", "name": "S.ESTRELA", "confirm": True}
    assert (await client.post(URL + "11", json=body, headers=root.h)).status == 200
    r = await client.get(URL + "11/tone", headers=root.h)
    assert r.status == 200 and (await r.json())["tone_hz"] == 88.5
    assert (await client.post(URL + "11", json={**body, "tone_hz": None}, headers=root.h)).status == 409          # a tone mode needs its tone
    assert (await client.post(URL + "11", json={**body, "tone_hz": "88.5"}, headers=root.h)).status == 400
    await add_user(root, "op", "operator")
    op = await login_as(root, "op")
    assert (await op.c.get(URL + "11/tone", headers=op.h)).status == 403
    assert (await op.c.post(URL + "11/delete", json={"confirm": True}, headers=op.h)).status == 403
    assert (await client.post(URL + "11/delete", json={}, headers=root.h)).status == 400                           # no confirmation
    assert (await client.post(URL + "0/delete", json={"confirm": True}, headers=root.h)).status == 400
    monkeypatch.setattr(client.app[K_GUARD], "keyed", True)
    assert (await client.post(URL + "11/delete", json={"confirm": True}, headers=root.h)).status == 409            # never while transmitting
    monkeypatch.setattr(client.app[K_GUARD], "keyed", False)
    assert (await client.post(URL + "11/delete", json={"confirm": True}, headers=root.h)).status == 200
    assert (await client.post(URL + "11/delete", json={"confirm": True}, headers=root.h)).status == 409            # already empty
    listed = [m["channel"] for m in (await (await client.get("/api/memories")).json())["channels"]]
    assert 11 not in listed
    events = [e["event"] for e in (await (await client.get("/api/audit")).json())["events"]]
    assert "memory_delete" in events
    await close_all(op, root)
