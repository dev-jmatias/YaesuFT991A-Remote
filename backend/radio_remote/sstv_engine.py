"""SSTV for the SSTV tab: receive (pictures from the radio's audio, saved as PNG files on the Pi) and transmit (a picture the page sends, as audio, through the usual PTT protection).
See audio/sstv.py for the codec.

Receive: the audio service hands every 20 ms frame to feed(); a worker thread runs the receiver and the events (header found, rows of the picture, picture finished) are pushed to every page
({"t": "sstv", "k": ...}). A finished picture (or a partial one that got far enough) is saved in <data>/sstv/ and listed.
Transmit: transmit() encodes the picture, hands the clip to the audio service, and keys PTT through the TxGuard (control of the radio, transmit permission, heartbeat, time limit). A picture
that would take longer than the transmit time limit is refused before anything is keyed.
"""
from __future__ import annotations

import asyncio
import base64
import calendar
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

from . import config
from .audio import sstv
from .audio.levels import db_to_gain
from .safety import TxRefused

log = logging.getLogger("sstv")

BANDS = [("80m", 3845000, "LSB"), ("40m", 7171000, "LSB"), ("20m", 14230000, "USB"), ("15m", 21340000, "USB"), ("10m", 28680000, "USB"), ("2m ISS", 145800000, "FM")]
TX_RATE = 16000                    # the audio service's playback clips are 16 kHz
LEAD_S, TAIL_S = 0.4, 0.3          # silence before and after the tones (PTT settles; the last line is not cut)
MAX_UPLOAD = 1_300_000             # bytes of picture data accepted for a transmission (PD240: 640 x 496 x 3 = 952 320)
NAME_RE = re.compile(r"rx-(\d{8})-(\d{6})-([a-z0-9]+)-(\d+)(-partial)?\.png")
MIN_ROWS = 16                      # a picture that ends before this many rows is not kept (a false header, a burst of noise)
BACKLOG = 400                      # frames (8 s) waiting for the worker before new ones are dropped: it is not keeping up


class SstvEngine:
    def __init__(self, get_cfg: Callable[[], dict], audio, broadcast: Callable[[dict], None], get_freq: Callable[[], int], guard, has_client: Callable[[str], bool], data_dir: Path):
        self.get_cfg, self.audio, self.broadcast, self.get_freq, self.guard, self.has_client = get_cfg, audio, broadcast, get_freq, guard, has_client
        self.dir = Path(data_dir) / "sstv"
        self.on = False
        self.rx: sstv.SstvRx | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sstv-rx")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiting = 0
        self.cur: dict | None = None                       # the picture being received: mode, size, rows so far
        self.cur_img: np.ndarray | None = None
        self.transmitting = False
        self.tx: dict | None = None
        self._hb_task: asyncio.Task | None = None
        self.last_error = ""

    # ------------------------------------------------------------ state
    def settings(self) -> dict:
        c = self.get_cfg()
        return {"callsign": config.ftx_callsign(c), "tx_level_db": c["sstv"]["tx_level_db"], "keep": c["sstv"]["keep"]}

    def apply_settings(self) -> None:
        self.broadcast({"t": "sstv", "k": "settings", "settings": self.settings()})

    def state(self) -> dict:
        return {"on": self.on, "modes": sstv.mode_list(), "bands": [{"name": n, "hz": h, "mode": m} for n, h, m in BANDS], "settings": self.settings(), "cur": self.cur,
                "images": self.images(), "transmitting": self.transmitting, "tx": self.tx, "error": self.last_error, "max_tx_s": self.guard.max_tx_s, "now": time.time()}

    # ------------------------------------------------------------ on / off
    async def set_on(self, on: bool) -> None:
        if on:
            if self.guard.keyed:
                raise ValueError("not while transmitting")
            self._loop = asyncio.get_running_loop()
            if not self.on:
                self.rx = sstv.SstvRx()
                self.audio.sstv_feed = self.feed
                await self.audio.hold_capture(True, "sstv")
                self.on = True
                log.info("SSTV on")
        elif self.on:
            await self.halt("switched off")
            self.audio.sstv_feed = None
            rx, self.on = self.rx, False
            if rx is not None:
                ev = await asyncio.get_running_loop().run_in_executor(self._pool, rx.flush)        # a picture in progress ends where it is
                self._handle(ev)
            self.rx = None
            await self.audio.hold_capture(False, "sstv")
            log.info("SSTV off")
        self.broadcast({"t": "sstv", "k": "state", "on": self.on})

    # ------------------------------------------------------------ receive
    def feed(self, pcm: bytes | None) -> None:
        rx = self.rx
        if rx is None or pcm is None or self._waiting > BACKLOG:
            return
        self._waiting += 1
        now = time.monotonic()
        self._pool.submit(self._work, rx, pcm, now)

    def _work(self, rx: sstv.SstvRx, pcm: bytes, now: float) -> None:
        try:
            ev = rx.feed(pcm, now)
        except Exception:
            log.exception("SSTV receive failed")
            ev = []
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._done_work, ev)

    def _done_work(self, ev: list) -> None:
        self._waiting = max(0, self._waiting - 1)
        if ev:
            self._handle(ev)

    def _handle(self, events: list) -> None:
        for e in events:
            k = e["k"]
            if k == "start":
                self.cur = {"mode": e["mode"], "name": e["name"], "w": e["w"], "h": e["h"], "seconds": e["seconds"], "rows": 0, "hz": int(self.get_freq() or 0), "since": time.time()}
                self.cur_img = np.zeros((e["h"], e["w"], 3), dtype=np.uint8)
                log.info("SSTV: header of %s", e["name"])
                self.broadcast({"t": "sstv", "k": "start", **self.cur})
            elif k == "rows" and self.cur is not None and self.cur_img is not None:
                w = self.cur["w"]
                rgb = np.frombuffer(e["rgb"], dtype=np.uint8).reshape(-1, w, 3)
                y0, n = e["y0"], len(rgb)
                self.cur_img[y0:y0 + n] = rgb
                self.cur["rows"] = y0 + n
                self.broadcast({"t": "sstv", "k": "rows", "y0": y0, "n": n, "d": base64.b64encode(e["rgb"]).decode("ascii")})
            elif k == "done":
                self._finished(e)
            elif k == "vis":
                log.info("SSTV: a header for a mode this program does not know (VIS %d)", e["code"])
                self.broadcast({"t": "sstv", "k": "vis", "code": e["code"]})

    def _finished(self, e: dict) -> None:
        cur, self.cur, self.cur_img = self.cur, None, None
        mode = sstv.MODES.get(e["mode"])
        keep = e["complete"] or (mode is not None and e["rows"] >= max(MIN_ROWS, mode.h * 0.1))
        info = None
        if keep and mode is not None:
            try:
                info = self._save(e["rgb"][:e["rows"]] if not e["complete"] else e["rgb"], mode, e["complete"], (cur or {}).get("hz", 0))
            except OSError:
                log.exception("could not save the SSTV picture")
        log.info("SSTV: picture %s, %d rows%s", e["mode"], e["rows"], "" if e["complete"] else " (partial)" + ("" if info else ", not kept"))
        self.broadcast({"t": "sstv", "k": "done", "complete": e["complete"], "rows": e["rows"], "image": info})

    # ------------------------------------------------------------ pictures
    def _save(self, rgb: np.ndarray, mode: sstv.Mode, complete: bool, hz: int) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        t = time.time()
        name = f"rx-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(t))}-{mode.key}-{int(round(hz / 1000))}{'' if complete else '-partial'}.png"
        (self.dir / name).write_bytes(sstv.png_bytes(rgb))
        keep = max(5, int(self.get_cfg()["sstv"]["keep"]))
        for old in sorted(self.dir.glob("rx-*.png"))[:-keep]:
            old.unlink(missing_ok=True)
        return self._describe(name) or {}

    def _describe(self, name: str) -> dict | None:
        m = NAME_RE.fullmatch(name)
        if not m:
            return None
        d, t, key, khz, partial = m.groups()
        mode = sstv.MODES.get(key)
        if mode is None:
            return None
        ts = calendar.timegm(time.strptime(d + t, "%Y%m%d%H%M%S"))                      # the name carries UTC
        return {"name": name, "t": ts, "mode": key, "mode_name": mode.name, "w": mode.w, "khz": int(khz), "partial": bool(partial)}

    def images(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = [self._describe(p.name) for p in sorted(self.dir.glob("rx-*.png"), reverse=True)]
        return [x for x in out if x]

    def image_path(self, name: str) -> Path | None:
        if not NAME_RE.fullmatch(name):
            return None
        p = self.dir / name
        return p if p.is_file() else None

    def delete_image(self, name: str) -> bool:
        p = self.image_path(name)
        if p is None:
            return False
        p.unlink(missing_ok=True)
        return True

    def current_png(self) -> bytes | None:
        return sstv.png_bytes(self.cur_img) if self.cur_img is not None else None

    # ------------------------------------------------------------ transmit
    async def transmit(self, conn: str, rgb: bytes, mode_key: str) -> dict:
        mode = sstv.MODES.get(mode_key)
        if mode is None:
            raise ValueError("unknown SSTV mode")
        if not self.on:
            raise ValueError("switch SSTV on first")
        if getattr(self.audio, "_fd_tx", None) is not None:             # FreeDV would turn the tones into modem input: never transmit like that
            raise ValueError("FreeDV is on: switch it off before transmitting SSTV")
        if self.transmitting or self.guard.keyed:
            raise ValueError("already transmitting")
        if len(rgb) != mode.w * mode.h * 3:
            raise ValueError(f"{mode.name} needs a {mode.w} x {mode.h} picture")
        total = mode.seconds + LEAD_S + TAIL_S
        if total > self.guard.max_tx_s:
            raise ValueError(f"{mode.name} takes {int(mode.seconds)} s but the transmit time limit is {int(self.guard.max_tx_s)} s: choose a faster mode, or an administrator can raise "
                             "the limit in Admin > Config > Safety")
        level = db_to_gain(float(self.get_cfg()["sstv"]["tx_level_db"]))
        img = np.frombuffer(rgb, dtype=np.uint8).reshape(mode.h, mode.w, 3)
        sig = await asyncio.get_running_loop().run_in_executor(None, sstv.encode, img, mode, TX_RATE, level)
        clip = np.concatenate([np.zeros(int(LEAD_S * TX_RATE)), sig * 32767.0, np.zeros(int(TAIL_S * TX_RATE))]).astype("<i2")
        await self.audio.hold_capture(True, "sstv")                      # the sound card output must be running
        self.audio.arm_playback(clip, conn)
        try:
            await self.guard.request(conn, True)
        except TxRefused as e:
            self.audio.arm_playback(None)
            raise ValueError(str(e)) from None
        self.transmitting = True
        self.last_error = ""
        self.tx = {"mode": mode.key, "name": mode.name, "seconds": round(total, 1), "since": time.time()}
        log.info("SSTV transmit %s (%.0f s)", mode.name, total)
        self.broadcast({"t": "patch", "d": {"ptt_owner": self.guard.owner}})
        self.broadcast({"t": "sstv", "k": "tx", **self.tx})
        self._hb_task = asyncio.create_task(self._heartbeat(conn))
        return {"seconds": round(total, 1), "mode": mode.name}

    async def _heartbeat(self, conn: str) -> None:
        """The whole transmission is on the server's clock, so the server keeps the PTT heartbeat going for as long as the operator's page is connected."""
        try:
            while self.guard.keyed and self.guard.owner == conn:
                if not self.has_client(conn):
                    await self.halt("the operator left")
                    break
                await self.guard.heartbeat(conn)
                await asyncio.sleep(0.25)
        finally:
            self.transmitting, self.tx = False, None
            self.broadcast({"t": "sstv", "k": "txdone", "error": self.last_error})

    async def halt(self, why: str = "halted") -> None:
        if self.transmitting and self.guard.keyed and self.guard.owner:
            self.last_error = why
            try:
                self.audio.arm_playback(None)
                await self.guard.request(self.guard.owner, False)
            except TxRefused:
                pass
            except Exception:
                log.exception("could not stop the SSTV transmission")

    def conn_gone(self, conn: str) -> None:
        pass                                                           # the heartbeat task notices that the owner left and stops
