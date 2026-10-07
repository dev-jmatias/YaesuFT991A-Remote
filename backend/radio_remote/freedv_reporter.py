"""FreeDV Reporter (https://qso.freedv.org): announce this station while it uses FreeDV, and list who else is on the air.

The site is a live list of FreeDV stations. A program joins it over a Socket.IO (v4) WebSocket: it sends who it is (callsign, grid square, program name)
when it connects, then small events: the frequency (`freq_change`), whether it is transmitting (`tx_report`: mode + on/off) and a short message
(`message_update`). The server sends back the same kind of events about every other station. This is a small hand-written client for exactly that (no extra
library). The protocol was taken from open-source clients of the site, not from official documentation, so everything here is defensive: unknown events and
fields are ignored and any failure only means "not connected".

Off by default. It connects only while FreeDV is switched on, and announces only with a callsign and a grid square. Roles: "report" (announce + see the
list), "report_wo" (announce only), "view" (see the list only).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import aiohttp

log = logging.getLogger("reporter")

PROTOCOL_VERSION = 2
MODE_NAMES = {"RADE": "RADEV1", "RADE2": "RADEV2"}          # our mode -> the name the site uses (RADEV1 is what FreeDV 2.x sends; RADEV2 is a guess: not confirmed against the site)
OUR_MODE = {v: k for k, v in MODE_NAMES.items()}
MAX_STATIONS = 2000
BACKOFF = (5, 10, 20, 40, 60)


class FreeDVReporter:
    def __init__(self, cfg: Callable[[], dict], version: str, get_frequency: Callable[[], int]):
        self._cfg, self._version, self._get_freq = cfg, version, get_frequency
        self.active = False                       # FreeDV is switched on
        self.mode = ""                            # our mode name ("RADE" ...)
        self.transmitting = False
        self.freq = 0
        self.connected = False
        self.role = ""
        self.error = ""
        self.my_sid = ""
        self.stations: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._wake = asyncio.Event()
        self._sent_cfg: tuple = ()

    # ------------------------------------------------------------- config
    def settings(self) -> dict:
        return self._cfg()["reporter"]

    def wanted_role(self) -> str:
        c = self.settings()
        if not (c["enabled"] and self.active):
            return ""
        can_announce = bool(c["announce"] and c["callsign"] and c["grid_square"])
        if can_announce and c["watch"]:
            return "report"
        if can_announce:
            return "report_wo"
        return "view" if c["watch"] else ""

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        t, self._task = self._task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        self.connected = False

    def apply(self) -> None:
        """The configuration or the FreeDV state changed: connect, disconnect or reconnect as needed."""
        self._wake.set()
        want = self.wanted_role()
        if self._ws is not None and (not want or want != self.role or self._identity() != self._sent_cfg):
            asyncio.ensure_future(self._drop())

    async def _drop(self) -> None:
        ws = self._ws
        if ws is not None and not ws.closed:
            await ws.close()

    def _identity(self) -> tuple:
        c = self.settings()
        return (c["callsign"], c["grid_square"], c["host"], c["tls"])

    # ------------------------------------------------- what the page tells us
    def set_active(self, on: bool, mode: str = "") -> None:
        self.active = bool(on)
        self.mode = MODE_NAMES.get(mode, mode) if on else self.mode
        if not on:
            self.transmitting = False
        self.apply()
        if on and self.connected:
            self._emit("tx_report", {"mode": self.mode, "transmitting": self.transmitting})

    def set_frequency(self, hz: int) -> None:
        if hz and hz != self.freq:
            self.freq = hz
            self._emit("freq_change", {"freq": int(hz)})

    def set_transmitting(self, on: bool) -> None:
        if on != self.transmitting:
            self.transmitting = on
            self._emit("tx_report", {"mode": self.mode, "transmitting": bool(on)})

    def set_message(self) -> None:
        self._emit("message_update", {"message": self.settings()["message"]})

    # ----------------------------------------------------------- the link
    def _emit(self, name: str, payload: dict) -> None:
        if not self.connected or self._ws is None or self.role not in ("report", "report_wo"):
            return
        asyncio.ensure_future(self._send("42" + json.dumps([name, payload], separators=(",", ":"))))

    async def _send(self, text: str) -> None:
        try:
            if self._ws is not None and not self._ws.closed:
                await self._ws.send_str(text)
        except (aiohttp.ClientError, ConnectionError, RuntimeError):
            pass

    async def _run(self) -> None:
        attempt = 0
        while True:
            role = self.wanted_role()
            if not role:
                self.error = ""
                self.stations.clear()
                self._wake.clear()
                await self._wake.wait()
                attempt = 0
                continue
            try:
                await self._session(role)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:                # any failure: show it, wait, try again
                self.error = str(e)[:200] or e.__class__.__name__
                log.warning("FreeDV Reporter: %s", self.error)
            finally:
                self.connected, self.role, self._ws, self.my_sid = False, "", None, ""
                self.stations.clear()                  # the list is rebuilt from the server when we connect again
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), BACKOFF[min(attempt, len(BACKOFF) - 1)])
            except asyncio.TimeoutError:
                pass
            attempt += 1

    async def _session(self, role: str) -> None:
        c = self.settings()
        url = f"{'wss' if c['tls'] else 'ws'}://{c['host']}/socket.io/?EIO=4&transport=websocket"
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as http, http.ws_connect(url, heartbeat=None, max_msg_size=8 * 1024 * 1024) as ws:
            self._ws = ws
            first = await asyncio.wait_for(ws.receive_str(), 15)
            if not first.startswith("0"):
                raise ConnectionError("not a Socket.IO server")
            auth = {"protocol_version": PROTOCOL_VERSION, "role": role}
            if role != "view":
                auth.update({"callsign": c["callsign"].upper(), "grid_square": c["grid_square"][:2].upper() + c["grid_square"][2:4] + c["grid_square"][4:6].lower() + c["grid_square"][6:],
                             "version": f"Radio Remote {self._version}",
                             "rx_only": False, "os": "Linux"})
            await ws.send_str("40" + json.dumps(auth, separators=(",", ":")))
            while True:                           # wait for the connect answer, answering pings meanwhile
                pkt = await asyncio.wait_for(ws.receive_str(), 20)
                if pkt == "2":
                    await ws.send_str("3")
                elif pkt.startswith("44"):
                    raise ConnectionError("the server refused the connection: " + pkt[2:80])
                elif pkt.startswith("40"):
                    try:
                        self.my_sid = str(json.loads(pkt[2:]).get("sid", ""))
                    except ValueError:
                        self.my_sid = ""
                    break
            self.role, self.connected, self.error, self._sent_cfg = role, True, "", self._identity()
            self.stations.clear()
            log.info("FreeDV Reporter: connected as %s", role)
            if role in ("report", "report_wo"):            # tell the site where we are and what we are doing
                self.freq = self._get_freq() or self.freq
                if self.freq:
                    self._emit("freq_change", {"freq": int(self.freq)})
                self._emit("tx_report", {"mode": self.mode, "transmitting": self.transmitting})
                if c["message"]:
                    self.set_message()
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                pkt = msg.data
                if pkt == "2":
                    await ws.send_str("3")
                elif pkt.startswith("42"):
                    try:
                        arr = json.loads(pkt[2:])
                        if isinstance(arr, list) and arr and isinstance(arr[0], str):
                            self._event(arr[0], arr[1] if len(arr) > 1 else None)
                    except ValueError:
                        pass

    # ------------------------------------------------------ what the site tells us
    def _event(self, name: str, p) -> None:
        if name == "bulk_update":
            if isinstance(p, list):
                for item in p:
                    if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str):
                        self._event(item[0], item[1])
            return
        if not isinstance(p, dict):
            return
        sid = p.get("sid")
        if not isinstance(sid, str) or not sid:
            return
        if name == "remove_connection":
            self.stations.pop(sid, None)
            return
        if name not in ("new_connection", "freq_change", "tx_report", "message_update", "rx_report"):
            return
        st = self.stations.get(sid)
        if st is None:
            if len(self.stations) >= MAX_STATIONS:
                return
            st = self.stations[sid] = {"sid": sid, "callsign": "", "grid_square": "", "version": "", "rx_only": False, "freq": 0, "mode": "",
                                       "transmitting": False, "message": "", "heard": "", "heard_snr": None, "seen": 0.0}
        st["seen"] = time.time()
        if name == "rx_report":                    # this station heard somebody: keep it apart from the station's own callsign and mode
            st["heard"] = str(p.get("callsign", ""))[:20]
            snr = p.get("snr")
            st["heard_snr"] = snr if isinstance(snr, (int, float)) and not isinstance(snr, bool) else None
            return
        for k, lim in (("callsign", 20), ("grid_square", 8), ("version", 60), ("mode", 12), ("message", 200)):
            if isinstance(p.get(k), str):
                st[k] = p[k][:lim]
        if isinstance(p.get("freq"), (int, float)) and not isinstance(p["freq"], bool):
            st["freq"] = int(p["freq"])
        if isinstance(p.get("transmitting"), bool):
            st["transmitting"] = p["transmitting"]
        if isinstance(p.get("rx_only"), bool):
            st["rx_only"] = p["rx_only"]

    # ----------------------------------------------------------- for the page
    def status(self, near_hz: int = 0, window_hz: int = 5000, limit: int = 60) -> dict:
        c = self.settings()
        mine = self.my_sid
        rows = []
        for st in self.stations.values():
            if st["sid"] == mine or not st["callsign"]:
                continue
            tunable = st["mode"] in OUR_MODE and st["freq"] > 0
            rows.append({"callsign": st["callsign"], "grid": st["grid_square"], "freq": st["freq"], "mode": OUR_MODE.get(st["mode"], st["mode"]),
                         "tx": st["transmitting"], "message": st["message"], "heard": st["heard"], "snr": st["heard_snr"], "listening": st["rx_only"],
                         "tunable": tunable, "near": bool(near_hz and st["freq"] and abs(st["freq"] - near_hz) <= window_hz)})
        rows.sort(key=lambda r: (not r["near"], not r["tx"], r["freq"], r["callsign"]))
        return {"enabled": bool(c["enabled"]), "wanted": self.wanted_role(), "role": self.role, "connected": self.connected, "error": self.error,
                "needs_callsign": bool(c["enabled"] and c["announce"] and not (c["callsign"] and c["grid_square"])),
                "total": len(rows), "near": sum(1 for r in rows if r["near"]), "stations": rows[:limit]}
