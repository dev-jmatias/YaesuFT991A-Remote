"""FT8 / FT4 for the FT8/FT4 tab: receive, transmit scheduling and the state the page shows. See audio/ftx.py for the codec and the slot cutter.

Receive: the audio service hands every 20 ms frame of the radio's audio to feed(); each finished UTC slot is decoded in a worker thread and the decodes are pushed to every page
({"t": "ftx", "k": "dec", ...}); a waterfall row of the audio goes out five times a second.
Transmit: the page asks for a message (set_tx); the engine sends it in every slot of the chosen parity (even or odd), through the same PTT safety as everything else (control lease,
transmit permission, owner heartbeat, time limit), until it is replaced, halted or has been repeated max_tx_repeats times. The page decides WHAT to send (the QSO sequence).
"""
from __future__ import annotations

import asyncio
import base64
import collections
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from . import config, cty
from .audio import ftx
from .audio.levels import db_to_gain
from .safety import TxRefused

log = logging.getLogger("ftx")

# dial frequencies (USB, Hz) the usual FT8 / FT4 activity is on
BANDS = {
    "FT8": [("160m", 1840000), ("80m", 3573000), ("60m", 5357000), ("40m", 7074000), ("30m", 10136000), ("20m", 14074000), ("17m", 18100000), ("15m", 21074000),
            ("12m", 24915000), ("10m", 28074000), ("6m", 50313000), ("2m", 144174000)],
    "FT4": [("80m", 3575000), ("40m", 7047500), ("30m", 10140000), ("20m", 14080000), ("17m", 18104000), ("15m", 21140000), ("12m", 24919000), ("10m", 28180000),
            ("6m", 50318000), ("2m", 144170000)],
}
HISTORY = 400                     # decodes kept for pages that connect later
KEY_AT_S = 0.2                    # PTT goes down this long after the slot starts (the audio follows after its lead-in, see LEAD_S)
LEAD_S = 0.2                      # silence in front of the tones: the signal starts KEY_AT_S + LEAD_S + the radio's switching time after the slot start (nominal 0.5 s)
TAIL_S = 0.1
LATE_START_S = 1.3                # a message asked for up to this long after PTT would normally go down still goes out in this slot (the tones then start about 1.5 s late, which receivers accept)
TX_RATE = 16000                   # the audio service's playback clips are 16 kHz
WF_COLS = 240                     # waterfall: 0..3000 Hz in 12.5 Hz columns
WF_EVERY = 2400                   # one waterfall row per 200 ms of audio


def _unit_ms(v: str, unit: str) -> float:
    return float(v) * {"us": 0.001, "µs": 0.001, "ms": 1.0, "s": 1000.0}.get(unit, 1.0)


def clock_info() -> dict:
    """The state of the Pi's clock: {"ok": synchronised by NTP (None = cannot tell), "offset_ms": how far it is from the time server (None = unknown), "source": which service says so}.
    FT8 and FT4 are timed to the second, so this matters more here than anywhere else in the program."""
    import re
    import subprocess

    def run(*cmd):
        try:
            r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=4)
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    out = {"ok": None, "offset_ms": None, "source": ""}
    v = run("timedatectl", "show", "-p", "NTPSynchronized", "--value").strip()
    if v:
        out["ok"] = v == "yes"
    m = re.search(r"Offset:\s*([+-]?[0-9.]+)\s*(us|µs|ms|s)\b", run("timedatectl", "timesync-status"))              # systemd-timesyncd (Raspberry Pi OS)
    if m:
        out["offset_ms"], out["source"] = _unit_ms(m.group(1), m.group(2)), "systemd-timesyncd"
    else:
        t = run("chronyc", "tracking")                                                                                 # chrony, when that is what keeps the time
        m = re.search(r"System time\s*:\s*([0-9.]+) seconds (fast|slow)", t)
        if m:
            out["offset_ms"] = float(m.group(1)) * 1000.0 * (1 if m.group(2) == "fast" else -1)
            out["source"] = "chrony"
            if out["ok"] is None:
                out["ok"] = "Leap status     : Normal" in t
    return out


class Pending:
    def __init__(self, conn: str, text: str, canon: str, f0: float, parity: int, repeats: int):
        self.conn, self.text, self.canon, self.f0, self.parity, self.repeats = conn, text, canon, f0, parity, repeats
        self.sent = 0


class FtxEngine:
    def __init__(self, get_cfg: Callable[[], dict], audio, broadcast: Callable[[dict], None], get_freq: Callable[[], int], guard, has_client: Callable[[str], bool]):
        self.get_cfg, self.audio, self.broadcast, self.get_freq, self.guard, self.has_client = get_cfg, audio, broadcast, get_freq, guard, has_client
        self.on = False
        self.mode = "FT8"
        self.rx: ftx.SlotRx | None = None
        self.history: collections.deque[dict] = collections.deque(maxlen=HISTORY)
        self.pending: Pending | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ftx-decode")
        self._tx_task: asyncio.Task | None = None
        self._hb_task: asyncio.Task | None = None
        self._last_tx_slot = -1
        self._tx_slots: set[int] = set()
        self._slot_texts: dict[int, set[str]] = {}
        self._wf_buf = np.zeros(0, dtype=np.float32)
        self._wf_win = np.hanning(2048).astype(np.float32)
        self.clock: dict = {"ok": None, "offset_ms": None, "source": ""}
        self._clock_at = 0.0
        self.last_error = ""
        self.transmitting = False
        self.decoding_ms = 0.0

    # ------------------------------------------------------------ settings
    def settings(self) -> dict:
        c = self.get_cfg()
        return {"callsign": config.ftx_callsign(c), "grid_square": config.ftx_grid(c), "tx_freq_hz": c["ftx"]["tx_freq_hz"], "tx_level_db": c["ftx"]["tx_level_db"],
                "max_tx_repeats": c["ftx"]["max_tx_repeats"], "cq_only": c["ftx"]["cq_only"], "clock_check": c["ftx"]["clock_check"]}

    def apply_settings(self) -> None:
        self.broadcast({"t": "ftx", "k": "settings", "settings": self.settings()})

    def state(self) -> dict:
        ok, why = ftx.available()
        return {"available": ok, "reason": why, "on": self.on, "mode": self.mode, "modes": list(ftx.MODES), "bands": {m: [{"name": n, "hz": h} for n, h in b] for m, b in BANDS.items()},
                "settings": self.settings(), "clock": self.clock, "pending": self._pending_view(), "transmitting": self.transmitting, "error": self.last_error,
                "now": time.time(), "slot": ftx.MODES[self.mode]["slot"], "history": list(self.history)}

    def _pending_view(self):
        p = self.pending
        return None if p is None else {"text": p.canon, "f0": p.f0, "parity": p.parity, "left": p.repeats, "sent": p.sent}

    # --------------------------------------------------------------- on / off
    async def set_on(self, on: bool, mode: str | None = None) -> None:
        if on:
            ok, why = ftx.available()
            if not ok:
                raise ValueError("FT8/FT4 is not available: " + why)
            m = mode or self.mode
            if m not in ftx.MODES:
                raise ValueError("mode must be FT8 or FT4")
            if self.guard.keyed:
                raise ValueError("not while transmitting")
            if self.on and m != self.mode:
                await self.set_on(False)
            self.mode = m
            self.rx = ftx.SlotRx(m)
            self._tx_slots.clear()
            self._wf_buf = np.zeros(0, dtype=np.float32)
            self.audio.ftx_feed = self.feed
            await self.audio.hold_capture(True)
            self.on = True
            if self._tx_task is None:
                self._tx_task = asyncio.create_task(self._tx_loop())
            asyncio.get_running_loop().create_task(self._check_clock(force=True))
            log.info("FT8/FT4 on, mode %s", m)
        else:
            await self.halt("switched off")
            self.audio.ftx_feed = None
            self.on, self.rx = False, None
            t, self._tx_task = self._tx_task, None
            if t:
                t.cancel()
            await self.audio.hold_capture(False)
            log.info("FT8/FT4 off")
        self.broadcast({"t": "ftx", "k": "state", "on": self.on, "mode": self.mode})

    async def _check_clock(self, force: bool = False) -> None:
        if not self.get_cfg()["ftx"]["clock_check"]:
            self.clock = {"ok": True, "offset_ms": None, "source": "not checked"}
            return
        if force or time.time() - self._clock_at > 60:
            self._clock_at = time.time()
            self.clock = await asyncio.get_running_loop().run_in_executor(None, clock_info)
            if self.clock["ok"] is False or (self.clock["offset_ms"] is not None and abs(self.clock["offset_ms"]) > 200):
                log.warning("FT8/FT4: the Pi's clock is %s (offset %s ms): decoding and transmitting are timed to the second",
                            "not synchronised" if self.clock["ok"] is False else "off", self.clock["offset_ms"])
            self.broadcast({"t": "ftx", "k": "clock", **self.clock})

    # ---------------------------------------------------------------- receive
    def feed(self, pcm: bytes) -> None:
        rx = self.rx
        if rx is None:
            return
        now = time.time()
        slot = rx.feed(pcm, now)
        self._waterfall(rx.last_chunk)
        if slot is not None:
            idx, t0, audio, final = slot
            if idx in self._tx_slots or self.guard.keyed and idx == ftx.slot_of(now, self.mode)[0]:
                if final:
                    self._tx_slots.discard(idx)                   # we transmitted in this slot: nothing to decode
                return
            asyncio.get_running_loop().create_task(self._decode(idx, t0, audio, self.mode, final))

    def _waterfall(self, chunk: np.ndarray) -> None:
        self._wf_buf = np.concatenate([self._wf_buf, chunk])
        while len(self._wf_buf) >= WF_EVERY:
            seg = self._wf_buf[:WF_EVERY]
            self._wf_buf = self._wf_buf[WF_EVERY:]
            win = np.concatenate([np.zeros(max(0, 2048 - len(seg)), dtype=np.float32), seg[-2048:]]) * self._wf_win
            p = np.abs(np.fft.rfft(win)) ** 2
            p = p[:int(3000 / (ftx.DECODE_RATE / 2048)) + 1]                       # 0..3000 Hz (513 bins of 5.86 Hz)
            cols = np.array([c.max() for c in np.array_split(p, WF_COLS)])          # 240 columns of 12.5 Hz, the loudest bin of each
            db = 10 * np.log10(cols + 1e-12)
            floor = float(np.median(db))
            row = np.clip((db - floor) * 6.0, 0, 255).astype(np.uint8)
            self.broadcast({"t": "ftx", "k": "wf", "r": base64.b64encode(row.tobytes()).decode("ascii")})

    async def _decode(self, idx: int, t0: float, audio: np.ndarray, mode: str, final: bool = True) -> None:
        t = time.perf_counter()
        try:
            found = await asyncio.get_running_loop().run_in_executor(self._pool, ftx.decode, audio, mode)
        except Exception:
            log.exception("FT8/FT4 decode failed")
            return
        self.decoding_ms = (time.perf_counter() - t) * 1000.0
        if not self.on or mode != self.mode:
            return
        hz = int(self.get_freq() or 0)
        had = self._slot_texts.setdefault(idx, set())                                # the early pass already showed some of these
        fresh = [d for d in sorted(found, key=lambda d: -d.snr) if d.text not in had]
        had.update(d.text for d in fresh)
        for old in [k for k in self._slot_texts if k < idx - 3]:
            del self._slot_texts[old]
        items = [{"f": round(d.freq), "dt": round(d.dt, 1), "snr": int(round(d.snr)), "text": d.text, "c": cty.country(cty.sender_of(d.text))} for d in fresh]
        log.info("%s slot %s (%s pass): %d decoded%s in %.0f ms", mode, time.strftime("%H:%M:%S", time.gmtime(t0)), "final" if final else "early", len(found),
                 f", {len(items)} new" if len(items) != len(found) else "", self.decoding_ms)
        dts = sorted(d.dt for d in found)
        dtm = round(dts[len(dts) // 2], 2) if len(dts) >= 3 else None                  # the median DT of the stations heard: how far the band is from OUR slot start
        if final and dtm is not None:
            log.info("%s slot %s: median DT of %d stations %+.2f s", mode, time.strftime("%H:%M:%S", time.gmtime(t0)), len(dts), dtm)
        if items:
            rec = {"slot": idx, "t0": t0, "parity": idx % 2, "mode": mode, "hz": hz, "items": items, "ms": int(self.decoding_ms), "dtm": dtm, "n": len(found), "late": bool(final and len(had) > len(items))}
            self.history.append(rec)
            self.broadcast({"t": "ftx", "k": "dec", **rec})
        if final:
            await self._check_clock()

    # --------------------------------------------------------------- transmit
    def set_tx(self, conn: str, text: str, f0: float, parity: int, repeats: int | None = None) -> dict:
        """Send this message in every slot of this parity (0 = even slots, 1 = odd) from the next one on, until replaced or halted."""
        if not self.on:
            raise ValueError("switch FT8/FT4 on first")
        if getattr(self.audio, "_fd_tx", None) is not None:             # FreeDV would turn the FT8/FT4 tones into modem input: never transmit like that
            raise ValueError("FreeDV is on: switch it off before transmitting FT8/FT4")
        if parity not in (0, 1):
            raise ValueError("parity must be 0 (even) or 1 (odd)")
        if not 200 <= f0 <= 2900:
            raise ValueError("the transmit frequency must be 200..2900 Hz")
        try:
            _, canon = ftx.encode(text, self.mode, f0, TX_RATE)
        except ftx.FtxUnavailable as e:
            raise ValueError(str(e)) from None
        cfg = self.get_cfg()["ftx"]
        n = max(1, min(int(repeats or cfg["max_tx_repeats"]), 30))
        self.pending = Pending(conn, text, canon, float(f0), parity, n)
        log.info("FT8/FT4: will send %r at %.0f Hz in %s slots (up to %d times)", canon, f0, "odd" if parity else "even", n)
        self.last_error = ""
        self.broadcast({"t": "ftx", "k": "pending", "pending": self._pending_view()})
        return {"message": canon}

    async def halt(self, why: str = "halted") -> None:
        had = self.pending is not None
        self.pending = None
        if self.guard.keyed and self.transmitting and self.guard.owner:
            try:
                self.audio.arm_playback(None)
                await self.guard.request(self.guard.owner, False)
            except TxRefused:
                pass
            except Exception:
                log.exception("could not stop the FT8/FT4 transmission")
        if had:
            self.broadcast({"t": "ftx", "k": "pending", "pending": None, "why": why})

    def conn_gone(self, conn: str) -> None:
        if self.pending is not None and self.pending.conn == conn:
            self.pending = None
            self.broadcast({"t": "ftx", "k": "pending", "pending": None, "why": "the operator left"})

    async def _tx_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            p = self.pending
            if p is None or not self.on or self.transmitting:
                continue
            now = time.time()
            idx, start = ftx.slot_of(now, self.mode)
            into = now - start
            if idx % 2 != p.parity or idx == self._last_tx_slot or into < KEY_AT_S or into > KEY_AT_S + LATE_START_S:
                continue
            try:
                await self._transmit(p, idx)
            except Exception:
                log.exception("FT8/FT4 transmit failed")
                self.pending, self.transmitting = None, False
                self.last_error = "the transmission failed (see the log)"
                self.broadcast({"t": "ftx", "k": "pending", "pending": None, "why": self.last_error})

    async def _transmit(self, p: Pending, idx: int) -> None:
        if not self.has_client(p.conn):
            self.pending = None
            self.broadcast({"t": "ftx", "k": "pending", "pending": None, "why": "the operator left"})
            return
        cfg = self.get_cfg()["ftx"]
        sig, canon = ftx.encode(p.text, self.mode, p.f0, TX_RATE)
        level = db_to_gain(float(cfg["tx_level_db"]))
        clip = np.concatenate([np.zeros(int(LEAD_S * TX_RATE)), sig * level * 32767.0, np.zeros(int(TAIL_S * TX_RATE))]).astype("<i2")
        await self.audio.hold_capture(True)                                          # the sound card output must be running
        self.audio.arm_playback(clip, p.conn)
        t0 = time.perf_counter()
        try:
            await self.guard.request(p.conn, True)
        except TxRefused as e:
            log.warning("FT8/FT4: the transmission was refused: %s", e)
            self.audio.arm_playback(None)
            self.pending = None
            self.last_error = str(e)
            self.broadcast({"t": "ftx", "k": "pending", "pending": None, "why": str(e)})
            return
        self.transmitting = True
        self._last_tx_slot = idx
        self._tx_slots.add(idx)
        p.sent += 1
        p.repeats -= 1
        log.info("FT8/FT4 transmit %s at %.0f Hz in slot %s (PTT took %.0f ms)", canon, p.f0, time.strftime("%H:%M:%S", time.gmtime(idx * ftx.MODES[self.mode]["slot"])), (time.perf_counter() - t0) * 1000)
        self.broadcast({"t": "patch", "d": {"ptt_owner": self.guard.owner}})
        self.broadcast({"t": "ftx", "k": "tx", "text": canon, "c": cty.country(canon.split()[0]) if not canon.startswith("CQ") else "", "f": round(p.f0), "t0": idx * ftx.MODES[self.mode]["slot"], "parity": idx % 2, "mode": self.mode, "hz": int(self.get_freq() or 0)})
        self._hb_task = asyncio.create_task(self._heartbeat(p))

    async def _heartbeat(self, p: Pending) -> None:
        """The whole transmission is on the server's clock, so the server keeps the PTT heartbeat going for as long as the operator's page is connected."""
        try:
            while self.guard.keyed and self.guard.owner == p.conn:
                if not self.has_client(p.conn):
                    await self.halt("the operator left")
                    break
                await self.guard.heartbeat(p.conn)
                await asyncio.sleep(0.25)
        finally:
            self.transmitting = False
            if self.pending is p and p.repeats <= 0:
                self.pending = None
                self.broadcast({"t": "ftx", "k": "pending", "pending": None, "why": "repeated the most times allowed"})
            else:
                self.broadcast({"t": "ftx", "k": "pending", "pending": self._pending_view()})
            self.broadcast({"t": "ftx", "k": "txdone"})
