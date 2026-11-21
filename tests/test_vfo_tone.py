"""VFO-A's tone mode (CT) shown and set as a control, and a memory write that cannot put VFO-A's tone back reported as an error (issue #2: a CTCSS tone squelch left on the VFO)."""
import pytest
from test_cat import rig  # noqa: F401  (driver + simulated FT-991A)

from radio_remote.radio import controls
from radio_remote.radio.base import RadioError
from radio_remote.radio.cat import ft991a_controls as fc
from radio_remote.radio.cat.proto import HfProto


@pytest.mark.parametrize("value,cmd", [("OFF", "CT00;"), ("CTCSS ENC/DEC", "CT01;"), ("CTCSS ENC", "CT02;"), ("DCS ENC/DEC", "CT03;"), ("DCS ENC", "CT04;")])
def test_tone_mode_encodes_and_decodes_like_the_manual(value, cmd):
    assert fc.ENCODE["tone_mode"] == ("CT0;", fc.ENCODE["tone_mode"][1]) and fc.ENCODE["tone_mode"][1](value) == [cmd]
    assert fc.PARSERS["CT"](cmd[2:-1]) == {"tone_mode": value}


def test_tone_mode_is_offered_only_where_the_profile_says_so():
    assert controls.SPEC_BY_NAME["tone_mode"]["choices"] == list(fc.TONE_NAMES)
    assert not any(s["name"] == "tone_mode" for s in controls.available({"vfo_tone": False}))
    assert HfProto("ftdx10").decode("CT01;") == {}                                     # the HF radios do not interpret it


async def test_the_vfo_tone_is_read_and_can_be_switched_off(rig):
    d, sim = rig.driver, rig.sim
    sim.vfo_tone["CT"] = "1"                                                           # CTCSS ENC/DEC left on VFO-A (set on the radio, or carried over)
    await d.client.request("CT0;")
    assert d.state["tone_mode"] == "CTCSS ENC/DEC"
    await d.set_control("tone_mode", "OFF")
    assert sim.vfo_tone["CT"] == "0" and d.state["tone_mode"] == "OFF"


async def test_a_memory_write_that_leaves_the_tone_on_vfo_a_says_so(rig):
    d, sim = rig.driver, rig.sim
    await d.memory_channels()
    sim.vfo_tone.update(CT="0", OS="0", CN0="008", CN1="000")
    handle = sim._handle

    async def drop_tone_off(c):                                                       # the radio misses the command that puts VFO-A's tone mode back to off
        if c == "CT00":
            return
        await handle(c)
    sim._handle = drop_tone_off
    with pytest.raises(RadioError, match="channel 11 was stored, but VFO-A's tone mode was not put back"):
        await d.memory_write(11, 145_600_000, "FM", "ctcss_encdec", "minus", "A", tone_hz=71.9)
    assert sim.mem_tone[11]["CT"] == "1"                                               # the channel itself was written
    assert sim.mem_ch == 0                                                             # and the radio is back in VFO mode
