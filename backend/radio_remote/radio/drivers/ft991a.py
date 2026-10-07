"""FT-991A driver: native CAT over USB serial.

Session lifecycle (re-run after every disconnect):
  open port -> ID; must be 0670 -> AI1; -> full read-out -> poll loops
The radio is authoritative: every set is followed by a read-back and state only
changes from frames the radio sends (answers or auto-information).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .. import controls
from ..bands import band_for
from ..base import Capabilities, RadioDriver, RadioError
from ..cat import frame
from ..cat import ft991a_controls as fc
from ..cat.proto import proto_for
from ..cat.client import (PRIO_METER, PRIO_POLL, PRIO_PTT, PRIO_USER, CatClient, CatError, CatRejected,
                          CatTimeout)
from ..cat.transport import SerialTransport, Transport, scan_ports

log = logging.getLogger("cat")


@dataclass
class Timings:
    meter_s: float = 0.12        # S-meter (RX) / one TX meter per cycle
    backstop_s: float = 1.0      # IF; + TX; in case auto-information is missed
    slow_s: float = 0.4          # one slow setting per tick (round-robin)
    timeout_s: float = 1.0       # per-command answer timeout
    max_timeouts: int = 3        # consecutive timeouts before declaring the link dead
    backoff_min_s: float = 1.0
    backoff_max_s: float = 10.0
    healthy_after_s: float = 5.0
    settle_s: float = 0.35       # the radio needs a moment after VFO operations before its answers are current
    wake_gap_s: float = 1.4      # PS1: dummy data first, then the command "after one second and before two seconds" (manual p.15)
    prefetch_s: float = 4.0      # read the memory list this long after connecting, so the first tap on Memories is instant (0 = never)


class YaesuCatDriver(RadioDriver):
    """One driver for every Yaesu radio with native CAT. The radio model (MODEL, or radio.model in the config) selects its
    capability profile and its protocol object (cat/proto.py), which builds and parses every command."""
    MODEL = "ft991a"

    def __init__(self, cfg: dict | None = None, transport_factory=None, timings: Timings | None = None, model: str | None = None):
        model = model or (cfg or {}).get("radio", {}).get("model") or self.MODEL
        if model == "mock":
            model = self.MODEL
        self.MODEL = model
        self.proto = proto_for(model)
        super().__init__(Capabilities.load(model))
        radio_cfg = (cfg or {}).get("radio", {})
        self._port_cfg = radio_cfg.get("serial_port", "auto")
        self._baud = radio_cfg.get("baud", 38400)
        self._factory = transport_factory
        self.t = timings or Timings()
        self.client: CatClient | None = None
        self._sup: asyncio.Task | None = None
        self._consec_timeouts = 0
        self.last_error: str | None = None
        self._bg: set[asyncio.Task] = set()
        self._port_used: dict | None = None
        self._power_on_req = False
        self._wake: asyncio.Event | None = None
        self._mem_cache: list[dict] | None = None             # memory channels read from the radio (re-read on request)
        self._mem_lock = asyncio.Lock()
        self._last_vfo_freq: int | None = None                  # the VFO frequency and mode last seen outside memory mode (see _on_frame)
        self._last_vfo_mode: str | None = None
        self.state.update(connected=False, model=model, tx=False, tx_source=None)

    def diagnostics(self) -> dict:
        c = self.client
        return {"driver": f"{self.MODEL}-cat", "port": self._port_used, "stats": dict(c.stats) if c else None,
                "last_error": self.last_error, "connected": self.state.get("connected", False)}

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._sup = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        if self._sup:
            self._sup.cancel()
            await asyncio.gather(self._sup, return_exceptions=True)
            self._sup = None
        await self._teardown()

    async def _teardown(self) -> None:
        c, self.client = self.client, None
        if c:
            await c.close()
        self._update(connected=False)

    async def _supervise(self) -> None:
        backoff = self.t.backoff_min_s
        loop = asyncio.get_running_loop()
        while True:
            began = loop.time()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("CAT session ended: %s", self.last_error)
            await self._teardown()
            if loop.time() - began > self.t.healthy_after_s:
                backoff = self.t.backoff_min_s
            await self._sleep_or_wake(backoff)
            if self._power_on_req:
                try:
                    await self._send_power_on()
                    backoff = self.t.backoff_min_s          # the radio takes a few seconds to boot: keep retrying quickly
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.last_error = f"power-on failed: {type(e).__name__}: {e}"
                    log.warning("%s", self.last_error)
                continue
            backoff = min(backoff * 2, self.t.backoff_max_s)

    async def _sleep_or_wake(self, seconds: float) -> None:
        if self._wake is None:
            self._wake = asyncio.Event()
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def _open_transport(self) -> Transport:
        if self._factory:
            return await self._factory()
        port, baud = self._port_cfg, self._baud
        self._port_used = None
        if port == "auto":
            from ..cat.proto import MODELS
            known = {"0670": "FT-991A", **{m.radio_id: m.name for m in MODELS.values()}}
            found, why = await asyncio.get_running_loop().run_in_executor(None, scan_ports, baud, self.proto.RADIO_ID, known)
            if not found:
                raise RadioError(f"no {self.proto.NAME} (ID{self.proto.RADIO_ID}) found on any USB serial port: {why}")
            port, baud = found
        self._port_used = {"port": port, "baud": baud}
        return SerialTransport(port, baud)

    async def _session(self) -> None:
        transport = await self._open_transport()
        await transport.open()
        self._consec_timeouts = 0
        self.client = c = CatClient(transport, self._on_frame, timeout=self.t.timeout_s)
        await c.start()
        ident = await c.request("ID;", prio=PRIO_USER)
        if ident != f"ID{self.proto.RADIO_ID};":
            raise RadioError(f"unexpected radio ID {ident!r} (expected {self.proto.RADIO_ID} for {self.proto.NAME})")
        await c.send("AI1;")                      # re-sent on every connect: radio resets it at power-off
        await self._full_sync()
        # A previous session may have died while we were keying; never inherit CAT-TX.
        if self.state.get("tx") and self.state.get("tx_source") == "cat":
            log.warning("radio still keyed by CAT after reconnect: releasing")
            await c.send("TX0;", prio=PRIO_PTT)
            await c.request("TX;", prio=PRIO_PTT)
        self._update(connected=True)
        self.last_error = None
        log.info("%s connected", self.proto.NAME)
        if self.caps.has("memories") and hasattr(self.proto, "memory_read"):
            self._mem_cache = None                          # a new connection: the memories may have been changed while the radio was off
            if self.t.prefetch_s > 0:
                t = asyncio.create_task(self._prefetch_memories())
                self._bg.add(t)
                t.add_done_callback(self._bg.discard)
        tasks = [asyncio.create_task(f()) for f in (self._poll_meters, self._poll_backstop, self._poll_slow)]
        waiter = asyncio.create_task(c.closed.wait())
        try:
            done, _ = await asyncio.wait([*tasks, waiter], return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                if d is not waiter and d.exception():
                    raise d.exception()
            raise RadioError("CAT link lost")
        finally:
            for t in (*tasks, waiter):
                t.cancel()
            await asyncio.gather(*tasks, waiter, return_exceptions=True)

    def _control_reads(self) -> list[str]:
        """Read commands for every control this radio profile exposes (width last: depends on mode/NA)."""
        feats = {k: self.caps.has(k) for k in ("width", "if_shift", "contour", "apf", "manual_notch", "auto_notch",
                                              "dnr", "noise_blanker", "ipo", "att", "att_levels", "agc", "rit", "xit", "processor",
                                              "monitor", "tuner", "dgid", "mic_select", "audio_out_level", "audio_in_level", "amc_level", "cw_keyer", "rear_select")}
        reads = []
        for spec in controls.available(feats):
            if spec["name"] in self.proto.ENCODE and self.proto.ENCODE[spec["name"]][0] not in reads:
                reads.append(self.proto.ENCODE[spec["name"]][0])
        if feats["width"]:
            reads.append(self.proto.READ_WIDTH)
        if self.caps.has("dual_receiver"):                                   # the SUB receiver's copies of the same controls
            for spec in controls.available(feats):
                sub = self.proto.ENCODE.get(spec["name"] + "_sub")
                if sub and sub[0] not in reads:
                    reads.append(sub[0])
            if feats["width"]:
                reads.append(self.proto.READ_WIDTH_SUB)
        return reads

    async def _full_sync(self) -> None:
        c = self.client
        for cmd in (*self.proto.sync_reads(self.caps), *self._control_reads()):
            try:
                await c.request(cmd)
            except CatRejected:
                log.info("radio rejected %s during sync (not valid in current mode?)", cmd)

    # --------------------------------------------------------------- polling
    async def _read(self, cmd: str, prio: int) -> None:
        c = self.client
        if not c:
            raise RadioError("not connected")
        try:
            await c.request(cmd, prio=prio)
            self._consec_timeouts = 0
        except CatRejected:
            self._consec_timeouts = 0            # the radio answered, it just refuses this read right now
        except CatTimeout:
            self._consec_timeouts += 1
            if self._consec_timeouts >= self.t.max_timeouts:
                raise RadioError("radio stopped answering") from None

    async def _poll_meters(self) -> None:
        tx_cycle = self.proto.TX_METER_CYCLE            # PO, SWR, ALC, COMP
        i = 0
        while True:
            if self.state.get("tx") or self.state.get("tuning"):
                await self._read(tx_cycle[i % 4], PRIO_METER)
                if self.state.get("tuning") and i % 4 == 0:
                    await self._read("TX;", PRIO_METER)        # watch the keyed state closely while the tuner transmits
                i += 1
            else:
                await self._read(self.proto.S_METER_READ, PRIO_METER)
            await asyncio.sleep(self.t.meter_s)

    async def _poll_backstop(self) -> None:
        while True:
            await asyncio.sleep(self.t.backstop_s)
            for cmd in self.proto.backstop_reads(self.caps):    # VFO B / split have no auto-information we rely on
                await self._read(cmd, PRIO_POLL)

    async def _poll_slow(self) -> None:
        cmds = [*self.proto.slow_reads(self.caps), *self._control_reads()]
        i = 0
        while True:
            await asyncio.sleep(self.t.slow_s)
            await self._read(cmds[i % len(cmds)], PRIO_POLL)
            i += 1

    # ---------------------------------------------------------------- frames
    def _on_frame(self, f: str) -> None:
        try:
            fields = self.proto.decode(f)
        except frame.FrameError as e:
            log.warning("bad frame from radio: %s (frame %r)", e, f)
            return
        # BENCH-FOUND on a friend's FT-991A: in memory mode its IF answer carries the channel number but still the VFO's frequency (MC009 and MC001 both answered
        # 14.236000), so the page kept showing the VFO frequency. While the radio is in memory mode and only repeats the VFO frequency we last saw, the stored
        # frequency and mode of that channel (read from the radio's own memory list) are shown instead. A frequency that differs from the VFO's is trusted as before.
        in_memory = fields.get("vfo_memory", self.state.get("vfo_memory")) == "memory"
        if not in_memory:
            if "frequency" in fields:
                self._last_vfo_freq = fields["frequency"]
            if "mode" in fields:
                self._last_vfo_mode = fields["mode"]
        elif self._mem_cache:
            ch = fields.get("memory_channel", self.state.get("memory_channel"))
            stored = next((m for m in self._mem_cache if m["channel"] == ch), None)
            if stored:
                if "frequency" in fields and fields["frequency"] == self._last_vfo_freq and stored["frequency"] != fields["frequency"]:
                    fields["frequency"] = stored["frequency"]
                if "mode" in fields and fields["mode"] == self._last_vfo_mode and stored["mode"] != fields["mode"]:
                    fields["mode"] = stored["mode"]
        if "frequency" in fields:
            fields["band"] = band_for(fields["frequency"])
        if "frequency_b" in fields:
            fields["band_b"] = band_for(fields["frequency_b"])
        if fields.get("tx") is False:
            fields.update(po_raw=0, swr_raw=0, alc=0, comp=0)
        old_mode = self.state.get("mode")
        old_mode_b = self.state.get("mode_b")
        self._update(**fields)
        if fields.keys() & {"mode", "narrow", "width_code"}:
            s = self.state
            self._update(width_options=self.proto.width_options(s.get("mode"), s.get("narrow")),
                         width=self.proto.width_hz(s.get("mode"), s.get("narrow"), s.get("width_code")))
        if fields.keys() & {"mode_b", "narrow_sub", "width_code_sub"}:
            s = self.state
            self._update(width_options_sub=self.proto.width_options(s.get("mode_b"), s.get("narrow_sub")),
                         width_sub=self.proto.width_hz(s.get("mode_b"), s.get("narrow_sub"), s.get("width_code_sub")))
        if "mode_b" in fields and old_mode_b is not None and fields["mode_b"] != old_mode_b and self.client and hasattr(self.proto, "NARROW_REREAD_SUB"):
            t = asyncio.get_running_loop().create_task(self._reread(self.proto.NARROW_REREAD_SUB))
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        if "mode" in fields and old_mode is not None and fields["mode"] != old_mode and self.client:
            # Filter widths are per mode class: refresh them after any mode change.
            t = asyncio.get_running_loop().create_task(self._reread(self.proto.NARROW_REREAD))
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)

    async def _reread(self, cmds: list[str]) -> None:
        for cmd in cmds:
            try:
                if self.client:
                    await self.client.request(cmd, prio=PRIO_POLL)
            except CatError:
                pass

    # ------------------------------------------------------------------ sets
    def _need_client(self) -> CatClient:
        if not self.client or not self.state.get("connected"):
            raise RadioError("radio not connected")
        return self.client

    async def _guard_cat(self, coro):
        try:
            return await coro
        except CatError as e:
            raise RadioError(str(e)) from None

    async def set_frequency(self, hz: int) -> None:
        c = self._need_client()
        try:
            cmd = self.proto.freq_set(hz)
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        await self._guard_cat(c.send(cmd))
        await self._guard_cat(c.request("FA;"))            # radio's answer is the truth

    async def set_frequency_b(self, hz: int) -> None:
        c = self._need_client()
        try:
            cmd = self.proto.freq_set(hz, "B")
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        await self._guard_cat(c.send(cmd))
        await self._settled_reads(["FB;"])

    async def _settled_reads(self, reads: list[str]) -> None:
        """Read back after a short pause (the radio answers with the OLD value if asked too soon), and once more later."""
        c = self._need_client()
        await asyncio.sleep(self.t.settle_s)
        for r in reads:
            await self._guard_cat(c.request(r))
        t = asyncio.get_running_loop().create_task(self._delayed_reread(reads, max(1.0, 3 * self.t.settle_s)))
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _delayed_reread(self, reads: list[str], delay: float) -> None:
        await asyncio.sleep(delay)
        await self._reread(reads)

    async def set_split(self, on: bool) -> None:
        """Split = receive on A, transmit on B. FT3; = TX on VFO B, FT2; = TX on VFO A (bench-verified); read FT; -> FT1;/FT0;."""
        c = self._need_client()
        if self.state.get("tx") or self.state.get("tuning"):
            raise RadioError("cannot change split while transmitting")
        await self._guard_cat(c.send(self.proto.split_set(on)))
        await self._guard_cat(c.request(self.proto.SPLIT_READ))

    async def set_receivers(self, main: bool, sub: bool) -> None:
        """FTDX101 only: which receivers are listening (FR P1 P2); at least one must stay on."""
        c = self._need_client()
        if not self.caps.has("dual_receiver") or not hasattr(self.proto, "receivers_set"):
            raise RadioError("this radio has no sub receiver")
        if not (main or sub):
            raise RadioError("at least one receiver must stay on")
        if self.state.get("tx") or self.state.get("tuning"):
            raise RadioError("cannot change the receivers while transmitting")
        await self._guard_cat(c.send(self.proto.receivers_set(main, sub)))
        if main != sub:                                                  # exactly one receiver listens: the USB audio (one receiver at a time) follows it
            for cmd in self.proto.audio_out_set("main" if main else "sub"):
                try:
                    await self._guard_cat(c.send(cmd))
                except RadioError as e:                                  # one menu item refused: the listening change itself already worked
                    log.warning("audio source %s: %s", cmd, e)
        await self._guard_cat(c.request("FR;"))

    async def set_tx_receiver(self, which: str) -> None:
        """FTDX101 only: transmit on the MAIN or the SUB receiver's frequency (FT2; / FT3;). With the receive side on the other one this is split."""
        c = self._need_client()
        if not self.caps.has("dual_receiver") or not hasattr(self.proto, "tx_receiver_set"):
            raise RadioError("this radio has no sub receiver")
        if self.state.get("tx") or self.state.get("tuning"):
            raise RadioError("cannot change the transmit receiver while transmitting")
        try:
            cmd = self.proto.tx_receiver_set(which)
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        await self._guard_cat(c.send(cmd))
        await self._guard_cat(c.request("FT;"))

    async def set_active_receiver(self, which: str) -> None:
        """FTDX101 only: which receiver the radio's own dial and keys operate (VS0; MAIN, VS1; SUB). It does not transmit and does not change what you hear."""
        c = self._need_client()
        if not self.caps.has("dual_receiver") or not hasattr(self.proto, "active_receiver_set"):
            raise RadioError("this radio has no sub receiver")
        try:
            cmd = self.proto.active_receiver_set(which)
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        await self._guard_cat(c.send(cmd))
        await self._guard_cat(c.request("VS;"))

    async def vfo_op(self, op: str) -> None:
        """SV; swap, AB; copy A to B, BA; copy B to A, QS; quick split (all documented, set-only). No command selects a VFO."""
        c = self._need_client()
        cmd = {"swap": "SV;", "a_to_b": "AB;", "b_to_a": "BA;", "quick_split": "QS;"}.get(op)
        if cmd is None:
            raise RadioError("unknown VFO operation")
        if op == "quick_split" and not self.caps.has("quick_split"):
            raise RadioError("quick split is not enabled for this radio profile")
        if self.state.get("tx") or self.state.get("tuning"):
            raise RadioError("cannot change VFOs while transmitting")
        await self._guard_cat(c.send(cmd))
        await self._settled_reads(self.proto.settled_vfo_reads(self.caps))        # the radio is the truth: re-read what may have changed

    async def set_mode(self, mode: str) -> None:
        c = self._need_client()
        try:
            cmd = self.proto.mode_set(mode)
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        await self._guard_cat(c.send(cmd))
        await self._guard_cat(c.request("MD0;"))

    async def set_level(self, name: str, value: int) -> None:
        c = self._need_client()
        if name not in self.proto.LEVELS:
            raise RadioError(f"unsupported level {name}")
        if name == "rf_power" and self.state.get("band") in ("2m", "70cm") and value > 50:
            raise RadioError("VHF/UHF power is limited to 50 W")
        try:
            cmd = self.proto.level_set(name, value)
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        await self._guard_cat(c.send(cmd))
        await self._guard_cat(c.request(self.proto.level_read(name)))

    async def set_control(self, name: str, value, receiver: str = "main") -> None:
        c = self._need_client()
        spec = controls.SPEC_BY_NAME.get(name)
        if not spec or not self.caps.has(spec["feature"]):
            raise RadioError(f"control {name} not supported")
        sub = receiver == "sub"
        if receiver not in ("main", "sub"):
            raise RadioError("receiver must be main or sub")
        if sub and (not self.caps.has("dual_receiver") or (name != "width" and name + "_sub" not in self.proto.ENCODE)):
            raise RadioError(f"{name} has no separate SUB receiver setting")
        try:
            value = controls.coerce(spec, value)
        except ValueError as e:
            raise RadioError(f"{name}: {e}") from None
        if name in ("mic_select", "rear_select") and self.state.get("tx"):
            raise RadioError("cannot change the audio input while transmitting")
        if name == "width":
            opts = {o["hz"]: o["code"] for o in self.state.get("width_options_sub" if sub else "width_options", [])}
            if value not in opts:
                raise RadioError("width not available in this mode / filter setting")
            if sub:
                await self._guard_cat(c.send(self.proto.width_set(opts[value], "1")))
                await self._guard_cat(c.request(self.proto.READ_WIDTH_SUB))
            else:
                await self._guard_cat(c.send(self.proto.width_set(opts[value])))
                await self._guard_cat(c.request(self.proto.READ_WIDTH))
            return
        key = name + "_sub" if sub else name
        if key not in self.proto.ENCODE:
            raise RadioError(f"control {name} is not available on the {self.proto.NAME}")
        read, encode = self.proto.ENCODE[key]
        cmds = encode(value)
        turned_on = False
        if name == "clarifier_hz" and value and not (self.state.get("rit") or self.state.get("xit")):
            cmds.insert(1, self.proto.CLARIFIER_ON)                  # RU/RD move the clarifier knob: with no clarifier on they would have no audible effect
            turned_on = True
        for cmd in cmds:
            await self._guard_cat(c.send(cmd))
        await self._guard_cat(c.request(read))
        if turned_on:
            await self._guard_cat(c.request("RT;"))
        if name == "narrow":                       # code meanings change with narrow/wide
            await self._guard_cat(c.request(self.proto.READ_WIDTH_SUB if sub else self.proto.READ_WIDTH))

    async def set_band(self, band: str) -> None:
        c = self._need_client()
        code = self.proto.BAND_CODES.get(band)
        if code is None or band not in self.caps.data.get("bands", {}).get("list", []):
            raise RadioError(f"unsupported band {band}")
        await self._guard_cat(c.send(f"BS{code:02d};"))
        await self._guard_cat(c.request("FA;"))
        await self._guard_cat(c.request("MD0;"))

    # ------------------------------------------------------------ memory channels (read and select only: never written)
    def _need_memories(self) -> None:
        if not self.caps.has("memories") or not hasattr(self.proto, "memory_read"):
            raise RadioError("memory channels are not enabled for this radio profile")

    async def memory_channels(self, refresh: bool = False) -> list[dict]:
        """The radio's regular memory channels 001-099 (frequency, mode, tag) via the READ form of MT. Cached; refresh=True re-reads."""
        self._need_memories()
        if self._mem_cache is not None and not refresh:
            return self._mem_cache
        async with self._mem_lock:
            if self._mem_cache is not None and not refresh:
                return self._mem_cache
            c = self._need_client()
            out, consecutive, rejected, silent = [], 0, 0, []
            for ch in range(1, 100):
                if self.state.get("tx") or self.state.get("tuning"):
                    raise RadioError("not while transmitting")
                ans, answered = None, False
                for attempt in (1, 2):                           # one retry: a single missed answer must not lose the whole list
                    try:
                        # BENCH-FOUND on a real FT-991A: the answer to MT00N; always says channel 001 in its P1 field, with the data of
                        # channel N. So the answer is matched on "MT" alone (one command is in flight at a time) and the channel
                        # number is taken from the request, never from the answer.
                        ans, answered = await c.request(self.proto.memory_read(ch), prio=PRIO_POLL, expect="MT"), True
                        break
                    except CatRejected:
                        rejected, answered = rejected + 1, True   # an empty channel is answered with ?;
                        break
                    except CatTimeout:
                        log.warning("memory list: no answer to %s (attempt %d)", self.proto.memory_read(ch), attempt)
                notag = False
                if not answered:
                    # MT (with tag) is silent for this channel: try the MR read, which has no tag (frequency and mode only)
                    try:
                        ans, answered, notag = await c.request(self.proto.memory_read_notag(ch), prio=PRIO_POLL, expect="MR"), True, True
                        log.info("memory list: no answer to MT%03d; but MR%03d; -> %s", ch, ch, ans)
                    except CatRejected:
                        answered = True
                        rejected += 1
                        log.info("memory list: no answer to MT%03d;, MR%03d; answered '?;'", ch, ch)
                    except CatTimeout:
                        log.warning("memory list: no answer to MR%03d; either", ch)
                if not answered:
                    silent.append(ch)
                    consecutive += 1
                    if consecutive >= self.t.max_timeouts + 1:
                        raise RadioError(f"the radio stopped answering while reading the memories (no answer for channels {silent[-consecutive:]})") from None
                    continue
                consecutive = 0
                if ans is None:
                    continue
                try:
                    item = self.proto.decode_memory_notag(ans) if notag else self.proto.decode_memory(ans)
                except frame.FrameError as e:
                    log.warning("unreadable memory answer %r: %s", ans, e)
                    continue
                log.info("memory list: %s -> %s", self.proto.memory_read(ch), ans)                  # stored channels are few: log them all
                if item:
                    item["channel"] = ch                                                        # from the request (see above)
                    item["band"] = band_for(item["frequency"])
                    out.append(item)
            self._mem_cache = out
            log.info("memory list: %d stored channel(s): %s; %d channel(s) answered '?;'; no answer for channels %s",
                     len(out), [m["channel"] for m in out][:30], rejected, silent or "none")
            return out

    async def _prefetch_memories(self) -> None:
        """Read the memory list in the background a few seconds after connecting (the live display keeps priority), so the Memories
        list opens at once. Failure is harmless: the list is simply read on first use as before."""
        try:
            await asyncio.sleep(self.t.prefetch_s)
            if self.state.get("tx") or self.state.get("tuning") or self._mem_cache is not None:
                return
            items = await self.memory_channels()
            log.info("memory list pre-read: %d stored channel(s)", len(items))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("memory list pre-read skipped: %s", e)

    async def memory_select(self, channel: int) -> None:
        """MC P1P1P1; (manual p.11): recall a stored channel, like turning to it with the radio's memory knob."""
        self._need_memories()
        c = self._need_client()
        if self.state.get("tx") or self.state.get("tuning"):
            raise RadioError("cannot change channel while transmitting")
        try:
            cmd = self.proto.memory_select(channel)
        except frame.FrameError as e:
            raise RadioError(str(e)) from None
        if self._mem_cache is not None and not any(m["channel"] == channel for m in self._mem_cache):
            raise RadioError("that memory channel is empty")
        await self._guard_cat(c.send(cmd))
        # Logged on purpose (INFO, only when a channel is recalled): what was sent and what the radio says it is on now, so a
        # recall that does not take effect can be diagnosed from the service log.
        await asyncio.sleep(self.t.settle_s)
        log.info("memory recall: sent %s, radio answers IF with %s", cmd, await self._guard_cat(c.request("IF;")))
        await self._settled_reads(["IF;", "MD0;"])               # IF carries frequency, mode and the memory channel

    async def memory_to_vfo(self) -> None:
        """Back from memory mode to the VFO: VM; is the radio's V/M key (a toggle), so it is only sent while the radio is in memory mode."""
        self._need_memories()
        c = self._need_client()
        if self.state.get("vfo_memory") != "memory":
            return
        if self.state.get("tx") or self.state.get("tuning"):
            raise RadioError("cannot change while transmitting")
        await self._guard_cat(c.send("VM;"))
        await asyncio.sleep(self.t.settle_s)
        log.info("back to VFO: sent VM;, radio answers IF with %s", await self._guard_cat(c.request("IF;")))
        await self._settled_reads(["IF;", "FA;", "MD0;"])

    async def power_on(self) -> None:
        """PS1; (manual p.15): the radio must first receive dummy data, and PS1; follows after one but before two seconds.
        The supervisor does it (it owns the serial port while the radio is off). Bench-verified on an FT-991A (power off, power on, back online in about 3 s)."""
        if not self.caps.has("power_on_cat"):
            raise RadioError("power on is not enabled for this radio profile")
        if self.state.get("connected"):
            raise RadioError("the radio is already on")
        if self._wake is None:
            self._wake = asyncio.Event()
        self._power_on_req = True
        self._wake.set()

    async def _send_power_on(self) -> None:
        self._power_on_req = False
        if self._factory:
            transport = await self._factory()
        else:
            port = self._port_cfg if self._port_cfg != "auto" else (self._port_used or {}).get("port")
            if not port:
                from ..cat.transport import candidate_ports
                found = await asyncio.get_running_loop().run_in_executor(None, candidate_ports)
                port = found[0] if found else None
            if not port:
                raise RadioError("no USB serial port found to wake the radio")
            transport = SerialTransport(port, self._baud)
        await transport.open()
        try:
            await transport.write(b";")                     # dummy data wakes the standby UART
            await asyncio.sleep(self.t.wake_gap_s)
            await transport.write(b"PS1;")
            await asyncio.sleep(0.3)
        finally:
            await transport.close()
        log.warning("power-on sent (PS1)")

    async def power_off(self) -> None:
        """PS0; (p.14). The radio then drops the link; the supervisor keeps retrying quietly."""
        c = self._need_client()
        if self.state.get("tx"):
            raise RadioError("refusing to power off while transmitting")
        try:
            await self._guard_cat(c.send("PS0;"))
        except RadioError:
            if not c.closed.is_set():
                raise              # a real failure; a dropped link right after PS0 is the expected outcome
        log.warning("power-off sent")

    async def tune_start(self) -> None:
        """AC002; = tuner START (manual p.4: P3 2 = tuning start/stop). KEYS THE TRANSMITTER. Only TxGuard calls this."""
        c = self._need_client()
        if not self.caps.has("tuner_tune"):
            raise RadioError("tune is not enabled for this radio profile")
        await self._guard_cat(c.send(self.proto.TUNE_START, prio=PRIO_PTT))

    async def tune_stop(self) -> None:
        """AC002; is a START/STOP toggle: sending it when the radio is NOT transmitting would START a tune.
        So a stop is sent only while the radio itself reports RF going out."""
        c = self.client
        if c is None or not (self.state.get("tx") or (self.state.get("po_raw") or 0) > 0):
            return
        await self._guard_cat(c.send(self.proto.TUNE_STOP, prio=PRIO_PTT))
        log.warning("tune stop sent")

    async def set_ptt(self, on: bool) -> None:
        """Only TxGuard calls this. TX1;/TX0; then read back TX; (p.17)."""
        c = self._need_client() if on else self.client
        if c is None:
            raise RadioError("radio not connected")
        await self._guard_cat(c.send("TX1;" if on else "TX0;", prio=PRIO_PTT))
        ans = await self._guard_cat(c.request("TX;", prio=PRIO_PTT))
        if on and ans != "TX1;":
            await self._guard_cat(c.send("TX0;", prio=PRIO_PTT))
            raise RadioError(f"radio did not key (answered {ans})")
        if not on and ans == "TX1;":
            raise RadioError("radio still keyed after TX0")


class FT991ADriver(YaesuCatDriver):
    MODEL = "ft991a"


class FTDX10Driver(YaesuCatDriver):
    MODEL = "ftdx10"            # EXPERIMENTAL: written from the manual, no radio to test with


class FTDX101DDriver(YaesuCatDriver):
    MODEL = "ftdx101d"          # EXPERIMENTAL


class FTDX101MPDriver(YaesuCatDriver):
    MODEL = "ftdx101mp"         # EXPERIMENTAL


class FT710Driver(YaesuCatDriver):
    MODEL = "ft710"             # EXPERIMENTAL


DRIVERS = {"ft991a": FT991ADriver, "ftdx10": FTDX10Driver, "ftdx101d": FTDX101DDriver, "ftdx101mp": FTDX101MPDriver,
           "ft710": FT710Driver}