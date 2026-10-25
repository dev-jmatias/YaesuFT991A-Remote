"""JS8 for the JS8 screen: keyboard-to-keyboard messaging with JS8Call.

JS8's message coding (a word dictionary and error-correcting tables) cannot be written into this MIT-licensed program, so the JS8 modem is JS8Call-improved (GPL-3.0, github.com/JS8Call-improved):
a separate program that the administrator installs with a button (a download from its own release page, nothing of it is bundled). It runs headless, in its own systemd service, and
this module only TALKS to it over its documented JSON API (TCP, localhost) and connects its audio to the radio:

  receive : the radio's audio (the audio service's 20 ms frames) -> js8_feed() -> aplay -> ALSA loopback hw:Loopback,0,0 -> (private PulseAudio) -> JS8Call
  transmit: JS8Call -> (private PulseAudio) -> loopback -> arecord hw:Loopback,0,1 -> _TxStream -> audio.arm_stream() -> the radio's USB sound card

JS8Call never touches the radio (no rig, no PTT, its network reporting is off). Transmitting goes through the usual protection (TxGuard: transmit permission, control of the radio, one
owner, heartbeat while it sends, the time limit): when JS8Call reports "PTT on" this module keys the radio, but only when an operator asked for a transmission a moment ago from a page
that is still connected; anything else (JS8Call replying by itself, for example) is stopped with RIG.TX_HALT.
Everything JS8Call needs from the system (libraries, PulseAudio, the loopback sound module) is prepared by one script run as root, scripts/js8_setup.sh, at an administrator's request.
"""
from __future__ import annotations

import asyncio
import collections
import ctypes.util
import json
import logging
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

import aiohttp

from . import config
from .audio.backends import AlsaSink, AlsaSource, AudioDeviceError
from .audio.levels import FRAME_BYTES, db_to_gain
from .safety import TxRefused

log = logging.getLogger("js8")

VERSION = "v3.0.3"                                                  # the JS8Call-improved release this was built and tested against
APP_URLS = {m: f"https://github.com/JS8Call-improved/JS8Call-improved/releases/download/{VERSION}/JS8Call-{VERSION}-{m}.AppImage" for m in ("aarch64", "x86_64")}
API_HOST, API_PORT = "127.0.0.1", 2442
RX_DEVICE, TX_DEVICE = "hw:Loopback,0,0", "hw:Loopback,0,1"
SPEEDS = [(4, "Slow", 30), (0, "Normal", 15), (1, "Fast", 10), (2, "Turbo (JS8 40)", 6), (8, "JS8 60", 4)]      # JS8Call's speed numbers, names and slot lengths in seconds
# the usual JS8 dial frequencies (USB)
BANDS = [("160m", 1842000), ("80m", 3578000), ("40m", 7078000), ("30m", 10130000), ("20m", 14078000), ("17m", 18104000), ("15m", 21078000), ("12m", 24922000), ("10m", 28078000), ("6m", 50318000)]
TX_MODES = ("USB", "LSB", "DATA-U", "DATA-L")
ARM_S = 90.0                                                        # how long after "Send" JS8Call may key the transmitter (a message waits for the next slot, long ones have several frames)
KEEP_ACTIVITY, KEEP_DIRECTED = 300, 100
MAX_TEXT = 220
START_TIMEOUT_S = 60.0


class _TxStream:
    """JS8Call's transmit audio, read from the loopback, handed to the audio service frame by frame while the radio is keyed for it."""

    def __init__(self) -> None:
        self.q: collections.deque[bytes] = collections.deque(maxlen=12)
        self.active = False
        self.gain = 0.25

    def push(self, pcm: bytes) -> None:
        if self.active:
            self.q.append(pcm)

    def frame(self) -> bytes | None:
        return self.q.popleft() if self.q else None


def merge_ini(path: Path, section: str, values: dict) -> None:
    """Set keys in one section of a QSettings .ini file and keep everything else as it is (JS8Call rewrites the file with all its own settings when it quits)."""
    lines = path.read_text().splitlines() if path.is_file() else []
    out, i, found = [], 0, False
    todo = dict(values)
    cur = None
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur == section and todo:                                  # the end of our section: add what was not there
                out.extend(f"{k}={v}" for k, v in todo.items())
                todo = {}
            cur = s[1:-1]
            found = found or cur == section
        elif cur == section and "=" in s:
            k = s.split("=", 1)[0]
            if k in todo:
                out.append(f"{k}={todo.pop(k)}")
                continue
        out.append(ln)
    if not found:
        out += ["", f"[{section}]"]
    if todo:
        if found and cur != section:                                      # (cannot happen: the section was closed above)
            pass
        out.extend(f"{k}={v}" for k, v in todo.items())
    path.write_text("\n".join(out) + "\n")


def prereqs(data: Path) -> dict:
    """What is missing on this machine for JS8Call (the web page shows it and offers the buttons that fix it)."""
    arch = platform.machine()
    missing = []
    platform_ok = sys.platform.startswith("linux") and arch in APP_URLS
    if not platform_ok:
        return {"platform_ok": False, "arch": arch, "missing": ["a supported system (Linux on a Raspberry Pi 4 or a 64-bit PC)"], "system_ok": False, "app_ok": False}
    libs = all(ctypes.util.find_library(n) for n in ("GL", "EGL", "fontconfig", "harfbuzz", "freetype"))
    pulse = shutil.which("pulseaudio") is not None
    try:
        loop = "Loopback" in Path("/proc/asound/cards").read_text()
    except OSError:
        loop = False
    if not libs:
        missing.append("graphics and font libraries")
    if not pulse:
        missing.append("PulseAudio")
    if not loop:
        missing.append("the loopback sound card")
    app_ok = (data / "app" / "squashfs-root" / "AppRun").is_file()
    return {"platform_ok": True, "arch": arch, "libs": libs, "pulse": pulse, "loopback": loop, "system_ok": libs and pulse and loop, "app_ok": app_ok, "missing": missing}


class Js8Engine:
    def __init__(self, get_cfg: Callable[[], dict], audio, broadcast: Callable[[dict], None], get_freq: Callable[[], int], guard, has_client: Callable[[str], bool], driver=None):
        self.get_cfg, self.audio, self.broadcast, self.get_freq, self.guard, self.has_client = get_cfg, audio, broadcast, get_freq, guard, has_client
        self.driver = driver
        self.on = False                                      # switched on by an operator
        self.status = "off"                                  # off | starting | on | error
        self.last_error = ""
        self.api_up = False
        self.dial, self.offset, self.speed = 0, 1500, 0
        self.activity: collections.deque[dict] = collections.deque(maxlen=KEEP_ACTIVITY)
        self.directed: collections.deque[dict] = collections.deque(maxlen=KEEP_DIRECTED)
        self.heard: dict[str, dict] = {}
        self.sending: str | None = None
        self.queue_depth = 0
        self.owner: str | None = None                       # the connection that asked for the transmission in progress
        self.armed_until = 0.0
        self.transmitting = False
        self.installing = ""                                # progress text of the JS8Call download
        self._tx = _TxStream()
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task] = []
        self._rxq: collections.deque[bytes] = collections.deque(maxlen=60)
        self._rx_event = asyncio.Event()
        self._hb_task: asyncio.Task | None = None
        self._release_task: asyncio.Task | None = None
        self._last_dial_sent = 0
        self._warned_tx = 0.0

    # ------------------------------------------------------------ paths and state
    @property
    def data(self) -> Path:
        return Path(self.get_cfg()["storage"]["data_dir"]) / "js8"

    def settings(self) -> dict:
        c = self.get_cfg()
        return {"callsign": config.ftx_callsign(c), "grid_square": config.ftx_grid(c), "tx_level_db": c["js8"]["tx_level_db"]}

    def state(self) -> dict:
        return {"on": self.on, "status": self.status, "error": self.last_error, "api_up": self.api_up, "prereq": prereqs(self.data), "installing": self.installing, "version": VERSION,
                "dial": self.dial, "offset": self.offset, "speed": self.speed, "speeds": [{"n": n, "name": nm, "period": p} for n, nm, p in SPEEDS], "bands": [{"name": n, "hz": h} for n, h in BANDS],
                "activity": list(self.activity)[-120:], "directed": list(self.directed), "heard": self._heard_list(), "sending": self.sending, "queue": self.queue_depth,
                "transmitting": self.transmitting, "settings": self.settings(), "setup_log": self._setup_log(), "now": time.time()}

    def _heard_list(self) -> list[dict]:
        return sorted(self.heard.values(), key=lambda h: -h["t"])[:60]

    def _setup_log(self) -> str:
        try:
            return (self.data.parent / "js8-setup.log").read_text()[-1500:]
        except OSError:
            return ""

    def _emit(self, k: str, **kw) -> None:
        self.broadcast({"t": "js8", "k": k, **kw})

    def _set_status(self, status: str, error: str = "") -> None:
        self.status, self.last_error = status, error
        self._emit("status", status=status, error=error, api_up=self.api_up)

    # ------------------------------------------------------------ install and system preparation
    async def install(self) -> None:
        """Download JS8Call-improved from its release page and unpack it (no root needed; the program stays under <data>/js8/app)."""
        arch = platform.machine()
        if arch not in APP_URLS:
            raise ValueError(f"no JS8Call build for {arch}")
        if self.installing:
            raise ValueError("already installing")
        app = self.data / "app"
        try:
            self.installing = "downloading JS8Call"
            self._emit("install", text=self.installing)
            app.mkdir(parents=True, exist_ok=True)
            img = app / "JS8Call.AppImage"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
                async with s.get(APP_URLS[arch]) as r:
                    if r.status != 200:
                        raise ValueError(f"the download failed (HTTP {r.status})")
                    total, got = int(r.headers.get("Content-Length") or 0), 0
                    with open(img, "wb") as f:
                        async for chunk in r.content.iter_chunked(1 << 20):
                            f.write(chunk)
                            got += len(chunk)
                            self.installing = f"downloading JS8Call {got // 1000000} of {total // 1000000} MB" if total else f"downloading JS8Call {got // 1000000} MB"
                            self._emit("install", text=self.installing)
            if img.stat().st_size < 20_000_000:
                raise ValueError("the downloaded file is too small to be JS8Call")
            img.chmod(0o755)
            self.installing = "unpacking JS8Call"
            self._emit("install", text=self.installing)
            shutil.rmtree(app / "squashfs-root", ignore_errors=True)
            p = await asyncio.create_subprocess_exec(str(img), "--appimage-extract", cwd=str(app), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(p.communicate(), 300)
            if p.returncode != 0 or not (app / "squashfs-root" / "AppRun").is_file():
                raise ValueError("could not unpack JS8Call: " + (err or b"").decode("utf-8", "replace")[-200:])
            img.unlink(missing_ok=True)
            log.info("JS8Call %s installed in %s", VERSION, app)
        finally:
            self.installing = ""
            self._emit("install", text="")
            self._emit("state_changed")

    def request_setup(self, user: str) -> None:
        """Ask the root helper (scripts/js8_setup.sh, started by systemd when this file appears) to prepare the system."""
        self.data.parent.mkdir(parents=True, exist_ok=True)
        req = self.data.parent / "js8-setup-request"
        (self.data.parent / "js8-setup.log").write_text("waiting for the system helper...\n")
        req.write_text(f"user={user}\n")

    # ------------------------------------------------------------ switching on and off
    def write_config(self) -> None:
        s = self.settings()
        if not s["callsign"]:
            raise ValueError("set your callsign first (FT settings on the FT8/FT4 screen)")
        cfgdir = self.data / "config"
        cfgdir.mkdir(parents=True, exist_ok=True)
        ini = cfgdir / "JS8Call - rr.ini"
        if not ini.is_file():
            ini.write_text("[MultiSettings]\nCurrentName=Default\n")
        merge_ini(ini, "Configuration", {
            "MyCall": s["callsign"], "MyGrid": s["grid_square"] or "", "TCPEnabled": "true", "AcceptTCPRequests": "true", "TCPServer": API_HOST, "TCPServerPort": API_PORT,
            "TCPMaxConnections": 2, "PSKReporter": "false", "SpotToAPRS": "false", "CheckForUpdates": "false",
            "SoundInId": "js8_in", "SoundInName": "js8_in", "SoundOutId": "js8_out", "SoundOutName": "js8_out", "AudioInputChannel": "Mono", "AudioOutputChannel": "Mono"})

    async def set_on(self, on: bool) -> None:
        if on:
            if self.guard.keyed:
                raise ValueError("not while transmitting")
            if self.on:
                return
            pre = prereqs(self.data)
            if not pre["platform_ok"]:
                raise ValueError("JS8 is not available on this system")
            if not pre["system_ok"]:
                raise ValueError("the system is not prepared for JS8Call: " + ", ".join(pre["missing"]) + " (an administrator can press Prepare the system)")
            if not pre["app_ok"]:
                raise ValueError("JS8Call is not installed (an administrator can press Install JS8Call)")
            self.write_config()
            self.data.mkdir(parents=True, exist_ok=True)
            self.on = True
            self.api_up = False
            self._set_status("starting")
            await self.audio.hold_capture(True, "js8")
            self.audio.js8_feed = self.feed
            self._tasks = [asyncio.create_task(self._rx_writer()), asyncio.create_task(self._tx_reader()), asyncio.create_task(self._api_loop()), asyncio.create_task(self._tick_loop())]
            (self.data / "run").write_text("1\n")                          # radio-remote-js8.path starts JS8Call and its sound server
            log.info("JS8 on")
        elif self.on:
            await self.halt("switched off")
            self.on = False
            self.audio.js8_feed = None
            (self.data / "run").unlink(missing_ok=True)                    # the service stops by itself
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []
            self.api_up = False
            await self.audio.hold_capture(False, "js8")
            self._rxq.clear()
            self._set_status("off")
            log.info("JS8 off")
        self._emit("state", on=self.on)

    async def shutdown(self) -> None:
        try:
            await self.set_on(False)
        except Exception:
            log.exception("JS8 did not stop cleanly")

    # ------------------------------------------------------------ audio
    def feed(self, pcm: bytes | None) -> None:
        """One 20 ms frame of the radio's audio (None while we transmit: silence goes in, so JS8Call does not hear our own transmitter and its clock keeps running)."""
        self._rxq.append(pcm if pcm is not None else bytes(FRAME_BYTES))
        self._rx_event.set()

    async def _rx_writer(self) -> None:
        backoff = 1.0
        while True:
            sink = AlsaSink(RX_DEVICE)
            try:
                await sink.start()
                backoff = 1.0
                while True:
                    while not self._rxq:
                        self._rx_event.clear()
                        await self._rx_event.wait()
                    await sink.write_frame(self._rxq.popleft())
            except AudioDeviceError as e:
                if self.on and self.status != "error":
                    log.warning("JS8 audio in: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
            finally:
                await asyncio.shield(sink.stop())

    async def _tx_reader(self) -> None:
        backoff = 1.0
        while True:
            src = AlsaSource(TX_DEVICE)
            try:
                await src.start()
                backoff = 1.0
                while True:
                    self._tx.push(await src.read_frame())
            except AudioDeviceError as e:
                log.warning("JS8 audio out: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
            finally:
                await asyncio.shield(src.stop())

    # ------------------------------------------------------------ the JS8Call API
    async def _cmd(self, typ: str, value: str = "", params: dict | None = None) -> bool:
        w = self._writer
        if w is None or w.is_closing():
            return False
        try:
            w.write((json.dumps({"type": typ, "value": value, "params": params or {}}) + "\n").encode())
            await w.drain()
            return True
        except (ConnectionError, OSError):
            return False

    async def _api_loop(self) -> None:
        t0 = time.monotonic()
        while self.on:
            try:
                reader, writer = await asyncio.open_connection(API_HOST, API_PORT)
            except OSError:
                if self.status == "starting" and time.monotonic() - t0 > START_TIMEOUT_S:
                    self._set_status("error", "JS8Call did not start: see the log of the radio-remote-js8 service")
                    t0 = time.monotonic()
                await asyncio.sleep(1.0)
                continue
            self._writer, self.api_up = writer, True
            log.info("JS8Call API connected")
            await self._cmd("MODE.GET_SPEED")
            await self._cmd("RIG.GET_FREQ")
            await self._cmd("RX.GET_CALL_ACTIVITY")
            await self._sync_dial(force=True)
            self._set_status("on")
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    try:
                        await self._handle(json.loads(line))
                    except (ValueError, KeyError, TypeError):
                        log.debug("odd JS8Call message %r", line[:200])
            except (ConnectionError, OSError):
                pass
            finally:
                self._writer, self.api_up = None, False
                writer.close()
            if self.on:
                await self._abort_tx("JS8Call went away")
                self._set_status("starting" if (self.data / "run").exists() else "error", "" if (self.data / "run").exists() else "JS8Call stopped")
                t0 = time.monotonic()
                if not (self.data / "run").exists():
                    self.on = False
                    await self.audio.hold_capture(False, "js8")
                    self.audio.js8_feed = None
                    self._emit("state", on=False)
                    return
            await asyncio.sleep(1.0)

    async def _sync_dial(self, force: bool = False) -> None:
        hz = int(self.get_freq() or 0)
        if hz and (force or abs(hz - self._last_dial_sent) >= 1) and not self.transmitting:
            self._last_dial_sent = hz
            await self._cmd("RIG.SET_FREQ", params={"DIAL": hz, "OFFSET": self.offset})

    async def _tick_loop(self) -> None:
        """Once a second: JS8Call follows the radio's dial frequency (it only uses it for its own display and logs)."""
        while True:
            await asyncio.sleep(1.0)
            if self.api_up:
                await self._sync_dial()

    async def _handle(self, m: dict) -> None:
        k, pr = m.get("type", ""), m.get("params") or {}
        v = m.get("value")
        if k == "STATION.STATUS" or k == "RIG.FREQ":
            self.dial = int(pr.get("DIAL", self.dial) or 0)
            self.offset = int(pr.get("OFFSET", self.offset) or 0)
            if "SPEED" in pr:
                self.speed = int(pr["SPEED"])
            self._emit("tuning", dial=self.dial, offset=self.offset, speed=self.speed)
        elif k == "MODE.SPEED":
            self.speed = int(pr.get("SPEED", self.speed))
            self._emit("tuning", dial=self.dial, offset=self.offset, speed=self.speed)
        elif k == "RX.ACTIVITY":
            a = {"t": time.time(), "text": str(v or ""), "offset": int(pr.get("OFFSET", 0) or 0), "snr": int(pr.get("SNR", 0) or 0), "speed": int(pr.get("SPEED", 0) or 0), "from": pr.get("FROM", "")}
            self.activity.append(a)
            self._emit("activity", a=a)
        elif k == "RX.DIRECTED":
            d = {"t": time.time(), "from": pr.get("FROM", ""), "to": pr.get("TO", ""), "cmd": str(pr.get("CMD", "")).strip(), "text": str(pr.get("TEXT", v or "")), "snr": int(pr.get("SNR", 0) or 0),
                 "offset": int(pr.get("OFFSET", 0) or 0), "grid": str(pr.get("GRID", "")).strip(), "speed": int(pr.get("SPEED", 0) or 0)}
            self.directed.append(d)
            if d["from"]:
                self.heard[d["from"]] = {"call": d["from"], "t": d["t"], "snr": d["snr"], "grid": d["grid"] or self.heard.get(d["from"], {}).get("grid", ""), "offset": d["offset"]}
            self._emit("directed", d=d, heard=self._heard_list())
        elif k == "RX.SPOT":
            c = str(pr.get("CALL", ""))
            if c:
                h = self.heard.get(c, {})
                self.heard[c] = {"call": c, "t": time.time(), "snr": int(pr.get("SNR", 0) or 0), "grid": str(pr.get("GRID", "")).strip() or h.get("grid", ""), "offset": int(pr.get("OFFSET", 0) or 0)}
                self._emit("heard", heard=self._heard_list())
        elif k == "RX.CALL_ACTIVITY":
            for c, h in pr.items():
                if c and c != "_ID" and isinstance(h, dict):
                    self.heard.setdefault(c, {"call": c, "t": (h.get("UTC") or 0) / 1000.0, "snr": int(h.get("SNR", 0) or 0), "grid": str(h.get("GRID", "")).strip(), "offset": 0})
        elif k == "TX.GET_QUEUE_DEPTH" or k == "TX.QUEUE_DEPTH":
            self.queue_depth = int(pr.get("DEPTH", 0) or 0)
        elif k == "RIG.PTT":
            if pr.get("PTT"):
                await self._ptt_on()
            else:
                await self._ptt_off()
        elif k == "TX.END":
            self.sending = None
            self._emit("tx", sending=None, transmitting=self.transmitting)
        elif k == "STATION.CLOSING":
            log.info("JS8Call is closing")

    # ------------------------------------------------------------ transmit
    @staticmethod
    def clean(text: str) -> str:
        t = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
        return "".join(ch for ch in t if ch.isprintable())[:MAX_TEXT]

    async def send(self, conn: str, text: str) -> dict:
        if not self.on or not self.api_up:
            raise ValueError("switch JS8 on first and wait for it to be ready")
        if not self.guard.permitted:
            raise ValueError("transmitting is disabled (safety.allow_ptt = false)")
        mode = (self.driver.state.get("mode") if self.driver is not None else None) or ""
        if mode not in TX_MODES:
            raise ValueError(f"the radio is in {mode or '?'}: JS8 is sent as audio tones, so put it in USB, LSB or a data mode (DATA-U)")
        if getattr(self.audio, "_fd_tx", None) is not None:
            raise ValueError("FreeDV is on: switch it off before sending JS8")
        if self.guard.keyed and not self.transmitting:
            raise ValueError("the radio is transmitting")
        t = self.clean(text)
        if not t:
            raise ValueError("nothing to send")
        self.owner, self.armed_until = conn, time.monotonic() + ARM_S
        if not await self._cmd("TX.SEND_MESSAGE", t):
            raise ValueError("JS8Call is not answering")
        self.sending = t
        self._emit("tx", sending=t, transmitting=self.transmitting)
        return {"text": t}

    async def _ptt_on(self) -> None:
        conn = self.owner if time.monotonic() < self.armed_until else None
        if conn is None or not self.has_client(conn):
            await self._abort_tx("JS8Call tried to transmit but nobody had asked for it just now: stopped")
            return
        try:
            await self.guard.request(conn, True)
        except TxRefused as e:
            await self._abort_tx(f"JS8 transmit refused: {e}")
            return
        self.transmitting = True
        self._tx.q.clear()
        self._tx.gain = db_to_gain(float(self.get_cfg()["js8"]["tx_level_db"]))
        self._tx.active = True
        self.audio.arm_stream(self._tx, conn)
        self.armed_until = max(self.armed_until, time.monotonic() + ARM_S)       # the frames that follow in the same message
        self.broadcast({"t": "patch", "d": {"ptt_owner": self.guard.owner}})
        self._emit("tx", sending=self.sending, transmitting=True)
        if self._hb_task is None or self._hb_task.done():
            self._hb_task = asyncio.create_task(self._heartbeat(conn))
        log.info("JS8 transmit on")

    async def _ptt_off(self) -> None:
        if not self.transmitting:
            return
        t = self._release_task
        if t is None or t.done():
            self._release_task = asyncio.create_task(self._release())

    async def _release(self) -> None:
        await asyncio.sleep(0.45)                                    # the last audio is still on its way to the sound card
        self._tx.active = False
        self.audio.arm_stream(None)
        conn = self.guard.owner
        self.transmitting = False
        if self.guard.keyed and conn:
            try:
                await self.guard.request(conn, False)
            except TxRefused:
                pass
        self._emit("tx", sending=self.sending, transmitting=False)
        log.info("JS8 transmit off")

    async def _heartbeat(self, conn: str) -> None:
        """The server keeps the PTT heartbeat going while JS8Call transmits, and stops everything when the operator's page is gone or the guard un-keyed (time limit)."""
        try:
            while self.transmitting:
                if not self.guard.keyed or self.guard.owner != conn:
                    await self._abort_tx("the radio stopped transmitting (time limit or lost control)")
                    break
                if not self.has_client(conn):
                    await self._abort_tx("the operator left")
                    break
                await self.guard.heartbeat(conn)
                await asyncio.sleep(0.25)
        except Exception:
            log.exception("JS8 heartbeat failed")

    async def _abort_tx(self, why: str) -> None:
        """Stop JS8Call from transmitting and un-key the radio now."""
        await self._cmd("RIG.TX_HALT")
        self.last_error = why
        if time.monotonic() - self._warned_tx > 5:
            log.warning("JS8: %s", why)
            self._warned_tx = time.monotonic()
        self._emit("error", text=why)
        self._tx.active = False
        self.audio.arm_stream(None)
        self.armed_until = 0.0
        self.sending = None
        was = self.transmitting
        self.transmitting = False
        conn = self.guard.owner
        if self.guard.keyed and conn and (was or conn == self.owner):
            try:
                await self.guard.request(conn, False)
            except TxRefused:
                pass
        self._emit("tx", sending=None, transmitting=False)

    async def halt(self, why: str = "halted") -> None:
        if self.api_up:
            await self._abort_tx(why)
        else:
            self.sending, self.armed_until = None, 0.0

    # ------------------------------------------------------------ settings from the page
    async def set_speed(self, n: int) -> None:
        if n not in {s[0] for s in SPEEDS}:
            raise ValueError("unknown speed")
        if self.transmitting:
            raise ValueError("not while transmitting")
        if not await self._cmd("MODE.SET_SPEED", params={"SPEED": n}):
            raise ValueError("JS8 is not running")
        self.speed = n
        self._emit("tuning", dial=self.dial, offset=self.offset, speed=self.speed)

    async def set_offset(self, hz: int) -> None:
        if not 300 <= hz <= 2900:
            raise ValueError("the offset must be 300..2900 Hz")
        if self.transmitting:
            raise ValueError("not while transmitting")
        self.offset = hz
        if not await self._cmd("RIG.SET_FREQ", params={"DIAL": int(self.get_freq() or self.dial), "OFFSET": hz}):
            raise ValueError("JS8 is not running")
        self._emit("tuning", dial=self.dial, offset=self.offset, speed=self.speed)

    def clear(self) -> None:
        self.activity.clear()
        self.directed.clear()
        self._emit("clear")
