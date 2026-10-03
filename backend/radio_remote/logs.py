"""Log hygiene: secrets are scrubbed before they reach any handler; recent lines are kept for diagnostics."""
from __future__ import annotations

import collections
import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)(cookie|authorization|x-csrf-token)\s*[:=]\s*[^\r\n]+"), r"\1: [redacted]"),
    (re.compile(r"(?i)(rr_session)=[^\s;,]+"), r"\1=[redacted]"),
    (re.compile(r"(?i)(password|passwd|secret|token)(\"?\s*[:=]\s*\"?)[^\s\",}]+"), r"\1\2[redacted]"),
]


def scrub(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = ()
        return True


class RingHandler(logging.Handler):
    def __init__(self, size: int = 300):
        super().__init__()
        self.lines: collections.deque[str] = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "aiohttp.access":        # request noise would drown the useful lines
            return
        self.lines.append(self.format(record))


RING = RingHandler()


def install() -> None:
    """Idempotent: attach the scrubber and the ring buffer to the root logger."""
    root = logging.getLogger()
    if RING not in root.handlers:
        RING.addFilter(ScrubFilter())
        root.addHandler(RING)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
    for h in root.handlers:
        if not any(isinstance(f, ScrubFilter) for f in h.filters):
            h.addFilter(ScrubFilter())
