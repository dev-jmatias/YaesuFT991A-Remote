"""CW for the CW tab: receive (Morse decoded from the radio's audio into text, see audio/cw.py) and send (typed text played by the radio's own keyer).

The audio service hands every 20 ms frame to feed(); a worker thread runs the decoder, and its text and status events are pushed to every page ({"t": "cw", "k": ...}).
The decoded text of the last while is kept here, so a page that is opened later (or reloaded) shows it too.

Send: the FT-991A's CAT cannot send free text to its keyer, it can only play its stored keyer memories (KY) and store text in them (KM). So typed text is cut into pieces of at most
`cw.chunk_chars` characters at word boundaries; each piece is written into keyer memory `cw.keyer_slot`, played, and the next one follows when the estimated sending time (from the keyer
speed) is over. The memory's old text is read first and put back at the end. Every piece runs inside a TxGuard keyer session (transmit permission, one owner, time limit, heartbeat; if
the page goes away the sending is stopped). The radio must be in CW-U or CW-L, and its break-in (BK-IN) on, or it will not transmit.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import config
from .audio import cw
from .safety import TxRefused

log = logging.getLogger("cw")

# Where CW is usually found (the lower end of each band; the operator can type any frequency).
BANDS = [("160m", 1830000), ("80m", 3530000), ("40m", 7030000), ("30m", 10115000), ("20m", 14030000), ("17m", 18085000), ("15m", 21030000), ("12m", 24910000), ("10m", 28030000)]
KEEP = 6000                        # characters of decoded text kept for pages that join later
ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,?/=+-'")      # what is typed into the keyer memory (a ';' would end the CAT command)
GAP_UNITS = 7                      # between two pieces: a word gap
BACKLOG = 400                      # frames (8 s) waiting for the worker before new ones are dropped: it is not keeping up


class CwEngine:
    def __init__(self, get_cfg: Callable[[], dict], audio, broadcast: Callable[[dict], None], get_freq: Callable[[], int], guard, has_client: Callable[[str], bool], driver=None):
        self.get_cfg, self.audio, self.broadcast, self.get_freq, self.guard, self.has_client = get_cfg, audio, broadcast, get_freq, guard, has_client
        self.driver = driver
        self.on = False
        self.rx: cw.CwRx | None = None
        self.text = ""
        self.fixed_hz: float | None = None                  # None: the pitch is found by itself
        self.fixed_wpm: float | None = None                 # None: the speed is learnt
        self.stat: dict = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cw-rx")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiting = 0
        self.last_error = ""
        self.queue: list[str] = []                           # pieces waiting to be sent
        self.sending: str | None = None
        self._tx_task: asyncio.Task | None = None
        self._halted = False
        self.ks: int | None = None                           # the radio's keyer speed and break-in, as last read
        self.bi: bool | None = None

    def can_send(self) -> bool:
        return bool(self.driver is not None and self.driver.caps.has("cw_text"))

    def state(self) -> dict:
        return {"on": self.on, "bands": [{"name": n, "hz": h} for n, h in BANDS], "text": self.text, "pitch": self.fixed_hz, "wpm": self.fixed_wpm, "stat": self.stat, "error": self.last_error,
                "can_send": self.can_send(), "callsign": config.ftx_callsign(self.get_cfg()), "sending": self.sending, "queue": list(self.queue), "slot": self.get_cfg()["cw"]["keyer_slot"], "keyer": {"speed": self.ks, "break_in": self.bi} if self.ks is not None else None, "now": time.time()}

    # ------------------------------------------------------------ on / off
    async def set_on(self, on: bool) -> None:
        if on:
            if self.guard.keyed:
                raise ValueError("not while transmitting")
            self._loop = asyncio.get_running_loop()
            if not self.on:
                self.rx = cw.CwRx(self.fixed_hz, self.fixed_wpm)
                self.audio.cw_feed = self.feed
                await self.audio.hold_capture(True, "cw")
                self.on = True
                log.info("CW on")
        elif self.on:
            self.audio.cw_feed = None
            rx, self.on = self.rx, False
            if rx is not None:
                self._text(rx.flush())
            self.rx, self.stat = None, {}
            await self.audio.hold_capture(False, "cw")
            log.info("CW off")
        self.broadcast({"t": "cw", "k": "state", "on": self.on})

    def set_params(self, pitch: float | None, wpm: float | None) -> None:
        """pitch in Hz (300..1100) or None for automatic; wpm (5..60) or None for automatic."""
        self.fixed_hz = float(pitch) if pitch else None
        self.fixed_wpm = float(wpm) if wpm else None
        if self.rx is not None:
            self.rx.set_pitch(self.fixed_hz)
            self.rx.set_wpm(self.fixed_wpm)
        self.broadcast({"t": "cw", "k": "params", "pitch": self.fixed_hz, "wpm": self.fixed_wpm})

    def clear(self) -> None:
        self.text = ""
        self.broadcast({"t": "cw", "k": "clear"})

    # ------------------------------------------------------------ receive
    def feed(self, pcm: bytes | None) -> None:
        rx = self.rx
        if rx is None or pcm is None or self._waiting > BACKLOG:
            return
        self._waiting += 1
        self._pool.submit(self._work, rx, pcm)

    def _work(self, rx: cw.CwRx, pcm: bytes) -> None:
        try:
            ev = rx.feed(pcm)
        except Exception:
            log.exception("CW receive failed")
            ev = []
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._done_work, ev)

    def _done_work(self, ev: list) -> None:
        self._waiting = max(0, self._waiting - 1)
        if ev:
            self._text(ev)

    def _text(self, events: list) -> None:
        for e in events:
            if e["k"] == "text":
                self.text = (self.text + e["s"])[-KEEP:]
                self.broadcast({"t": "cw", "k": "text", "s": e["s"]})
            elif e["k"] == "stat" and self.on:
                self.stat = {k: v for k, v in e.items() if k != "k"}
                self.broadcast({"t": "cw", "k": "stat", **self.stat})

    # ------------------------------------------------------------ send
    @staticmethod
    def clean(text: str) -> tuple[str, str]:
        """Upper case, one space between words, only characters the keyer memory takes; the second value lists what was left out."""
        up = " ".join(text.upper().split())
        kept = "".join(ch for ch in up if ch in ALLOWED)
        dropped = "".join(dict.fromkeys(ch for ch in up if ch not in ALLOWED))
        return " ".join(kept.split()), dropped

    @staticmethod
    def pieces(text: str, n: int) -> list[str]:
        out: list[str] = []
        cur = ""
        for w in text.split():
            while len(w) > n:                                      # a word longer than a piece is cut
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(w[:n])
                w = w[n:]
            if cur and len(cur) + 1 + len(w) > n:
                out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}" if cur else w
        if cur:
            out.append(cur)
        return out

    def _wpm(self) -> float:
        return float(min(60, max(4, self.ks or 12)))                # unknown: assume slow, so that a piece is never cut short by the next one

    async def refresh_keyer(self) -> dict | None:
        """The radio's keyer speed and break-in, read from the radio (None if it cannot be read)."""
        if not self.can_send():
            return None
        try:
            k = await self.driver.cw_keyer_get()
        except Exception as e:
            self.ks, self.bi = None, None
            log.warning("could not read the keyer: %s", e)
            return None
        self.ks, self.bi = k["speed"], k["break_in"]
        return k

    async def set_keyer(self, speed: int | None, break_in: bool | None) -> dict | None:
        await self.driver.cw_keyer_set(speed, break_in)
        k = await self.refresh_keyer()
        self.broadcast({"t": "cw", "k": "keyer", "keyer": k})
        return k

    async def send(self, conn: str, text: str) -> dict:
        if not self.can_send():
            raise ValueError("this radio cannot send CW from the keyboard")
        if not self.guard.permitted:
            raise ValueError("transmitting is disabled (safety.allow_ptt = false)")
        if (self.driver.state.get("mode") or "") not in ("CW-U", "CW-L"):
            raise ValueError("put the radio in CW-U or CW-L first")
        if self.guard.keyed and not self._tx_task:
            raise ValueError("the radio is transmitting")
        if getattr(self.audio, "_fd_tx", None) is not None:
            raise ValueError("FreeDV is on: switch it off before sending CW")
        clean, dropped = self.clean(text)
        if not clean:
            raise ValueError("nothing to send (letters, digits and . , ? / = + - ' only)")
        if await self.refresh_keyer() is None:
            raise ValueError("could not read the radio's keyer speed: is the radio connected?")
        cfg = self.get_cfg()["cw"]
        new = self.pieces(clean, int(cfg["chunk_chars"]))
        unit = 1.2 / self._wpm()
        total = sum(cw.duration_units(p) for p in new) * unit
        if len(self.queue) + len(new) > 40:
            raise ValueError("too much text queued: wait for some of it to be sent")
        self.queue.extend(new)
        self.last_error = ""
        self.broadcast({"t": "cw", "k": "queue", "queue": list(self.queue), "sending": self.sending})
        if self._tx_task is None or self._tx_task.done():
            self._halted = False
            self._tx_task = asyncio.create_task(self._run(conn, int(cfg["keyer_slot"])))
        return {"pieces": len(new), "seconds": round(total, 1), "dropped": dropped, "text": clean}

    async def _run(self, conn: str, slot: int) -> None:
        saved: str | None = None
        try:
            try:
                saved = await self.driver.cw_memory_get(slot)
            except Exception:
                log.exception("could not read keyer memory %d", slot)
            while self.queue and not self._halted:
                piece = self.queue.pop(0)
                self.sending = piece
                await self.refresh_keyer()                         # the knob on the radio may have been turned since the last piece
                unit = 1.2 / self._wpm()
                secs = cw.duration_units(piece) * unit
                await self.guard.start_keyer(conn, secs + 1.0)
                try:
                    await self.driver.cw_memory_set(slot, piece)
                    await self.driver.cw_play(slot)
                    self.text = (self.text + f"\n[TX] {piece}\n")[-KEEP:]
                    self.broadcast({"t": "cw", "k": "text", "s": f"\n[TX] {piece}\n"})
                    self.broadcast({"t": "cw", "k": "queue", "queue": list(self.queue), "sending": piece, "seconds": round(secs, 1)})
                    log.info("CW send %r (%.1f s)", piece, secs)
                    end = asyncio.get_running_loop().time() + secs + (GAP_UNITS * unit if self.queue else 0.0) + 0.5
                    while asyncio.get_running_loop().time() < end and not self._halted:
                        if not self.has_client(conn):
                            self._halted, self.last_error = True, "the operator left"
                            break
                        await self.guard.heartbeat(conn)
                        await asyncio.sleep(0.2)
                except BaseException:
                    await self.guard.end_keyer(conn, stop=True)
                    raise
                await self.guard.end_keyer(conn, stop=self._halted)
        except TxRefused as e:
            self.last_error = str(e)
            log.warning("CW send refused: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.exception("CW send failed")
        finally:
            self.queue.clear()
            self.sending = None
            if saved:
                try:
                    await self.driver.cw_memory_set(slot, saved[:50])         # the radio's own message is put back
                except Exception:
                    log.exception("could not restore keyer memory %d", slot)
            self.broadcast({"t": "cw", "k": "txdone", "error": self.last_error})

    async def halt(self, why: str = "halted") -> None:
        """Stop sending now: the waiting pieces are dropped and the radio is told to un-key."""
        if self._tx_task is None or self._tx_task.done():
            self.queue.clear()
            return
        self._halted = True
        self.last_error = why
        self.queue.clear()
        if self.guard.keyer and self.guard.owner:
            try:
                await self.guard.end_keyer(self.guard.owner, stop=True)
            except Exception:
                log.exception("could not stop the CW keyer")

    async def shutdown(self) -> None:
        await self.halt("shutting down")
        t = self._tx_task
        if t is not None and not t.done():
            try:
                await asyncio.wait_for(t, 3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
