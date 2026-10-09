import pytest
from test_cat import FAST, Rig, wait_for
from test_tune_dgid import rig  # noqa: F401  (the FT-991A driver on the simulator)

from radio_remote.radio.cat import ft991a_controls as fc
from radio_remote.radio.cat import frame
from radio_remote.safety import TxGuard


def test_scan_command_and_answer():
    assert fc.ENCODE["scan"][0] == "SC;"
    assert fc.ENCODE["scan"][1](True) == ["SC1;"] and fc.ENCODE["scan"][1](False) == ["SC0;"]
    assert fc.PARSERS["SC"]("0") == {"scan": False}
    assert fc.PARSERS["SC"]("1") == {"scan": True} and fc.PARSERS["SC"]("2") == {"scan": True}
    with pytest.raises(frame.FrameError):
        fc.PARSERS["SC"]("7")


async def test_scan_starts_and_stops_from_the_driver(rig):  # noqa: F811
    d, sim = rig.driver, rig.sim
    assert d.caps.has("scan")
    await d.set_control("scan", True)
    assert sim.reg["SC"] == "1" and d.state["scan"] is True
    await d.set_control("scan", False)
    assert sim.reg["SC"] == "0" and d.state["scan"] is False
    sim.reg["SC"] = "1"                                          # started on the radio itself: the page follows
    await wait_for(lambda: d.state.get("scan") is True, timeout=5)


async def test_a_key_or_unkey_in_progress_is_not_reported_as_the_radio_unkeying_itself(rig, caplog):  # noqa: F811
    """The PTT watchdog saw TX0 take effect before the guard had finished un-keying and logged 'radio is no longer keyed' on a quarter of all releases."""
    d = rig.driver
    g = TxGuard(d, allow_ptt=True, max_tx_s=60, heartbeat_timeout_s=1.0)
    await g.request("a", True)
    await wait_for(lambda: d.state["tx"])
    async with g._lock:
        d.state["tx"] = False                                    # the radio already answers TX0 while the guard holds its lock
        await g.tick()
    assert "no longer keyed" not in caplog.text and g.keyed
    await g.request("a", False)
