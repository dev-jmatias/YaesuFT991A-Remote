"""Hamlib "NET rigctl" server: lets a logbook (or any program that speaks rigctl over TCP) on the home network follow the radio.

Scope, on purpose: the logbook can READ frequency, mode and the transmit state, and can SET frequency and mode. It can never key the
transmitter (PTT on is refused) and never switch the radio off. Off by default; when on it listens on one TCP port (default 4532) and
only answers addresses in the allow list (default: this machine and the private home-network ranges). The rigctl protocol has no
passwords, which is why the allow list exists and why the port must never be forwarded on the router.

The protocol is the line-based text protocol of Hamlib's rigctld (short commands such as `f`, `F 14074000`, long names such as
`\\get_freq`, answers are the values one per line, a set answers `RPRT 0`, a failure `RPRT -n`). The subset below is what logbooks use.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from typing import Callable

from .radio.base import RadioDriver, RadioError

log = logging.getLogger("rigctl")

PRIVATE_NETS = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "::1/128", "fe80::/10", "fc00::/7")
MAX_CLIENTS = 8
MAX_LINE = 512
HAMLIB_RIG_MODEL = 1035                       # "Yaesu FT-991" in Hamlib's model list; only used to fill dump_state

# Hamlib error codes
OK, EINVAL, EIO, ERJCTED, ENAVAIL = 0, -1, -6, -9, -11

# our mode name -> Hamlib mode name, and the default passband (Hz) we report for it
TO_HAMLIB = {"LSB": "LSB", "USB": "USB", "CW-U": "CW", "CW-L": "CWR", "AM": "AM", "AM-N": "AM", "FM": "FM", "FM-N": "FM",
             "RTTY-L": "RTTY", "RTTY-U": "RTTYR", "DATA-L": "PKTLSB", "DATA-U": "PKTUSB", "DATA-FM": "PKTFM", "PSK": "PKTUSB",
             "C4FM": "C4FM"}
FROM_HAMLIB = {"LSB": "LSB", "USB": "USB", "CW": "CW-U", "CWR": "CW-L", "AM": "AM", "FM": "FM", "RTTY": "RTTY-L", "RTTYR": "RTTY-U",
               "PKTLSB": "DATA-L", "PKTUSB": "DATA-U", "PKTFM": "DATA-FM", "C4FM": "C4FM"}
PASSBAND = {"LSB": 2400, "USB": 2400, "CW": 500, "CWR": 500, "AM": 6000, "FM": 12000, "RTTY": 500, "RTTYR": 500,
            "PKTLSB": 3000, "PKTUSB": 3000, "PKTFM": 12000, "C4FM": 12000}
HAMLIB_MODE_BITS = 0x1dbf                     # AM CW USB LSB RTTY FM CWR RTTYR PKTLSB PKTUSB PKTFM

LONG = {                                      # long name -> short letter
    "get_freq": "f", "set_freq": "F", "get_mode": "m", "set_mode": "M", "get_ptt": "t", "set_ptt": "T", "get_vfo": "v",
    "set_vfo": "V", "get_split_vfo": "s", "set_split_vfo": "S", "get_split_freq": "i", "set_split_freq": "I",
    "get_split_mode": "x", "set_split_mode": "X", "chk_vfo": "chk_vfo", "dump_state": "dump_state",
    "get_powerstat": "get_powerstat", "set_powerstat": "set_powerstat", "get_lock_mode": "get_lock_mode",
    "get_rig_info": "get_rig_info", "dump_caps": "dump_caps",
}
NAMES = {v: k for k, v in LONG.items() if len(v) == 1}                  # for the "+" (extended) answer header


def parse_allow(text: str) -> list:
    """'private' or a comma-separated list of addresses / networks -> list of ip_network. Raises ValueError on a bad entry."""
    nets = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower() == "private":
            nets += [ipaddress.ip_network(n) for n in PRIVATE_NETS]
        else:
            nets.append(ipaddress.ip_network(part, strict=False))
    if not nets:
        raise ValueError("the allow list is empty")
    return nets


def _allowed(ip: str, nets: list) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return any(a in n for n in nets if n.version == a.version)


class RigctlServer:
    def __init__(self, driver: RadioDriver, cfg: Callable[[], dict], audit: Callable[[str, str], None] | None = None):
        self.driver, self._cfg, self._audit = driver, cfg, audit
        self._server: asyncio.AbstractServer | None = None
        self._port = 0
        self.clients: dict[int, dict] = {}
        self._seq = 0
        self.refused = 0
        self.last_error = ""

    # ------------------------------------------------------------ lifecycle
    @property
    def listening(self) -> bool:
        return self._server is not None

    async def apply(self) -> None:
        """Bring the listener in line with the current config (start, stop or move to another port)."""
        c = self._cfg()["rigctl"]
        if self._server and (not c["enabled"] or c["port"] != self._port):
            await self.stop()
        if c["enabled"] and not self._server:
            try:
                nets = parse_allow(c["allow"])
                self._server = await asyncio.start_server(self._client, "0.0.0.0", c["port"], limit=MAX_LINE * 4)
                self._port = c["port"]
                self.last_error = ""
                log.info("rigctl server listening on port %d (allowed: %s)", self._port, ", ".join(str(n) for n in nets))
            except (OSError, ValueError) as e:
                self.last_error = f"could not start: {e}"
                log.error("rigctl server %s", self.last_error)

    start = apply

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            for c in list(self.clients.values()):
                c["writer"].close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 3)
            except Exception:
                pass
            self._server = None
            log.info("rigctl server stopped")

    def status(self) -> dict:
        c = self._cfg()["rigctl"]
        return {"enabled": c["enabled"], "listening": self.listening, "port": self._port or c["port"], "error": self.last_error,
                "refused": self.refused,
                "clients": [{"ip": v["ip"], "since": v["since"], "last": v["last"]} for v in self.clients.values()]}

    # ----------------------------------------------------------- connection
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        c = self._cfg()["rigctl"]
        try:
            ok = _allowed(peer, parse_allow(c["allow"]))
        except ValueError:
            ok = False
        if not ok or len(self.clients) >= MAX_CLIENTS:
            self.refused += 1
            log.warning("rigctl: refused %s (%s)", peer, "not in the allow list" if not ok else "too many clients")
            writer.close()
            return
        self._seq += 1
        cid = self._seq
        self.clients[cid] = {"ip": peer, "since": time.time(), "last": "", "writer": writer}
        log.info("rigctl: %s connected", peer)
        try:
            while True:
                try:
                    raw = await reader.readuntil(b"\n")
                except asyncio.LimitOverrunError:
                    break
                except asyncio.IncompleteReadError:
                    break
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                if line[0] in "qQ" and (len(line) == 1 or line.lower().startswith(("\\quit", "quit"))):
                    break
                self.clients[cid]["last"] = line[:40]
                try:
                    out = await self.command(line, peer)
                except Exception:                       # never let one bad line kill the listener
                    log.exception("rigctl: command failed: %r", line)
                    out = f"RPRT {EIO}\n"
                writer.write(out.encode("ascii", "replace"))
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.clients.pop(cid, None)
            writer.close()
            log.info("rigctl: %s disconnected", peer)

    # ------------------------------------------------------------- commands
    def _rprt(self, n: int) -> str:
        return f"RPRT {n}\n"

    async def command(self, line: str, peer: str = "?") -> str:
        ext = line[0] == "+"
        if ext:
            line = line[1:].lstrip()
        if line.startswith("\\"):
            word, _, rest = line[1:].partition(" ")
            cmd = LONG.get(word, word)
        else:
            cmd, _, rest = line.partition(" ")
        args = rest.split()
        st = self.driver.state
        cfg = self._cfg()["rigctl"]
        caps = self.driver.caps

        if cmd == "chk_vfo":
            return "0\n"
        if cmd == "dump_state":
            return self._dump_state()
        if cmd == "get_powerstat":
            return "1\n" if st.get("connected") else "0\n"
        if cmd in ("set_powerstat", "dump_caps"):
            log.warning("rigctl: %s refused for %s (power is not available to a logbook)", cmd, peer)
            return self._rprt(ERJCTED if cmd == "set_powerstat" else ENAVAIL)
        if cmd == "get_lock_mode":
            return "0\n"
        if cmd == "get_rig_info":
            return f"Rig: {caps.data['model']['name']} (via Radio Remote)\n"

        reads = {"f", "m", "t", "v", "s", "i", "x"}
        if cmd in reads and not st.get("connected"):
            return self._rprt(EIO)

        if cmd == "f":
            return self._ans(ext, "f", [("Frequency", int(st.get("frequency") or 0))])
        if cmd == "m":
            hm = TO_HAMLIB.get(st.get("mode") or "", "None")
            return self._ans(ext, "m", [("Mode", hm), ("Passband", PASSBAND.get(hm, 0))])
        if cmd == "t":
            return self._ans(ext, "t", [("PTT", 1 if st.get("tx") else 0)])
        if cmd == "v":
            return self._ans(ext, "v", [("VFO", "VFOA")])
        if cmd == "s":
            return self._ans(ext, "s", [("Split", 1 if st.get("split") else 0), ("TX VFO", "VFOB" if st.get("split") else "VFOA")])
        if cmd == "i":
            f = st.get("frequency_b") if st.get("split") else st.get("frequency")
            return self._ans(ext, "i", [("TX Frequency", int(f or 0))])
        if cmd == "x":
            m = st.get("mode_b") if st.get("split") else st.get("mode")
            hm = TO_HAMLIB.get(m or "", "None")
            return self._ans(ext, "x", [("TX Mode", hm), ("TX Passband", PASSBAND.get(hm, 0))])

        if cmd == "T":
            if args and args[-1] == "0":
                return self._set(ext, "T", args)                     # "unkey" is harmless: nothing here can ever key
            log.warning("rigctl: PTT on refused for %s (a logbook link cannot transmit)", peer)
            return self._set(ext, "T", args, ERJCTED)
        if cmd == "V":
            return self._set(ext, "V", args, OK if args and args[-1] in ("VFOA", "currVFO", "Main") else ENAVAIL)
        if cmd in ("S", "I", "X"):
            return self._set(ext, cmd, args, ENAVAIL)

        if cmd in ("F", "M"):
            if not cfg["set"]:
                return self._set(ext, cmd, args, ERJCTED)
            if not st.get("connected"):
                return self._set(ext, cmd, args, EIO)
            if self.driver.state.get("tx"):
                return self._set(ext, cmd, args, ERJCTED)            # never retune while transmitting
            try:
                if cmd == "F":
                    hz = int(float(args[-1])) if args else 0
                    if hz < 1 or not caps.frequency_ok(hz):
                        return self._set(ext, cmd, args, EINVAL)
                    await self.driver.set_frequency(hz)
                    log.info("rigctl: %s set frequency %d Hz", peer, hz)
                else:
                    ours = FROM_HAMLIB.get(args[-2] if len(args) >= 2 else (args[-1] if args else ""), "")
                    if not ours or ours not in caps.modes():
                        return self._set(ext, cmd, args, EINVAL if not ours else ENAVAIL)
                    await self.driver.set_mode(ours)
                    log.info("rigctl: %s set mode %s", peer, ours)
            except (ValueError, IndexError):
                return self._set(ext, cmd, args, EINVAL)
            except RadioError as e:
                log.warning("rigctl: %s", e)
                return self._set(ext, cmd, args, EIO)
            if self._audit:
                self._audit("rigctl_set", f"{peer}: {line[:60]}")
            return self._set(ext, cmd, args)

        return self._rprt(ENAVAIL)

    def _ans(self, ext: bool, cmd: str, items: list[tuple[str, object]]) -> str:
        if ext:
            return f"{NAMES[cmd]}:\n" + "".join(f"{k}: {v}\n" for k, v in items) + "RPRT 0\n"
        return "".join(f"{v}\n" for _, v in items)

    def _set(self, ext: bool, cmd: str, args: list[str], code: int = OK) -> str:
        if ext:
            return f"{NAMES.get(cmd, cmd)}: {' '.join(args)}\n" + self._rprt(code)
        return self._rprt(code)

    def _dump_state(self) -> str:
        """The capability dump a Hamlib client reads when it connects (protocol version 1 layout)."""
        rng = self.driver.caps.data["frequency"]["ranges"]
        rows = "".join(f"{lo} {hi} 0x{HAMLIB_MODE_BITS:x} -1 -1 0x3 0x0\n" for lo, hi in rng) + "0 0 0 0 0 0 0\n"
        tx = "".join(f"{lo} {hi} 0x{HAMLIB_MODE_BITS:x} 5000 100000 0x3 0x0\n" for lo, hi in rng) + "0 0 0 0 0 0 0\n"
        return ("1\n" f"{HAMLIB_RIG_MODEL}\n" "0\n" + rows + tx
                + f"0x{HAMLIB_MODE_BITS:x} 1\n0 0\n"                      # tuning steps
                + f"0x{HAMLIB_MODE_BITS:x} 3000\n0 0\n"                    # filters
                + "0\n0\n0\n0\n0\n"                                       # max RIT, XIT, IF shift, announces, (spare)
                + "0\n0\n"                                                # preamp, attenuator lists
                + "0x0\n0x0\n0x0\n0x0\n0x0\n0x0\n"                        # has get/set func, level, parm
                + "vfo_ops=0x0\nptt_type=0x1\ntargetable_vfo=0x0\nhas_set_vfo=1\nhas_get_vfo=1\nhas_set_freq=1\nhas_get_freq=1\n"
                  "timeout=0\ndone\n")
