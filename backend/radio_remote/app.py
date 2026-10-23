"""HTTP + WebSocket application (aiohttp)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from . import __version__, admin, logs, sdnotify, updates
from .audio.service import AudioService, AudioUnavailable
from .auth import AuthStore, Session
from .common import (COOKIE, K_AUDIO, K_AUTH, K_CFG, K_CFGPATH, K_DRIVER, K_GUARD, K_HUB, K_FTX, K_QSOLOG, K_REPORTER, K_RESTART, K_SESSION, K_SSTV, K_CW, K_RTTY,
                     K_RIGCTL, K_STARTED, K_UPDATES, CommandError, client_ip, is_https, read_json)
from .lease import ControlLease, LeaseError
from .radio import controls
from .radio.base import METER_FIELDS, RadioDriver, RadioError
from .radio.registry import create_driver
from . import config
from .config import FREEDV_MODES, freedv_callsign
from .ratelimit import TokenBucket
from .freedv_reporter import FreeDVReporter
from .rigctl import RigctlServer
from .safety import TxGuard, TxRefused
from .scan import RangeScanner, ScanError

log = logging.getLogger("web")

PUBLIC_API = {"/api/status", "/api/login", "/api/setup"}
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs-html"          # the offline manual (built by scripts/build_docs.py), served at /docs/
# Identifies the running release (the timestamped folder under /opt/radio-remote/releases on a Pi). The browser compares it
# after every reconnect and offers a reload when an update has been installed underneath an open page.
BUILD = Path(__file__).resolve().parents[2].name
FLUSH_INTERVAL_S = 0.05
MAX_WS_CLIENTS = 24
CMD_RATE, CMD_BURST, MAX_STRIKES = 40.0, 80, 200
LEASE_OPS = {"request_control", "respond_control", "release_control", "force_control"}
MAX_EQ_PRESETS = 20
SCAN_KEEPS = {"scan_range", "scan_range_stop", "set_level", "set_control", "freedv", "ftx", "ftx_tx", "ftx_halt", "sstv", "sstv_halt", "cw", "cw_set", "cw_clear", "cw_send", "cw_halt", "cw_keyer", "rtty", "rtty_set", "rtty_clear", "rtty_send", "rtty_halt", "set_eq", "set_dyn", "eq_preset_save", "eq_preset_delete", "rec"}     # commands that leave a running range scan alone


def _int(v, lo=None, hi=None) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise CommandError("expected an integer")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise CommandError("value out of range")
    return v


@dataclass
class Client:
    ws: web.WebSocketResponse
    session: Session
    ip: str
    sid: str
    since: float = field(default_factory=time.time)


class Hub:
    """Fans radio state out to WebSocket clients, owns the control lease, and executes validated commands."""

    def __init__(self, driver: RadioDriver, guard: TxGuard, auth: AuthStore, lease_timeout_s: float = 10.0):
        self.driver, self.guard, self.auth = driver, guard, auth
        self.clients: dict[str, Client] = {}
        self._pending: dict = {}
        self._flush_handle = None
        self.extra: dict = {}            # non-radio state (audio levels) included in snapshots
        self.audio = None
        self.ui: dict = {}
        self.reporter = None                     # FreeDV Reporter (set by create_app)
        self.ftx = None                          # the FT8/FT4 engine (set by create_app)
        self.sstv = None                         # the SSTV engine (set by create_app)
        self.cw = None                           # the CW engine (set by create_app)
        self.rtty = None                         # the RTTY engine (set by create_app)
        self.scanner = RangeScanner(self)        # the start/stop frequency scan (scan.py)
        self.auto_width = lambda: True           # freedv.auto_width (set by create_app): widen a narrow radio filter when FreeDV is on
        self._widen_task: asyncio.Task | None = None
        self.eq_presets: dict[str, dict] = {}    # the equalizer sets saved by the operators (name -> {bands, cut}), shared like the equalizer itself
        self.eq_path: Path | None = None         # where the equalizer settings are kept (eq.json in the data folder)
        self._last_freq = driver.state.get("frequency") or 0
        self.mic_reset_delay_s = 15.0            # how long the radio stays on REAR after the last operator left (a reload or a network blip is not "left")
        self._mic_reset_task: asyncio.Task | None = None
        self.lease = ControlLease(
            guard=guard, broadcast=self._broadcast, send_to=self.send_to, timeout_s=lease_timeout_s,
            audit=lambda event, user, detail: auth.audit(event, user or None, None, detail))
        driver.subscribe(self._on_change)

    # ----------------------------------------------------------- fan-out
    def push(self, fields: dict) -> None:
        """Non-radio state (e.g. audio levels) joins the same batched fan-out."""
        self.extra.update(fields)
        self._on_change(fields)

    def _schedule_widen(self) -> None:
        """RADE V1 needs about 1500 Hz (750..2250 Hz of the audio), V2 about 900 Hz. A radio left in a narrow filter (a DATA mode defaults to 500 Hz) can never decode it. In the DATA modes the
        filter is centred on 1500 Hz, so 1700 Hz is the least that carries V1 and about 2000 Hz is comfortable; in USB/LSB the passband starts near 300 Hz, so it needs about 2100 Hz.
        A moment after FreeDV is switched on, or the mode changes while it is on, a filter under that is opened to the narrowest comfortable value (freedv.auto_width)."""
        if not self.auto_width():
            return
        if self._widen_task is not None and not self._widen_task.done():
            self._widen_task.cancel()
        self._widen_task = asyncio.get_running_loop().create_task(self._widen_after(0.9))

    async def _widen_after(self, delay: float) -> None:
        await asyncio.sleep(delay)                                  # the mode change and the new width have to be read back first
        try:
            await self._widen_now()
        except Exception:
            log.warning("could not widen the radio's filter for FreeDV", exc_info=True)

    async def _widen_now(self) -> None:
        d, st = self.driver, self.driver.state
        if not (self._wants_wide() and st.get("connected")) or st.get("tx") or st.get("tuning") or st.get("narrow"):
            return
        width, opts = st.get("width"), st.get("width_options") or []
        data = st.get("mode") in ("DATA-L", "DATA-U")
        need, want = (1700, 2000) if data else (2100, 2400)
        if not width or width >= need:
            return
        target = min((o["hz"] for o in opts if want <= o["hz"] <= 3000), default=0)
        if target < need:
            return
        await d.set_control("width", target)
        log.info("FreeDV / SSTV: the radio's %s filter was %s Hz wide; widened to %s Hz", st.get("mode"), width, target)

    def _wants_wide(self) -> bool:
        """FreeDV and SSTV both need about 1500 Hz of audio (SSTV: 1100..2300 Hz): a radio left in a narrow filter hears nothing of them."""
        return bool((self.audio and self.audio.freedv_state().get("on")) or (self.sstv and self.sstv.on))

    def _on_change(self, changed: dict) -> None:
        if "mode" in changed and self.audio is not None and self._wants_wide():
            self._schedule_widen()
        if "frequency" in changed:                                   # FreeDV keeps its software tuning when the dial moves a little
            old, self._last_freq = self._last_freq, changed["frequency"] or 0
            if self.reporter is not None:
                self.reporter.set_frequency(self._last_freq)
            if self.audio is not None and old and self._last_freq:
                self.audio.freedv_dial_moved(old, self._last_freq, self.driver.state.get("mode"))
        self._pending.update(changed)
        if self._flush_handle is None:
            self._flush_handle = asyncio.get_running_loop().call_later(FLUSH_INTERVAL_S, self._flush)

    def _flush(self) -> None:
        self._flush_handle = None
        pending, self._pending = self._pending, {}
        meters = {k: v for k, v in pending.items() if k in METER_FIELDS}
        patch = {k: v for k, v in pending.items() if k not in METER_FIELDS}
        if patch:
            self._broadcast({"t": "patch", "d": patch})
        if meters:
            self._broadcast({"t": "meters", "d": meters})

    def _broadcast(self, msg: dict) -> None:
        data = json.dumps(msg)
        for c in list(self.clients.values()):
            if not c.ws.closed:
                asyncio.ensure_future(c.ws.send_str(data))

    def send_to(self, conn: str, msg: dict) -> None:
        c = self.clients.get(conn)
        if c and not c.ws.closed:
            asyncio.ensure_future(c.ws.send_str(json.dumps(msg)))

    # ------------------------------------------------- radio input safety
    def _operators(self) -> bool:
        return any(c.session.role in ("operator", "admin") for c in self.clients.values())

    def cancel_mic_reset(self) -> None:
        t, self._mic_reset_task = self._mic_reset_task, None
        if t and not t.done():
            t.cancel()

    def schedule_mic_reset(self) -> None:
        """When the last operator has gone, put the radio's input back to its own MIC (menu 106) after a short wait: REAR is only for remote
        operation, and with nobody left who can transmit the front microphone is the right setting."""
        if self._operators():
            return
        self.cancel_mic_reset()
        self._mic_reset_task = asyncio.ensure_future(self._mic_reset_later())

    async def _mic_reset_later(self) -> None:
        try:
            await asyncio.sleep(self.mic_reset_delay_s)
        except asyncio.CancelledError:
            return
        d = self.driver
        if self._operators() or d.state.get("mic_select") != "REAR" or not d.caps.has("mic_select"):
            return
        if not d.state.get("connected") or d.state.get("tx"):
            return
        try:
            await d.set_control("mic_select", "MIC")
            log.info("the last operator left: the radio input is back on MIC")
            self.auth.audit("mic_input_reset", None, None, "last operator left")
        except Exception as e:                    # the radio is off or busy: leave it as it is
            log.warning("could not switch the radio input back to MIC: %s", e)

    # ---------------------------------------------------------- registry
    def clients_info(self) -> list[dict]:
        return [{"conn": cid, "user": c.session.username, "role": c.session.role, "ip": c.ip, "since": c.since,
                 "holder": self.lease.holds(cid), "ptt": self.guard.owner == cid} for cid, c in self.clients.items()]

    async def kick_conn(self, conn: str, reason: str, code: int = 4403) -> bool:
        c = self.clients.get(conn)
        if not c:
            return False
        await c.ws.close(code=code, message=reason.encode()[:100])
        return True

    async def kick_user(self, user_id: int, reason: str, except_sid: str | None = None) -> None:
        for cid, c in list(self.clients.items()):
            if c.session.user_id == user_id and c.sid != except_sid:
                await self.kick_conn(cid, reason, code=4401)

    def snapshot(self, session: Session, conn_id: str = "") -> dict:
        a = self.audio.status() if self.audio else {"available": False, "reason": "no audio service"}
        return {
            "t": "hello", "version": __version__, "build": BUILD, "conn": conn_id,
            "user": {"name": session.username, "role": session.role, "trusted": session.trusted},
            "caps": self.driver.caps.public(),
            "ui": self.ui,
            "lease": self.lease.state(),
            "audio": {"available": a["available"], "reason": a["reason"],
                      "mic": session.role in ("operator", "admin"),
                      "freedv": self.audio.freedv_state() if self.audio else {"available": False, "reason": "no audio service", "on": False},
                      "ftx": {"available": self.ftx_ok(), "on": bool(self.ftx and self.ftx.on)},
                      "sstv": {"available": bool(self.sstv and self.audio and self.audio.available), "on": bool(self.sstv and self.sstv.on)},
                      "cw": {"available": bool(self.cw and self.audio and self.audio.available), "on": bool(self.cw and self.cw.on)},
                      "rtty": {"available": bool(self.rtty and self.audio and self.audio.available), "on": bool(self.rtty and self.rtty.on)}},
            "state": {**self.driver.state, **self.extra, "ptt_owner": self.guard.owner},
            "safety": {"ptt_permitted": self.guard.permitted, "tx_timeout_s": self.guard.max_tx_s},
        }

    def ftx_ok(self) -> bool:
        from .audio import ftx as _ftx
        return bool(self.audio and self.audio.available and _ftx.available()[0])

    # ---------------------------------------------------------- commands
    async def handle(self, conn_id: str, session: Session, msg: dict):
        try:
            return await self._handle(conn_id, session, msg)
        except RadioError as e:
            raise CommandError(str(e)) from None
        except LeaseError as e:
            raise CommandError(str(e)) from None

    async def _handle(self, conn_id: str, session: Session, msg: dict):
        typ = msg.get("type")
        caps = self.driver.caps
        if typ in LEASE_OPS:
            return await self._lease_op(typ, conn_id, session, msg)
        if typ == "ptt_hb":                      # heartbeats only count for the actual PTT owner (checked in guard)
            await self.guard.heartbeat(conn_id)
            return None
        if typ == "power_on":                   # works while the radio is off, so it needs no control lease; admin only
            if session.role != "admin":
                raise CommandError("admin only")
            if not caps.has("power_on_cat"):
                raise CommandError("power on is not supported by this radio")
            self.auth.audit("power_on", session.username, None)
            await self.driver.power_on()
            return None
        if session.role == "viewer":
            raise CommandError("viewers cannot control the radio")
        if not self.lease.holds(conn_id):
            h = self.lease.holder
            raise CommandError(f"{h.user} has control. Request control to take over." if h
                               else "Nobody has control. Request control first.")
        if typ not in SCAN_KEEPS or (typ == "set_control" and msg.get("name") == "scan"):
            await self.scanner.stop("another command")                  # anything that touches the radio's frequency, mode, memory or keying ends the range scan
        if typ == "scan_range":
            if not caps.has("range_scan"):
                raise CommandError("the range scan is not available for this radio")
            vals = [_int(msg.get(k), 0) for k in ("start", "stop", "step", "level")]
            hold = msg.get("hold_s", 2)
            if isinstance(hold, bool) or not isinstance(hold, (int, float)):
                raise CommandError("hold_s must be a number")
            try:
                await self.scanner.start(conn_id, *vals, float(hold), bool(msg.get("stop_on_signal", False)))
            except ScanError as e:
                raise CommandError(str(e)) from None
            self.auth.audit("scan_range", session.username, f"{vals[0]}-{vals[1]} step {vals[2]}")
        elif typ == "scan_range_stop":
            await self.scanner.stop("stopped by the operator")
        elif typ == "rec":
            rec = self.audio.recorder if self.audio else None
            if rec is None:
                raise CommandError("audio is not available")
            on = msg.get("on")
            if not isinstance(on, bool):
                raise CommandError("'on' must be boolean")
            if on:
                st = self.driver.state
                rec.start({"frequency": st.get("frequency"), "mode": st.get("mode"),
                           "freedv": self.audio.freedv_mode if self.audio.freedv_state().get("on") else None})
            else:
                rec.stop()                                               # saves the clip (a few MB at most) and drops the oldest beyond five
            self.auth.audit("rec_start" if on else "rec_stop", session.username, None)
        elif typ == "set_dyn":
            if not self.audio:
                raise CommandError("audio is not available")
            try:
                self.audio.set_dyn({k: msg[k] for k in ("gate", "leveler", "limiter", "rx_gate") if k in msg})
            except ValueError as e:
                raise CommandError(str(e)) from None
            self.push({"dyn": self.audio.dyn_state()})
            self._save_eq()
        elif typ == "eq_preset_save":
            from .audio import eq as eqmod
            name = msg.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24 or not name.strip().isprintable():
                raise CommandError("the name must be 1 to 24 printable characters")
            name = name.strip()
            try:
                preset = {"bands": eqmod.clean_bands(msg.get("bands")), "cut": eqmod.clean_cut(msg.get("cut", {}))}
            except ValueError as e:
                raise CommandError(str(e)) from None
            if name not in self.eq_presets and len(self.eq_presets) >= MAX_EQ_PRESETS:
                raise CommandError(f"at most {MAX_EQ_PRESETS} saved sets: delete one first")
            self.eq_presets[name] = preset
            self.push({"eq_presets": self.eq_presets})
            self._save_eq()
        elif typ == "eq_preset_delete":
            name = msg.get("name")
            if not isinstance(name, str) or name not in self.eq_presets:
                raise CommandError("no such saved set")
            del self.eq_presets[name]
            self.push({"eq_presets": self.eq_presets})
            self._save_eq()
        elif typ == "set_eq":
            if not self.audio:
                raise CommandError("audio is not available")
            kw: dict = {}
            for k in ("rx_on", "tx_on"):
                if k in msg:
                    if not isinstance(msg[k], bool):
                        raise CommandError(f"{k} must be true or false")
                    kw[k] = msg[k]
            for k in ("rx", "tx", "rx_cut", "tx_cut"):
                if k in msg:
                    kw[k] = msg[k]
            try:
                self.audio.set_eq(**kw)
            except ValueError as e:
                raise CommandError(str(e)) from None
            self.push({"eq": self.audio.eq_state()})                    # every connected page follows
            self._save_eq()
        elif typ == "set_frequency":
            hz = _int(msg.get("hz"), 1)
            if not caps.frequency_ok(hz):
                raise CommandError("frequency outside radio coverage")
            vfo = msg.get("vfo", "A")
            if vfo == "B":
                if not caps.has("vfo_b"):
                    raise CommandError("VFO B is not available for this radio")
                await self.driver.set_frequency_b(hz)
            elif vfo == "A":
                await self.driver.set_frequency(hz)
            else:
                raise CommandError("vfo must be A or B")
        elif typ == "vfo":
            if not caps.has("vfo_b"):
                raise CommandError("VFO operations are not available for this radio")
            op = msg.get("op")
            if op not in ("swap", "a_to_b", "b_to_a", "quick_split"):
                raise CommandError("op must be swap, a_to_b, b_to_a or quick_split")
            if op == "quick_split" and not caps.has("quick_split"):
                raise CommandError("quick split is not available for this radio")
            self._not_while_transmitting()
            await self.driver.vfo_op(op)
        elif typ == "split":
            if not caps.has("split"):
                raise CommandError("split is not available for this radio")
            on = msg.get("on")
            if not isinstance(on, bool):
                raise CommandError("'on' must be boolean")
            self._not_while_transmitting()
            await self.driver.set_split(on)
        elif typ == "receivers":                                      # FTDX101: which of MAIN / SUB are listening
            if not caps.has("dual_receiver"):
                raise CommandError("this radio has no sub receiver")
            main, sub = msg.get("main"), msg.get("sub")
            if not isinstance(main, bool) or not isinstance(sub, bool):
                raise CommandError("'main' and 'sub' must be boolean")
            if not (main or sub):
                raise CommandError("at least one receiver must stay on")
            self._not_while_transmitting()
            await self.driver.set_receivers(main, sub)
        elif typ == "tx_receiver":                                    # FTDX101: transmit on MAIN or on SUB (split when it differs from the one you listen to)
            if not caps.has("dual_receiver"):
                raise CommandError("this radio has no sub receiver")
            which = msg.get("receiver")
            if which not in ("main", "sub"):
                raise CommandError("receiver must be main or sub")
            self._not_while_transmitting()
            await self.driver.set_tx_receiver(which)
        elif typ == "active_receiver":                                # FTDX101: the receiver the radio's own dial and keys operate
            if not caps.has("dual_receiver"):
                raise CommandError("this radio has no sub receiver")
            which = msg.get("receiver")
            if which not in ("main", "sub"):
                raise CommandError("receiver must be main or sub")
            await self.driver.set_active_receiver(which)
        elif typ == "set_mode":
            mode = msg.get("mode")
            if mode not in caps.modes():
                raise CommandError("unsupported mode")
            await self.driver.set_mode(mode)
        elif typ == "freedv":
            if msg.get("afc") == "reset":                            # "start the tuning search again"
                if self.audio:
                    self.audio.freedv_afc_reset()
                return None
            on, mode = msg.get("on"), msg.get("mode")
            if not isinstance(on, bool) or (mode is not None and mode not in FREEDV_MODES):
                raise CommandError("freedv needs on (true/false) and optionally mode " + ", ".join(FREEDV_MODES))
            if not self.audio:
                raise CommandError("audio is not available")
            self._not_while_transmitting()
            try:
                self.audio.set_freedv(on, mode)
            except AudioUnavailable as e:
                raise CommandError(str(e)) from None
            self.auth.audit("freedv_on" if on else "freedv_off", session.username, None, self.audio.freedv_mode if on else "")
            if on:
                self._schedule_widen()
        elif typ == "ftx":
            on, mode = msg.get("on"), msg.get("mode")
            if not isinstance(on, bool) or (mode is not None and mode not in ("FT8", "FT4")):
                raise CommandError("ftx needs on (true/false) and optionally mode FT8 or FT4")
            if not self.ftx or not self.audio:
                raise CommandError("FT8/FT4 is not available")
            try:
                await self.ftx.set_on(on, mode)
            except (ValueError, AudioUnavailable) as e:
                raise CommandError(str(e)) from None
            self.auth.audit("ftx_on" if on else "ftx_off", session.username, None, self.ftx.mode if on else "")
        elif typ == "ftx_tx":
            if not self.ftx:
                raise CommandError("FT8/FT4 is not available")
            text, f0, parity, reps = msg.get("text"), msg.get("freq"), msg.get("parity"), msg.get("repeats")
            if not isinstance(text, str) or not 1 <= len(text) <= 40 or isinstance(f0, bool) or not isinstance(f0, (int, float)) or isinstance(parity, bool) or not isinstance(parity, int):
                raise CommandError("ftx_tx needs text, freq (Hz) and parity (0 or 1)")
            if reps is not None and (isinstance(reps, bool) or not isinstance(reps, int)):
                raise CommandError("repeats must be a number")
            try:
                return self.ftx.set_tx(conn_id, text, float(f0), parity, reps)
            except ValueError as e:
                raise CommandError(str(e)) from None
        elif typ == "ftx_halt":
            if self.ftx:
                await self.ftx.halt("halted by the operator")
        elif typ == "sstv":
            on = msg.get("on")
            if not isinstance(on, bool):
                raise CommandError("sstv needs on (true/false)")
            if not self.sstv or not self.audio or not self.audio.available:
                raise CommandError("SSTV is not available (no audio)")
            try:
                await self.sstv.set_on(on)
            except (ValueError, AudioUnavailable) as e:
                raise CommandError(str(e)) from None
            self.auth.audit("sstv_on" if on else "sstv_off", session.username, None)
            if on:
                self._schedule_widen()
        elif typ == "sstv_halt":
            if self.sstv:
                await self.sstv.halt("halted by the operator")
        elif typ == "cw":
            on = msg.get("on")
            if not isinstance(on, bool):
                raise CommandError("cw needs on (true/false)")
            if not self.cw or not self.audio or not self.audio.available:
                raise CommandError("CW is not available (no audio)")
            try:
                await self.cw.set_on(on)
            except (ValueError, AudioUnavailable) as e:
                raise CommandError(str(e)) from None
            self.auth.audit("cw_on" if on else "cw_off", session.username, None)
        elif typ == "cw_set":
            if not self.cw:
                raise CommandError("CW is not available")
            pitch, wpm = msg.get("pitch"), msg.get("wpm")
            if pitch is not None and (isinstance(pitch, bool) or not isinstance(pitch, (int, float)) or not 300 <= pitch <= 1100):
                raise CommandError("pitch must be 300..1100 Hz (or null for automatic)")
            if wpm is not None and (isinstance(wpm, bool) or not isinstance(wpm, (int, float)) or not 5 <= wpm <= 60):
                raise CommandError("speed must be 5..60 WPM (or null for automatic)")
            self.cw.set_params(pitch, wpm)
        elif typ == "cw_clear":
            if self.cw:
                self.cw.clear()
        elif typ == "cw_send":
            if not self.cw:
                raise CommandError("CW is not available")
            text = msg.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 400:
                raise CommandError("cw_send needs text (1..400 characters)")
            try:
                r = await self.cw.send(conn_id, text)
            except ValueError as e:
                raise CommandError(str(e)) from None
            self.auth.audit("cw_send", session.username, None, r["text"][:80])
            return r
        elif typ == "cw_keyer":
            if not self.cw or not self.cw.can_send():
                raise CommandError("this radio cannot send CW from the keyboard")
            sp, bi = msg.get("speed"), msg.get("break_in")
            if sp is not None and (isinstance(sp, bool) or not isinstance(sp, int) or not 4 <= sp <= 60):
                raise CommandError("speed must be 4..60 WPM")
            if bi is not None and not isinstance(bi, bool):
                raise CommandError("break_in must be true or false")
            try:
                return await self.cw.set_keyer(sp, bi)
            except Exception as e:
                raise CommandError(str(e)) from None
        elif typ == "rtty":
            on = msg.get("on")
            if not isinstance(on, bool):
                raise CommandError("rtty needs on (true/false)")
            if not self.rtty or not self.audio or not self.audio.available:
                raise CommandError("RTTY is not available (no audio)")
            try:
                await self.rtty.set_on(on)
            except (ValueError, AudioUnavailable) as e:
                raise CommandError(str(e)) from None
            self.auth.audit("rtty_on" if on else "rtty_off", session.username, None)
        elif typ == "rtty_set":
            if not self.rtty:
                raise CommandError("RTTY is not available")
            try:
                self.rtty.set_params(**{k: msg[k] for k in ("baud", "shift", "lo", "rev", "usos") if k in msg})
            except (ValueError, TypeError) as e:
                raise CommandError(str(e)) from None
        elif typ == "rtty_clear":
            if self.rtty:
                self.rtty.clear()
        elif typ == "rtty_send":
            if not self.rtty:
                raise CommandError("RTTY is not available")
            text = msg.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 400:
                raise CommandError("rtty_send needs text (1..400 characters)")
            try:
                r = await self.rtty.send(conn_id, text)
            except ValueError as e:
                raise CommandError(str(e)) from None
            self.auth.audit("rtty_send", session.username, None, r["text"][:80])
            return r
        elif typ == "rtty_halt":
            if self.rtty:
                await self.rtty.halt("halted by the operator")
        elif typ == "cw_halt":
            if self.cw:
                await self.cw.halt("halted by the operator")
        elif typ == "set_level":
            name = msg.get("name")
            rng = caps.level_range(name) if isinstance(name, str) else None
            if not rng:
                raise CommandError("unsupported level")
            await self.driver.set_level(name, _int(msg.get("value"), *rng))
        elif typ == "set_control":
            name = msg.get("name")
            spec = controls.SPEC_BY_NAME.get(name) if isinstance(name, str) else None
            if not spec or not caps.has(spec["feature"]):
                raise CommandError("unsupported control")
            try:
                value = controls.coerce(spec, msg.get("value"))
            except ValueError as e:
                raise CommandError(f"{name}: {e}") from None
            receiver = msg.get("receiver", "main")                    # FTDX101: the same control for the SUB receiver
            if receiver not in ("main", "sub") or (receiver == "sub" and not caps.has("dual_receiver")):
                raise CommandError("receiver must be main (or sub on a dual-receiver radio)")
            await self.driver.set_control(name, value, receiver)
        elif typ == "set_band":
            band = msg.get("band")
            if band not in caps.data.get("bands", {}).get("list", []):
                raise CommandError("unsupported band")
            self._not_while_transmitting()
            await self.driver.set_band(band)
        elif typ == "memory_select":
            ch = _int(msg.get("channel"), 1)
            if not 1 <= ch <= 99:
                raise CommandError("memory channel must be 1..99")
            self._not_while_transmitting()
            log.info("memory_select: the page asked for channel %s (%s)", ch, session.username)
            await self.driver.memory_select(ch)
            self.auth.audit("memory_select", session.username, None)
        elif typ == "memory_vfo":
            self._not_while_transmitting()
            await self.driver.memory_to_vfo()
        elif typ == "power_off":
            if session.role != "admin":
                raise CommandError("admin only")
            if msg.get("confirm") is not True:
                raise CommandError("confirmation required")
            if not caps.has("power_off_cat"):
                raise CommandError("power off not supported by this radio")
            if self.guard.keyed:
                raise CommandError("refusing to power off while transmitting")
            self.auth.audit("power_off", session.username, None)
            await self.driver.power_off()
        elif typ == "tune":
            if not caps.has("tuner_tune"):
                raise CommandError("tune is not available for this radio")
            try:
                await self.guard.start_tune(conn_id)
            except TxRefused as e:
                raise CommandError(str(e)) from None
            self.auth.audit("tune_start", session.username, None)
        elif typ == "tune_stop":
            try:
                await self.guard.stop_tune(conn_id)
            except TxRefused as e:
                raise CommandError(str(e)) from None
            self.auth.audit("tune_stop", session.username, None)
        elif typ == "ptt":
            on = msg.get("on")
            if not isinstance(on, bool):
                raise CommandError("'on' must be boolean")
            if not on and self.audio and self.guard.keyed and self.guard.owner == conn_id:
                try:
                    await self.audio.drain_freedv_tx()                    # FreeDV: let the last tones reach the radio before it is unkeyed
                except Exception:
                    log.exception("FreeDV transmit drain failed; releasing PTT anyway")
            test = on and msg.get("test") is True
            play = msg.get("play") if on else None
            if test and not (self.audio and self.audio.freedv_state().get("on")):
                raise CommandError("the level test needs FreeDV switched on")
            if play is not None and (test or not isinstance(play, str)):
                raise CommandError("play must be the id of a recording (and not together with the level test)")
            samples = None
            if play is not None:
                samples = self.audio.recorder.read(play) if self.audio and self.audio.recorder else None
                if samples is None:
                    raise CommandError("that recording no longer exists")
            if self.audio:
                self.audio.set_level_test(test)                          # a steady tone instead of the microphone, for setting the level (ALC / power meters)
                self.audio.arm_playback(samples, conn_id)                # or a recording of received audio, sent as the report
            try:
                await self.guard.request(conn_id, on)
            except TxRefused as e:
                if self.audio:
                    self.audio.set_level_test(False)
                    self.audio.arm_playback(None)
                raise CommandError(str(e)) from None
            self._broadcast({"t": "patch", "d": {"ptt_owner": self.guard.owner}})
            self.auth.audit("ptt_on" if on else "ptt_off", session.username, None)
        else:
            raise CommandError("unknown command")
        return None

    def _playback_done(self) -> None:
        """The recording that PTT was sending has ended: release PTT after a moment, so the last of it goes out and the operator need not catch the moment."""
        if self.guard.keyed and self.guard.owner is not None:
            asyncio.get_running_loop().create_task(self._release_after_playback(self.guard.owner))

    async def _release_after_playback(self, owner: str) -> None:
        await asyncio.sleep(0.4)
        if not (self.guard.keyed and self.guard.owner == owner):
            return
        try:
            if self.audio:
                await self.audio.drain_freedv_tx()
            await self.guard.request(owner, False)
        except Exception:
            log.exception("could not release PTT after the recording")
            return
        if self.audio:
            self.audio.arm_playback(None)
        self._broadcast({"t": "patch", "d": {"ptt_owner": self.guard.owner}})
        log.info("the recording has been sent; PTT released")

    def _save_eq(self) -> None:
        if self.eq_path is None or self.audio is None:
            return
        try:
            st = self.audio.eq_state()
            tmp = self.eq_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"rx": st["rx"], "tx": st["tx"], "dyn": self.audio.dyn_save(), "presets": self.eq_presets}), encoding="utf-8")
            tmp.replace(self.eq_path)
        except OSError:
            log.warning("could not save the equalizer settings", exc_info=True)

    def load_eq(self) -> None:
        """At start: the settings saved by the last session (a missing or damaged file just leaves the equalizers flat and off)."""
        if self.eq_path is not None and self.audio is not None:
            try:
                d = json.loads(self.eq_path.read_text(encoding="utf-8"))
                from .audio import eq as eqmod
                conv = lambda side: eqmod.from_old_gains(side["gains"]) if "gains" in side else side["bands"]       # the first version saved 8 fixed gains
                self.audio.set_eq(rx_on=bool(d["rx"]["on"]), tx_on=bool(d["tx"]["on"]), rx=conv(d["rx"]), tx=conv(d["tx"]),
                                  rx_cut=d["rx"].get("cut", {"low": 0, "high": 0}), tx_cut=d["tx"].get("cut", {"low": 0, "high": 0}))
                if "dyn" in d:
                    self.audio.set_dyn(d["dyn"])
                for name, p in (d.get("presets") or {}).items():                  # the saved sets (an entry that no longer validates is dropped)
                    try:
                        self.eq_presets[str(name)] = {"bands": eqmod.clean_bands(p["bands"]), "cut": eqmod.clean_cut(p.get("cut", {}))}
                    except (ValueError, KeyError, TypeError, AttributeError):
                        pass
            except (OSError, ValueError, KeyError, TypeError):
                log.info("no usable saved equalizer settings; starting flat and off")
        if self.audio is not None:
            self.extra["eq"] = self.audio.eq_state()
            self.extra["dyn"] = self.audio.dyn_state()
            self.extra["eq_presets"] = self.eq_presets

    def _not_while_transmitting(self) -> None:
        """Changing which VFO transmits, or swapping VFOs, mid-transmission would move the signal: refuse."""
        if self.guard.keyed or self.driver.state.get("tx"):
            raise CommandError("not while transmitting")

    async def _lease_op(self, typ: str, conn: str, session: Session, msg: dict):
        if typ == "request_control":
            return await self.lease.request(conn, session.username, session.role, session.trusted)
        if typ == "respond_control":
            if not isinstance(msg.get("accept"), bool):
                raise CommandError("'accept' must be boolean")
            await self.lease.respond(conn, msg["accept"])
        elif typ == "release_control":
            await self.lease.release(conn)
        elif typ == "force_control":
            if session.role != "admin":
                raise CommandError("admin only")
            await self.lease.force(conn, session.username)
        return None


# ------------------------------------------------------------------ middleware
def _session(request: web.Request) -> Session | None:
    return request.app[K_AUTH].get_session(request.cookies.get(COOKIE))


def _set_cookie(request: web.Request, resp: web.Response, sid: str) -> None:
    resp.set_cookie(COOKIE, sid, httponly=True, samesite="Strict", secure=is_https(request), path="/",
                    max_age=7 * 24 * 3600)


@web.middleware
async def security_headers(request, handler):
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    )
    if is_https(request):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    else:
        # Static app files: always revalidate (ETag), so an upgrade is never masked by a stale cached module.
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


@web.middleware
async def api_auth(request, handler):
    path = request.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        s = _session(request)
        if not s:
            raise web.HTTPUnauthorized(text="login required")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            tok = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(tok, s.csrf):
                raise web.HTTPForbidden(text="bad CSRF token")
        request[K_SESSION] = s
    return await handler(request)


# ------------------------------------------------------------------- handlers
async def status(request):
    return web.json_response({"version": __version__, "build": BUILD, "setup_required": request.app[K_AUTH].user_count() == 0})


async def _login_response(request, auth: AuthStore, username: str, password: str):
    ip = client_ip(request)
    wait = auth.retry_after(ip, username)
    if wait > 0:
        raise web.HTTPTooManyRequests(text=f"too many attempts; retry in {int(wait) + 1}s")
    res = await asyncio.get_running_loop().run_in_executor(None, auth.login, username, password, ip)
    if not res:
        raise web.HTTPUnauthorized(text="invalid credentials")
    sid, s = res
    resp = web.json_response({"user": {"name": s.username, "role": s.role}, "csrf": s.csrf})
    _set_cookie(request, resp, sid)
    return resp


async def login(request):
    b = await read_json(request)
    u, p = b.get("username"), b.get("password")
    if not isinstance(u, str) or not isinstance(p, str):
        raise web.HTTPBadRequest(text="username and password required")
    return await _login_response(request, request.app[K_AUTH], u, p)


async def setup(request):
    auth = request.app[K_AUTH]
    if auth.user_count() != 0:
        raise web.HTTPForbidden(text="already configured")
    b = await read_json(request)
    u, p = b.get("username"), b.get("password")
    if not isinstance(u, str) or not isinstance(p, str):
        raise web.HTTPBadRequest(text="username and password required")
    try:
        await asyncio.get_running_loop().run_in_executor(None, auth.create_user, u, p, "admin")
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    auth.audit("admin_created", u, client_ip(request))
    return await _login_response(request, auth, u, p)


async def logout(request):
    request.app[K_AUTH].logout(request.cookies.get(COOKIE, ""))
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


async def me(request):
    s = request[K_SESSION]
    return web.json_response({"user": {"name": s.username, "role": s.role}, "csrf": s.csrf})


def _origin_ok(request: web.Request, allowed: list[str]) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return False
    if origin in allowed:
        return True
    return urlsplit(origin).netloc == request.headers.get("Host")


async def ws_handler(request):
    app = request.app
    s = _session(request)
    if not s:
        raise web.HTTPUnauthorized(text="login required")
    if not _origin_ok(request, app[K_CFG]["server"]["allowed_origins"]):
        raise web.HTTPForbidden(text="origin not allowed")
    hub: Hub = app[K_HUB]
    if len(hub.clients) >= MAX_WS_CLIENTS:
        raise web.HTTPServiceUnavailable(text="too many connected clients")
    ws = web.WebSocketResponse(heartbeat=10, max_msg_size=4096)
    await ws.prepare(request)
    conn_id = secrets.token_hex(8)
    sid = request.cookies.get(COOKIE, "")
    hub.clients[conn_id] = Client(ws, s, client_ip(request), sid)
    if s.role in ("operator", "admin"):
        hub.cancel_mic_reset()
    bucket, strikes = TokenBucket(CMD_RATE, CMD_BURST), 0
    log.info("ws connect user=%s conn=%s", s.username, conn_id)
    try:
        await ws.send_str(json.dumps(hub.snapshot(s, conn_id)))
        await hub.lease.on_connect(conn_id, s.username, s.role)
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                continue
            mid = None
            try:
                msg = json.loads(m.data)
                if not isinstance(msg, dict):
                    raise CommandError("bad message")
                mid = msg.get("id")
                # Session may have been revoked/expired, or the role changed, since connect.
                s = app[K_AUTH].get_session(sid)
                if not s:
                    await ws.close(code=4401, message=b"session expired")
                    break
                hub.clients[conn_id].session = s
                if msg.get("type") != "ptt_hb" and not bucket.allow():
                    strikes += 1
                    if strikes > MAX_STRIKES:
                        await ws.close(code=4429, message=b"rate limit")
                        break
                    raise CommandError("rate limited; slow down")
                result = await hub.handle(conn_id, s, msg)
                if mid is not None:
                    ack = {"t": "ack", "id": mid, "ok": True}
                    if result:
                        ack["result"] = result
                    await ws.send_str(json.dumps(ack))
            except (CommandError, json.JSONDecodeError) as e:
                await ws.send_str(json.dumps({"t": "ack", "id": mid, "ok": False, "error": str(e)}))
            except Exception:
                log.exception("command failed")
                await ws.send_str(json.dumps({"t": "ack", "id": mid, "ok": False, "error": "internal error"}))
    finally:
        hub.clients.pop(conn_id, None)
        if hub.ftx:
            hub.ftx.conn_gone(conn_id)
        await app[K_GUARD].release_owner(conn_id)   # never leave TX keyed by a dead connection
        if hub.scanner.owner == conn_id:
            await hub.scanner.stop("the operator left")
        if app[K_AUDIO]:
            app[K_AUDIO].set_level_test(False)
            app[K_AUDIO].arm_playback(None)
        if app[K_AUDIO]:
            await app[K_AUDIO].close_conn(conn_id)
        await hub.lease.on_disconnect(conn_id)
        hub.schedule_mic_reset()
        log.info("ws disconnect conn=%s", conn_id)
    return ws


async def audio_offer(request):
    app, s = request.app, request[K_SESSION]
    audio: AudioService = app[K_AUDIO]
    b = await read_json(request)
    sdp, typ, conn = b.get("sdp"), b.get("type"), b.get("conn")
    if not (isinstance(sdp, str) and typ == "offer" and isinstance(conn, str)) or len(sdp) > 20000:
        raise web.HTTPBadRequest(text="sdp, type=offer and conn required")
    owner = app[K_HUB].clients.get(conn)
    if not owner or owner.session.user_id != s.user_id:
        raise web.HTTPForbidden(text="conn does not belong to this session")
    try:
        return web.json_response(await audio.offer(sdp, typ, user=s.username, role=s.role, conn_id=conn))
    except AudioUnavailable as e:
        raise web.HTTPServiceUnavailable(text=str(e)) from None


async def audio_ws(request):
    """Audio over a WebSocket: the fallback for networks that block WebRTC (UDP). Binary Opus packets both ways; see AudioService.ws_session."""
    app = request.app
    s = _session(request)
    if not s:
        raise web.HTTPUnauthorized(text="login required")
    if not _origin_ok(request, app[K_CFG]["server"]["allowed_origins"]):
        raise web.HTTPForbidden(text="origin not allowed")
    conn = request.query.get("conn", "")
    owner = app[K_HUB].clients.get(conn)
    if not owner or owner.session.user_id != s.user_id:
        raise web.HTTPForbidden(text="conn does not belong to this session")
    audio: AudioService = app[K_AUDIO]
    if not audio.available:
        raise web.HTTPServiceUnavailable(text=audio.reason)
    ws = web.WebSocketResponse(heartbeat=10, max_msg_size=4096)
    await ws.prepare(request)
    try:
        await audio.ws_session(ws, user=s.username, role=s.role, conn_id=conn)
    except AudioUnavailable as e:
        await ws.send_json({"ok": False, "error": str(e)})
    finally:
        await ws.close()
    return ws


async def freedv_info(request):
    """FreeDV state and the preset channel list (any signed-in user may look; administrators change the list in the config)."""
    from .audio import freedv_tune as _tune
    from .audio import rade as _rade
    app = request.app
    fd, audio = app[K_CFG]["freedv"], app[K_AUDIO]
    st = audio.freedv_state()
    return web.json_response({**st, "tx_level_db": fd["tx_level_db"], "data_mode": bool(fd.get("data_mode", False)), "auto_width": bool(fd.get("auto_width", True)), "tx_bpf": _rade.tx_bpf_available(), "install_rade": "sudo /opt/radio-remote/current/scripts/install_rade.sh",
                          "rade_installable": _rade.installable(), "arch": platform.machine(),
                          "rade_path": _rade.loaded_path(),
                          "tune": {m: {"centre": p["centre"], "width": p["width"]} for m, p in _tune.PARAMS.items()}, "channels": [
        {"name": n, "hz": int(h), "mode": m} for n, h, m in (c.split("|") for c in fd["channels"])]})


async def freedv_reporter_status(request):
    """FreeDV Reporter: the connection state and the stations it lists, those near our frequency first (any signed-in user)."""
    app = request.app
    return web.json_response(app[K_REPORTER].status(near_hz=app[K_DRIVER].state.get("frequency") or 0))


async def rtty_info(request):
    """RTTY: the state, the standard frequencies, the settings and the decoded text of the last while (any signed-in user)."""
    return web.json_response(request.app[K_RTTY].state())


async def cw_info(request):
    """CW: the state, the standard frequencies and the decoded text of the last while (any signed-in user)."""
    eng = request.app[K_CW]
    await eng.refresh_keyer()                                      # the radio's own keyer speed and break-in, for the screen
    return web.json_response(eng.state())


async def sstv_info(request):
    """SSTV: the state, the modes, the standard frequencies, the settings and the saved pictures (any signed-in user)."""
    return web.json_response(request.app[K_SSTV].state())


async def sstv_image(request):
    name = request.match_info["name"]
    p = request.app[K_SSTV].image_path(name)
    if p is None:
        raise web.HTTPNotFound(text="no such picture")
    headers = {"Content-Type": "image/png", "Cache-Control": "private, max-age=3600"}
    if request.query.get("download") == "1":
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return web.FileResponse(p, headers=headers)


async def sstv_current(request):
    png = request.app[K_SSTV].current_png()
    if png is None:
        raise web.HTTPNotFound(text="no picture is being received")
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})


async def sstv_image_delete(request):
    s = _operator(request)
    name = request.match_info["name"]
    if not request.app[K_SSTV].delete_image(name):
        raise web.HTTPNotFound(text="no such picture")
    request.app[K_AUTH].audit("sstv_delete", s.username, client_ip(request), name)
    request.app[K_HUB]._broadcast({"t": "sstv", "k": "deleted", "name": name})
    return web.json_response({"ok": True})


async def sstv_tx(request):
    """Transmit a picture. The body is the raw RGB bytes (width x height x 3) of the picture the page prepared for the chosen mode (?mode=<key>&conn=<this page's connection id>). Only the
    page that has control of the radio may do it; the transmit protection (permission, heartbeat, time limit) applies as for every transmission."""
    from .sstv_engine import MAX_UPLOAD
    s = _operator(request)
    app = request.app
    conn = request.query.get("conn", "")
    c = app[K_HUB].clients.get(conn)
    if c is None or c.session.user_id != s.user_id or not app[K_HUB].lease.holds(conn):
        raise web.HTTPForbidden(text="only the page that has control of the radio can transmit")
    n = request.content_length or 0
    if n > MAX_UPLOAD:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=n)
    body = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        body += chunk
        if len(body) > MAX_UPLOAD:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=len(body))
    try:
        r = await app[K_SSTV].transmit(conn, bytes(body), request.query.get("mode", ""))
    except ValueError as e:
        raise web.HTTPConflict(text=str(e)) from None
    app[K_AUTH].audit("sstv_tx", s.username, client_ip(request), r["mode"])
    return web.json_response(r)


async def ftx_info(request):
    """FT8/FT4: the state, the settings, the standard frequencies and the recent decodes (any signed-in user)."""
    app = request.app
    st = app[K_FTX].state()
    u = app[K_CFG]["upload"]
    st["upload"] = {"qrz": bool(u["qrz_key"]), "eqsl": bool(u["eqsl_user"] and u["eqsl_password"]), "clublog": bool(u["clublog_email"] and u["clublog_password"]),
                    "clublog_api": bool(u["clublog_api"]), "auto": {"qrz": u["auto_qrz"], "eqsl": u["auto_eqsl"], "clublog": u["auto_clublog"]}}
    st["log_count"] = len(app[K_QSOLOG].items)
    return web.json_response(st)


def _operator(request):
    if request[K_SESSION].role == "viewer":
        raise web.HTTPForbidden(text="viewers cannot change the log")
    return request[K_SESSION]


async def ftx_log_list(request):
    return web.json_response({"log": request.app[K_QSOLOG].recent(300), "total": len(request.app[K_QSOLOG].items)})


async def ftx_log_adif(request):
    ids = request.query.get("id")
    body = request.app[K_QSOLOG].adif(__version__, ids.split(",") if ids else None)
    return web.Response(text=body, content_type="text/plain", headers={"Content-Disposition": 'attachment; filename="radio-remote-ft8.adi"', "Cache-Control": "no-store"})


async def ftx_log_add(request):
    """Log a contact (the page sends it when a QSO completes, or the operator adds it by hand). Auto-upload follows when it is switched on."""
    from . import qsolog
    s = _operator(request)
    app = request.app
    b = await read_json(request)
    conn = b.pop("conn", None)
    if conn is not None:                                   # the page says which of the user's connections it is: only the one that has control of the radio keeps the log
        c = app[K_HUB].clients.get(conn) if isinstance(conn, str) else None
        if c is None or c.session.user_id != s.user_id or not app[K_HUB].lease.holds(conn):
            raise web.HTTPForbidden(text="only the page that has control of the radio can log contacts")
    b.setdefault("station_callsign", config.ftx_callsign(app[K_CFG]))
    b.setdefault("my_gridsquare", config.ftx_grid(app[K_CFG]))
    dup = app[K_QSOLOG].duplicate_of(b)
    if dup is not None:                                    # the same contact reported twice: say so and keep the one in the log
        log.info("FT8/FT4 log: %s (%s) is already logged, not adding it again", dup["call"], dup["band"])
        return web.json_response({"q": dup, "duplicate": True}, status=200)
    try:
        q = app[K_QSOLOG].add(b)
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    app[K_AUTH].audit("ftx_logged", s.username, client_ip(request), f"{q['call']} {q['mode']} {q['band']}")
    app[K_HUB]._broadcast({"t": "ftx", "k": "logged", "q": q})
    u = app[K_CFG]["upload"]
    for svc in qsolog.SERVICES:
        if u.get(f"auto_{svc}"):
            asyncio.get_running_loop().create_task(_upload_one(app, svc, q))
    return web.json_response({"q": q}, status=201)


async def _upload_one(app, service: str, q: dict) -> tuple[bool, str]:
    from . import uploaders
    ok, msg = await uploaders.upload(service, app[K_CFG], q, config.ftx_callsign(app[K_CFG]))
    app[K_QSOLOG].mark(q["id"], service, ok, msg)
    app[K_HUB]._broadcast({"t": "ftx", "k": "uploaded", "id": q["id"], "service": service, "ok": ok, "message": msg})
    log.info("upload %s %s: %s", service, q["call"], msg)
    return ok, msg


async def ftx_log_delete(request):
    s = _operator(request)
    if not request.app[K_QSOLOG].delete(request.match_info["id"]):
        raise web.HTTPNotFound(text="no such contact")
    request.app[K_AUTH].audit("ftx_log_delete", s.username, client_ip(request), request.match_info["id"])
    return web.json_response({"ok": True})


async def ftx_upload(request):
    """Upload logged contacts (one id, or "all" not yet sent) to one service: qrz, eqsl or clublog."""
    from . import qsolog
    _operator(request)
    app = request.app
    b = await read_json(request)
    svc, which = b.get("service"), b.get("id", "all")
    if svc not in qsolog.SERVICES:
        raise web.HTTPBadRequest(text="service must be qrz, eqsl or clublog")
    if which == "all":
        todo = app[K_QSOLOG].pending(svc)[:50]
    else:
        q = app[K_QSOLOG].get(which) if isinstance(which, str) else None
        if q is None:
            raise web.HTTPNotFound(text="no such contact")
        todo = [q]
    done, failed, last = 0, 0, ""
    for q in todo:
        ok, last = await _upload_one(app, svc, q)
        done += ok
        failed += not ok
        if not ok and ("not set" in last or "needs an application" in last):
            break                                               # credentials missing: no point trying the rest
    return web.json_response({"sent": done, "failed": failed, "message": last or "nothing to upload", "left": len(app[K_QSOLOG].pending(svc))})


async def audio_status(request):
    return web.json_response(request.app[K_AUDIO].status())


async def recordings_list(request):
    rec = request.app[K_AUDIO].recorder if request.app[K_AUDIO] else None
    return web.json_response({"recordings": rec.list() if rec else []})


async def recording_file(request):
    """One saved clip as a WAV file (the page's player streams it; ?download=1 saves it). Any signed-in user may listen. {clip}.raw.wav is the radio's own audio before FreeDV
    decoding (kept only for clips recorded with FreeDV on), for analysis."""
    rec = request.app[K_AUDIO].recorder if request.app[K_AUDIO] else None
    p = rec.path(request.match_info["clip"], raw=request.path.endswith(".raw.wav")) if rec else None
    if p is None:
        raise web.HTTPNotFound(text="no such recording")
    headers = {"Content-Type": "audio/wav", "Cache-Control": "no-store"}
    if request.query.get("download") == "1":
        headers["Content-Disposition"] = f'attachment; filename="{p.name}"'
    return web.FileResponse(p, headers=headers)


async def recording_info(request):
    """One clip's description as JSON, including what the live FreeDV decoder believed second by second (sync, SNR, tuning offset, slowest frame)."""
    rec = request.app[K_AUDIO].recorder if request.app[K_AUDIO] else None
    clip = request.match_info["clip"]
    item = next((i for i in rec.list() if i["id"] == clip), None) if rec else None
    if item is None:
        raise web.HTTPNotFound(text="no such recording")
    headers = {"Content-Disposition": f'attachment; filename="{clip}.json"'} if request.query.get("download") == "1" else {}
    return web.json_response(item, headers=headers)


async def recording_delete(request):
    if request[K_SESSION].role == "viewer":
        raise web.HTTPForbidden(text="viewers cannot delete recordings")
    app = request.app
    rec = app[K_AUDIO].recorder if app[K_AUDIO] else None
    if rec is None or not rec.delete(request.match_info["clip"]):
        raise web.HTTPNotFound(text="no such recording")
    app[K_HUB].push({"recordings": rec.list()})
    app[K_AUTH].audit("rec_delete", request[K_SESSION].username, client_ip(request), request.match_info["clip"])
    return web.json_response({"ok": True})


async def memories(request):
    """The radio's stored channels (read from the radio, cached by the driver; ?refresh=1 re-reads). Any signed-in user may look."""
    refresh = request.query.get("refresh") == "1"
    log.info("memory list requested (refresh=%s) by %s", refresh, request[K_SESSION].username)
    try:
        items = await request.app[K_DRIVER].memory_channels(refresh=refresh)
    except RadioError as e:
        log.warning("memory list failed: %s", e)
        raise web.HTTPConflict(text=str(e)) from None
    return web.json_response({"channels": items})


async def memory_write(request):
    """Add or edit ONE memory channel of the radio (FT-991A). Administrators only, with a confirmation, never while the radio transmits. The driver reads the whole list before and after and
    proves the write (see FT991A.memory_write); it takes about 20 seconds."""
    from .common import client_ip, read_json, require_admin
    admin_user = require_admin(request)
    app = request.app
    try:
        ch = int(request.match_info["channel"])
    except ValueError:
        raise web.HTTPBadRequest(text="memory channel must be 1..99") from None
    if not 1 <= ch <= 99:
        raise web.HTTPBadRequest(text="memory channel must be 1..99")
    body = await read_json(request)
    if body.get("confirm") is not True:
        raise web.HTTPBadRequest(text="confirmation required: this overwrites the channel stored in the radio")
    hz, mode, tone, shift, name = body.get("frequency"), body.get("mode"), body.get("tone_mode", "off"), body.get("shift", "simplex"), body.get("name", "")
    tone_hz, dcs_code = body.get("tone_hz"), body.get("dcs_code")
    if isinstance(hz, bool) or not isinstance(hz, int) or not all(isinstance(v, str) for v in (mode, tone, shift, name)):
        raise web.HTTPBadRequest(text="frequency (Hz, a whole number), mode, tone_mode, shift and name are needed")
    if (tone_hz is not None and (isinstance(tone_hz, bool) or not isinstance(tone_hz, (int, float)))) or (dcs_code is not None and not isinstance(dcs_code, str)):
        raise web.HTTPBadRequest(text="tone_hz must be a number and dcs_code a text")
    guard, driver = app[K_GUARD], app[K_DRIVER]
    if guard.keyed:
        raise web.HTTPConflict(text="not while transmitting")
    log.warning("memory_write: %s asked to write channel %s: %s Hz %s tone=%s %s%s shift=%s name=%r", admin_user.username, ch, hz, mode, tone, tone_hz or "", dcs_code or "", shift, name)
    try:
        item = await driver.memory_write(ch, hz, mode, tone, shift, name, tone_hz=tone_hz, dcs_code=dcs_code)
    except RadioError as e:
        app[K_AUTH].audit("memory_write_failed", admin_user.username, client_ip(request), f"ch {ch}: {str(e)[:150]}")
        raise web.HTTPConflict(text=str(e)) from None
    app[K_AUTH].audit("memory_write", admin_user.username, client_ip(request), f"ch {ch}: {hz} Hz {mode} tone={tone} {tone_hz or ''}{dcs_code or ''} shift={shift} name={name.strip()[:12]!r}")
    return web.json_response({"ok": True, "channel": item})


async def memory_tone(request):
    """The tone mode, tone frequency / DCS code and shift a stored channel holds (the editor pre-fills its form with it). The radio shows them only for a recalled channel, so the driver recalls it
    briefly and puts the radio back. Administrators only: it moves the radio's front for a moment."""
    from .common import require_admin
    require_admin(request)
    try:
        ch = int(request.match_info["channel"])
    except ValueError:
        raise web.HTTPBadRequest(text="memory channel must be 1..99") from None
    if not 1 <= ch <= 99:
        raise web.HTTPBadRequest(text="memory channel must be 1..99")
    if request.app[K_GUARD].keyed:
        raise web.HTTPConflict(text="not while transmitting")
    try:
        return web.json_response(await request.app[K_DRIVER].memory_tone(ch))
    except RadioError as e:
        raise web.HTTPConflict(text=str(e)) from None


async def memory_delete(request):
    """Empty ONE memory channel (FT-991A): administrators only, with a confirmation, never while the radio transmits; proved like a write (see FT991A.memory_delete)."""
    from .common import client_ip, read_json, require_admin
    admin_user = require_admin(request)
    app = request.app
    try:
        ch = int(request.match_info["channel"])
    except ValueError:
        raise web.HTTPBadRequest(text="memory channel must be 1..99") from None
    if not 1 <= ch <= 99:
        raise web.HTTPBadRequest(text="memory channel must be 1..99")
    if (await read_json(request)).get("confirm") is not True:
        raise web.HTTPBadRequest(text="confirmation required: this empties the channel stored in the radio")
    if app[K_GUARD].keyed:
        raise web.HTTPConflict(text="not while transmitting")
    log.warning("memory_delete: %s asked to empty channel %s", admin_user.username, ch)
    try:
        await app[K_DRIVER].memory_delete(ch)
    except RadioError as e:
        app[K_AUTH].audit("memory_delete_failed", admin_user.username, client_ip(request), f"ch {ch}: {str(e)[:150]}")
        raise web.HTTPConflict(text=str(e)) from None
    app[K_AUTH].audit("memory_delete", admin_user.username, client_ip(request), f"ch {ch}")
    return web.json_response({"ok": True})


async def audio_devices(request):
    if request[K_SESSION].role != "admin":
        raise web.HTTPForbidden(text="admin only")
    from .audio import devices
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, devices.list_devices))


async def docs_redirect(request):
    raise web.HTTPFound("/docs/")


async def docs_index(request):
    return web.FileResponse(DOCS_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def index(request):
    return web.FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


# -------------------------------------------------------------------- factory
def create_app(cfg: dict, driver: RadioDriver | None = None, auth: AuthStore | None = None,
               config_path: str | Path | None = None, restart_hook=None,
               lease_timeout_s: float | None = None) -> web.Application:
    logs.install()
    app = web.Application(middlewares=[security_headers, api_auth], client_max_size=16 * 1024)
    driver = driver or create_driver(cfg)
    auth = auth or AuthStore(Path(cfg["storage"]["data_dir"]) / "radio-remote.db")
    guard = TxGuard(
        driver, allow_ptt=cfg["safety"]["allow_ptt"], max_tx_s=cfg["safety"]["tx_timeout_s"],
        heartbeat_timeout_s=cfg["safety"]["ptt_heartbeat_timeout_s"],
    )
    app[K_CFG], app[K_AUTH], app[K_DRIVER], app[K_GUARD] = cfg, auth, driver, guard
    if config_path is not None:
        app[K_CFGPATH] = Path(config_path)
    if restart_hook is not None:
        app[K_RESTART] = restart_hook
    app[K_HUB] = Hub(driver, guard, auth,
                     lease_timeout_s if lease_timeout_s is not None else cfg["safety"]["control_request_timeout_s"])
    from .audio import rade as _rade
    _rade.set_install_dir(Path(cfg["storage"]["data_dir"]) / "lib")           # BEFORE the audio service looks for the library: the copy installed from the page (newer) wins over /opt
    audio = AudioService(
        cfg["audio"], is_mock=driver.is_mock,
        tx_gate=lambda cid: guard.keyed and not guard.tuning and guard.owner == cid,      # TUNE sends a carrier only: no microphone or modem tones
        on_levels=app[K_HUB].push,
    )
    app[K_AUDIO] = app[K_HUB].audio = audio
    app[K_HUB].auto_width = lambda: bool(app[K_CFG]["freedv"].get("auto_width", True))
    app[K_HUB].eq_path = Path(cfg["storage"]["data_dir"]) / "eq.json"
    app[K_HUB].load_eq()
    from .audio.recorder import Recorder
    audio.recorder = Recorder(Path(cfg["storage"]["data_dir"]) / "recordings", on_change=app[K_HUB].push)    # the last five clips of received audio
    audio.on_playback_done = app[K_HUB]._playback_done
    app[K_HUB].extra.update(rec=audio.recorder.state(), recordings=audio.recorder.list())
    audio.set_freedv_params(cfg["freedv"]["mode"], cfg["freedv"]["tx_level_db"], freedv_callsign(cfg))
    app[K_HUB].ui = {"steps": cfg["ui"]["tuning_steps_hz"],
                     "meter": {"alc_full": cfg["ui"]["meter_alc_full"], "comp_full": cfg["ui"]["meter_comp_full"],
                               "swr_warn": cfg["ui"]["swr_warn"], "swr_raw_at_3": cfg["ui"]["swr_raw_at_3"]}}
    app[K_STARTED] = time.monotonic()
    app[K_UPDATES] = updates.UpdateChecker(lambda: app[K_CFG]["updates"])        # reads the live config: Admin > Config applies at once

    app[K_RIGCTL] = rigctl = RigctlServer(driver, lambda: app[K_CFG], lambda ev, detail: auth.audit(ev, None, None, detail))

    app[K_REPORTER] = reporter = FreeDVReporter(lambda: app[K_CFG], __version__, lambda: driver.state.get("frequency") or 0)
    audio.on_freedv_state = reporter.set_active
    audio.on_freedv_tx = reporter.set_transmitting
    app[K_HUB].reporter = reporter

    from .sstv_engine import SstvEngine
    from .cw_engine import CwEngine
    from .rtty_engine import RttyEngine
    from .ftx_engine import FtxEngine
    from .qsolog import QsoLog
    app[K_SSTV] = app[K_HUB].sstv = sstv_engine = SstvEngine(lambda: app[K_CFG], audio, app[K_HUB]._broadcast, lambda: driver.state.get("frequency") or 0, guard,
                                                              lambda cid: cid in app[K_HUB].clients, Path(cfg["storage"]["data_dir"]))
    app[K_CW] = app[K_HUB].cw = cw_engine = CwEngine(lambda: app[K_CFG], audio, app[K_HUB]._broadcast, lambda: driver.state.get("frequency") or 0, guard, lambda cid: cid in app[K_HUB].clients, driver)
    app[K_RTTY] = app[K_HUB].rtty = rtty_engine = RttyEngine(lambda: app[K_CFG], audio, app[K_HUB]._broadcast, lambda: driver.state.get("frequency") or 0, guard, lambda cid: cid in app[K_HUB].clients, driver)
    app[K_QSOLOG] = QsoLog(Path(cfg["storage"]["data_dir"]) / "ftx_log.jsonl")
    app[K_FTX] = app[K_HUB].ftx = ftx_engine = FtxEngine(lambda: app[K_CFG], audio, app[K_HUB]._broadcast, lambda: driver.state.get("frequency") or 0, guard,
                                                          lambda cid: cid in app[K_HUB].clients)

    watchdog = sdnotify.Watchdog(lambda: guard._task is not None and not guard._task.done())

    async def on_start(app):
        await driver.start()
        guard.start()
        try:
            await audio.start()
        except Exception:
            log.exception("audio failed to start; continuing without audio")
        log.info("started: radio=%s allow_ptt=%s", driver.caps.data["model"]["name"], guard.allow_ptt)
        watchdog.start()
        await rigctl.apply()
        reporter.start()
        if not driver.is_mock and not os.environ.get("RADIO_REMOTE_TESTING"):        # a simulated radio / the tests never phone home
            app[K_UPDATES].start()

    async def on_stop(app):
        await app[K_UPDATES].stop()
        app[K_HUB].cancel_mic_reset()
        await rigctl.stop()
        await reporter.stop()
        try:
            await ftx_engine.set_on(False)
        except Exception:
            log.exception("FT8/FT4 did not stop cleanly")
        try:
            await sstv_engine.set_on(False)
        except Exception:
            log.exception("SSTV did not stop cleanly")
        try:
            await rtty_engine.shutdown()
            await rtty_engine.set_on(False)
        except Exception:
            log.exception("RTTY did not stop cleanly")
        try:
            await cw_engine.shutdown()
            await cw_engine.set_on(False)
        except Exception:
            log.exception("CW did not stop cleanly")
        await watchdog.stop()
        await guard.stop()          # un-keys first
        await audio.stop()
        await driver.stop()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)

    app.router.add_get("/api/status", status)
    app.router.add_post("/api/setup", setup)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/me", me)
    admin.add_routes(app)
    app.router.add_post("/api/audio/offer", audio_offer)
    app.router.add_get("/api/audio/status", audio_status)
    app.router.add_get("/api/freedv", freedv_info)
    app.router.add_get("/api/freedv/reporter", freedv_reporter_status)
    app.router.add_get("/api/cw", cw_info)
    app.router.add_get("/api/rtty", rtty_info)
    app.router.add_get("/api/sstv", sstv_info)
    app.router.add_get("/api/sstv/current.png", sstv_current)
    app.router.add_get(r"/api/sstv/images/{name:[a-z0-9.-]+}", sstv_image)
    app.router.add_post(r"/api/sstv/images/{name:[a-z0-9.-]+}/delete", sstv_image_delete)
    app.router.add_post("/api/sstv/tx", sstv_tx)
    app.router.add_get("/api/ftx", ftx_info)
    app.router.add_get("/api/ftx/log", ftx_log_list)
    app.router.add_post("/api/ftx/log", ftx_log_add)
    app.router.add_get("/api/ftx/log.adi", ftx_log_adif)
    app.router.add_post("/api/ftx/log/{id:[0-9a-f]+}/delete", ftx_log_delete)
    app.router.add_post("/api/ftx/upload", ftx_upload)
    app.router.add_get("/ws/audio", audio_ws)
    app.router.add_get("/api/audio/devices", audio_devices)
    app.router.add_get("/api/recordings", recordings_list)
    app.router.add_get(r"/api/recordings/{clip:[0-9a-z]+}.wav", recording_file)
    app.router.add_get(r"/api/recordings/{clip:[0-9a-z]+}.raw.wav", recording_file)
    app.router.add_get(r"/api/recordings/{clip:[0-9a-z]+}.json", recording_info)
    app.router.add_post(r"/api/recordings/{clip:[0-9a-z]+}/delete", recording_delete)
    app.router.add_get("/api/memories", memories)
    app.router.add_post("/api/memories/{channel}", memory_write)
    app.router.add_get("/api/memories/{channel}/tone", memory_tone)
    app.router.add_post("/api/memories/{channel}/delete", memory_delete)
    app.router.add_get("/ws", ws_handler)
    if (DOCS_DIR / "index.html").is_file():
        app.router.add_get("/docs", docs_redirect)
        app.router.add_get("/docs/", docs_index)
        app.router.add_static("/docs", DOCS_DIR, follow_symlinks=False)        # documentation is public: no secrets in it
    app.router.add_get("/", index)
    if FRONTEND_DIR.exists():
        app.router.add_static("/", FRONTEND_DIR, show_index=False)
    return app
