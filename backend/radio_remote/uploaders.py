"""Upload logged contacts to QRZ.com, eQSL and Club Log, one contact at a time, with the person's own credentials from the config ([upload]).

Each function returns (ok, message): ok = the service has the contact now (a duplicate counts, it is already there). Nothing is retried here; the page shows the message and the
person presses Upload again. The network call is injected (`post`) so the tests run without any service.
NOT yet checked against the live services (that needs real accounts): see docs/ft8.md.
"""
from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable
from urllib.parse import parse_qs

from . import __version__
from .qsolog import adif_field, adif_header, adif_record

log = logging.getLogger("upload")

UA = f"RadioRemote/{__version__} (+https://github.com/dev-jmatias/YaesuFT991A-Remote)"
Post = Callable[..., Awaitable[tuple[int, str]]]            # post(url, data=dict|None, files=dict|None) -> (status, body text)


async def aiohttp_post(url: str, data: dict | None = None, files: dict | None = None) -> tuple[int, str]:
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        if files:
            form = aiohttp.FormData()
            for k, v in (data or {}).items():
                form.add_field(k, v)
            for k, (fname, content) in files.items():
                form.add_field(k, content, filename=fname, content_type="text/plain")
            async with s.post(url, data=form) as r:
                return r.status, await r.text()
        async with s.post(url, data=data) as r:
            return r.status, await r.text()


async def qrz(key: str, q: dict, post: Post = aiohttp_post) -> tuple[bool, str]:
    """QRZ.com Logbook API: INSERT. Needs the logbook's API key (QRZ logbook settings); the logbook owner needs an XML (or higher) subscription."""
    if not key:
        return False, "the QRZ logbook API key is not set (Admin > Config > Upload)"
    try:
        status, body = await post("https://logbook.qrz.com/api", data={"KEY": key, "ACTION": "INSERT", "ADIF": adif_record(q)})
    except Exception as e:
        return False, f"could not reach QRZ: {e}"
    r = {k: v[0] for k, v in parse_qs(body.strip(), keep_blank_values=True).items()}
    res = r.get("RESULT", "").upper()
    if res in ("OK", "REPLACE"):
        return True, f"QRZ: {'logged' if res == 'OK' else 'already there, replaced'} (log id {r.get('LOGID', '?')})"
    reason = r.get("REASON", body.strip()[:150] or f"HTTP {status}")
    if "duplicate" in reason.lower():
        return True, "QRZ: already in the logbook"
    return False, f"QRZ refused it: {reason}"


async def eqsl(user: str, password: str, q: dict, post: Post = aiohttp_post) -> tuple[bool, str]:
    """eQSL.cc ImportADIF: the ADIF file carries the login in its header."""
    if not user or not password:
        return False, "the eQSL user name and password are not set (Admin > Config > Upload)"
    adif = adif_header(__version__, {"EQSL_USER": user, "EQSL_PSWD": password}) + adif_record(q)
    try:
        status, body = await post("https://www.eqsl.cc/qslcard/ImportADIF.cfm", files={"Filename": ("radio-remote.adi", adif)})
    except Exception as e:
        return False, f"could not reach eQSL: {e}"
    text = re.sub(r"<[^>]+>", " ", body)
    text = " ".join(text.split())
    m = re.search(r"Result:\s*(\d+)\s+out of\s+(\d+)\s+records added", text, re.I)
    if m and int(m.group(1)) >= 1:
        return True, "eQSL: logged"
    if re.search(r"duplicate", text, re.I):
        return True, "eQSL: already there"
    err = re.search(r"Error:?\s*([^.]{3,120})", text, re.I)
    return False, "eQSL refused it: " + (err.group(1).strip() if err else (text[:150] or f"HTTP {status}"))


async def clublog(email: str, password: str, api: str, callsign: str, q: dict, post: Post = aiohttp_post) -> tuple[bool, str]:
    """Club Log real-time upload of one contact. Needs the account e-mail and (application) password, and the application API key that Club Log issues to a program."""
    if not (email and password):
        return False, "the Club Log e-mail and password are not set (Admin > Config > Upload)"
    if not api:
        return False, "Club Log needs an application API key for this program (Admin > Config > Upload); see the FT8 guide"
    if not callsign:
        return False, "the station callsign is not set"
    try:
        status, body = await post("https://clublog.org/realtime.php", data={"email": email, "password": password, "callsign": callsign, "api": api, "adif": adif_record(q)})
    except Exception as e:
        return False, f"could not reach Club Log: {e}"
    b = body.strip()
    if status == 200 and b.upper().startswith("OK"):
        return True, "Club Log: logged"
    if "dupe" in b.lower() or "duplicate" in b.lower():
        return True, "Club Log: already there"
    return False, f"Club Log refused it (HTTP {status}): {b[:150] or 'no reason given'}"


async def upload(service: str, cfg: dict, q: dict, station_callsign: str, post: Post = aiohttp_post) -> tuple[bool, str]:
    u = cfg["upload"]
    if service == "qrz":
        return await qrz(u["qrz_key"], q, post)
    if service == "eqsl":
        return await eqsl(u["eqsl_user"], u["eqsl_password"], q, post)
    if service == "clublog":
        return await clublog(u["clublog_email"], u["clublog_password"], u["clublog_api"], q.get("station_callsign") or station_callsign, q, post)
    return False, "unknown service"
