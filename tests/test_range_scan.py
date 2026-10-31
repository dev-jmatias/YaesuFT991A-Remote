import asyncio
import types

import pytest

from radio_remote.scan import RangeScanner, ScanError


class FakeDriver:
    def __init__(self, signals=None):
        self.state = {"connected": True, "smeter": 0}
        self.caps = types.SimpleNamespace(frequency_ok=lambda hz: 100_000 <= hz <= 470_000_000, has=lambda n: False)
        self.freqs, self.signals = [], signals or {}

    async def set_frequency(self, hz):
        self.freqs.append(hz)
        self.state["frequency"] = hz

    async def read_signal(self, settle_s):
        await asyncio.sleep(0)
        self.state["smeter"] = self.signals.get(self.state["frequency"], 0)
        return self.state["smeter"]


class FakeHub:
    def __init__(self, driver):
        self.driver = driver
        self.guard = types.SimpleNamespace(keyed=False)
        self.lease = types.SimpleNamespace(holds=lambda c: c == "c1")
        self.pushed = []

    def push(self, fields):
        self.pushed.append(fields)


async def until(pred, t=2.0):
    end = asyncio.get_running_loop().time() + t
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


async def test_steps_between_the_two_frequencies_and_loops():
    d = FakeDriver(); h = FakeHub(d); s = RangeScanner(h)
    await s.start("c1", 145_000_000, 145_000_300, 100, 100, 0, False)
    await until(lambda: len(d.freqs) >= 9)
    assert d.freqs[:9] == [145_000_000, 145_000_100, 145_000_200, 145_000_300] * 2 + [145_000_000]      # up to the stop frequency, then back to the start
    assert h.pushed[0]["range_scan"]["start"] == 145_000_000
    await s.stop()
    assert h.pushed[-1]["range_scan"] is None and not s.running


async def test_waits_on_a_signal_then_carries_on_after_the_quiet_time(monkeypatch):
    monkeypatch.setattr("radio_remote.scan.HOLD_POLL_S", 0.01)
    d = FakeDriver({145_000_100: 200}); h = FakeHub(d); s = RangeScanner(h)
    await s.start("c1", 145_000_000, 145_000_300, 100, 100, 0.05, False)
    await until(lambda: d.freqs and d.freqs[-1] == 145_000_100 and s.running and h.pushed[-1]["range_scan"]["holding"])
    n = len(d.freqs)
    await asyncio.sleep(0.05)
    assert len(d.freqs) == n                                             # parked on the signal
    d.signals.clear(); d.state["smeter"] = 0                             # the signal ends
    await until(lambda: len(d.freqs) > n)                                # and the scan moves on
    await s.stop()


async def test_stop_on_signal_ends_the_scan_on_that_frequency():
    d = FakeDriver({145_000_200: 180}); h = FakeHub(d); s = RangeScanner(h)
    await s.start("c1", 145_000_000, 145_000_300, 100, 100, 1, True)
    await until(lambda: not s.running)
    assert d.freqs[-1] == 145_000_200 and h.pushed[-1]["range_scan"] is None


async def test_ends_when_transmitting_or_control_is_lost():
    d = FakeDriver(); h = FakeHub(d); s = RangeScanner(h)
    await s.start("c1", 145_000_000, 145_001_000, 100, 100, 0, False)
    h.guard.keyed = True
    await until(lambda: not s.running)
    h.guard.keyed = False
    await s.start("c1", 145_000_000, 145_001_000, 100, 100, 0, False)
    h.lease.holds = lambda c: False                                      # somebody took control
    await until(lambda: not s.running)


async def test_refuses_bad_ranges_and_memory_mode():
    d = FakeDriver(); h = FakeHub(d); s = RangeScanner(h)
    for args in [(145_100_000, 145_000_000, 100), (145_000_000, 145_000_100, 1), (145_000_000, 600_000_000, 100), (145_000_000, 146_000_000, 10)]:
        with pytest.raises(ScanError):
            await s.start("c1", *args, 100, 0, False)
    d.state["vfo_memory"] = "memory"
    with pytest.raises(ScanError, match="memory"):
        await s.start("c1", 145_000_000, 145_000_300, 100, 100, 0, False)
    d.state["vfo_memory"] = "vfo"; h.guard.keyed = True
    with pytest.raises(ScanError, match="transmitting"):
        await s.start("c1", 145_000_000, 145_000_300, 100, 100, 0, False)
    assert not s.running


async def test_a_radio_error_ends_the_scan_without_crashing():
    d = FakeDriver(); h = FakeHub(d); s = RangeScanner(h)

    async def boom(hz):
        raise RuntimeError("radio offline")
    d.set_frequency = boom
    await s.start("c1", 145_000_000, 145_000_300, 100, 100, 0, False)
    await until(lambda: not s.running)
    assert h.pushed[-1]["range_scan"] is None


class ListDriver(FakeDriver):
    def __init__(self, signals=None, empty=()):
        super().__init__(signals)
        self.chans, self.empty = [], set(empty)

    async def memory_select(self, ch):
        from radio_remote.radio.base import RadioError
        if ch in self.empty:
            raise RadioError("that memory channel is empty")
        self.chans.append(ch)
        self.state["frequency"] = ch


async def test_list_scan_visits_only_the_chosen_channels_and_loops():
    d = ListDriver(); h = FakeHub(d); s = RangeScanner(h)
    await s.start_list("c1", [3, 7, 20], 100, 0, False)
    await until(lambda: len(d.chans) >= 7)
    assert d.chans[:7] == [3, 7, 20, 3, 7, 20, 3]
    assert h.pushed[0]["range_scan"]["channels"] == [3, 7, 20]
    await s.stop()


async def test_list_scan_parks_on_a_signal_and_can_stop_there():
    d = ListDriver({7: 200}); h = FakeHub(d); s = RangeScanner(h)
    await s.start_list("c1", [3, 7, 20], 100, 1, True)
    await until(lambda: not s.running)
    assert d.chans[-1] == 7 and h.pushed[-1]["range_scan"] is None


async def test_list_scan_skips_a_channel_emptied_in_the_radio_and_ends_if_none_work():
    d = ListDriver(empty={7}); h = FakeHub(d); s = RangeScanner(h)
    await s.start_list("c1", [3, 7, 20], 100, 0, False)
    await until(lambda: len(d.chans) >= 4)
    assert 7 not in d.chans
    await s.stop()
    d2 = ListDriver(empty={3, 7}); h2 = FakeHub(d2); s2 = RangeScanner(h2)
    await s2.start_list("c1", [3, 7], 100, 0, False)
    await until(lambda: not s2.running)


async def test_list_scan_refuses_a_bad_list():
    d = ListDriver(); h = FakeHub(d); s = RangeScanner(h)
    for chans in ([5], [5, 5], [0, 5], [5, 100], ["a", 5]):
        with pytest.raises(ScanError):
            await s.start_list("c1", chans, 100, 0, False)
    assert not s.running


def test_a_capability_file_can_rename_controls_to_what_the_radio_calls_them():
    from radio_remote.radio import controls
    feats = {"auto_notch": True, "manual_notch": True, "rit": True, "xit": True}
    plain = {c["name"]: c["label"] for c in controls.available(feats)}
    named = {c["name"]: c["label"] for c in controls.available(feats, {"auto_notch": "DNF", "rit": "CLAR RX"})}
    assert plain["auto_notch"] == "Auto notch" and named["auto_notch"] == "DNF" and named["rit"] == "CLAR RX" and named["xit"] == "XIT"
