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
from .common import (COOKIE, K_AUDIO, K_AUTH, K_CFG, K_CFGPATH, K_DRIVER, K_GUARD, K_HUB, K_RESTART, K_SESSION,
                     K_RIGCTL, K_STARTED, K_UPDATES, CommandError, client_ip, is_https, read_json)
from .lease import ControlLease, LeaseError
from .radio import controls
from .radio.base import METER_FIELDS, RadioDriver, RadioError
from .radio.registry import create_driver
from .config import FREEDV_MODES
from .ratelimit import TokenBucket
from .rigctl import RigctlServer
from .safety import TxGuard, TxRefused

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

    def _on_change(self, changed: dict) -> None:
        if "frequency" in changed:                                   # FreeDV keeps its software tuning when the dial moves a little
            old, self._last_freq = self._last_freq, changed["frequency"] or 0
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
                      "freedv": self.audio.freedv_state() if self.audio else {"available": False, "reason": "no audio service", "on": False}},
            "state": {**self.driver.state, **self.extra, "ptt_owner": self.guard.owner},
            "safety": {"ptt_permitted": self.guard.permitted, "tx_timeout_s": self.guard.max_tx_s},
        }

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
        if typ == "set_frequency":
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
            await self.driver.set_control(name, value)
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
            try:
                await self.guard.request(conn_id, on)
            except TxRefused as e:
                raise CommandError(str(e)) from None
            self._broadcast({"t": "patch", "d": {"ptt_owner": self.guard.owner}})
            self.auth.audit("ptt_on" if on else "ptt_off", session.username, None)
        else:
            raise CommandError("unknown command")
        return None

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
        await app[K_GUARD].release_owner(conn_id)   # never leave TX keyed by a dead connection
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
    return web.json_response({**st, "tx_level_db": fd["tx_level_db"], "install_rade": "sudo /opt/radio-remote/current/scripts/install_rade.sh",
                          "rade_installable": platform.machine() in ("aarch64", "arm64"), "arch": platform.machine(),
                          "rade_path": _rade.loaded_path(),
                          "tune": {m: {"centre": p["centre"], "width": p["width"]} for m, p in _tune.PARAMS.items()}, "channels": [
        {"name": n, "hz": int(h), "mode": m} for n, h, m in (c.split("|") for c in fd["channels"])]})


async def audio_status(request):
    return web.json_response(request.app[K_AUDIO].status())


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
    audio = AudioService(
        cfg["audio"], is_mock=driver.is_mock,
        tx_gate=lambda cid: guard.keyed and guard.owner == cid,
        on_levels=app[K_HUB].push,
    )
    app[K_AUDIO] = app[K_HUB].audio = audio
    audio.set_freedv_params(cfg["freedv"]["mode"], cfg["freedv"]["tx_level_db"])
    from .audio import rade as _rade
    _rade.set_install_dir(Path(cfg["storage"]["data_dir"]) / "lib")
    app[K_HUB].ui = {"steps": cfg["ui"]["tuning_steps_hz"],
                     "meter": {"alc_full": cfg["ui"]["meter_alc_full"], "comp_full": cfg["ui"]["meter_comp_full"],
                               "swr_warn": cfg["ui"]["swr_warn"], "swr_raw_at_3": cfg["ui"]["swr_raw_at_3"]}}
    app[K_STARTED] = time.monotonic()
    app[K_UPDATES] = updates.UpdateChecker(lambda: app[K_CFG]["updates"])        # reads the live config: Admin > Config applies at once

    app[K_RIGCTL] = rigctl = RigctlServer(driver, lambda: app[K_CFG], lambda ev, detail: auth.audit(ev, None, None, detail))

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
        if not driver.is_mock and not os.environ.get("RADIO_REMOTE_TESTING"):        # a simulated radio / the tests never phone home
            app[K_UPDATES].start()

    async def on_stop(app):
        await app[K_UPDATES].stop()
        app[K_HUB].cancel_mic_reset()
        await rigctl.stop()
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
    app.router.add_get("/ws/audio", audio_ws)
    app.router.add_get("/api/audio/devices", audio_devices)
    app.router.add_get("/api/memories", memories)
    app.router.add_get("/ws", ws_handler)
    if (DOCS_DIR / "index.html").is_file():
        app.router.add_get("/docs", docs_redirect)
        app.router.add_get("/docs/", docs_index)
        app.router.add_static("/docs", DOCS_DIR, follow_symlinks=False)        # documentation is public: no secrets in it
    app.router.add_get("/", index)
    if FRONTEND_DIR.exists():
        app.router.add_static("/", FRONTEND_DIR, show_index=False)
    return app
