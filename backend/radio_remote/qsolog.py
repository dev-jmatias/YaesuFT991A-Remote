"""The contact log (FT8, FT4, JS8, CW and RTTY): one JSON line per contact in the data directory, exported as an ADIF file (every logging program imports ADIF).

A contact is a dict with the ADIF-style fields below. `uploaded` remembers per service ("qrz", "eqsl", "clublog") whether and when it went, so nothing is sent twice.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path

log = logging.getLogger("qsolog")

BANDS = [("2190m", 0.1357, 0.1378), ("630m", 0.472, 0.479), ("160m", 1.8, 2.0), ("80m", 3.5, 4.0), ("60m", 5.06, 5.45), ("40m", 7.0, 7.3), ("30m", 10.1, 10.15),
         ("20m", 14.0, 14.35), ("17m", 18.068, 18.168), ("15m", 21.0, 21.45), ("12m", 24.89, 24.99), ("10m", 28.0, 29.7), ("6m", 50.0, 54.0), ("4m", 70.0, 71.0),
         ("2m", 144.0, 148.0), ("70cm", 420.0, 450.0)]
SERVICES = ("qrz", "eqsl", "clublog")
CALL_RE = re.compile(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](/[A-Z0-9]{1,4})?|[A-Z0-9]{1,4}/[A-Z0-9]{3,7}")
GRID_RE = re.compile(r"[A-R]{2}[0-9]{2}([A-X]{2})?")
FIELDS = ("call", "gridsquare", "mode", "rst_sent", "rst_rcvd", "freq_mhz", "band", "qso_date", "time_on", "qso_date_off", "time_off", "station_callsign", "my_gridsquare", "comment", "name")


def band_of(freq_mhz: float) -> str:
    for name, lo, hi in BANDS:
        if lo <= freq_mhz <= hi:
            return name
    return ""


def adif_field(name: str, value) -> str:
    v = "" if value is None else str(value)
    return f"<{name.upper()}:{len(v)}>{v}" if v != "" else ""


def report_comment(q: dict) -> str:
    """The comment every logged contact carries unless it has one of its own, e.g. "FT8 Sent: +06 Rcvd: +01" (the mode of the contact, FT8 or FT4, and the reports as exchanged)."""
    parts = [q.get("mode", "FT8")]
    for label, key in (("Sent", "rst_sent"), ("Rcvd", "rst_rcvd")):
        if q.get(key):
            parts.append(f"{label}: {q[key]}")
    return " ".join(parts) if len(parts) > 1 else ""


def adif_record(q: dict, extra: dict | None = None) -> str:
    """One contact as an ADIF record. FT8, CW and RTTY are ADIF modes of their own; FT4 and JS8 are submodes of MFSK."""
    mode = q.get("mode", "FT8")
    parts = [adif_field("CALL", q["call"]), adif_field("QSO_DATE", q.get("qso_date")), adif_field("TIME_ON", q.get("time_on")),
             adif_field("QSO_DATE_OFF", q.get("qso_date_off")), adif_field("TIME_OFF", q.get("time_off")),
             adif_field("BAND", q.get("band") or band_of(float(q.get("freq_mhz") or 0))),
             adif_field("FREQ", f"{float(q['freq_mhz']):.6f}" if q.get("freq_mhz") else ""),
             adif_field("MODE", "MFSK" if mode in ("FT4", "JS8") else mode), adif_field("SUBMODE", mode if mode in ("FT4", "JS8") else ""),
             adif_field("RST_SENT", q.get("rst_sent")), adif_field("RST_RCVD", q.get("rst_rcvd")),
             adif_field("GRIDSQUARE", q.get("gridsquare")), adif_field("NAME", q.get("name")),
             adif_field("STATION_CALLSIGN", q.get("station_callsign")), adif_field("MY_GRIDSQUARE", q.get("my_gridsquare")),
             adif_field("COMMENT", q.get("comment") or report_comment(q))]
    for k, v in (extra or {}).items():
        parts.append(adif_field(k, v))
    return " ".join(p for p in parts if p) + " <EOR>\n"


def adif_header(version: str, extra: dict | None = None) -> str:
    h = "Radio Remote log\n" + adif_field("ADIF_VER", "3.1.4") + adif_field("PROGRAMID", "Radio Remote") + adif_field("PROGRAMVERSION", version)
    for k, v in (extra or {}).items():
        h += adif_field(k, v)
    return h + "\n<EOH>\n"


def clean(entry: dict) -> dict:
    """Validate one contact from the page. Raises ValueError with a message for the person."""
    out = {}
    call = str(entry.get("call", "")).strip().upper()
    if not CALL_RE.fullmatch(call):
        raise ValueError("the callsign does not look right")
    out["call"] = call
    grid = str(entry.get("gridsquare", "") or "").strip().upper()
    if grid and not GRID_RE.fullmatch(grid):
        raise ValueError("the grid square does not look right")
    out["gridsquare"] = grid
    mode = str(entry.get("mode", "FT8")).upper()
    if mode not in ("FT8", "FT4", "JS8", "CW", "RTTY"):
        raise ValueError("mode must be FT8, FT4, JS8, CW or RTTY")
    out["mode"] = mode
    for k in ("rst_sent", "rst_rcvd"):
        v = str(entry.get(k, "") or "").strip()
        if v and not re.fullmatch(r"[+-]?\d{1,3}", v):
            raise ValueError(f"{k} must be a signal report such as -10, +05 or 599")
        out[k] = v
    try:
        f = float(entry.get("freq_mhz"))
    except (TypeError, ValueError):
        raise ValueError("the frequency is missing") from None
    if not 0.1 <= f <= 1000:
        raise ValueError("the frequency is out of range")
    out["freq_mhz"] = round(f, 6)
    out["band"] = band_of(f)
    for k, pat in (("qso_date", r"\d{8}"), ("qso_date_off", r"\d{8}"), ("time_on", r"\d{6}"), ("time_off", r"\d{6}")):
        v = str(entry.get(k, "") or "")
        if not re.fullmatch(pat, v):
            raise ValueError(f"{k} must be UTC digits (YYYYMMDD / HHMMSS)")
        out[k] = v
    for k in ("station_callsign", "my_gridsquare", "comment", "name"):
        v = str(entry.get(k, "") or "").strip()
        if len(v) > 80 or any(ord(c) < 32 for c in v):
            raise ValueError(f"{k} is too long or has control characters")
        out[k] = v
    return out


def epoch_of(q: dict) -> float:
    """UTC time the contact began, as seconds since 1970 (0 when the date and time are unusable)."""
    import calendar
    try:
        d, t = q["qso_date"], q["time_on"]
        return float(calendar.timegm((int(d[:4]), int(d[4:6]), int(d[6:8]), int(t[:2]), int(t[2:4]), int(t[4:6]))))
    except (KeyError, ValueError, TypeError):
        return 0.0


DUPLICATE_WINDOW_S = 600


class QsoLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.items: list[dict] = []
        self._load()

    def _load(self) -> None:
        self.items = []
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                q = json.loads(line)
                if isinstance(q, dict) and "id" in q and "call" in q:
                    self.items.append(q)
            except ValueError:
                log.warning("skipped a damaged line in %s", self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(q, separators=(",", ":")) + "\n" for q in self.items), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, entry: dict) -> dict:
        q = clean(entry)
        q["id"] = secrets.token_hex(5)
        q["logged_at"] = int(time.time())
        q["uploaded"] = {}
        with self._lock:
            self.items.append(q)
            self._save()
        return q

    def duplicate_of(self, entry: dict) -> dict | None:
        """The contact already logged for the same station, band and mode that began within ten minutes of this one: the same contact reported twice (two pages open on one station,
        a repeated final message), not a new one."""
        try:
            q = clean(entry)
        except ValueError:
            return None
        t = epoch_of(q)
        for old in reversed(self.items[-50:]):
            if old["call"] == q["call"] and old["mode"] == q["mode"] and old["band"] == q["band"] and abs(epoch_of(old) - t) <= DUPLICATE_WINDOW_S:
                return old
        return None

    def get(self, qid: str) -> dict | None:
        return next((q for q in self.items if q["id"] == qid), None)

    def delete(self, qid: str) -> bool:
        with self._lock:
            n = len(self.items)
            self.items = [q for q in self.items if q["id"] != qid]
            if len(self.items) != n:
                self._save()
                return True
        return False

    def mark(self, qid: str, service: str, ok: bool, message: str) -> None:
        with self._lock:
            q = self.get(qid)
            if q is None:
                return
            q.setdefault("uploaded", {})[service] = {"ok": ok, "at": int(time.time()), "message": message[:200]}
            self._save()

    def pending(self, service: str) -> list[dict]:
        return [q for q in self.items if not q.get("uploaded", {}).get(service, {}).get("ok")]

    def recent(self, limit: int = 200) -> list[dict]:
        return list(reversed(self.items[-limit:]))

    def adif(self, version: str, ids: list[str] | None = None) -> str:
        sel = [q for q in self.items if ids is None or q["id"] in ids]
        return adif_header(version) + "".join(adif_record(q) for q in sel)
