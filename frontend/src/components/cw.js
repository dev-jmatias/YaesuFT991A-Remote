import { api } from "../api.js";
import { el, pref } from "../util.js";
import { soundGuard } from "./digital-sound.js";
import { createQsoPanel, pickCallsign } from "./qso-panel.js";
import { createTxPower } from "./controls.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// CW screen (see docs/cw.md). The Pi decodes the Morse in the radio's audio into text and sends it to the page together with a small spectrum (300-1100 Hz) and the pitch and speed it found.
const fmtDial = (hz) => { const s = String(Math.round(hz || 0)).padStart(8, " "); return `${s.slice(0, -6).trim() || "0"}.${s.slice(-6, -3)}.${s.slice(-3)}`; };
const LO = 300, HI = 1100;
const CWMODES = ["CW-U", "CW-L"];

export function createCw(host, ctx) {
  const { S, send, toast, signal } = ctx;
  const viewer = S.user.role === "viewer";
  let info = { on: false, bands: [], text: "", pitch: null, wpm: null, stat: {} };
  let spec = [];
  const root = el(`<div class="cw">
    <div class="ftx-top">
      <div class="ftx-freq" title="The radio's dial frequency"><b id="cw-freq">0.000.000</b><small id="cw-rmode"></small></div>
      <label class="ftx-band">Band <select id="cw-band" aria-label="CW frequency"></select></label>
      <button type="button" id="cw-onoff" class="led">RX off</button>
      <div class="ftx-timebox"><span class="ftx-chip" id="cw-chip">off</span></div>
    </div>
    <div class="row sstv-tune">
      <form class="sstv-setf" id="cw-setf"><label>Frequency <input id="cw-fin" inputmode="decimal" placeholder="7.030" autocomplete="off" aria-label="Frequency in MHz (7.030), kHz (7030) or Hz"></label><button type="button" id="cw-set">Set</button></form>
      <span class="sstv-step"><button type="button" id="cw-dn" title="Tune down by one step" aria-label="Tune down">&minus;</button><select id="cw-step" aria-label="Tuning step"></select><button type="button" id="cw-up" title="Tune up by one step" aria-label="Tune up">+</button></span>
      <label>Mode <select id="cw-mode-r" aria-label="The radio's mode"></select></label>
      <span id="cw-pwr"></span>
    </div>
    <p class="ftx-notice" id="cw-notice" hidden></p>
    <section class="cw-scope">
      <canvas id="cw-spec" width="640" height="84" aria-label="Spectrum of the audio between 300 and 1100 Hz. Click to fix the pitch there."></canvas>
      <div class="cw-axis"><span>300</span><span>500</span><span>700</span><span>900</span><span>1100 Hz</span></div>
      <div class="row cw-ctl">
        <label>Pitch <input id="cw-pitch" type="number" min="300" max="1100" step="10" aria-label="CW pitch in Hz"> Hz</label>
        <label class="chk"><input type="checkbox" id="cw-pauto"> Auto</label>
        <label>Speed <input id="cw-wpm" type="number" min="5" max="60" step="1" aria-label="CW speed in words per minute"> WPM</label>
        <label class="chk"><input type="checkbox" id="cw-wauto"> Auto</label>
        <span class="dim" id="cw-info"></span>
      </div>
    </section>
    <h3 class="blabel">Received <span class="ftx-hb"><button type="button" class="ftx-clear" id="cw-copy" title="Copy the text">Copy</button><button type="button" class="ftx-clear" id="cw-clear" title="Clear the text (for everybody on this radio)">Clear</button></span></h3>
    <div class="cw-text" id="cw-text" role="log" aria-live="off" tabindex="0" aria-label="Decoded Morse"></div>
    <section class="cw-send" id="cw-sendbox" hidden>
      <h3 class="blabel">Send <span class="dim" id="cw-sendinfo"></span></h3>
      <p class="ftx-notice" id="cw-sendnote" hidden></p>
      <div class="row cw-ctl">
        <input id="cw-in" class="cw-in" maxlength="400" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Type what to send, then Send (Enter)" aria-label="Text to send as CW">
        <button type="button" id="cw-send" class="active">Send</button><button type="button" id="cw-halt" class="danger">Halt TX</button>
      </div>
      <div class="row cw-ctl">
        <button type="button" data-m="cq" title="CQ CQ CQ DE your callsign, twice, K">CQ</button>
        <button type="button" data-m="call" title="Your callsign twice">My call</button>
        <button type="button" data-m="rst" title="599 599">599</button>
        <button type="button" data-m="73" title="73 and your callsign, SK">73</button>
        <label>Keyer speed <input id="cw-ks" type="number" min="5" max="60" step="1" aria-label="The radio's keyer speed in words per minute"> WPM</label>
        <label class="chk" id="cw-bkw"><input type="checkbox" id="cw-bk"> Break-in</label>
        <span class="dim" id="cw-qinfo"></span>
      </div>
    </section>
    <div id="cw-log"></div>
    <details class="fdv-edit ftx-set"><summary>CW settings</summary>
      <div class="row ftx-acc ftx-me">
        <label title="What this device plays while this screen is open (other devices are not affected). The decoder works on the Pi's own audio, so it needs no sound.">Sound here <select id="cw-snd"><option value="off">Off</option><option value="low">Low</option><option value="on">Normal</option></select></label>
      </div>
    </details>
  </div>`);
  host.append(root);
  const $ = (id) => root.querySelector("#" + id);
  const can = () => !viewer && S.lease.holder?.conn === S.conn;
  const sound = soundGuard(ctx, "cwSound", $("cw-snd"));
  const my = () => (info.callsign || "").toUpperCase();
  const qso = createQsoPanel($("cw-log"), ctx, { mode: "CW", defaultRst: "599" });
  const pwr = createTxPower($("cw-pwr"), ctx);
  const MODES = ["CW-U", "CW-L", "USB", "LSB"].filter((m) => (S.caps?.modes || []).includes(m));
  $("cw-mode-r").innerHTML = `<option value="">-</option>` + MODES.map((m) => `<option value="${m}">${m}</option>`).join("");
  const STEPS = [10, 50, 100, 500, 1000];
  $("cw-step").innerHTML = STEPS.map((h) => `<option value="${h}">${h >= 1000 ? h / 1000 + " kHz" : h + " Hz"}</option>`).join("");
  $("cw-step").value = String(pref.get("cwStep", 100));
  const parseHz = (t) => { const v = parseFloat(String(t).replace(",", ".")); return !(v > 0) ? 0 : v < 1000 ? Math.round(v * 1e6) : v < 1e6 ? Math.round(v * 1e3) : Math.round(v); };
  let tracking = false;                                                     // the screen has tuned the radio: its frequency and mode are remembered for the next visit

  // ------------------------------------------------------------------ top bar
  function paintTop() {
    const s = S.state;
    $("cw-freq").textContent = fmtDial(s.frequency);
    $("cw-rmode").textContent = s.mode || "";
    const sel = $("cw-band");
    if (sel.dataset.n !== String(info.bands.length)) {
      sel.dataset.n = String(info.bands.length);
      sel.innerHTML = `<option value="">Band...</option>` + info.bands.map((b) => `<option value="${b.hz}">${esc(b.name)}  ${(b.hz / 1e6).toFixed(3)}</option>`).join("");
    }
    const near = info.bands.find((b) => Math.abs(b.hz - (s.frequency || 0)) < 3000);
    sel.value = near ? String(near.hz) : "";
    if (document.activeElement !== $("cw-mode-r")) $("cw-mode-r").value = MODES.includes(s.mode) ? s.mode : "";
    for (const id of ["cw-fin", "cw-set", "cw-dn", "cw-up", "cw-step", "cw-mode-r", "cw-onoff", "cw-band"]) $(id).disabled = viewer || !can();
    if (tracking && s.frequency && MODES.includes(s.mode)) {
      const last = pref.get("cwLast", null);
      if (!last || last.hz !== s.frequency || last.mode !== s.mode) pref.set("cwLast", { hz: s.frequency, mode: s.mode });
    }
    $("cw-onoff").textContent = info.on ? "RX on" : "RX off";
    $("cw-onoff").classList.toggle("active", !!info.on);
    const n = $("cw-notice");
    n.hidden = true;
    if (info.on && s.mode && !CWMODES.includes(s.mode)) { n.hidden = false; n.textContent = `The radio is in ${s.mode}: for CW use CW-U or CW-L (the Mode box above), so the radio's CW filter and pitch apply.`; }
    paintCtl();
    paintSend();
    qso.update();
    pwr.update();
  }
  function paintSend() {
    const box = $("cw-sendbox"), s = S.state;
    box.hidden = !info.can_send || viewer;
    if (box.hidden) return;
    const busy = !!info.sending;
    $("cw-send").disabled = !can() || !$("cw-in").value.trim();
    $("cw-halt").disabled = !can() || !(busy || (info.queue || []).length);
    $("cw-in").disabled = !can();
    for (const b of box.querySelectorAll("[data-m]")) b.disabled = !can() || (b.dataset.m !== "rst" && !my());
    const ks = $("cw-ks");
    ks.disabled = !can();
    const kz = info.keyer;
    if (document.activeElement !== ks && kz?.speed != null) ks.value = kz.speed;
    const bk = $("cw-bk");
    $("cw-bkw").hidden = !kz;
    bk.checked = !!kz?.break_in; bk.disabled = !can();
    const q = (info.queue || []).length;
    $("cw-qinfo").textContent = busy ? `Sending: ${info.sending}${q ? `  (${q} more piece${q > 1 ? "s" : ""} waiting)` : ""}` : "";
    const note = $("cw-sendnote");
    let msg = "";
    if (!CWMODES.includes(s.mode)) msg = `The radio is in ${s.mode || "?"}: switch it to CW-U or CW-L (the Mode box above) to send.`;
    else if (info.keyer?.break_in === false) msg = "Break-in is off: the radio may not transmit when it plays the text. Tick Break-in.";
    note.hidden = !msg; note.textContent = msg;
    $("cw-sendinfo").textContent = `Plays through the radio's keyer (memory ${info.slot}; its old text is put back). New: try it into a dummy load first.`;
  }
  function paintCtl() {
    const st = info.stat || {};
    const pauto = info.pitch == null, wauto = info.wpm == null;
    $("cw-pauto").checked = pauto; $("cw-wauto").checked = wauto;
    for (const id of ["cw-pauto", "cw-wauto"]) $(id).disabled = viewer || !can();
    $("cw-pitch").disabled = pauto || viewer || !can(); $("cw-wpm").disabled = wauto || viewer || !can();
    if (document.activeElement !== $("cw-pitch")) $("cw-pitch").value = pauto ? (st.hz ?? "") : Math.round(info.pitch);
    if (document.activeElement !== $("cw-wpm")) $("cw-wpm").value = wauto ? (st.wpm != null ? Math.round(st.wpm) : "") : Math.round(info.wpm);
    const chip = $("cw-chip");
    const tone = !!info.on && !!st.locked;
    chip.className = "ftx-chip " + (tone ? "ok" : "");
    chip.textContent = !info.on ? "off" : tone ? `${st.wpm ?? "?"} WPM` : "listening";
    $("cw-info").textContent = info.on ? (tone ? `Tone at ${st.hz} Hz` : "No steady tone yet") : "";
  }

  // ------------------------------------------------------------------ spectrum
  const cv = $("cw-spec"), c2 = cv.getContext("2d");
  function drawSpec() {
    const cs = getComputedStyle(cv);
    const W = cv.width, H = cv.height, n = spec.length || 64, bw = W / n;
    const bar = cs.getPropertyValue("--accent").trim() || "#22d3ee", bg = cs.getPropertyValue("--screen").trim() || "#060a14", mark = cs.getPropertyValue("--yellow").trim() || "#fbbf24";
    c2.fillStyle = bg; c2.fillRect(0, 0, W, H);
    c2.fillStyle = bar;
    for (let i = 0; i < spec.length; i++) { const h = Math.max(1, (spec[i] / 255) * (H - 6)); c2.globalAlpha = 0.35 + 0.65 * spec[i] / 255; c2.fillRect(i * bw + 1, H - h, bw - 2, h); }
    c2.globalAlpha = 1;
    const hz = info.pitch ?? info.stat?.hz;
    if (hz) { const x = ((hz - LO) / (HI - LO)) * W; c2.fillStyle = mark; c2.fillRect(x - 1, 0, 2, H); }
  }
  cv.onclick = (e) => {
    if (!can()) { toast("You need control of the radio: press Take control."); return; }
    const r = cv.getBoundingClientRect();
    const hz = Math.round((LO + ((e.clientX - r.left) / r.width) * (HI - LO)) / 10) * 10;
    setParams({ pitch: hz });
  };
  const resize = () => drawSpec();
  new ResizeObserver(resize).observe(cv);

  // ------------------------------------------------------------------ settings sent to the Pi
  async function setParams(p) {
    const next = { pitch: info.pitch, wpm: info.wpm, ...p };
    try { await send("cw_set", next); } catch (e) { toast(e.message); }
  }
  $("cw-pauto").onchange = () => setParams({ pitch: $("cw-pauto").checked ? null : Math.round(info.stat?.hz || 700) });
  $("cw-wauto").onchange = () => setParams({ wpm: $("cw-wauto").checked ? null : Math.round(info.stat?.wpm || 20) });
  $("cw-pitch").onchange = () => setParams({ pitch: Math.min(HI, Math.max(LO, +$("cw-pitch").value || 700)) });
  $("cw-wpm").onchange = () => setParams({ wpm: Math.min(60, Math.max(5, +$("cw-wpm").value || 20)) });

  // ------------------------------------------------------------------ text
  const box = $("cw-text");
  pickCallsign(box, qso, toast);
  function addText(s) {
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    box.textContent = (box.textContent + s).slice(-6000);
    if (atEnd) box.scrollTop = box.scrollHeight;
  }
  $("cw-clear").onclick = () => { send("cw_clear").catch((e) => toast(e.message)); };
  $("cw-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(box.textContent); toast("Copied."); }
    catch { const r = document.createRange(); r.selectNodeContents(box); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); toast("Selected: press Ctrl+C to copy."); }
  };

  // ------------------------------------------------------------------ sending
  async function sendText() {
    const t = $("cw-in").value.trim();
    if (!t) return;
    if (!can()) { toast("You need control of the radio to transmit: press Take control."); return; }
    try {
      const r = await send("cw_send", { text: t });
      $("cw-in").value = "";
      if (r?.dropped) toast(`Not sent (the keyer cannot send them): ${r.dropped}`);
      paintSend();
    } catch (e) { toast(e.message); }
  }
  $("cw-send").onclick = () => { sendText(); };
  $("cw-in").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); sendText(); } };
  $("cw-in").oninput = () => { $("cw-in").value = $("cw-in").value.toUpperCase(); paintSend(); };
  $("cw-halt").onclick = () => { send("cw_halt").catch((e) => toast(e.message)); };
  const MACROS = { cq: () => `CQ CQ CQ DE ${my()} ${my()} K`, call: () => `${my()} ${my()}`, rst: () => "599 599", 73: () => `73 DE ${my()} SK` };
  for (const b of root.querySelectorAll("[data-m]")) b.onclick = () => { const f = MACROS[b.dataset.m]; if (f) { $("cw-in").value = f(); sendText(); } };
  const setKeyer = async (p) => {
    try { const k = await send("cw_keyer", p); if (k) { info.keyer = k; paintSend(); } } catch (e) { toast(e.message); }
  };
  $("cw-ks").onchange = () => { setKeyer({ speed: Math.min(60, Math.max(5, Math.round(+$("cw-ks").value || 20))) }); };
  $("cw-bk").onchange = () => { setKeyer({ break_in: $("cw-bk").checked }); };

  // ------------------------------------------------------------------ buttons
  async function setOn(on) {
    try { await send("cw", { on }); } catch (e) { toast(e.message); }
  }
  $("cw-onoff").onclick = () => { setOn(!info.on); };
  const setFreq = () => { const hz = parseHz($("cw-fin").value); if (hz) { ctx.tuner.set(hz); $("cw-fin").value = ""; } else toast("Type a frequency, for example 7.030 (MHz)."); };
  $("cw-set").onclick = setFreq;
  $("cw-setf").onsubmit = (e) => { e.preventDefault(); setFreq(); };
  $("cw-fin").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); setFreq(); } };
  $("cw-step").onchange = () => { pref.set("cwStep", +$("cw-step").value); ctx.setStep?.(+$("cw-step").value); };
  $("cw-dn").onclick = () => { ctx.tune(-(+$("cw-step").value)); };
  $("cw-up").onclick = () => { ctx.tune(+$("cw-step").value); };
  $("cw-mode-r").onchange = () => { const m = $("cw-mode-r").value; if (m) send("set_mode", { mode: m }); };
  $("cw-band").onchange = async (e) => {
    const b = info.bands.find((x) => x.hz === +e.target.value);
    if (!b) return;
    try {
      if (!CWMODES.includes(S.state.mode)) await send("set_mode", { mode: S.caps?.modes?.includes("CW-U") ? "CW-U" : "USB" });
      await send("set_frequency", { hz: b.hz });
      if (!info.on) await setOn(true);
    } catch (err) { toast(err.message); }
  };
  // Entering this screen puts the radio in a CW mode (on the last CW frequency, when there is one) and switches decoding on; leaving it switches decoding off and puts back the mode and frequency the radio had.
  let away = null;
  ctx.cwActive = async (active) => {
    sound.set(active);
    if (!can()) return;
    try {
      if (active) {
        if (S.state.freedv_on) { await send("freedv", { on: false }); toast("FreeDV switched off: CW and FreeDV cannot share the radio."); }
        away = { mode: S.state.mode, hz: S.state.frequency };
        const last = pref.get("cwLast", null);
        if (last && last.hz && MODES.includes(last.mode)) {
          await send("set_mode", { mode: last.mode });
          await send("set_frequency", { hz: last.hz });
          await ctx.settle?.(last);
        } else if (!CWMODES.includes(S.state.mode) && S.caps?.modes?.includes("CW-U")) {
          await send("set_mode", { mode: "CW-U" });
          await ctx.settle?.({ mode: "CW-U", hz: S.state.frequency });
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

  // ------------------------------------------------------------------ events from the Pi
  function onEvent(m) {
    if (m.k === "text") addText(m.s);
    else if (m.k === "stat") { info.stat = m; spec = m.spec || spec; drawSpec(); paintCtl(); }
    else if (m.k === "state") { info.on = m.on; if (!m.on) { info.stat = {}; spec = []; drawSpec(); } paintTop(); }
    else if (m.k === "params") { info.pitch = m.pitch; info.wpm = m.wpm; paintCtl(); drawSpec(); }
    else if (m.k === "clear") box.textContent = "";
    else if (m.k === "keyer") { info.keyer = m.keyer; paintSend(); }
    else if (m.k === "queue") { info.queue = m.queue || []; info.sending = m.sending || null; paintSend(); }
    else if (m.k === "txdone") { info.queue = []; info.sending = null; paintSend(); if (m.error) toast("CW: " + m.error); }
  }
  ctx.cwEvent = onEvent;

  async function load() {
    try {
      info = await api("/api/cw");
      spec = info.stat?.spec || [];
      box.textContent = info.text || "";
      box.scrollTop = box.scrollHeight;
      paintTop(); drawSpec();
    } catch (e) { toast("CW: " + e.message); }
  }
  load();
  signal?.addEventListener("abort", () => { if (ctx.cwEvent === onEvent) ctx.cwEvent = null; ctx.cwActive = null; qso.close(); });
  addEventListener("rr-skin", drawSpec);
  return { update: paintTop };
}
