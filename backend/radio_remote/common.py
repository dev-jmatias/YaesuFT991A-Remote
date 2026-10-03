"""Shared web helpers and app keys (kept out of app.py so admin.py can import them without cycles)."""
from __future__ import annotations

import json

from aiohttp import web

from .auth import Session

COOKIE = "rr_session"

K_CFG, K_AUTH, K_DRIVER, K_GUARD, K_HUB, K_AUDIO = (
    web.AppKey(n, object) for n in ("cfg", "auth", "driver", "guard", "hub", "audio"))
K_CFGPATH = web.AppKey("cfgpath", object)
K_RESTART = web.AppKey("restart", object)
K_STARTED = web.AppKey("started", float)
K_SESSION = web.RequestKey("session", Session)


class CommandError(Exception):
    pass


def client_ip(request: web.Request) -> str:
    """Peer address; X-Forwarded-For is trusted ONLY when the peer is the local reverse proxy."""
    ip = request.remote or "?"
    if ip in ("127.0.0.1", "::1"):
        fwd = request.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
    return ip


def is_https(request: web.Request) -> bool:
    return request.secure or (
        request.remote in ("127.0.0.1", "::1") and request.headers.get("X-Forwarded-Proto") == "https")


async def read_json(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="invalid JSON") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="JSON object expected")
    return body


def require_admin(request: web.Request) -> Session:
    s = request[K_SESSION]
    if s.role != "admin":
        raise web.HTTPForbidden(text="admin only")
    return s
