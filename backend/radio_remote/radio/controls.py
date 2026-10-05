"""Radio-independent description of adjustable controls (metadata + validation).

A control is only offered to the UI when the radio's capability file has its
`feature` set to exactly `true`. CAT encodings live next to each driver.
"""
from __future__ import annotations

from typing import Any

# kind: bool | int | enum | width (hz chosen from radio-supplied options)
SPECS: list[dict[str, Any]] = [
    # --- Filter
    {"name": "width", "label": "Width", "group": "Filter", "kind": "width", "feature": "width", "unit": "Hz"},
    {"name": "narrow", "label": "Narrow filter", "group": "Filter", "kind": "bool", "feature": "width"},
    {"name": "if_shift", "label": "IF Shift", "group": "Filter", "kind": "int", "feature": "if_shift",
     "min": -1200, "max": 1200, "step": 20, "unit": "Hz"},
    {"name": "contour", "label": "Contour", "group": "Filter", "kind": "bool", "feature": "contour"},
    {"name": "contour_freq", "label": "Contour freq", "group": "Filter", "kind": "int", "feature": "contour",
     "min": 10, "max": 3200, "step": 10, "unit": "Hz", "default": 1500},
    {"name": "apf", "label": "APF", "group": "Filter", "kind": "bool", "feature": "apf"},
    {"name": "apf_freq", "label": "APF offset", "group": "Filter", "kind": "int", "feature": "apf",
     "min": -250, "max": 250, "step": 10, "unit": "Hz"},
    {"name": "notch", "label": "Manual notch", "group": "Noise", "kind": "bool", "feature": "manual_notch"},
    {"name": "notch_freq", "label": "Notch freq", "group": "Noise", "kind": "int", "feature": "manual_notch",
     "min": 10, "max": 3200, "step": 10, "unit": "Hz", "default": 1500},
    {"name": "auto_notch", "label": "Auto notch", "group": "Noise", "kind": "bool", "feature": "auto_notch"},
    # --- Noise
    {"name": "nr", "label": "DNR", "group": "Noise", "kind": "bool", "feature": "dnr"},
    {"name": "nr_level", "label": "DNR level", "group": "Noise", "kind": "int", "feature": "dnr",
     "min": 1, "max": 15, "step": 1},
    {"name": "nb", "label": "Noise blanker", "group": "Noise", "kind": "bool", "feature": "noise_blanker"},
    {"name": "nb_level", "label": "NB level", "group": "Noise", "kind": "int", "feature": "noise_blanker",
     "min": 0, "max": 10, "step": 1},
    # --- Receiver front end
    {"name": "ipo", "label": "IPO / Preamp", "group": "Receiver", "kind": "enum", "feature": "ipo",
     "choices": ["IPO", "AMP1", "AMP2"]},
    {"name": "att", "label": "ATT", "group": "Receiver", "kind": "bool", "feature": "att"},
    {"name": "att_level", "label": "ATT", "group": "Receiver", "kind": "enum", "feature": "att_levels",
     "choices": ["OFF", "6 dB", "12 dB", "18 dB"]},                                  # newer HF radios: RA P2 0..3
    {"name": "agc", "label": "AGC", "group": "Receiver", "kind": "enum", "feature": "agc",
     "choices": ["OFF", "FAST", "MID", "SLOW", "AUTO"]},                            # GT: set 0..4; the radio answers AUTO-FAST/MID/SLOW as AUTO
    {"name": "rit", "label": "RIT", "group": "Receiver", "kind": "bool", "feature": "rit"},
    {"name": "xit", "label": "XIT", "group": "Receiver", "kind": "bool", "feature": "xit"},
    {"name": "clarifier_hz", "label": "Clarifier offset", "group": "Receiver", "kind": "int", "feature": "rit",
     "min": -9990, "max": 9990, "step": 10, "unit": "Hz"},
    # --- Transmit / audio
    {"name": "processor", "label": "Processor", "group": "Transmit", "kind": "bool", "feature": "processor"},
    {"name": "processor_level", "label": "Processor level", "group": "Transmit", "kind": "int",
     "feature": "processor", "min": 0, "max": 100, "step": 1},
    # FTDX101: the rear / USB audio menus (CAT manual p.10): "<mode> OUT LEVEL" (receive audio sent to the Pi) and "RPORT GAIN" (transmit audio coming in from the Pi), 0..100,
    # written for SSB, AM and FM together so the level does not change with the mode.
    {"name": "audio_out_level", "label": "Audio out level", "group": "Audio", "kind": "int", "feature": "audio_out_level", "min": 0, "max": 100, "step": 1},
    # FTDX101: REAR SELECT (CAT manual p.10) = where the transmit audio comes in from the rear: 0 DATA socket, 1 USB. Remote audio needs USB. SSB 01-01-12, AM 01-02-13, FM 01-03-12, DATA 01-04-14.
    {"name": "rear_select", "label": "Transmit audio source", "group": "Audio", "kind": "enum", "feature": "rear_select", "choices": ["DATA", "USB"]},
    {"name": "audio_in_level", "label": "Audio in level", "group": "Audio", "kind": "int", "feature": "audio_in_level", "min": 0, "max": 100, "step": 1},
    # FTDX101: AO "AMC OUTPUT LEVEL" 001..100 (the level the radio's PROC/PITCH knob shows as "AMC out" on its screen)
    {"name": "amc_level", "label": "AMC output level", "group": "Transmit", "kind": "int", "feature": "amc_level", "min": 1, "max": 100, "step": 1},
    # --- CW (none of these transmit): KS key speed 4..60 WPM, KP pitch 300..1050 Hz in 10 Hz steps, KR electronic keyer on/off, BI break-in on/off
    {"name": "cw_speed", "label": "Keyer speed", "group": "CW", "kind": "int", "feature": "cw_keyer", "min": 4, "max": 60, "step": 1, "unit": "WPM"},
    {"name": "cw_pitch", "label": "CW pitch", "group": "CW", "kind": "int", "feature": "cw_keyer", "min": 300, "max": 1050, "step": 10, "unit": "Hz", "default": 700},
    {"name": "keyer", "label": "Keyer", "group": "CW", "kind": "bool", "feature": "cw_keyer"},
    {"name": "break_in", "label": "Break-in", "group": "CW", "kind": "bool", "feature": "cw_keyer"},
    {"name": "monitor", "label": "Monitor", "group": "Transmit", "kind": "bool", "feature": "monitor"},
    {"name": "monitor_level", "label": "Monitor level", "group": "Transmit", "kind": "int", "feature": "monitor",
     "min": 0, "max": 100, "step": 1},
    {"name": "tuner", "label": "Antenna tuner", "group": "Transmit", "kind": "bool", "feature": "tuner"},
    # Radio menu 153 "WIRES DG-ID": AUTO or 1..99. This is the WIRES-X setting, NOT the C4FM RX/TX DG-ID you see on the radio's
    # C4FM screen: the CAT manual has no command for those. Bench report: changing it shows nothing on the radio's C4FM screen.
    # Radio menu 106 "SSB MIC SELECT" (CAT manual p.9): 0 = MIC (front microphone), 1 = REAR (rear data/mic jack, used by the USB audio path).
    {"name": "mic_select", "label": "Mic input", "group": "Transmit", "kind": "enum", "feature": "mic_select", "quick": True,
     "choices": ["MIC", "REAR"]},
    {"name": "dgid", "label": "WIRES DG-ID", "group": "C4FM", "kind": "enum", "feature": "dgid", "quick": True,
     "modes": ["C4FM"], "choices": ["AUTO"] + [str(n) for n in range(1, 100)]},
]
SPEC_BY_NAME = {s["name"]: s for s in SPECS}


def available(features: dict[str, bool]) -> list[dict[str, Any]]:
    return [s for s in SPECS if features.get(s["feature"]) is True]


def coerce(spec: dict[str, Any], value: Any) -> Any:
    """Validate a browser-supplied value for a control. Raises ValueError."""
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("expected true/false")
        return value
    if kind in ("int", "width"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected an integer")
        if kind == "int":
            if not spec["min"] <= value <= spec["max"]:
                raise ValueError(f"out of range {spec['min']}..{spec['max']}")
            if spec["step"] > 1 and value % spec["step"] != 0:
                raise ValueError(f"must be a multiple of {spec['step']}")
        return value
    if kind == "enum":
        if value not in spec["choices"]:
            raise ValueError("not a valid choice")
        return value
    raise ValueError("unknown control kind")
