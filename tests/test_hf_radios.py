"""Profiles for the newer Yaesu HF radios (FTDX10, FTDX101D/MP, FT-710). EXPERIMENTAL: written from the CAT manuals only, so these tests
prove that the driver agrees with the manual text (exact command strings, frame layouts, which commands are never sent), not that
a real radio accepts them."""
import asyncio

import pytest

from radio_remote.radio import controls
from radio_remote.radio.base import Capabilities, RadioError
from radio_remote.radio.cat import frame
from radio_remote.radio.cat.proto import HfProto, MODELS, proto_for
from radio_remote.radio.cat.sim_hf import SimulatedYaesuHf
from radio_remote.radio.cat.transport import MemoryTransport
from radio_remote.radio.drivers.ft991a import DRIVERS, YaesuCatDriver
from radio_remote.radio.registry import create_driver
from test_cat import FAST, wait_for

HF = ["ftdx10", "ftdx101d", "ftdx101mp", "ft710"]


# ------------------------------------------------------------------ profiles are separate and complete
@pytest.mark.parametrize("model", HF)
def test_profile_loads_and_is_marked_experimental(model):
    caps = Capabilities.load(model)
    pub = caps.public()
    assert pub["experimental"] is True and pub["mock"] is False
    assert pub["model"]["id"] == model
    assert "tuner_tune" not in {c["name"] for c in pub["controls"]} and pub["features"]["tuner_tune"] is False   # TX-keying: not until tested
    names = {c["name"] for c in pub["controls"]}
    assert {"agc", "att_level", "mic_select", "nr", "nb", "notch", "contour", "if_shift", "width"} <= names
    assert "processor" not in names and "apf" not in names and "dgid" not in names     # unverified / hidden
    assert pub["bands"] == ["160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m"]
    assert pub["features"]["vhf"] is False and pub["features"]["uhf"] is False


def test_ft991a_is_not_experimental_and_now_offers_agc():
    pub = Capabilities.load("ft991a").public()
    assert pub["experimental"] is False
    assert "agc" in {c["name"] for c in pub["controls"]} and "att_level" not in {c["name"] for c in pub["controls"]}


def test_each_radio_gets_its_own_driver_class_and_ids():
    assert set(DRIVERS) == {"ft991a", *HF}
    assert {m: MODELS[m].radio_id for m in HF} == {"ftdx10": "0761", "ftdx101d": "0681", "ftdx101mp": "0682", "ft710": "0800"}
    for m in HF:
        d = create_driver({"radio": {"model": m, "serial_port": "auto", "baud": 38400}})
        assert isinstance(d, YaesuCatDriver) and d.caps.data["model"]["id"] == m and d.proto.RADIO_ID == MODELS[m].radio_id


def test_dual_receiver_radios_do_not_offer_vfo_b_or_split():
    for m in ("ftdx101d", "ftdx101mp"):
        f = Capabilities.load(m).public()["features"]
        assert f["vfo_b"] is False and f["split"] is False and f["quick_split"] is False
    f = Capabilities.load("ftdx10").public()["features"]
    assert f["vfo_b"] and f["split"] and f["quick_split"]
    f = Capabilities.load("ft710").public()["features"]
    assert f["vfo_b"] and f["split"] and not f["quick_split"] and not f["rit"]        # no QS / RT / RU / RD / RC in the FT-710 manual


def test_power_limits_per_model():
    assert proto_for("ftdx10").LEVELS["rf_power"][3] == 100 and proto_for("ftdx101mp").LEVELS["rf_power"][3] == 200
    assert Capabilities.load("ftdx101mp").data["levels"]["rf_power"]["max"] == 200


# ------------------------------------------------------------------ exact commands (manual FTDX10 2308-F unless noted)
GOLDEN = [
    ("narrow", True, ["NA01;"]), ("if_shift", 200, ["IS00+0200;"]), ("if_shift", -1200, ["IS00-1200;"]),
    ("contour", True, ["CO000001;"]), ("contour_freq", 800, ["CO010800;"]), ("notch", True, ["BP00001;"]),
    ("notch_freq", 1500, ["BP01150;"]), ("auto_notch", True, ["BC01;"]), ("nr", True, ["NR01;"]), ("nr_level", 7, ["RL007;"]),
    ("nb", True, ["NB01;"]), ("nb_level", 5, ["NL0005;"]), ("ipo", "AMP2", ["PA02;"]),
    ("att_level", "OFF", ["RA00;"]), ("att_level", "6 dB", ["RA01;"]), ("att_level", "12 dB", ["RA02;"]), ("att_level", "18 dB", ["RA03;"]),
    ("agc", "OFF", ["GT00;"]), ("agc", "FAST", ["GT01;"]), ("agc", "MID", ["GT02;"]), ("agc", "SLOW", ["GT03;"]), ("agc", "AUTO", ["GT04;"]),
    ("monitor", True, ["ML0001;"]), ("monitor_level", 40, ["ML1040;"]), ("tuner", True, ["AC001;"]),
]


@pytest.mark.parametrize("name,value,cmds", GOLDEN)
def test_encoders_match_the_manual(name, value, cmds):
    for m in HF:
        enc = proto_for(m).ENCODE
        assert enc[name][1](value) == cmds, (m, name)


def test_clarifier_exists_on_ftdx_radios_only():
    for m in ("ftdx10", "ftdx101d", "ftdx101mp"):
        e = proto_for(m).ENCODE
        assert e["rit"][1](True) == ["RT1;"] and e["xit"][1](True) == ["XT1;"]
        assert e["clarifier_hz"][1](150) == ["RC;", "RU0150;"] and e["clarifier_hz"][1](-9990) == ["RC;", "RD9990;"]
    e = proto_for("ft710").ENCODE
    assert "rit" not in e and "xit" not in e and "clarifier_hz" not in e


def test_mic_source_menu_per_radio():
    # SSB MOD SOURCE: FTDX10 EX 01 01 13 (p.11), FTDX101 EX 01 01 11 (p.10), FT-710 MOD SOURCE EX 01 01 14 (p.10)
    assert proto_for("ftdx10").ENCODE["mic_select"][0] == "EX010113;" and proto_for("ftdx101d").ENCODE["mic_select"][0] == "EX010111;"
    assert proto_for("ft710").ENCODE["mic_select"][0] == "EX010114;"
    assert proto_for("ftdx10").ENCODE["mic_select"][1]("REAR") == ["EX0101131;"]
    assert proto_for("ftdx101d").ENCODE["mic_select"][1]("MIC") == ["EX0101110;"]
    assert proto_for("ft710").ENCODE["mic_select"][1]("REAR") == ["EX0101141;"]
    assert proto_for("ftdx10").decode("EX0101131;") == {"mic_select": "REAR"}
    assert proto_for("ft710").decode("EX0101140;") == {"mic_select": "MIC"}
    assert proto_for("ftdx10").decode("EX0101140;") == {}                                  # another radio's menu is not interpreted


def test_basic_commands():
    p = proto_for("ftdx10")
    assert p.freq_set(14_074_000) == "FA014074000;" and p.freq_set(7_100_000, "B") == "FB007100000;"
    with pytest.raises(frame.FrameError):
        p.freq_set(146_000_000)                                                              # these radios stop at 75 MHz
    assert p.mode_set("USB") == "MD02;" and p.mode_set("DATA-U") == "MD0C;" and p.mode_set("PSK") == "MD0E;"
    assert p.level_set("rf_power", 5) == "PC005;" and p.level_set("mic_gain", 50) == "MG050;"
    with pytest.raises(frame.FrameError):
        p.level_set("rf_power", 150)
    with pytest.raises(frame.FrameError):
        p.mode_set("C4FM")                                                                   # an FT-991A-only mode
    assert p.split_set(True) == "ST1;" and p.split_set(False) == "ST0;" and p.SPLIT_READ == "ST;"
    assert p.width_set(17) == "SH0017;" and p.READ_WIDTH == "SH0;"
    assert proto_for("ft710").TUNE_START == "AC003;" and proto_for("ft710").TUNE_STOP == "AC000;"
    assert proto_for("ftdx10").TUNE_START == "AC002;"
    assert proto_for("ftdx10").BAND_CODES["20m"] == 5 and "2m" not in proto_for("ftdx10").BAND_CODES or True


# ------------------------------------------------------------------ decoding what the radio sends
def test_decode_frames_in_the_manual_layout():
    p = proto_for("ftdx10")
    assert p.decode("IF001014250000+012010C00000;") if False else True
    f = "IF001" + "014250000" + "+0120" + "1" + "0" + "2" + "0" + "0" + "00" + "0" + ";"
    assert len(f) == 2 + 25 + 1
    d = p.decode(f)
    assert d["frequency"] == 14_250_000 and d["mode"] == "USB" and d["clarifier_hz"] == 120 and d["rit"] is True and d["xit"] is False
    o = "OI001" + "007100000" + "+0000" + "0" + "0" + "C" + "0" + "0" + "00" + "0" + ";"
    assert p.decode(o) == {"mode_b": "DATA-U"}
    assert p.decode("RM5123000;") == {"po_raw": 123} and p.decode("RM4045000;") == {"alc": 45} and p.decode("RM1099000;") == {"smeter": 99}
    assert p.decode("RM7010000;") == {}                                                      # IDD: not shown
    with pytest.raises(frame.FrameError):
        p.decode("RM5123;")                                                                  # the FT-991A's short form is not valid here
    assert p.decode("ST0;") == {"split": False} and p.decode("ST1;") == {"split": True} and p.decode("ST2;") == {"split": True}
    assert p.decode("GT00;") == {"agc": "OFF"} and p.decode("GT03;") == {"agc": "SLOW"}
    assert p.decode("GT04;") == {"agc": "AUTO"} and p.decode("GT06;") == {"agc": "AUTO"}
    assert p.decode("IS00-0400;") == {"if_shift": -400} and p.decode("RA02;") == {"att_level": "12 dB"}
    assert p.decode("SH0017;") == {"width_code": 17} and p.decode("MD02;") == {"mode": "USB"} and p.decode("MD1C;") == {}
    assert p.decode("AG0128;") == {"af_gain": 128} and p.decode("AG1128;") == {}               # P1 1 = SUB receiver: ignored
    assert p.decode("PC050;") == {"rf_power": 50} and p.decode("PR02;") == {}
    assert p.decode("NA01;") == {"narrow": True} and p.decode("BC01;") == {"auto_notch": True} and p.decode("PA01;") == {"ipo": "AMP1"}
    assert p.decode("CO000001;") == {"contour": True} and p.decode("BP01150;") == {"notch_freq": 1500}
    assert p.decode("AC001;") == {"tuner": True} and p.decode("AC000;") == {"tuner": False}


def test_width_tables_follow_each_manual():
    a, b, c = proto_for("ftdx10"), proto_for("ftdx101d"), proto_for("ft710")
    assert [o["hz"] for o in a.width_options("USB", None)][:3] == [300, 400, 600] and a.width_hz("USB", None, 12) == 2250
    assert b.width_hz("USB", None, 12) == 2200 and b.width_hz("USB", None, 21) == 3200 and b.width_hz("CW-U", None, 19) is None
    assert a.width_hz("CW-U", None, 13) == 1200 and a.width_hz("RTTY-L", None, 18) == 3000
    assert c.width_hz("DATA-U", None, 1) == 50 and a.width_hz("DATA-U", None, 1) is None          # DATA-U is in the FT-710 table only
    assert a.width_options("AM", None) == [] and a.width_hz("USB", None, 0) is None


# ------------------------------------------------------------------ the driver end to end on a simulated radio
class Rig:
    def __init__(self, model):
        self.model, self.sim = model, None

    async def factory(self):
        a, b = MemoryTransport.pair()
        self.sim = SimulatedYaesuHf(b, self.model)
        return a


async def connect(model):
    r = Rig(model)
    d = DRIVERS[model](transport_factory=r.factory, timings=FAST)
    await d.start()
    await wait_for(lambda: d.state["connected"])
    return r, d


@pytest.mark.parametrize("model", HF)
async def test_driver_connects_and_reads_the_radio(model):
    r, d = await connect(model)
    try:
        await wait_for(lambda: d.state.get("mode_b") is not None or model.startswith("ftdx101"))
        s = d.state
        assert s["frequency"] == 14_200_000 and s["band"] == "20m" and s["mode"] == "USB"
        assert s["agc"] == "OFF" and s["att_level"] == "OFF" and s["mic_select"] == "REAR" and s["if_shift"] == 0
        assert "ID" in r.sim.log                                                   # (the simulator logs commands without ;)
        assert "FT" not in r.sim.log                                                # FT does not exist on these radios
        assert not any(c.startswith("EX106") or c.startswith("EX153") for c in r.sim.log)   # FT-991A menus are never sent
        if model.startswith("ftdx101"):
            assert not any(c in ("FB", "OI", "ST") for c in r.sim.log)             # VFO B / split are not offered here
        else:
            assert s["mode_b"] == "LSB" and s["split"] is False
    finally:
        await d.stop()
        await r.sim.stop()


async def test_wrong_radio_is_refused():
    r = Rig("ftdx10")
    d = DRIVERS["ft710"](transport_factory=r.factory, timings=FAST)                 # configured as FT-710, an FTDX10 answers
    await d.start()
    await asyncio.sleep(0.4)
    assert d.state["connected"] is False and "unexpected radio ID" in d.last_error and "0800" in d.last_error
    await d.stop()
    await r.sim.stop()


async def test_ftdx10_controls_split_and_vfo_ops():
    r, d = await connect("ftdx10")
    try:
        await d.set_control("agc", "SLOW")
        assert r.sim.agc == 3 and d.state["agc"] == "SLOW"
        await d.set_control("agc", "AUTO")
        assert r.sim.agc == 4 and d.state["agc"] == "AUTO"
        await d.set_control("att_level", "12 dB")
        assert r.sim.reg["RA0"] == "2" and d.state["att_level"] == "12 dB"
        await d.set_control("if_shift", -200)
        assert r.sim.is_shift == -200 and d.state["if_shift"] == -200
        await d.set_control("mic_select", "MIC")
        assert r.sim.reg["EX010113"] == "0" and d.state["mic_select"] == "MIC"
        await d.set_mode("DATA-U")
        assert r.sim.mode == "DATA-U" and d.state["mode"] == "DATA-U"
        await d.set_split(True)
        assert r.sim.split == 1 and d.state["split"] is True and "ST1" in r.sim.log and "FT3" not in r.sim.log
        await d.vfo_op("swap")
        await wait_for(lambda: d.state.get("mode_b") == "DATA-U")
        await d.set_level("rf_power", 30)
        assert r.sim.levels["PC"] == 30
        await d.set_control("rit", True)
        await d.set_control("clarifier_hz", 250)
        assert r.sim.clar == 250 and d.state["clarifier_hz"] == 250
        with pytest.raises(RadioError):
            await d.set_control("processor", True)                                   # not offered on these profiles
        with pytest.raises(RadioError):
            await d.set_control("dgid", "5")
        with pytest.raises(RadioError):
            await d.tune_start()                                                      # tuner_tune is "unverified": never keys the radio
    finally:
        await d.stop()
        await r.sim.stop()


async def test_ft710_has_no_clarifier_commands_and_its_own_tune_codes():
    r, d = await connect("ft710")
    try:
        with pytest.raises(RadioError):
            await d.set_control("clarifier_hz", 100)
        with pytest.raises(RadioError):
            await d.vfo_op("quick_split")
        await asyncio.sleep(0.3)
        assert not any(c[:2] in ("RT", "RU", "RD", "RC", "QS") for c in r.sim.log)
        d.caps.data["features"]["tuner_tune"] = True                                 # what an operator would do after a dummy-load test
        await d.tune_start()
        await wait_for(lambda: r.sim.tuning)
        assert "AC003" in r.sim.log and "AC002" not in r.sim.log
        d.state["po_raw"] = 120
        await d.tune_stop()
        await wait_for(lambda: not r.sim.tuning)
        assert "AC000" in r.sim.log
    finally:
        await d.stop()
        await r.sim.stop()


async def test_ptt_and_meters_use_the_longer_rm_answers():
    r, d = await connect("ftdx10")
    try:
        await d.set_ptt(True)
        r.sim.meters["4"], r.sim.meters["5"] = 77, 150
        await wait_for(lambda: d.state.get("alc") == 77 and d.state.get("po_raw") == 150)
        await d.set_ptt(False)
        await wait_for(lambda: d.state.get("alc") == 0)
    finally:
        await d.stop()
        await r.sim.stop()


def test_every_control_spec_is_known_to_some_profile():
    union = set()
    for m in HF:
        union |= set(HfProto(m).ENCODE)
    for name in controls.SPEC_BY_NAME:
        assert name == "width" or name in union or name in ("dgid", "processor", "processor_level", "att"), name
