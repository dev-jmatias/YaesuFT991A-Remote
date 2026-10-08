"""Amateur band lookup (broad, region-agnostic edges; display aid only, never a TX limiter)."""

BANDS = [
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_250_000, 5_450_000),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
    ("6m", 50_000_000, 54_000_000),
    ("AIR", 108_000_000, 137_000_000),                     # the FT-991A's AIR band (receive; 118-137 MHz airband AM plus the 108-118 MHz navigation range)
    ("2m", 144_000_000, 148_000_000),
    ("70cm", 420_000_000, 450_000_000),
]


def band_for(hz: int) -> str | None:
    for name, lo, hi in BANDS:
        if lo <= hz <= hi:
            return name
    return None


def band_start(name: str) -> int | None:
    """Default frequency used by the band buttons (start of the band)."""
    for n, lo, _ in BANDS:
        if n == name:
            return lo
    return None
