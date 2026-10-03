import asyncio
import json

import pytest
from conftest import setup_admin
from test_cat import FAST, Rig, wait_for
from test_web import origin, recv_until

from radio_remote.radio import controls
from radio_remote.radio.base import Capabilities, RadioError
from radio_remote.radio.cat import frame
from radio_remote.radio.cat import ft991a_controls as fc
from radio_remote.radio.drivers.ft991a import FT991ADriver


# ------------------------------------------------------------ width tables (manual p.16)
def test_width_options_by_mode_class():
    ssb_n = {o["hz"] for o in fc.width_options("USB", True)}
    assert ssb_n == {200, 400, 600, 850, 1100, 1350, 1500, 1650, 1800}
    ssb_w = {o["hz"]: o["code"] for o in fc.width_options("LSB", False)}
    assert ssb_w[3200] == 21 and ssb_w[1800] == 9 and ssb_w[2400] == 14
    cw_n = {o["hz"]: o["code"] for o in fc.width_options("CW-U", True)}
    assert cw_n[50] == 1 and cw_n[500] == 10
    cw_w = {o["hz"]: o["code"] for o in fc.width_options("CW-L", False)}
    assert cw_w[3000] == 17 and 2400 in cw_w
    assert fc.width_options("RTTY-L", True)[0] == {"code": 1, "hz": 50}


def test_width_unavailable_where_not_documented():
    for mode in ("AM", "FM", "C4FM", "DATA-U", "DATA-L", None):
        assert fc.width_options(mode, False) == []
        assert fc.width_hz(mode, False, 5) is None


def test_width_default_code_and_codes_unique():
    assert fc.width_hz("USB", True, 0) == 1500 and fc.width_hz("USB", False, 0) == 2400
    assert fc.width_hz("CW-U", True, 0) == 500 and fc.width_hz("RTTY-U", True, 0) == 300
    for mode in ("USB", "CW-U", "RTTY-L"):
        for narrow in (True, False):
            opts = fc.width_options(mode, narrow)
            assert len({o["hz"] for o in opts}) == len(opts) and 0 not in {o["code"] for o in opts}


# ------------------------------------------------- golden CAT strings from the manual
GOLDEN = [
    ("narrow", True, ["NA01;"]), ("narrow", False, ["NA00;"]),
    ("if_shift", 100, ["IS0+0100;"]), ("if_shift", -1200, ["IS0-1200;"]), ("if_shift", 0, ["IS0+0000;"]),
    ("contour", True, ["CO000001;"]), ("contour", False, ["CO000000;"]),
    ("contour_freq", 800, ["CO010800;"]), ("contour_freq", 3200, ["CO013200;"]),
    ("apf", True, ["CO020001;"]), ("apf_freq", 0, ["CO030025;"]), ("apf_freq", -250, ["CO030000;"]),
    ("apf_freq", 250, ["CO030050;"]),
    ("notch", True, ["BP00001;"]), ("notch_freq", 1000, ["BP01100;"]), ("notch_freq", 3200, ["BP01320;"]),
    ("auto_notch", True, ["BC01;"]),
    ("nr", True, ["NR01;"]), ("nr_level", 7, ["RL007;"]), ("nr_level", 15, ["RL015;"]),
    ("nb", False, ["NB00;"]), ("nb_level", 10, ["NL0010;"]),
    ("ipo", "IPO", ["PA00;"]), ("ipo", "AMP2", ["PA02;"]),
    ("att", True, ["RA01;"]), ("rit", True, ["RT1;"]), ("xit", False, ["XT0;"]),
    ("clarifier_hz", 0, ["RC;"]), ("clarifier_hz", 150, ["RC;", "RU0150;"]), ("clarifier_hz", -9999, ["RC;", "RD9999;"]),
    ("processor", True, ["PR01;"]), ("processor", False, ["PR00;"]), ("processor_level", 100, ["PL100;"]),
    ("monitor", True, ["ML0001;"]), ("monitor_level", 50, ["ML1050;"]),
    ("tuner", True, ["AC001;"]), ("tuner", False, ["AC000;"]),
    ("dgid", "AUTO", ["EX15300;"]), ("dgid", "7", ["EX15307;"]), ("dgid", "99", ["EX15399;"]),
]


@pytest.mark.parametrize("name,value,cmds", GOLDEN)
def test_encoders_match_manual(name, value, cmds):
    assert fc.ENCODE[name][1](value) == cmds


@pytest.mark.parametrize("name,value,cmds", [g for g in GOLDEN if g[0] not in ("clarifier_hz", "ipo")])
def test_set_format_is_parseable_as_answer(name, value, cmds):
    """Set and answer frames share a format, so what we send must decode back to the same value."""
    assert frame.decode(cmds[0]) == {name: value}


def test_processor_answers_seen_on_real_radio():
    assert frame.decode("PR00;") == {"processor": False}       # captured from a real FT-991A
    assert frame.decode("PR10;") == {"mic_eq": False}          # captured from a real FT-991A
    assert frame.decode("PR01;") == {"processor": True}        # PR01; set the processor ON on the real radio
    assert frame.decode("PR11;") == {"mic_eq": True}
    with pytest.raises(frame.FrameError):
        frame.decode("PR02;")                                  # the radio answers ?; to PR02, so it never reports 2


def test_ipo_and_width_and_clarifier_answers():
    assert frame.decode("PA02;") == {"ipo": "AMP2"}
    assert frame.decode(fc.width_set(9)) == {"width_code": 9}
    assert fc.width_set(21) == "SH021;"
    assert frame.decode("IF001014250000-012310200000;"[:0] or
                        "IF001014250000+0150" + "1" + "0" + "2" + "00000;")["clarifier_hz"] == 150


@pytest.mark.parametrize("bad", ["NA02;", "IS0+12;", "CO09;", "CO000001x;", "BP0X0001;", "PA03;", "PR03;", "RL0;"])
def test_parsers_reject_garbage(bad):
    with pytest.raises(frame.FrameError):
        frame.decode(bad)


def test_every_encoded_control_has_a_spec_and_feature():
    for name in fc.ENCODE:
        assert name in controls.SPEC_BY_NAME, name
    from radio_remote.radio.cat.proto import HfProto
    known = set(fc.ENCODE) | set(HfProto("ftdx10").ENCODE)           # a control may exist on the newer radios only (att_level)
    for name, spec in controls.SPEC_BY_NAME.items():
        assert name == "width" or name in known, name


def test_band_codes_from_manual():
    assert fc.BAND_CODES["20m"] == 5 and fc.BAND_CODES["2m"] == 15 and fc.BAND_CODES["70cm"] == 16
    assert fc.BAND_CODES["12m"] == 8 and fc.BAND_CODES["60m"] == 2


# ------------------------------------------------------------------ validation
def test_coerce_rules():
    spec = controls.SPEC_BY_NAME
    assert controls.coerce(spec["if_shift"], 40) == 40
    for name, bad in [("if_shift", 30), ("if_shift", 1300), ("if_shift", True), ("nr_level", 0), ("nr_level", 16),
                      ("nb_level", 11), ("narrow", 1), ("ipo", "AMP3"), ("notch_freq", 5), ("apf_freq", 255),
                      ("contour_freq", 5), ("clarifier_hz", 10000), ("nr", "on"), ("dgid", "0"), ("dgid", "100"), ("dgid", 7)]:
        with pytest.raises(ValueError):
            controls.coerce(spec[name], bad)


def test_ft991a_ui_exposes_only_verified_controls():
    pub = Capabilities.load("ft991a").public()
    names = {c["name"] for c in pub["controls"]}
    assert {"width", "if_shift", "nr", "nb", "ipo", "att", "rit", "xit", "processor", "monitor", "tuner"} <= names
    assert pub["features"]["power_on_cat"] is True and pub["features"]["tuner_tune"] is True and pub["features"]["dgid"] is True
    assert "agc" in names and "apf" not in names and pub["features"]["apf"] is False       # AGC is offered (documented); APF stays hidden
    assert pub["features"]["antenna_switching"] is False
    assert pub["bands"][-2:] == ["2m", "70cm"]


# ------------------------------------------------------- driver against the simulator
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


async def test_initial_controls_are_read(rig):
    s = rig.driver.state
    assert s["narrow"] is False and s["nr"] is False and s["nr_level"] == 5 and s["ipo"] == "IPO"
    assert s["if_shift"] == 0 and s["contour_freq"] == 1500        # APF is not offered on the FT-991A (CW-only, did not work on the bench)
    assert s["width"] == 2400 and s["width_options"]       # USB wide, default code 00


@pytest.mark.parametrize("name,value,reg,payload", [
    ("if_shift", -200, "IS0", "-0200"), ("contour", True, "CO00", "0001"), ("contour_freq", 1500, "CO01", "1500"),
    ("notch", True, "BP00", "001"), ("notch_freq", 2000, "BP01", "200"),
    ("auto_notch", True, "BC0", "1"), ("nr", True, "NR0", "1"), ("nr_level", 12, "RL0", "12"),
    ("nb", True, "NB0", "1"), ("nb_level", 8, "NL0", "008"), ("ipo", "AMP1", "PA0", "1"), ("att", True, "RA0", "1"),
    ("rit", True, "RT", "1"), ("xit", True, "XT", "1"), ("processor", True, "PR0", "1"), ("processor", False, "PR0", "0"),
    ("processor_level", 70, "PL", "070"), ("monitor", True, "ML0", "001"), ("monitor_level", 20, "ML1", "020"),
    ("tuner", True, "AC", "001"), ("dgid", "12", "EX153", "12"), ("dgid", "AUTO", "EX153", "00"),
])
async def test_set_control_roundtrip(rig, name, value, reg, payload):
    await rig.driver.set_control(name, value)
    assert rig.sim.reg[reg] == payload
    assert rig.driver.state[name] == value               # state comes from the radio's read-back


async def test_clarifier_offset_signs(rig):
    await rig.driver.set_control("clarifier_hz", 250)
    assert rig.sim.clar == 250 and rig.driver.state["clarifier_hz"] == 250
    await rig.driver.set_control("clarifier_hz", -120)
    assert rig.sim.clar == -120 and rig.driver.state["clarifier_hz"] == -120
    await rig.driver.set_control("clarifier_hz", 0)
    assert rig.sim.clar == 0 and rig.driver.state["clarifier_hz"] == 0


async def test_width_follows_narrow_and_mode(rig):
    d = rig.driver
    await d.set_control("narrow", True)
    assert rig.sim.reg["NA0"] == "1" and d.state["width"] == 1500       # narrow default
    await d.set_control("width", 600)
    assert rig.sim.reg["SH0"] == "03" and d.state["width"] == 600
    with pytest.raises(RadioError):
        await d.set_control("width", 3000)                              # wide-only value
    await d.set_mode("CW-U")
    await wait_for(lambda: d.state["width_options"] and d.state["width_options"][0]["hz"] == 50)
    await d.set_mode("FM")
    await wait_for(lambda: d.state["width_options"] == [])
    with pytest.raises(RadioError):
        await d.set_control("width", 600)


async def test_front_panel_mode_change_refreshes_width(rig):
    rig.sim.mode = "CW-U"
    rig.sim.reg["NA0"], rig.sim.reg["SH0"] = "1", "05"      # CW narrow, code 05 = 250 Hz
    await rig.sim._send("MD03;")
    await wait_for(lambda: rig.driver.state["mode"] == "CW-U" and rig.driver.state["width"] == 250)


async def test_set_control_rejects_bad_input_without_touching_radio(rig):
    before = list(rig.sim.log)
    for name, value in [("nr_level", 99), ("agc", 1), ("if_shift", 30), ("bogus", 1), ("tuner_tune", True)]:
        with pytest.raises(RadioError):
            await rig.driver.set_control(name, value)
    assert rig.sim.log == before


async def test_set_band_uses_bs_and_reads_back(rig):
    await rig.driver.set_band("40m")
    assert "BS03" in rig.sim.log and rig.driver.state["band"] == "40m"
    await rig.driver.set_band("70cm")
    assert rig.driver.state["band"] == "70cm" and rig.driver.state["frequency"] == 432_100_000
    with pytest.raises(RadioError):
        await rig.driver.set_band("23cm")


async def test_power_off_sends_ps0_and_link_drops(rig):
    await rig.driver.power_off()
    assert "PS0" in rig.sim.log
    await wait_for(lambda: not rig.driver.state["connected"])


async def test_power_off_refused_while_transmitting(rig):
    await rig.driver.set_ptt(True)
    with pytest.raises(RadioError):
        await rig.driver.power_off()
    assert "PS0" not in rig.sim.log
    await rig.driver.set_ptt(False)


async def test_power_on_and_tune_start_are_never_sent(rig):
    await asyncio.sleep(0.5)                                   # let every poll loop run
    assert "PS1" not in rig.sim.log and "AC002" not in rig.sim.log
    assert all(c in ("EX153", "EX106") or not c.startswith("EX") for c in rig.sim.log)    # menus 153/106 may be READ; nothing writes a menu on its own


# ------------------------------------------------------------------------ hub rules
async def ws_ready(client):
    await setup_admin(client)
    ws = await client.ws_connect("/ws", headers=origin(client))
    hello = json.loads((await ws.receive()).data)
    return ws, hello


async def ack_for(ws, msg):
    await ws.send_json(msg)
    return await recv_until(ws, lambda m: m["t"] == "ack" and m["id"] == msg["id"])


async def test_hello_contains_controls_and_bands(client):
    ws, hello = await ws_ready(client)
    assert any(c["name"] == "nr" for c in hello["caps"]["controls"]) and "70cm" in hello["caps"]["bands"]
    assert hello["state"]["width_options"]
    await ws.close()


async def test_ws_set_control_and_validation(client):
    ws, _ = await ws_ready(client)
    assert (await ack_for(ws, {"id": 1, "type": "set_control", "name": "nr", "value": True}))["ok"]
    p = await recv_until(ws, lambda m: m["t"] == "patch" and "nr" in m["d"])
    assert p["d"]["nr"] is True
    for i, bad in enumerate([
        {"name": "nr", "value": "yes"}, {"name": "nr_level", "value": 99}, {"name": "agc", "value": 1},
        {"name": "if_shift", "value": 7}, {"name": 5, "value": 1}, {"name": "width", "value": 99},
    ], start=10):
        a = await ack_for(ws, {"id": i, "type": "set_control", **bad})
        assert a["ok"] is False, bad
    assert (await ack_for(ws, {"id": 30, "type": "set_band", "band": "40m"}))["ok"]
    assert (await ack_for(ws, {"id": 31, "type": "set_band", "band": "9m"}))["ok"] is False
    await ws.close()


async def test_power_off_hub_rules(client):
    ws, _ = await ws_ready(client)
    a = await ack_for(ws, {"id": 1, "type": "power_off"})
    assert a["ok"] is False and "confirm" in a["error"]
    a = await ack_for(ws, {"id": 2, "type": "power_off", "confirm": True})
    assert a["ok"] is False and "not supported" in a["error"]        # mock profile has no CAT power-off
    await ws.close()


async def test_clarifier_offset_switches_rit_on_when_no_clarifier_is_on(rig):
    d, sim = rig.driver, rig.sim
    assert d.state["rit"] is False and d.state["xit"] is False
    await d.set_control("clarifier_hz", 300)
    assert sim.reg["RT"] == "1" and d.state["rit"] is True and d.state["clarifier_hz"] == 300
    await d.set_control("rit", False)
    await d.set_control("xit", True)
    await d.set_control("clarifier_hz", -50)                 # XIT is on: RIT must not be switched on behind the operator's back
    assert sim.reg["RT"] == "0" and d.state["xit"] is True and d.state["clarifier_hz"] == -50


# ------------------------------------------------------------------ power on (PS1 after dummy data, manual p.15)
async def test_power_on_sends_dummy_data_then_ps1_after_the_gap():
    import time
    from radio_remote.radio.cat.transport import Transport, TransportClosed
    from radio_remote.radio.drivers.ft991a import FT991ADriver, Timings

    writes = []

    class DeadRadio(Transport):                          # a radio in standby: accepts bytes, never answers
        def __init__(self):
            self._ev = asyncio.Event()
        async def open(self):
            pass
        async def close(self):
            self._ev.set()
        async def write(self, data):
            writes.append((time.monotonic(), data))
        async def read(self):
            await self._ev.wait()
            raise TransportClosed("closed")

    async def factory():
        return DeadRadio()

    d = FT991ADriver(None, transport_factory=factory,
                     timings=Timings(timeout_s=0.05, backoff_min_s=0.05, backoff_max_s=0.1, wake_gap_s=0.2))
    await d.start()
    await asyncio.sleep(0.3)
    writes.clear()
    await d.power_on()
    for _ in range(60):
        if any(w[1] == b"PS1;" for w in writes):
            break
        await asyncio.sleep(0.05)
    await d.stop()
    sent = [w for w in writes if w[1] in (b";", b"PS1;")]
    assert [w[1] for w in sent] == [b";", b"PS1;"]
    assert sent[1][0] - sent[0][0] >= 0.19               # PS1 only after the gap


async def test_power_on_refused_when_the_radio_is_already_on(rig):
    with pytest.raises(RadioError):
        await rig.driver.power_on()


# ------------------------------------------------------------------ VFO B mode (OI; opposite band information, manual p.14)
def test_oi_frame_gives_the_vfo_b_mode():
    assert frame.decode("OI001007100000+000000" + "1" + "00000;") == {"mode_b": "LSB"}
    assert frame.decode("OI001007100000+000000" + "C" + "00000;") == {"mode_b": "DATA-U"}
    with pytest.raises(frame.FrameError):
        frame.decode("OI001007100000+0000000;")


async def test_vfo_b_mode_is_read_and_follows_swap_and_copy(rig):
    d, sim = rig.driver, rig.sim
    for _ in range(40):
        if d.state.get("mode_b"):
            break
        await asyncio.sleep(0.05)
    assert d.state["mode_b"] == "LSB" and d.state["mode"] == "USB"
    await d.vfo_op("swap")
    for _ in range(40):
        if d.state.get("mode_b") == "USB":
            break
        await asyncio.sleep(0.05)
    assert d.state["mode_b"] == "USB" and d.state["mode"] == "LSB"
    await d.vfo_op("a_to_b")
    for _ in range(40):
        if d.state.get("mode_b") == "LSB":
            break
        await asyncio.sleep(0.05)
    assert d.state["mode_b"] == "LSB"
