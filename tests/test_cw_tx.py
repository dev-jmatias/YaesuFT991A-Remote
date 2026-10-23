"""CW from the keyboard: the text is cut into pieces, each piece is written into a keyer memory and played through a TxGuard keyer session; the memory's old text is put back."""
import asyncio
import copy

import pytest

from radio_remote import config
from radio_remote.cw_engine import CwEngine
from radio_remote.radio.mock import MockDriver
from radio_remote.safety import TxGuard, TxRefused


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class FakeAudio:
    cw_feed = None
    _fd_tx = None

    async def hold_capture(self, on, who="ftx"):
        pass


async def rig(wpm=60, mode="CW-U", **kw):
    d = MockDriver()
    await d.start()
    d._update(mode=mode, cw_speed=wpm, break_in=True)
    guard = TxGuard(d, allow_ptt=True, max_tx_s=kw.pop("max_tx", 60), heartbeat_timeout_s=1.0)
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["cw"].update(kw)
    ev = []
    eng = CwEngine(lambda: cfg, FakeAudio(), ev.append, lambda: 7030000, guard, lambda cid: cid == "c1", d)
    return d, guard, eng, ev


async def until(cond, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.05)


def test_text_is_cleaned_and_cut_at_word_boundaries():
    assert CwEngine.clean("cq  cq de m0dht <k> ; k") == ("CQ CQ DE M0DHT K K", "<>;")
    assert CwEngine.clean("73 !") == ("73", "!")
    assert CwEngine.pieces("CQ CQ DE M0DHT K", 10) == ["CQ CQ DE", "M0DHT K"]
    assert CwEngine.pieces("ABCDEFGHIJKLMNOP QRS", 10) == ["ABCDEFGHIJ", "KLMNOP QRS"]
    assert CwEngine.pieces("", 24) == [] and CwEngine.pieces("A", 24) == ["A"]
    assert all(len(p) <= 24 for p in CwEngine.pieces("THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 1234567890", 24))


async def test_send_plays_each_piece_in_the_keyer_memory_and_restores_it():
    d, guard, eng, ev = await rig(chunk_chars=10)
    r = await eng.send("c1", "cq cq de m0dht k")
    assert r["pieces"] == 2 and r["text"] == "CQ CQ DE M0DHT K" and r["dropped"] == ""
    await until(lambda: eng._tx_task.done())
    assert d.cw_calls == [("set", 5, "CQ CQ DE"), ("play", 5, "CQ CQ DE"), ("set", 5, "M0DHT K"), ("play", 5, "M0DHT K"), ("set", 5, "MY OLD MESSAGE")]
    assert d.keyer_memories[5] == "MY OLD MESSAGE"                      # the radio's own message is back
    assert not guard.keyed and not guard.keyer and eng.sending is None and eng.queue == []
    sent = "".join(e["s"] for e in ev if e.get("k") == "text")
    assert "[TX] CQ CQ DE" in sent and "[TX] M0DHT K" in sent and "[TX] CQ CQ DE" in eng.state()["text"]
    assert ev[-1] == {"t": "cw", "k": "txdone", "error": ""}
    await d.stop()


async def test_the_keyer_slot_is_a_setting():
    d, guard, eng, ev = await rig(keyer_slot=2)
    await eng.send("c1", "E")
    await until(lambda: eng._tx_task.done())
    assert ("set", 2, "E") in d.cw_calls and ("play", 2, "E") in d.cw_calls
    await d.stop()


async def test_refused_when_the_radio_is_not_in_cw_or_nothing_valid_is_typed():
    d, guard, eng, ev = await rig(mode="USB")
    with pytest.raises(ValueError, match="CW-U or CW-L"):
        await eng.send("c1", "CQ")
    d._update(mode="CW-L")
    with pytest.raises(ValueError, match="nothing to send"):
        await eng.send("c1", "<>;!")
    eng.audio._fd_tx = object()
    with pytest.raises(ValueError, match="FreeDV"):
        await eng.send("c1", "CQ")
    assert d.cw_calls == []
    await d.stop()


async def test_a_piece_longer_than_the_time_limit_is_refused_before_anything_is_keyed():
    d, guard, eng, ev = await rig(wpm=5, max_tx=2)                      # 5 WPM: "CQ DE M0DHT" takes far more than 2 s
    await eng.send("c1", "CQ DE M0DHT")
    await until(lambda: eng._tx_task.done())
    assert "time limit" in eng.last_error and not guard.keyed
    assert [c for c in d.cw_calls if c[0] == "play"] == []
    assert ev[-1]["k"] == "txdone" and "time limit" in ev[-1]["error"]
    await d.stop()


async def test_halt_stops_the_keyer_drops_the_queue_and_restores_the_memory():
    d, guard, eng, ev = await rig(wpm=10, chunk_chars=10)
    await eng.send("c1", "CQ CQ CQ CQ CQ CQ CQ CQ")
    await until(lambda: guard.keyer)
    assert guard.keyed and guard.owner == "c1" and d.state["tx"] is True
    await eng.halt("test")
    await until(lambda: eng._tx_task.done())
    assert ("stop",) in d.cw_calls and not guard.keyed and d.state["tx"] is False
    assert eng.queue == [] and eng.last_error == "test"
    assert d.cw_calls[-1] == ("set", 5, "MY OLD MESSAGE") or d.cw_calls[-2:] == [("stop",), ("set", 5, "MY OLD MESSAGE")]
    assert len([c for c in d.cw_calls if c[0] == "play"]) == 1           # the rest of the text was never played
    await d.stop()


async def test_sending_stops_when_the_operator_leaves():
    d, guard, eng, ev = await rig(wpm=10)
    await eng.send("c2", "CQ CQ CQ CQ")                                 # "c2" is not a connected client
    await until(lambda: eng._tx_task.done())
    assert "operator left" in eng.last_error and not guard.keyed and ("stop",) in d.cw_calls
    await d.stop()


async def test_a_second_send_while_sending_joins_the_queue():
    d, guard, eng, ev = await rig(wpm=40, chunk_chars=10)
    await eng.send("c1", "CQ CQ DE")
    await until(lambda: eng.sending is not None)
    await eng.send("c1", "M0DHT")
    await until(lambda: eng._tx_task.done())
    played = [c[2] for c in d.cw_calls if c[0] == "play"]
    assert played == ["CQ CQ DE", "M0DHT"]
    await d.stop()


# ---------------------------------------------------------------- the guard's keyer session
async def guard_rig(allow=True, max_tx=60):
    d = MockDriver()
    await d.start()
    clk = Clock()
    return d, TxGuard(d, allow_ptt=allow, max_tx_s=max_tx, heartbeat_timeout_s=1.0, clock=clk), clk


async def test_guard_keyer_session_blocks_ptt_and_others_and_ends_cleanly():
    d, g, clk = await guard_rig()
    await g.start_keyer("a", 5)
    assert g.keyed and g.keyer and g.owner == "a"
    with pytest.raises(TxRefused, match="already|keyer"):
        await g.start_keyer("b", 5)
    with pytest.raises(TxRefused, match="keyer is sending"):
        await g.request("a", True)
    with pytest.raises(TxRefused):
        await g.start_tune("a")
    await g.end_keyer("b")                                               # not the owner: nothing happens
    assert g.keyed
    await g.end_keyer("a")
    assert not g.keyed and not g.keyer and d.cw_calls == []              # a normal end sends nothing to the radio
    await d.stop()


async def test_guard_keyer_stops_on_heartbeat_loss_and_on_timeout_and_ignores_the_radios_tx_flag():
    d, g, clk = await guard_rig()
    await g.start_keyer("a", 10)
    d._update(tx=False)                                                  # the radio keys itself between elements: its TX flag flickers
    clk.t += 0.5
    await g.heartbeat("a")
    await g.tick()
    assert g.keyed and g.keyer                                           # the ordinary "radio is no longer keyed" rule must not end it
    clk.t += 1.5                                                         # no heartbeat for more than a second
    await g.tick()
    assert not g.keyed and ("stop",) in d.cw_calls
    d.cw_calls.clear()
    await g.start_keyer("a", 2)
    for _ in range(11):
        clk.t += 0.6
        await g.heartbeat("a")
        await g.tick()
    assert not g.keyed and ("stop",) in d.cw_calls                       # the piece took far longer than announced
    await d.stop()


async def test_guard_keyer_needs_permission_and_respects_the_time_limit():
    d, g, _ = await guard_rig(allow=False)
    d.caps.data["model"]["is_mock"] = False
    with pytest.raises(TxRefused, match="disabled"):
        await g.start_keyer("a", 5)
    d2, g2, _ = await guard_rig(max_tx=3)
    with pytest.raises(TxRefused, match="time limit"):
        await g2.start_keyer("a", 10)
    await d.stop()
    await d2.stop()


async def test_a_profile_without_the_keyer_feature_refuses():
    d = MockDriver()
    await d.start()
    d._update(mode="CW-U")
    d.caps.data["features"]["cw_text"] = False
    eng = CwEngine(lambda: copy.deepcopy(config.DEFAULTS), FakeAudio(), lambda e: None, lambda: 7030000, TxGuard(d, allow_ptt=True, max_tx_s=60), lambda c: True, d)
    assert eng.state()["can_send"] is False
    with pytest.raises(ValueError, match="cannot send CW"):
        await eng.send("c1", "CQ")
    await d.stop()


# ---------------------------------------------------------------- the real FT-991A driver against the simulated radio
import pytest_asyncio  # noqa: E402,F401
from test_cat import FAST, Rig, wait_for  # noqa: E402
from radio_remote.radio.base import RadioError  # noqa: E402
from radio_remote.radio.drivers.ft991a import FT991ADriver  # noqa: E402


@pytest.fixture
async def real():
    r = Rig()
    d = FT991ADriver(transport_factory=r.factory, timings=FAST)
    r.driver = d
    await d.start()
    await wait_for(lambda: d.state["connected"])
    yield r
    await d.stop()
    for s in r.sims:
        await s.stop()


async def test_driver_writes_reads_and_plays_keyer_memories(real):
    d, sim = real.driver, real.sim
    assert d.caps.has("cw_text")
    assert await d.cw_memory_get(5) == "STORED MSG"
    await d.cw_memory_set(5, "CQ DE M0DHT K")
    assert sim.keyer[5] == "CQ DE M0DHT K" and "KM5CQ DE M0DHT K" in sim.log
    assert await d.cw_memory_get(5) == "CQ DE M0DHT K"
    await d.cw_play(5)
    await wait_for(lambda: sim.ky_plays == [5])
    for bad in (0, 6):
        with pytest.raises(RadioError):
            await d.cw_play(bad)
        with pytest.raises(RadioError):
            await d.cw_memory_set(bad, "E")
    for bad in ("", "X" * 51, "A;B"):
        with pytest.raises(RadioError):
            await d.cw_memory_set(5, bad)
    assert sim.ky_plays == [5] and sim.keyer[5] == "CQ DE M0DHT K"        # the bad ones never reached the radio


async def test_engine_over_the_real_driver_sends_and_restores(real):
    d, sim = real.driver, real.sim
    await d.set_mode("CW-U")
    await wait_for(lambda: d.state.get("mode") == "CW-U")
    d._update(cw_speed=60)
    guard = TxGuard(d, allow_ptt=True, max_tx_s=60, heartbeat_timeout_s=1.0)
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["cw"]["chunk_chars"] = 10
    eng = CwEngine(lambda: cfg, FakeAudio(), lambda e: None, lambda: 7030000, guard, lambda cid: True, d)
    await eng.send("c1", "cq cq de m0dht k")
    await until(lambda: eng._tx_task.done(), timeout=15)
    assert sim.ky_plays == [5, 5] and sim.keyer[5] == "STORED MSG"          # played twice (two pieces), the radio's own text is back
    assert not guard.keyed and eng.last_error == ""


# ---------------------------------------------------------------- every Yaesu profile gets the same keyer commands (the HF simulators answer KM / KY / KS / BI too)
import pytest as _pytest  # noqa: E402
from test_hf_radios import connect as hf_connect  # noqa: E402


@_pytest.mark.parametrize("model", ["ftdx10", "ftdx101d", "ftdx101mp", "ft710"])
async def test_keyer_commands_work_for_every_radio_profile(model):
    r, d = await hf_connect(model)
    try:
        assert d.caps.has("cw_text")
        k = await d.cw_keyer_get()
        assert k["speed"] == 25 and k["break_in"] is False                  # the HF simulators start at 25 WPM, break-in off
        await d.cw_keyer_set(speed=18, break_in=True)
        assert (await d.cw_keyer_get()) == {"speed": 18, "break_in": True}
        assert await d.cw_memory_get(5) == "STORED MSG"
        await d.cw_memory_set(5, "CQ TEST")
        await d.cw_play(5)
        await wait_for(lambda: r.sim.ky_plays == [5])
        assert r.sim.keyer[5] == "CQ TEST" and "KM5CQ TEST" in r.sim.log
        with _pytest.raises(RadioError):
            await d.cw_keyer_set(speed=3)
    finally:
        await d.stop()
        await r.sim.stop()
