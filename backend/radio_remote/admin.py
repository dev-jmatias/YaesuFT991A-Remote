"""Admin and account REST endpoints. Every route here is behind the session + CSRF middleware."""
from __future__ import annotations

import asyncio
import copy
import logging
import platform
import sys
import time
from pathlib import Path

from aiohttp import web

from . import __version__, config, sysinfo
from .common import (COOKIE, K_AUDIO, K_AUTH, K_CFG, K_CFGPATH, K_DRIVER, K_GUARD, K_HUB, K_RESTART, K_SESSION,
                     K_RIGCTL, K_STARTED, K_UPDATES, client_ip, read_json, require_admin)
from .logs import RING, scrub

log = logging.getLogger("admin")

# What the generic config form may change. Deliberately NOT here: safety.allow_ptt (it has its own endpoint, POST /api/admin/ptt,
# which asks for the administrator's password), server.* (a typo would lock you out of your own radio), storage.*.
EDITABLE = {
    "radio": {"model", "serial_port", "baud", "hamlib_model"},
    "audio": {"enabled", "backend", "input_device", "output_device", "rx_gain_db", "tx_gain_db",
              "opus_bitrate", "max_peers"},
    "safety": {"tx_timeout_s", "ptt_heartbeat_timeout_s", "control_request_timeout_s"},
    "ui": {"tuning_steps_hz", "meter_alc_full", "meter_comp_full", "swr_warn", "swr_raw_at_3"},
    "logging": {"level"},
    "updates": {"check"},
    "rigctl": {"enabled", "port", "allow", "set"},
    "freedv": {"mode", "tx_level_db", "channels"},
}
LOCKED = ["safety.allow_ptt", "server.host", "server.port", "server.allowed_origins", "storage.data_dir", "updates.repo"]
LIVE = {("logging", "level"), ("audio", "rx_gain_db"), ("audio", "tx_gain_db"), ("updates", "check"), ("rigctl", "enabled"), ("rigctl", "port"), ("rigctl", "allow"), ("rigctl", "set"), ("freedv", "mode"), ("freedv", "tx_level_db"), ("freedv", "channels")}


async def _run(fn, *a):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *a)


# ----------------------------------------------------------------------- users
async def list_users(request):
    require_admin(request)
    return web.json_response({"users": request.app[K_AUTH].list_users()})


async def create_user(request):
    admin = require_admin(request)
    auth = request.app[K_AUTH]
    b = await read_json(request)
    u, p, role = b.get("username"), b.get("password"), b.get("role")
    if not (isinstance(u, str) and isinstance(p, str) and isinstance(role, str)):
        raise web.HTTPBadRequest(text="username, password and role required")
    try:
        uid = await _run(auth.create_user, u, p, role)
        if b.get("trusted") is True:
            auth.set_trusted(uid, True)
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    except Exception as e:                      # sqlite3.IntegrityError (duplicate name)
        if "UNIQUE" in str(e):
            raise web.HTTPConflict(text="username already exists") from None
        raise
    auth.audit("user_created", admin.username, client_ip(request), f"{u} ({role})")
    return web.json_response({"id": uid}, status=201)


async def update_user(request):
    admin = require_admin(request)
    app, auth = request.app, request.app[K_AUTH]
    uid = _uid(request)
    target = auth.get_user(uid)
    if not target:
        raise web.HTTPNotFound(text="no such user")
    b = await read_json(request)
    changed = []
    try:
        if "role" in b:
            if not isinstance(b["role"], str):
                raise ValueError("bad role")
            if uid == admin.user_id and b["role"] != "admin":
                raise ValueError("you cannot demote yourself")
            auth.set_role(uid, b["role"])
            changed.append(f"role={b['role']}")
        if "trusted" in b:
            if not isinstance(b["trusted"], bool):
                raise ValueError("trusted must be true or false")
            auth.set_trusted(uid, b["trusted"])
            changed.append(f"trusted={b['trusted']}")
        if "password" in b:
            if not isinstance(b["password"], str):
                raise ValueError("bad password")
            await _run(auth.set_password, uid, b["password"], None)
            changed.append("password reset")
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    if not changed:
        raise web.HTTPBadRequest(text="nothing to change")
    if any(c.startswith(("role", "password")) for c in changed):
        await app[K_HUB].kick_user(uid, "account changed by administrator")   # reconnect picks up the new role
    auth.audit("user_updated", admin.username, client_ip(request), f"{target['username']}: {', '.join(changed)}")
    return web.json_response({"ok": True})


async def delete_user(request):
    admin = require_admin(request)
    app, auth = request.app, request.app[K_AUTH]
    uid = _uid(request)
    if uid == admin.user_id:
        raise web.HTTPBadRequest(text="you cannot delete yourself")
    target = auth.get_user(uid)
    if not target:
        raise web.HTTPNotFound(text="no such user")
    try:
        auth.delete_user(uid)
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    await app[K_HUB].kick_user(uid, "account deleted")
    auth.audit("user_deleted", admin.username, client_ip(request), target["username"])
    return web.json_response({"ok": True})


def _uid(request) -> int:
    try:
        return int(request.match_info["uid"])
    except ValueError:
        raise web.HTTPBadRequest(text="bad user id") from None


async def change_own_password(request):
    s = request[K_SESSION]
    auth = request.app[K_AUTH]
    b = await read_json(request)
    cur, new = b.get("current"), b.get("new")
    if not (isinstance(cur, str) and isinstance(new, str)):
        raise web.HTTPBadRequest(text="current and new password required")
    if not await _run(auth.check_password, s.user_id, cur):
        auth.audit("password_change_failed", s.username, client_ip(request))
        raise web.HTTPForbidden(text="current password is wrong")
    try:
        await _run(auth.set_password, s.user_id, new, request.cookies.get(COOKIE))   # other sessions are revoked
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    await request.app[K_HUB].kick_user(s.user_id, "password changed", except_sid=request.cookies.get(COOKIE))
    auth.audit("password_changed", s.username, client_ip(request))
    return web.json_response({"ok": True})


# --------------------------------------------------------------------- clients
async def list_clients(request):
    require_admin(request)
    hub = request.app[K_HUB]
    return web.json_response({"clients": hub.clients_info(), "lease": hub.lease.state()})


async def kick_client(request):
    admin = require_admin(request)
    hub = request.app[K_HUB]
    conn = request.match_info["conn"]
    if not await hub.kick_conn(conn, "disconnected by administrator"):
        raise web.HTTPNotFound(text="no such client")
    request.app[K_AUTH].audit("client_kicked", admin.username, client_ip(request), conn)
    return web.json_response({"ok": True})


# ------------------------------------------------------------------------ audit
async def audit_log(request):
    require_admin(request)
    try:
        limit = int(request.query.get("limit", "200"))
    except ValueError:
        raise web.HTTPBadRequest(text="bad limit") from None
    return web.json_response({"events": request.app[K_AUTH].audit_tail(limit)})


# ----------------------------------------------------------------------- config
async def get_config(request):
    require_admin(request)
    app = request.app

    def ports():
        try:
            from serial.tools import list_ports
            return [{"device": p.device, "description": p.description} for p in list_ports.comports()]
        except Exception:
            return []

    cfg = app[K_CFG]
    return web.json_response({
        "config": cfg, "editable": {k: sorted(v) for k, v in EDITABLE.items()}, "locked": LOCKED,
        "models": sorted(config.KNOWN_MODELS), "serial_ports": await _run(ports),
        "writable": app.get(K_CFGPATH) is not None,
        "ptt": {"enabled": app[K_GUARD].allow_ptt, "mock": app[K_DRIVER].is_mock},
    })


async def set_ptt_permission(request):
    """Turn the transmit permission (safety.allow_ptt) on or off from the web UI.

    Enabling needs the administrator's password again (a stolen session cannot switch the transmitter on) and is audited.
    It takes effect immediately and is written to the config file so it survives a restart. Disabling needs no password and
    first un-keys a running transmission."""
    admin = require_admin(request)
    app, auth = request.app, request.app[K_AUTH]
    path = app.get(K_CFGPATH)
    if path is None:
        raise web.HTTPConflict(text="no config file is in use; start with --config to enable editing")
    b = await read_json(request)
    enabled, password = b.get("enabled"), b.get("password")
    if not isinstance(enabled, bool) or b.get("confirm") is not True:
        raise web.HTTPBadRequest(text="enabled (true/false) and confirm are required")
    ip, guard = client_ip(request), app[K_GUARD]
    if enabled:
        if not isinstance(password, str):
            raise web.HTTPBadRequest(text="your password is required to enable transmitting")
        key = f"ptt-permission:{admin.username}"
        wait = auth.retry_after(ip, key)
        if wait > 0:
            raise web.HTTPTooManyRequests(text=f"too many attempts; retry in {int(wait) + 1}s")
        if not await _run(auth.check_password, admin.user_id, password):
            auth._record_fail(ip, key)
            auth.audit("ptt_permission_denied", admin.username, ip, "wrong password")
            raise web.HTTPForbidden(text="wrong password")
        auth._fails.pop((ip, key.lower()), None)
    new = copy.deepcopy(app[K_CFG])
    new["safety"]["allow_ptt"] = enabled
    try:
        config.validate(new)
        config.save(new, path)
    except config.ConfigError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    except OSError as e:
        raise web.HTTPConflict(text=f"could not write the config file: {e}") from None
    if not enabled and guard.keyed:
        await guard.emergency_unkey("transmitting disabled by administrator")
    guard.allow_ptt = enabled
    app[K_CFG].update(new)
    app[K_HUB]._broadcast({"t": "safety", "d": {"ptt_permitted": guard.permitted}})      # open pages update their PTT button
    auth.audit("ptt_enabled" if enabled else "ptt_disabled", admin.username, ip)
    log.warning("transmitting %s by %s", "ENABLED" if enabled else "disabled", admin.username)
    return web.json_response({"ok": True, "enabled": enabled})


async def put_config(request):
    admin = require_admin(request)
    app = request.app
    path = app.get(K_CFGPATH)
    if path is None:
        raise web.HTTPConflict(text="no config file is in use; start with --config to enable editing")
    body = await read_json(request)
    for section, values in body.items():
        if not isinstance(values, dict) or section not in EDITABLE:
            raise web.HTTPBadRequest(text=f"section '{section}' is not editable here")
        for key in values:
            if key not in EDITABLE[section]:
                raise web.HTTPForbidden(text=f"{section}.{key} cannot be changed from the web UI")
    try:
        # Merge onto the *file's* values (not DEFAULTS) so unrelated settings are preserved.
        new = config._merge(copy.deepcopy(config.DEFAULTS), _flatten_over(app[K_CFG], body))
        config.validate(new)
        config.save(new, path)
    except config.ConfigError as e:
        raise web.HTTPBadRequest(text=str(e)) from None
    changed = [f"{s}.{k}" for s, v in body.items() for k in v if app[K_CFG][s][k] != new[s][k]]
    live_only = all((s, k) in LIVE for s, v in body.items() for k in v)
    if ("logging", "level") in {(s, k) for s, v in body.items() for k in v}:
        logging.getLogger().setLevel(new["logging"]["level"].upper())
    app[K_CFG].update(new)
    if "rigctl" in body and app.get(K_RIGCTL):
        await app[K_RIGCTL].apply()                     # start, stop or move the logbook link at once
    if "freedv" in body and app[K_AUDIO]:
        app[K_AUDIO].set_freedv_params(new["freedv"]["mode"], new["freedv"]["tx_level_db"])
    if any(k in ("rx_gain_db", "tx_gain_db") for k in body.get("audio", {})) and app[K_AUDIO]:
        app[K_AUDIO].set_gains(new["audio"]["rx_gain_db"], new["audio"]["tx_gain_db"])     # takes effect immediately
    app[K_AUTH].audit("config_changed", admin.username, client_ip(request), ", ".join(changed) or "no change")
    return web.json_response({"ok": True, "changed": changed, "restart_required": bool(changed) and not live_only})


MAX_RESTORE_BYTES = 16 * 1024            # the server refuses larger request bodies anyway (client_max_size); a settings file is ~1 KB


_rade_lock = asyncio.Lock()


async def _run_rade_install(dest: Path, repo: str) -> tuple[int, str]:
    """Run scripts/rr_admin.py fetch-rade as a child process (download, SHA-256 check, safe unpack into dest). Separate so tests can replace it."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "rr_admin.py"
    proc = await asyncio.create_subprocess_exec(sys.executable, str(script), "fetch-rade", "--repo", repo, "--dest", str(dest),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 600)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, "timed out"
    return proc.returncode or 0, out.decode("utf-8", "replace")[-2000:]


async def rade_install(request):
    """The Install RADE button: download the RADE library from this version's GitHub release (checksum verified) into the data folder and
    start using it at once, with no restart. Administrators only; the same trust as the update check (the project's own release page)."""
    from .audio import freedv, rade
    admin = require_admin(request)
    app = request.app
    body = await read_json(request)
    if body.get("confirm") is not True:
        raise web.HTTPBadRequest(text="confirmation required")
    if platform.machine() not in ("aarch64", "arm64"):
        raise web.HTTPConflict(text="the RADE library is only built for 64-bit ARM (Raspberry Pi OS 64-bit)")
    dest = rade.install_dir()
    if dest is None or not app[K_AUDIO]:
        raise web.HTTPConflict(text="audio is not available on this server")
    if _rade_lock.locked():
        raise web.HTTPConflict(text="an installation is already running")
    async with _rade_lock:
        rc, out = await _run_rade_install(dest, app[K_CFG]["updates"]["repo"])
    last = next((ln.strip() for ln in reversed(out.splitlines()) if ln.strip()), "no output")
    app[K_AUTH].audit("rade_installed" if rc == 0 else "rade_install_failed", admin.username, client_ip(request), last[:200])
    if rc != 0:
        log.warning("RADE install failed: %s", last)
        return web.json_response({"ok": False, "error": last[:300]}, status=502)
    app[K_AUDIO].refresh_freedv()
    why = freedv.mode_status().get("RADE", "")
    return web.json_response({"ok": not why, "reason": why, "message": last[:300]})


async def rigctl_state(request):
    require_admin(request)
    return web.json_response(request.app[K_RIGCTL].status())


async def update_state(request):
    """What the update check knows (administrators only): current and newest version, link, when it last asked, last error."""
    require_admin(request)
    return web.json_response(request.app[K_UPDATES].state())


# ---- "Update now": the page only drops a request file; a root helper started by systemd (radio-remote-update.path) does the update
UPDATE_UNIT = Path("/etc/systemd/system/radio-remote-update.path")           # present when the helper is installed (install.sh / update.sh put it there)
UPDATE_STALE_S = 1800


def _update_files(app) -> tuple[Path, Path, Path]:
    d = Path(app[K_CFG]["storage"]["data_dir"])
    return d / "update-request", d / "update-status", d / "update.log"


def _update_status(app) -> dict:
    req, st, logf = _update_files(app)
    state, since, who = "idle", 0, ""
    try:
        parts = st.read_text(encoding="utf-8").split()
        state, since, who = parts[0], int(parts[1]), (parts[2] if len(parts) > 2 else "")
    except (OSError, ValueError, IndexError):
        pass
    if state == "running" and time.time() - since > UPDATE_STALE_S:
        state = "failed"                                    # a helper that died (power loss) must not block the button for ever
    if req.exists() and state != "running":
        state = "requested"
    try:
        tail = [scrub(ln) for ln in logf.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]]
    except OSError:
        tail = []
    return {"available": UPDATE_UNIT.is_file(), "state": state, "since": since, "by": who, "log": tail, "version": __version__}


async def update_status(request):
    require_admin(request)
    return web.json_response(_update_status(request.app))


async def update_start(request):
    """The "Update now" button: ask the root helper to install the newest release (it downloads, checks the SHA-256, backs up, installs and rolls
    back if the new version does not start). Needs the administrator's password again, a known newer version and the helper installed."""
    admin = require_admin(request)
    app, auth, ip = request.app, request.app[K_AUTH], client_ip(request)
    body = await read_json(request)
    if body.get("confirm") is not True or not isinstance(body.get("password"), str):
        raise web.HTTPBadRequest(text="your password and a confirmation are required")
    st = _update_status(app)
    if not st["available"]:
        raise web.HTTPConflict(text="the update helper is not installed on this system (run update.sh once, or use self_update.sh on the Pi)")
    if st["state"] in ("running", "requested"):
        raise web.HTTPConflict(text="an update is already running")
    if not app[K_UPDATES].state().get("newer"):
        raise web.HTTPConflict(text="no newer version is known: press Check now first")
    key = f"update-now:{admin.username}"
    wait = auth.retry_after(ip, key)
    if wait > 0:
        raise web.HTTPTooManyRequests(text=f"too many attempts; retry in {int(wait) + 1}s")
    if not await _run(auth.check_password, admin.user_id, body["password"]):
        auth._record_fail(ip, key)
        auth.audit("update_denied", admin.username, ip, "wrong password")
        raise web.HTTPForbidden(text="wrong password")
    auth._fails.pop((ip, key.lower()), None)
    req, _, _ = _update_files(app)
    try:
        tmp = req.with_name(req.name + ".tmp")
        tmp.write_text(f"user={admin.username}\n", encoding="utf-8")
        tmp.replace(req)
    except OSError as e:
        raise web.HTTPConflict(text=f"could not ask for the update: {e}") from None
    auth.audit("update_requested", admin.username, ip, app[K_UPDATES].state().get("latest") or "")
    log.warning("update to %s requested by %s", app[K_UPDATES].state().get("latest"), admin.username)
    return web.json_response({"ok": True}, status=202)


async def update_check_now(request):
    """Ask GitHub right now (the 'Check now' button). Does nothing when the check is switched off."""
    admin = require_admin(request)
    state = await request.app[K_UPDATES].check()
    request.app[K_AUTH].audit("update_check", admin.username, client_ip(request), state["latest"] or state["error"] or "")
    return web.json_response(state)


async def backup_config(request):
    """Download the current settings as a TOML file (administrators only). It holds no passwords or keys."""
    admin = require_admin(request)
    app = request.app
    stamp = time.strftime("%Y%m%d-%H%M%S")
    text = (f"# Radio Remote settings backup, {time.strftime('%Y-%m-%d %H:%M:%S')}, version {__version__}.\n"
            "# Restore it in Admin > Config > Settings backup. Only the settings the web page may change are restored:\n"
            "# server.*, storage.* and safety.allow_ptt are never taken from a backup.\n\n" + config.dumps(app[K_CFG]))
    app[K_AUTH].audit("config_backup", admin.username, client_ip(request))
    return web.Response(text=text, content_type="text/plain", charset="utf-8",
                        headers={"Content-Disposition": f'attachment; filename="radio-remote-settings-{stamp}.toml"',
                                 "Cache-Control": "no-store"})


async def restore_config(request):
    """Apply a settings backup (TOML text). Only the settings the web UI may change are taken (EDITABLE): the listener address, the
    storage folder and the transmit permission are never restored, so a backup from another Pi or an old backup cannot lock you out
    or switch transmitting on. Needs a restart to take effect."""
    import tomllib
    admin = require_admin(request)
    app = request.app
    path = app.get(K_CFGPATH)
    if path is None:
        raise web.HTTPConflict(text="no config file is in use; start with --config to enable editing")
    b = await read_json(request)
    text = b.get("toml")
    if not isinstance(text, str) or not text.strip():
        raise web.HTTPBadRequest(text="the backup text is missing")
    if len(text.encode("utf-8")) > MAX_RESTORE_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_RESTORE_BYTES, actual_size=len(text))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise web.HTTPBadRequest(text=f"this is not a valid settings file: {e}") from None
    applied: dict[str, dict] = {}
    ignored: list[str] = []
    for section, values in data.items():
        if not isinstance(values, dict) or section not in config.DEFAULTS:
            ignored.append(str(section))
            continue
        for key, val in values.items():
            if key in EDITABLE.get(section, ()):
                applied.setdefault(section, {})[key] = val
            else:
                ignored.append(f"{section}.{key}")
    if not applied:
        raise web.HTTPBadRequest(text="the file has no settings that can be restored here")
    try:
        new = config._merge(copy.deepcopy(config.DEFAULTS), _flatten_over(app[K_CFG], applied))
        config.validate(new)
        config.save(new, path)
    except config.ConfigError as e:
        raise web.HTTPBadRequest(text=f"the backup was refused: {e}") from None
    except OSError as e:
        raise web.HTTPConflict(text=f"could not write the config file: {e}") from None
    changed = [f"{s}.{k}" for s, v in applied.items() for k in v if app[K_CFG][s][k] != new[s][k]]
    app[K_CFG].update(new)
    if "rigctl" in applied and app.get(K_RIGCTL):
        await app[K_RIGCTL].apply()
    app[K_AUTH].audit("config_restored", admin.username, client_ip(request), ", ".join(changed) or "no change")
    return web.json_response({"ok": True, "changed": changed, "ignored": ignored, "restart_required": bool(changed)})


def _flatten_over(current: dict, body: dict) -> dict:
    out = copy.deepcopy(current)
    for s, v in body.items():
        out[s].update(v)
    return out


async def restart(request):
    admin = require_admin(request)
    b = await read_json(request)
    if b.get("confirm") is not True:
        raise web.HTTPBadRequest(text="confirmation required")
    hook = request.app.get(K_RESTART)
    if hook is None:
        raise web.HTTPNotImplemented(text="restart is only available when running as a service (systemd restarts it)")
    request.app[K_AUTH].audit("restart", admin.username, client_ip(request))
    hook()
    return web.json_response({"ok": True})


# ------------------------------------------------------------------ diagnostics
async def diagnostics(request):
    require_admin(request)
    app = request.app
    drv, guard, hub = app[K_DRIVER], app[K_GUARD], app[K_HUB]
    sysd, vers = await _run(sysinfo.system), await _run(sysinfo.versions)
    audio = app[K_AUDIO].status() if app[K_AUDIO] else None
    return web.json_response({
        "app": {"version": __version__, "uptime_s": int(time.monotonic() - app[K_STARTED])},
        "system": sysd, "versions": vers,
        "radio": {"model": drv.caps.data["model"]["name"], "connected": drv.state.get("connected"),
                  "mock": drv.is_mock, **drv.diagnostics()},
        "ptt": {"permitted": guard.permitted, "keyed": guard.keyed, "owner": guard.owner, "max_tx_s": guard.max_tx_s},
        "audio": audio,
        "clients": hub.clients_info(), "lease": hub.lease.state(),
        "log": [scrub(l) for l in list(RING.lines)[-200:]],
    })


def add_routes(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/users", list_users)
    r.add_post("/api/users", create_user)
    r.add_patch("/api/users/{uid}", update_user)
    r.add_delete("/api/users/{uid}", delete_user)
    r.add_post("/api/me/password", change_own_password)
    r.add_get("/api/clients", list_clients)
    r.add_post("/api/clients/{conn}/kick", kick_client)
    r.add_get("/api/audit", audit_log)
    r.add_get("/api/config", get_config)
    r.add_put("/api/config", put_config)
    r.add_post("/api/admin/ptt", set_ptt_permission)
    r.add_get("/api/admin/update", update_state)
    r.add_get("/api/admin/rigctl", rigctl_state)
    r.add_post("/api/admin/rade/install", rade_install)
    r.add_get("/api/admin/update/status", update_status)
    r.add_post("/api/admin/update/start", update_start)
    r.add_post("/api/admin/update/check", update_check_now)
    r.add_get("/api/admin/backup", backup_config)
    r.add_post("/api/admin/restore", restore_config)
    r.add_post("/api/admin/restart", restart)
    r.add_get("/api/diagnostics", diagnostics)
