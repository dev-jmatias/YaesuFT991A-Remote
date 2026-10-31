import { api } from "../api.js";
import { el, pref } from "../util.js";
import { soundGuard } from "./digital-sound.js";
import { createQsoPanel, pickCallsign } from "./qso-panel.js";
import { createTxPower } from "./controls.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// RTTY screen (see docs/rtty.md). The Pi decodes the two tones (mark and space) of the radio's audio into text and sends it to the page with a spectrum (300-3000 Hz) and the tones it found;
// typed text is sent to the Pi, made into Baudot tones and transmitted through the usual PTT protection.
const fmtDial = (hz) => { const s = String(Math.round(hz || 0)).padStart(8, " "); return `${s.slice(0, -6).trim() || "0"}.${s.slice(-6, -3)}.${s.slice(-3)}`; };
const LO = 300, HI = 3000;
const MODES = ["DATA-U", "USB", "LSB", "DATA-L"];

export function createRtty(host, ctx) {
  const { S, send, toast, signal } = ctx;
  const viewer = S.user.role === "viewer";
  let info = { on: false, bands: [], bauds: [45.45, 50, 75, 100], shifts: [170, 200, 425, 850], params: { baud: 45.45, shift: 170, lo: null, rev: false, usos: true }, text: "", stat: {}, settings: {}, sending: null, queue: [], max_tx_s: 120 };
  let spec = [];
  const root = el(`<div class="rtty">
    <div class="ftx-top">
      <div class="ftx-freq" title="The radio's dial frequency"><b id="rt-freq">0.000.000</b><small id="rt-rmode"></small></div>
      <label class="ftx-band">Band <select id="rt-band" aria-label="RTTY frequency"></select></label>
      <button type="button" id="rt-onoff" class="led">RX off</button>
      <div class="ftx-timebox"><span class="ftx-chip" id="rt-chip">off</span></div>
    </div>
    <div class="row sstv-tune">
      <form class="sstv-setf" id="rt-setf"><label>Frequency <input id="rt-fin" inputmode="decimal" placeholder="14.080" autocomplete="off" aria-label="Frequency in MHz (14.080), kHz (14080) or Hz"></label><button type="button" id="rt-set">Set</button></form>
      <span class="sstv-step"><button type="button" id="rt-dn" title="Tune down by one step" aria-label="Tune down">&minus;</button><select id="rt-step" aria-label="Tuning step"></select><button type="button" id="rt-up" title="Tune up by one step" aria-label="Tune up">+</button></span>
      <label>Mode <select id="rt-mode-r" aria-label="The radio's mode"></select></label>
      <span id="rt-pwr"></span>
    </div>
    <p class="ftx-notice" id="rt-notice" hidden></p>
    <section class="cw-scope">
      <canvas id="rt-spec" width="640" height="96" aria-label="Spectrum of the audio between 300 and 3000 Hz. Click to put the lower tone there."></canvas>
      <div class="cw-axis"><span>300</span><span>1000</span><span>1700</span><span>2400</span><span>3000 Hz</span></div>
      <div class="row cw-ctl">
        <label>Baud <select id="rt-baud" aria-label="Baud rate"></select></label>
        <label>Shift <select id="rt-shift" aria-label="Shift in Hz"></select></label>
        <label>Lower tone <input id="rt-lo" type="number" min="300" max="2900" step="5" aria-label="Lower tone in Hz"> Hz</label>
        <label class="chk"><input type="checkbox" id="rt-auto"> Auto</label>
        <label class="chk" title="Swap mark and space: needed in lower sideband, or when the other station sends the other way round"><input type="checkbox" id="rt-rev"> Reverse</label>
        <label class="chk" title="Unshift on space: after a space the letters shift is assumed (what most stations send)"><input type="checkbox" id="rt-usos"> USOS</label>
        <span class="dim" id="rt-info"></span>
      </div>
    </section>
    <h3 class="blabel">Received <span class="ftx-hb"><button type="button" class="ftx-clear" id="rt-copy" title="Copy the text">Copy</button><button type="button" class="ftx-clear" id="rt-clear" title="Clear the text (for everybody on this radio)">Clear</button></span></h3>
    <div class="cw-text" id="rt-text" role="log" aria-live="off" tabindex="0" aria-label="Decoded RTTY"></div>
    <section class="cw-send" id="rt-sendbox">
      <h3 class="blabel">Send <span class="dim" id="rt-sendinfo"></span></h3>
      <p class="ftx-notice" id="rt-sendnote" hidden></p>
      <div class="row cw-ctl">
        <input id="rt-in" class="cw-in" maxlength="400" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Type what to send, then Send (Enter)" aria-label="Text to send as RTTY">
        <button type="button" id="rt-send" class="active">Send</button><button type="button" id="rt-halt" class="danger">Halt TX</button>
      </div>
      <div class="row cw-ctl">
        <button type="button" data-m="cq" title="CQ CQ CQ DE your callsign, twice, K">CQ</button>
        <button type="button" data-m="call" title="Your callsign twice">My call</button>
        <button type="button" data-m="rst" title="UR RST 599 599">RST</button>
        <button type="button" data-m="73" title="73 and your callsign, SK">73</button>
        <span class="dim" id="rt-qinfo"></span>
      </div>
    </section>
    <div id="rt-log"></div>
    <details class="fdv-edit ftx-set"><summary>RTTY settings</summary>
      <div class="row ftx-acc ftx-me">
        <label title="Level of the tones sent to the radio. Keep the radio's ALC at zero: more is not better.">TX level, dB <input id="rt-lvl" type="number" min="-40" max="0" step="1"></label>
        <label title="What this device plays while this screen is open (other devices are not affected). The decoder works on the Pi's own audio, so it needs no sound.">Sound here <select id="rt-snd"><option value="off">Off</option><option value="low">Low</option><option value="on">Normal</option></select></label>
        <button type="button" id="rt-save" class="active">Apply</button>
      </div>
      <p class="dim" id="rt-setnote"></p>
    </details>
  </div>`);
  host.append(root);
  const $ = (id) => root.querySelector("#" + id);
  const can = () => !viewer && S.lease.holder?.conn === S.conn;
  const admin = S.user.role === "admin";
  const my = () => (info.settings.callsign || "").toUpperCase();
  const sound = soundGuard(ctx, "rttySound", $("rt-snd"));
  const pwr = createTxPower($("rt-pwr"), ctx);
  const qso = createQsoPanel($("rt-log"), ctx, { mode: "RTTY", defaultRst: "599" });
  pickCallsign($("rt-text"), qso, toast);
  const RMODES = MODES.filter((m) => (S.caps?.modes || []).includes(m));
  $("rt-mode-r").innerHTML = `<option value="">-</option>` + RMODES.map((m) => `<option value="${m}">${m}</option>`).join("");
  const STEPS = [10, 50, 100, 500, 1000];
  $("rt-step").innerHTML = STEPS.map((h) => `<option value="${h}">${h >= 1000 ? h / 1000 + " kHz" : h + " Hz"}</option>`).join("");
  $("rt-step").value = String(pref.get("rttyStep", 100));
  const parseHz = (t) => { const v = parseFloat(String(t).replace(",", ".")); return !(v > 0) ? 0 : v < 1000 ? Math.round(v * 1e6) : v < 1e6 ? Math.round(v * 1e3) : Math.round(v); };
  let tracking = false;

  // ------------------------------------------------------------------ top bar
  function paintTop() {
    const s = S.state;
    $("rt-freq").textContent = fmtDial(s.frequency);
    $("rt-rmode").textContent = s.mode || "";
    const sel = $("rt-band");
    if (sel.dataset.n !== String(info.bands.length)) {
      sel.dataset.n = String(info.bands.length);
      sel.innerHTML = `<option value="">Band...</option>` + info.bands.map((b) => `<option value="${b.hz}">${esc(b.name)}  ${(b.hz / 1e6).toFixed(3)}</option>`).join("");
    }
    const near = info.bands.find((b) => Math.abs(b.hz - (s.frequency || 0)) < 3000);
    sel.value = near ? String(near.hz) : "";
    if (document.activeElement !== $("rt-mode-r")) $("rt-mode-r").value = RMODES.includes(s.mode) ? s.mode : "";
    for (const id of ["rt-fin", "rt-set", "rt-dn", "rt-up", "rt-step", "rt-mode-r", "rt-onoff", "rt-band"]) $(id).disabled = viewer || !can();
    if (tracking && s.frequency && RMODES.includes(s.mode)) {
      const last = pref.get("rttyLast", null);
      if (!last || last.hz !== s.frequency || last.mode !== s.mode) pref.set("rttyLast", { hz: s.frequency, mode: s.mode });
    }
    $("rt-onoff").textContent = info.on ? "RX on" : "RX off";
    $("rt-onoff").classList.toggle("active", !!info.on);
    pwr.update(); qso.update();
    paintCtl(); paintSend();
  }
  function paintCtl() {
    const st = info.stat || {}, p = info.params;
    const bs = $("rt-baud"), ss = $("rt-shift");
    if (bs.dataset.n !== String(info.bauds.length)) { bs.dataset.n = String(info.bauds.length); bs.innerHTML = info.bauds.map((b) => `<option value="${b}">${b}</option>`).join(""); }
    if (ss.dataset.n !== String(info.shifts.length)) { ss.dataset.n = String(info.shifts.length); ss.innerHTML = info.shifts.map((b) => `<option value="${b}">${b} Hz</option>`).join(""); }
    if (document.activeElement !== bs) bs.value = String(p.baud);
    if (document.activeElement !== ss) ss.value = String(p.shift);
    const auto = p.lo == null;
    $("rt-auto").checked = auto; $("rt-rev").checked = !!p.rev; $("rt-usos").checked = !!p.usos;
    if (document.activeElement !== $("rt-lo")) $("rt-lo").value = auto ? (st.lo ?? "") : Math.round(p.lo);
    for (const id of ["rt-baud", "rt-shift", "rt-auto", "rt-rev", "rt-usos"]) $(id).disabled = viewer || !can();
    $("rt-lo").disabled = auto || viewer || !can();
    const tone = !!info.on && !!st.locked;
    const chip = $("rt-chip");
    chip.className = "ftx-chip " + (tone ? "ok" : "");
    chip.textContent = !info.on ? "off" : tone ? `${p.baud} baud` : "listening";
    $("rt-info").textContent = info.on ? (tone ? `Tones ${st.lo} / ${st.hi} Hz, mark ${st.mark} Hz` : "No RTTY tones yet") : "";
    const n = $("rt-notice");
    n.hidden = true;
    if (info.on && S.state.mode && !MODES.includes(S.state.mode)) { n.hidden = false; n.textContent = `The radio is in ${S.state.mode}: RTTY is heard through its audio, so use USB, LSB or a data mode (DATA-U) in the Mode box above.`; }
  }
  function paintSend() {
    const s = S.state, busy = !!info.sending;
    $("rt-send").disabled = !can() || !$("rt-in").value.trim() || !info.on;
    $("rt-halt").disabled = !can() || !(busy || (info.queue || []).length);
    $("rt-in").disabled = !can();
    for (const b of root.querySelectorAll("[data-m]")) b.disabled = !can() || !info.on || !my();
    const q = (info.queue || []).length;
    $("rt-qinfo").textContent = busy ? `Sending: ${info.sending}${q ? `  (${q} more piece${q > 1 ? "s" : ""} waiting)` : ""}` : "";
    const note = $("rt-sendnote");
    let msg = "";
    if (!info.on) msg = "Switch RTTY on (RX on) to send.";
    else if (s.mode && !["USB", "LSB", "DATA-U", "DATA-L"].includes(s.mode)) msg = `The radio is in ${s.mode}: put it in USB, LSB or DATA-U to send.`;
    else if (!my()) msg = "Your callsign is not set, so the CQ, call and 73 buttons are off: set it in the FT settings of the FT8/FT4 screen.";
    note.hidden = !msg; note.textContent = msg;
    $("rt-sendinfo").textContent = "Sent as audio tones at the lower tone shown above.";
  }

  // ------------------------------------------------------------------ spectrum
  const cv = $("rt-spec"), c2 = cv.getContext("2d");
  const xOf = (hz) => ((hz - LO) / (HI - LO)) * cv.width;
  function drawSpec() {
    const cs = getComputedStyle(cv);
    const W = cv.width, H = cv.height, n = spec.length || 96, bw = W / n;
    const bar = cs.getPropertyValue("--accent").trim() || "#22d3ee", bg = cs.getPropertyValue("--screen").trim() || "#060a14";
    const mk = cs.getPropertyValue("--green").trim() || "#34d399", sp = cs.getPropertyValue("--red").trim() || "#f43f5e";
    c2.fillStyle = bg; c2.fillRect(0, 0, W, H);
    c2.fillStyle = bar;
    for (let i = 0; i < spec.length; i++) { const h = Math.max(1, (spec[i] / 255) * (H - 6)); c2.globalAlpha = 0.35 + 0.65 * spec[i] / 255; c2.fillRect(i * bw + 1, H - h, bw - 2, h); }
    c2.globalAlpha = 1;
    const st = info.stat || {};
    if (st.mark) { c2.fillStyle = mk; c2.fillRect(xOf(st.mark) - 1, 0, 2, H); c2.fillStyle = sp; c2.fillRect(xOf(st.mark === st.lo ? st.hi : st.lo) - 1, 0, 2, H); }
  }
  cv.onclick = (e) => {
    if (!can()) { toast("You need control of the radio: press Take control."); return; }
    const r = cv.getBoundingClientRect();
    setParams({ lo: Math.round((LO + ((e.clientX - r.left) / r.width) * (HI - LO)) / 5) * 5 });
  };
  new ResizeObserver(() => drawSpec()).observe(cv);

  // ------------------------------------------------------------------ settings sent to the Pi
  async function setParams(p) {
    try { await send("rtty_set", { ...info.params, ...p }); } catch (e) { toast(e.message); }
  }
  $("rt-baud").onchange = () => { setParams({ baud: +$("rt-baud").value }); };
  $("rt-shift").onchange = () => { setParams({ shift: +$("rt-shift").value }); };
  $("rt-auto").onchange = () => { setParams({ lo: $("rt-auto").checked ? null : Math.round(info.stat?.lo || 2125) }); };
  $("rt-lo").onchange = () => { setParams({ lo: Math.min(2900, Math.max(300, +$("rt-lo").value || 2125)) }); };
  $("rt-rev").onchange = () => { setParams({ rev: $("rt-rev").checked }); };
  $("rt-usos").onchange = () => { setParams({ usos: $("rt-usos").checked }); };

  // ------------------------------------------------------------------ text
  const box = $("rt-text");
  function addText(s) {
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    box.textContent = (box.textContent + s).slice(-6000);
    if (atEnd) box.scrollTop = box.scrollHeight;
  }
  $("rt-clear").onclick = () => { send("rtty_clear").catch((e) => toast(e.message)); };
  $("rt-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(box.textContent); toast("Copied."); }
    catch { const r = document.createRange(); r.selectNodeContents(box); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); toast("Selected: press Ctrl+C to copy."); }
  };

  // ------------------------------------------------------------------ sending
  async function sendText() {
    const t = $("rt-in").value.trim();
    if (!t) return;
    if (!can()) { toast("You need control of the radio to transmit: press Take control."); return; }
    try {
      const r = await send("rtty_send", { text: t });
      $("rt-in").value = "";
      if (r?.dropped) toast(`Not sent (RTTY cannot carry them): ${r.dropped}`);
      paintSend();
    } catch (e) { toast(e.message); }
  }
  $("rt-send").onclick = () => { sendText(); };
  $("rt-in").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); sendText(); } };
  $("rt-in").oninput = () => { $("rt-in").value = $("rt-in").value.toUpperCase(); paintSend(); };
  $("rt-halt").onclick = () => { send("rtty_halt").catch((e) => toast(e.message)); };
  const MACROS = { cq: () => `CQ CQ CQ DE ${my()} ${my()} ${my()} PSE K`, call: () => `${my()} ${my()}`, rst: () => "UR RST 599 599", 73: () => `73 DE ${my()} SK` };
  for (const b of root.querySelectorAll("[data-m]")) b.onclick = () => { const f = MACROS[b.dataset.m]; if (f) { $("rt-in").value = f(); sendText(); } };

  // ------------------------------------------------------------------ buttons
  async function setOn(on) {
    try { await send("rtty", { on }); } catch (e) { toast(e.message); }
  }
  $("rt-onoff").onclick = () => { setOn(!info.on); };
  const setFreq = () => { const hz = parseHz($("rt-fin").value); if (hz) { ctx.tuner.set(hz); $("rt-fin").value = ""; } else toast("Type a frequency, for example 14.080 (MHz)."); };
  $("rt-set").onclick = setFreq;
  $("rt-setf").onsubmit = (e) => { e.preventDefault(); setFreq(); };
  $("rt-fin").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); setFreq(); } };
  $("rt-step").onchange = () => { pref.set("rttyStep", +$("rt-step").value); ctx.setStep?.(+$("rt-step").value); };
  $("rt-dn").onclick = () => { ctx.tune(-(+$("rt-step").value)); };
  $("rt-up").onclick = () => { ctx.tune(+$("rt-step").value); };
  $("rt-mode-r").onchange = () => { const m = $("rt-mode-r").value; if (m) send("set_mode", { mode: m }); };
  $("rt-band").onchange = async (e) => {
    const b = info.bands.find((x) => x.hz === +e.target.value);
    if (!b) return;
    try {
      if (!MODES.includes(S.state.mode)) await send("set_mode", { mode: RMODES[0] || "USB" });
      await send("set_frequency", { hz: b.hz });
      if (!info.on) await setOn(true);
    } catch (err) { toast(err.message); }
  };
  let away = null;
  ctx.rttyActive = async (active, keep) => {
    sound.set(active);
    if (!active && keep && info.on) { tracking = false; away = null; return true; }              // leaving for the Radio tab: it keeps running until it is switched off here
    if (!can()) return;
    try {
      if (active) {
        if (S.state.freedv_on) { await send("freedv", { on: false }); toast("FreeDV switched off: RTTY and FreeDV cannot share the radio."); }
        away = { mode: S.state.mode, hz: S.state.frequency };
        if (info.on) { tracking = true; return; }                                      // still running from before: leave the radio where it is
        const last = pref.get("rttyLast", null);
        if (last && last.hz && RMODES.includes(last.mode)) {
          await send("set_mode", { mode: last.mode });
          await send("set_frequency", { hz: last.hz });
          await ctx.settle?.(last);
        } else if (!MODES.includes(S.state.mode) && RMODES.length) {
          await send("set_mode", { mode: RMODES[0] });
          await ctx.settle?.({ mode: RMODES[0], hz: S.state.frequency });
        }
        tracking = true;
        await setOn(true);
      } else {
        tracking = false;
        if (info.on) await setOn(false);
        if (away) {
          const a = away; away = null;
          if (a.mode) await send("set_mode", { mode: a.mode });
          if (a.hz) await send("set_frequency", { hz: a.hz });
          await ctx.settle?.(a);
        }
      }
    } catch (e) { toast(e.message); }
  };

  // ------------------------------------------------------------------ settings
  function paintSettings() {
    if (document.activeElement?.closest?.(".rtty .ftx-set")) return;
    $("rt-lvl").value = info.settings.tx_level_db ?? -6;
    $("rt-lvl").disabled = $("rt-save").disabled = !admin;
    $("rt-setnote").textContent = admin ? "" : "Only an administrator can change the level.";
  }
  $("rt-save").onclick = async () => {
    try { await api("/api/config", "PUT", { rtty: { tx_level_db: +$("rt-lvl").value } }); toast("RTTY settings applied."); } catch (e) { toast(e.message); }
  };

  // ------------------------------------------------------------------ events from the Pi
  function onEvent(m) {
    if (m.k === "text") addText(m.s);
    else if (m.k === "stat") { info.stat = m; spec = m.spec || spec; drawSpec(); paintCtl(); }
    else if (m.k === "state") { info.on = m.on; if (!m.on) { info.stat = {}; spec = []; drawSpec(); } paintTop(); }
    else if (m.k === "params") { info.params = m.params; paintCtl(); drawSpec(); }
    else if (m.k === "clear") box.textContent = "";
    else if (m.k === "queue") { info.queue = m.queue || []; info.sending = m.sending || null; paintSend(); }
    else if (m.k === "txdone") { info.queue = []; info.sending = null; paintSend(); if (m.error) toast("RTTY: " + m.error); }
    else if (m.k === "settings") { info.settings = m.settings; paintSettings(); paintSend(); }
  }
  ctx.rttyEvent = onEvent;

  async function load() {
    try {
      info = await api("/api/rtty");
      spec = info.stat?.spec || [];
      box.textContent = info.text || "";
      box.scrollTop = box.scrollHeight;
      paintSettings(); paintTop(); drawSpec();
    } catch (e) { toast("RTTY: " + e.message); }
  }
  load();
  signal?.addEventListener("abort", () => { if (ctx.rttyEvent === onEvent) ctx.rttyEvent = null; ctx.rttyActive = null; qso.close(); });
  addEventListener("rr-skin", drawSpec);
  return { update: paintTop };
}
