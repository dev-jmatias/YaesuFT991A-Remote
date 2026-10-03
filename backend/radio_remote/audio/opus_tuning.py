"""Lower aiortc's Opus bitrate.

aiortc's encoder is hard-wired to 48 kHz stereo 'voip' at 96 kbps. For speech from a radio
~24-32 kbps is plenty and is kinder to Wi-Fi/Tailscale relays. aiortc exposes no setting for this, so we wrap
OpusEncoder.__init__. If a future aiortc changes the class, we log and carry on at the default bitrate.
"""
from __future__ import annotations

import logging

log = logging.getLogger("audio")
_patched = False


def apply(bitrate: int) -> bool:
    global _patched
    try:
        from aiortc.codecs import opus
    except ImportError:
        return False
    if _patched:
        opus.OpusEncoder.bitrate = bitrate
        return True
    orig = opus.OpusEncoder.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        try:
            self.codec.bit_rate = type(self).bitrate
        except Exception:                       # pragma: no cover - depends on aiortc/PyAV internals
            log.warning("could not set Opus bitrate; using aiortc default")

    opus.OpusEncoder.bitrate = bitrate
    opus.OpusEncoder.__init__ = init
    _patched = True
    return True
