import asyncio

import pytest

from radio_remote.radio.cat import frame
from radio_remote.radio.cat.client import CatClient, CatRejected, CatTimeout
from radio_remote.radio.cat.sim import SimulatedFT991A
from radio_remote.radio.cat.transport import MemoryTransport, SerialTransport
from radio_remote.radio.drivers.ft991a import FT991ADriver, Timings
from radio_remote.radio.base import RadioError
from radio_remote.safety import TxGuard


# ------------------------------------------------------------------ frames
def test_split_frames_handles_partial_and_garbage():
    buf = bytearray(b"FA0142")
    assert frame.split_frames(buf) == []
    buf += b"50000;MD02;\r\n?;ID0"
    assert frame.split_frames(buf) == ["FA014250000;", "MD02;", "?;"]
    assert buf == bytearray(b"ID0")


def test_freq_builder_and_limits():
    assert frame.freq_set(14_250_000) == "FA014250000;"
    assert frame.freq_set(144_174_000, "B") == "FB144174000;"
    with pytest.raises(frame.FrameError):
        frame.freq_set(29_999)
    with pytest.raises(frame.FrameError):
        frame.freq_set(470_000_001)


def test_mode_roundtrip_all_modes():
    for name, code in frame.MODE_CODES.items():
        assert frame.mode_set(name) == f"MD0{code};"
        assert frame.decode(f"MD0{code};") == {"mode": name}
    assert frame.MODES["E"] == "C4FM"
    with pytest.raises(frame.FrameError):
        frame.mode_set("FT8")


def test_level_builders():
    assert frame.level_set("af_gain", 7) == "AG0007;"
    assert frame.level_set("mic_gain", 100) == "MG100;"
    assert frame.level_set("rf_power", 5) == "PC005;"
    for bad in [("af_gain", 256), ("mic_gain", 101), ("rf_power", 4), ("rf_power", 101), ("rf_gain", -1)]:
        with pytest.raises(frame.FrameError):
            frame.level_set(*bad)


def test_decode_basic_answers():
    assert frame.decode("FA014250000;") == {"frequency": 14_250_000}
    assert frame.decode("FB007100000;") == {"frequency_b": 7_100_000}
    assert frame.decode("AG0128;") == {"af_gain": 128}
    assert frame.decode("RG0255;") == {"rf_gain": 255}
    assert frame.decode("MG050;") == {"mic_gain": 50}
    assert frame.decode("PC100;") == {"rf_power": 100}
    assert frame.decode("SM0123;") == {"smeter": 123}
    assert frame.decode("RM5200;") == {"po_raw": 200}
    assert frame.decode("RM6010;") == {"swr_raw": 10}
    assert frame.decode("TX0;") == {"tx": False, "tx_source": None}
    assert frame.decode("TX1;") == {"tx": True, "tx_source": "cat"}
    assert frame.decode("TX2;") == {"tx": True, "tx_source": "radio"}
    assert frame.decode("ID0670;") == {}
    assert frame.decode("ZZ123;") == {}


def test_decode_if_frame():
    #            P1    P2          P3      P4 P5 P6 P7 P8 P9   P10
    f = "IF" + "001" + "014250000" + "-0123" + "1" + "0" + "2" + "0" + "0" + "00" + "0" + ";"
    d = frame.decode(f)
    assert d["frequency"] == 14_250_000 and d["mode"] == "USB"
    assert d["rit"] is True and d["xit"] is False and d["clarifier_hz"] == -123
    assert d["vfo_memory"] == "vfo"


@pytest.mark.parametrize("bad", ["FA1234;", "MD0Z;", "TX9;", "IF00114250000;", "SM0abc;", "garbage", "AG0xyz1;"])
def test_decode_rejects_malformed(bad):
    with pytest.raises(frame.FrameError):
        frame.decode(bad)


# ------------------------------------------------------- real serial safety
async def test_serial_transport_refuses_under_tests():
    with pytest.raises(RuntimeError):
        await SerialTransport("COM99", 38400).open()


# ------------------------------------------------------------------ client
async def _client_with_sim():
    a, b = MemoryTransport.pair()
    sim = SimulatedFT991A(b)
    seen = []
    c = CatClient(a, seen.append, timeout=0.2)
    await c.start()
    return c, sim, seen


async def test_client_request_answer_and_unsolicited():
    c, sim, seen = await _client_with_sim()
    assert await c.request("ID;") == "ID0670;"
    await c.send("AI1;")
    await sim.front_panel_freq(7_074_000)
    await asyncio.sleep(0.05)
    assert "FA007074000;" in seen
    await c.close()
    await sim.stop()


async def test_client_rejected_and_timeout():
    c, sim, _ = await _client_with_sim()
    with pytest.raises(CatRejected):
        await c.request("XX;")
    sim.drop = 1
    with pytest.raises(CatTimeout):
        await c.request("ID;")
    assert await c.request("ID;") == "ID0670;"       # recovers
    assert c.stats["timeouts"] == 1
    await c.close()
    await sim.stop()


async def test_client_priority_order():
    c, sim, _ = await _client_with_sim()
    jobs = [asyncio.create_task(c.request("SM0;", prio=3)), asyncio.create_task(c.request("ID;", prio=3))]
    await asyncio.sleep(0)               # let both enqueue; worker holds the first
    urgent = asyncio.create_task(c.request("TX;", prio=0))
    await asyncio.gather(*jobs, urgent)
    order = [x for x in sim.log if x in ("SM0", "ID", "TX")]
    assert order.index("TX") < order.index("ID")
    await c.close()
    await sim.stop()


# ------------------------------------------------------------------ driver
FAST = Timings(meter_s=0.02, backstop_s=0.1, slow_s=0.05, timeout_s=0.15, max_timeouts=3,
               backoff_min_s=0.05, backoff_max_s=0.1, healthy_after_s=0.2, settle_s=0.0)


class Rig:
    """Driver wired to simulated radios; each (re)connect builds a fresh link."""

    def __init__(self):
        self.sims: list[SimulatedFT991A] = []
        self.shared: dict | None = None      # state carried across reconnects (it is the same radio)
        self.fail_open = 0

    async def factory(self):
        if self.fail_open:
            self.fail_open -= 1
            raise OSError("port missing")
        a, b = MemoryTransport.pair()
        sim = SimulatedFT991A(b)
        if self.sims:                        # same physical radio after a USB blip
            old = self.sims[-1]
            sim.freq, sim.mode, sim.tx, sim.levels = old.freq, old.mode, old.tx, old.levels
            await old.stop()
        self.sims.append(sim)
        return a

    @property
    def sim(self):
        return self.sims[-1]


async def wait_for(pred, timeout=3.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


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


async def test_initial_sync(rig):
    s = rig.driver.state
    assert s["frequency"] == 14_200_000 and s["band"] == "20m" and s["mode"] == "USB"
    assert s["af_gain"] == 80 and s["rf_gain"] == 255 and s["mic_gain"] == 50 and s["rf_power"] == 50
    assert s["tx"] is False
    assert rig.sim.ai == 1                      # AI1; enabled


async def test_set_frequency_reads_back(rig):
    await rig.driver.set_frequency(7_074_000)
    assert rig.sim.freq == 7_074_000
    assert rig.driver.state["frequency"] == 7_074_000 and rig.driver.state["band"] == "40m"


async def test_set_mode_and_levels(rig):
    await rig.driver.set_mode("C4FM")
    assert rig.driver.state["mode"] == "C4FM" and rig.sim.mode == "C4FM"
    await rig.driver.set_level("af_gain", 200)
    assert rig.sim.levels["AG0"] == 200 and rig.driver.state["af_gain"] == 200
    with pytest.raises(RadioError):
        await rig.driver.set_level("rf_power", 3)
    await rig.driver.set_frequency(145_500_000)
    with pytest.raises(RadioError):
        await rig.driver.set_level("rf_power", 80)       # VHF cap 50 W


async def test_front_panel_change_is_pushed(rig):
    await rig.sim.front_panel_freq(21_074_000)
    await wait_for(lambda: rig.driver.state["frequency"] == 21_074_000)
    assert rig.driver.state["band"] == "15m"


async def test_ai_lost_after_radio_reboot_is_caught_by_backstop(rig):
    rig.sim.power_cycle()                        # AI back to 0, link stays up
    rig.sim.freq = 3_573_000                     # changed on the radio; no push will come
    await wait_for(lambda: rig.driver.state["frequency"] == 3_573_000)


async def test_meters_rx_and_tx(rig):
    rig.sim.meters["1"] = 99
    await wait_for(lambda: rig.driver.state.get("smeter") == 99)
    await rig.driver.set_ptt(True)
    rig.sim.meters["5"], rig.sim.meters["6"] = 180, 12
    await wait_for(lambda: rig.driver.state.get("po_raw") == 180 and rig.driver.state.get("swr_raw") == 12)
    await rig.driver.set_ptt(False)
    await wait_for(lambda: rig.driver.state.get("po_raw") == 0)


async def test_ptt_sets_and_releases(rig):
    await rig.driver.set_ptt(True)
    assert rig.sim.tx == 1 and rig.driver.state["tx"] and rig.driver.state["tx_source"] == "cat"
    await rig.driver.set_ptt(False)
    assert rig.sim.tx == 0 and not rig.driver.state["tx"]


async def test_radio_keyed_locally_is_reported(rig):
    await rig.sim.front_panel_ptt(True)
    await wait_for(lambda: rig.driver.state["tx"] and rig.driver.state["tx_source"] == "radio")


async def test_reconnect_after_usb_disconnect(rig):
    first = rig.sim
    await first.t.close()                        # cable pulled
    await wait_for(lambda: not rig.driver.state["connected"])
    rig.sim.freq = 10_136_000
    await wait_for(lambda: rig.driver.state["connected"] and rig.sim is not first)
    await wait_for(lambda: rig.driver.state["frequency"] == 10_136_000)   # radio wins; state re-synced
    assert rig.sim.ai == 1                       # AI1; re-sent


async def test_reconnect_after_open_failures(rig):
    rig.fail_open = 2
    await rig.sim.t.close()
    await wait_for(lambda: rig.fail_open == 0 and rig.driver.state["connected"], timeout=5)


async def test_radio_stops_answering_triggers_reconnect(rig):
    first = rig.sim
    first.drop = 10_000                          # radio hangs: link up, no answers
    await wait_for(lambda: not rig.driver.state["connected"])
    await wait_for(lambda: rig.driver.state["connected"] and rig.sim is not first)


async def test_cat_tx_not_inherited_after_reconnect(rig):
    await rig.driver.set_ptt(True)
    assert rig.sim.tx == 1
    first = rig.sim
    await first.t.close()
    await wait_for(lambda: rig.sim is not first and rig.driver.state["connected"])
    await wait_for(lambda: rig.sim.tx == 0)      # released on reconnect
    assert rig.driver.state["tx"] is False


async def test_set_fails_cleanly_when_disconnected(rig):
    rig.fail_open = 1000
    await rig.sim.t.close()
    await wait_for(lambda: not rig.driver.state["connected"])
    with pytest.raises(RadioError):
        await rig.driver.set_frequency(7_000_000)


async def test_wrong_radio_id_never_connects():
    async def factory():
        a, b = MemoryTransport.pair()
        sim = SimulatedFT991A(b)
        sim_id = sim._handle

        async def wrong(c):
            if c == "ID":
                return await sim._send("ID0681;")
            return await sim_id(c)
        sim._handle = wrong
        return a

    d = FT991ADriver(transport_factory=factory, timings=FAST)
    await d.start()
    await asyncio.sleep(0.4)
    assert d.state["connected"] is False and "unexpected radio ID" in d.last_error
    await d.stop()


async def test_guard_with_cat_driver_unkeys_on_heartbeat_loss(rig):
    clock = [0.0]
    g = TxGuard(rig.driver, allow_ptt=True, max_tx_s=60, heartbeat_timeout_s=1.0, clock=lambda: clock[0])
    await g.request("a", True)
    assert rig.sim.tx == 1
    clock[0] += 2
    await g.tick()
    assert rig.sim.tx == 0
