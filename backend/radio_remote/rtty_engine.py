"""RTTY for the RTTY tab: receive (the two tones decoded into text, see audio/rtty.py) and send (typed text as AFSK audio through the usual PTT protection, like the FT8/FT4 and SSTV tones).

Receive: the audio service hands every 20 ms frame to feed(); a worker thread runs the decoder and its text and status events go to every page ({"t": "rtty", "k": ...}).
Send: the text is made into 5-bit Baudot and played as two audio tones (mark and space) in a USB, LSB or data mode, in pieces that each fit the transmit time limit. The pieces are
played one after the other; each is keyed through the TxGuard (transmit permission, one owner, heartbeat, time limit) and stopped if the operator's page goes away.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from . import config
from .audio import rtty
from .audio.levels import db_to_gain
from .safety import TxRefused

log = logging.getLogger("rtty")

# Where RTTY is usually found (the calling segments; the operator can type any frequency). AFSK in an upper-sideband data mode.
BANDS = [("80m", 3590000), ("40m", 7045000), ("30m", 10140000), ("20m", 14080000), ("17m", 18100000), ("15m", 21080000), ("12m", 24920000), ("10m", 28080000)]
TX_MODES = ("USB", "LSB", "DATA-U", "DATA-L")
TX_RATE = 16000                    # the audio service's playback clips are 16 kHz
KEEP = 6000
BACKLOG = 400
LEAD_S, TAIL_S = 0.5, 0.4


class RttyEngine:
    def __init__(self, get_cfg: Callable[[], dict], audio, broadcast: Callable[[dict], None], get_freq: Callable[[], int], guard, has_client: Callable[[str], bool], driver=None):
        self.get_cfg, self.audio, self.broadcast, self.get_freq, self.guard, self.has_client = get_cfg, audio, broadcast, get_freq, guard, has_client
        self.driver = driver
        self.on = False
        self.rx: rtty.RttyRx | None = None
        self.text = ""
        self.p = {"baud": 45.45, "shift": 170, "lo": None, "rev": False, "usos": True}      # the operator's settings, kept across switching off and on
        self.stat: dict = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rtty-rx")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiting = 0
        self.queue: list[str] = []
        self.sending: str | None = None
        self._tx_task: asyncio.Task | None = None
        self._halted = False
        self.last_error = ""

    # ------------------------------------------------------------ state
    def settings(self) -> dict:
        return {"tx_level_db": self.get_cfg()["rtty"]["tx_level_db"], "callsign": config.ftx_callsign(self.get_cfg())}

    def apply_settings(self) -> None:
        self.broadcast({"t": "rtty", "k": "settings", "settings": self.settings()})

    def state(self) -> dict:
        return {"on": self.on, "bands": [{"name": n, "hz": h} for n, h in BANDS], "bauds": list(rtty.BAUDS), "shifts": list(rtty.SHIFTS), "params": dict(self.p), "text": self.text,
                "stat": self.stat, "settings": self.settings(), "sending": self.sending, "queue": list(self.queue), "max_tx_s": self.guard.max_tx_s, "error": self.last_error,
                "now": time.time()}

    # ------------------------------------------------------------ on / off
    async def set_on(self, on: bool) -> None:
        if on:
            if self.guard.keyed:
                raise ValueError("not while transmitting")
            self._loop = asyncio.get_running_loop()
            if not self.on:
                self.rx = rtty.RttyRx(**self.p)
                self.audio.rtty_feed = self.feed
                await self.audio.hold_capture(True, "rtty")
                self.on = True
                log.info("RTTY on")
        elif self.on:
            await self.halt("switched off")
            self.audio.rtty_feed = None
            self.on, self.rx, self.stat = False, None, {}
            await self.audio.hold_capture(False, "rtty")
            log.info("RTTY off")
        self.broadcast({"t": "rtty", "k": "state", "on": self.on})

    def set_params(self, **kw) -> None:
        """baud (45.45, 50, 75, 100), shift (170, 200, 425, 850), lo (tone Hz 300..3000 or None for automatic), rev, usos."""
        p = dict(self.p)
        for k in ("baud", "shift", "lo", "rev", "usos"):
            if k in kw:
                p[k] = kw[k]
        if float(p["baud"]) not in rtty.BAUDS:
            raise ValueError("baud must be 45.45, 50, 75 or 100")
        if int(p["shift"]) not in rtty.SHIFTS:
            raise ValueError("shift must be 170, 200, 425 or 850 Hz")
        if p["lo"] is not None and not 300 <= float(p["lo"]) <= 3000 - int(p["shift"]):
            raise ValueError("the tone must be between 300 Hz and 3000 Hz minus the shift")
        self.p = {"baud": float(p["baud"]), "shift": int(p["shift"]), "lo": (float(p["lo"]) if p["lo"] else None), "rev": bool(p["rev"]), "usos": bool(p["usos"])}
        if self.rx is not None:
            self.rx.set_params(self.p["baud"], self.p["shift"], self.p["lo"], self.p["rev"], self.p["usos"])
        self.broadcast({"t": "rtty", "k": "params", "params": dict(self.p)})

    def clear(self) -> None:
        self.text = ""
        self.broadcast({"t": "rtty", "k": "clear"})

    # ------------------------------------------------------------ receive
    def feed(self, pcm: bytes | None) -> None:
        rx = self.rx
        if rx is None or pcm is None or self._waiting > BACKLOG:
            return
        self._waiting += 1
        self._pool.submit(self._work, rx, pcm)

    def _work(self, rx: rtty.RttyRx, pcm: bytes) -> None:
        try:
            ev = rx.feed(pcm)
        except Exception:
            log.exception("RTTY receive failed")
            ev = []
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._done_work, ev)

    def _done_work(self, ev: list) -> None:
        self._waiting = max(0, self._waiting - 1)
        for e in ev:
            if e["k"] == "text":
                self.text = (self.text + e["s"])[-KEEP:]
                self.broadcast({"t": "rtty", "k": "text", "s": e["s"]})
            elif e["k"] == "stat" and self.on:
                self.stat = {k: v for k, v in e.items() if k != "k"}
                self.broadcast({"t": "rtty", "k": "stat", **self.stat})

    # ------------------------------------------------------------ send
    @staticmethod
    def clean(text: str) -> tuple[str, str]:
        up = " ".join(text.upper().replace("\r", " ").replace("\n", " ").split())
        kept = "".join(ch for ch in up if ch in rtty.ALLOWED)
        dropped = "".join(dict.fromkeys(ch for ch in up if ch not in rtty.ALLOWED))
        return " ".join(kept.split()), dropped

    def pieces(self, text: str) -> list[str]:
        """Pieces at word boundaries that each fit the transmit time limit."""
        limit = self.guard.max_tx_s - LEAD_S - TAIL_S - 1.0
        out: list[str] = []
        cur = ""
        for w in text.split():
            cand = f"{cur} {w}" if cur else w
            if cur and rtty.duration_s(cand, self.p["baud"]) > limit:
                out.append(cur)
                cur = w
            else:
                cur = cand
        if cur:
            out.append(cur)
        return out

    async def send(self, conn: str, text: str) -> dict:
        if not self.on:
            raise ValueError("switch RTTY on first (RX on)")
        if not self.guard.permitted:
            raise ValueError("transmitting is disabled (safety.allow_ptt = false)")
        mode = (self.driver.state.get("mode") if self.driver is not None else None) or ""
        if mode not in TX_MODES:
            raise ValueError(f"the radio is in {mode or '?'}: RTTY is sent as audio tones, so put it in USB, LSB or a data mode (DATA-U)")
        if getattr(self.audio, "_fd_tx", None) is not None:
            raise ValueError("FreeDV is on: switch it off before sending RTTY")
        if (self.guard.keyed or self.guard.tuning) and not (self._tx_task and not self._tx_task.done()):
            raise ValueError("the radio is transmitting")
        clean, dropped = self.clean(text)
        if not clean:
            raise ValueError("nothing to send (letters, digits and the usual punctuation only)")
        new = self.pieces(clean)
        if len(self.queue) + len(new) > 20:
            raise ValueError("too much text queued: wait for some of it to be sent")
        total = sum(rtty.duration_s(p, self.p["baud"]) for p in new) + LEAD_S + TAIL_S
        self.queue.extend(new)
        self.last_error = ""
        self.broadcast({"t": "rtty", "k": "queue", "queue": list(self.queue), "sending": self.sending})
        if self._tx_task is None or self._tx_task.done():
            self._halted = False
            self._tx_task = asyncio.create_task(self._run(conn))
        return {"pieces": len(new), "seconds": round(total, 1), "dropped": dropped, "text": clean}

    async def _run(self, conn: str) -> None:
        try:
            while self.queue and not self._halted:
                piece = self.queue.pop(0)
                lo = self.rx.lo if self.rx is not None else (self.p["lo"] or 2125.0)
                level = db_to_gain(float(self.get_cfg()["rtty"]["tx_level_db"]))
                sig = await asyncio.get_running_loop().run_in_executor(None, lambda: rtty.encode(piece, self.p["baud"], self.p["shift"], float(lo), TX_RATE, 1.0, self.p["rev"], LEAD_S, TAIL_S))
                clip = (sig * level * 32767.0).astype("<i2")
                secs = len(clip) / TX_RATE
                await self.audio.hold_capture(True, "rtty")                   # the sound card output must be running
                self.audio.arm_playback(clip, conn)
                try:
                    await self.guard.request(conn, True)
                except TxRefused:
                    self.audio.arm_playback(None)
                    raise
                self.sending = piece
                self.text = (self.text + f"\n[TX] {piece}\n")[-KEEP:]
                self.broadcast({"t": "rtty", "k": "text", "s": f"\n[TX] {piece}\n"})
                self.broadcast({"t": "rtty", "k": "queue", "queue": list(self.queue), "sending": piece, "seconds": round(secs, 1)})
                log.info("RTTY send %r (%.1f s)", piece, secs)
                while self.guard.keyed and self.guard.owner == conn and not self._halted:
                    if not self.has_client(conn):
                        self._halted, self.last_error = True, "the operator left"
                        break
                    await self.guard.heartbeat(conn)
                    await asyncio.sleep(0.25)
                if self._halted and self.guard.keyed and self.guard.owner == conn:
                    self.audio.arm_playback(None)
                    try:
                        await self.guard.request(conn, False)
                    except TxRefused:
                        pass
                await asyncio.sleep(0.3)
        except TxRefused as e:
            self.last_error = str(e)
            log.warning("RTTY send refused: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.exception("RTTY send failed")
        finally:
            self.queue.clear()
            self.sending = None
            self.broadcast({"t": "rtty", "k": "txdone", "error": self.last_error})

    async def halt(self, why: str = "halted") -> None:
        t = self._tx_task
        if t is None or t.done():
            self.queue.clear()
            return
        self._halted = True
        self.last_error = why
        self.queue.clear()
        if self.guard.keyed and self.guard.owner:
            try:
                self.audio.arm_playback(None)
                await self.guard.request(self.guard.owner, False)
            except TxRefused:
                pass
            except Exception:
                log.exception("could not stop the RTTY transmission")

    async def shutdown(self) -> None:
        await self.halt("shutting down")
        t = self._tx_task
        if t is not None and not t.done():
            try:
                await asyncio.wait_for(t, 3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
