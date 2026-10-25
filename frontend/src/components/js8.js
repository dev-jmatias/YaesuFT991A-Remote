import { api } from "../api.js";
import { el, pref } from "../util.js";
import { soundGuard } from "./digital-sound.js";
import { createQsoPanel, pickCallsign, CALL_RE } from "./qso-panel.js";
import { createTxPower } from "./controls.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// JS8 screen (see docs/js8.md). The messaging is done by JS8Call, a separate program that runs on the Pi (installed with a button here); the Pi connects it to the radio's audio and sends what
// JS8Call hears to this page: the band activity, messages directed to anybody, the stations heard. What you type here is sent through JS8Call and the usual transmit protection.
const fmtDial = (hz) => { const s = String(Math.round(hz || 0)).padStart(8, " "); return `${s.slice(0, -6).trim() || "0"}.${s.slice(-6, -3)}.${s.slice(-3)}`; };
const clock = (t) => new Date(t * 1000).toISOString().slice(11, 19);
const MODES = ["DATA-U", "USB", "LSB", "DATA-L"];

export function createJs8(host, ctx) {
  const { S, send, toast, signal } = ctx;
  const viewer = S.user.role === "viewer", admin = S.user.role === "admin";
  let info = { on: false, status: "off", error: "", api_up: false, prereq: {}, installing: "", dial: 0, offset: 1500, speed: 0, speeds: [], bands: [], activity: [], directed: [], heard: [], sending: null, transmitting: false, settings: {}, setup_log: "" };
  let target = "";
  const root = el(`<div class="js8">
    <div class="ftx-top">
      <div class="ftx-freq" title="The radio's dial frequency"><b id="j8-freq">0.000.000</b><small id="j8-rmode"></small></div>
      <label class="ftx-band">Band <select id="j8-band" aria-label="JS8 frequency"></select></label>
      <button type="button" id="j8-onoff" class="led">JS8 off</button>
      <div class="ftx-timebox"><span class="ftx-chip" id="j8-chip">off</span></div>
    </div>
    <div class="js8-setup" id="j8-setup" hidden>
      <p id="j8-setmsg"></p>
      <div class="row">
        <button type="button" id="j8-prep" class="active">Prepare the system</button>
        <button type="button" id="j8-inst" class="active">Install JS8Call</button>
        <span class="dim" id="j8-setst"></span>
      </div>
      <pre class="js8-log" id="j8-setlog" hidden></pre>
    </div>
    <div class="row sstv-tune">
      <form class="sstv-setf" id="j8-setf"><label>Frequency <input id="j8-fin" inputmode="decimal" placeholder="14.078" autocomplete="off" aria-label="Frequency in MHz (14.078), kHz (14078) or Hz"></label><button type="button" id="j8-set">Set</button></form>
      <span class="sstv-step"><button type="button" id="j8-dn" title="Tune down by one step" aria-label="Tune down">&minus;</button><select id="j8-step" aria-label="Tuning step"></select><button type="button" id="j8-up" title="Tune up by one step" aria-label="Tune up">+</button></span>
      <label>Mode <select id="j8-mode-r" aria-label="The radio's mode"></select></label>
      <span id="j8-pwr"></span>
    </div>
    <div class="row cw-ctl">
      <label>Speed <select id="j8-speed" aria-label="JS8 speed"></select></label>
      <label title="Where your signal sits in the radio's audio (the tone offset). Click a station in the lists to see where it is; transmit where nobody else is.">TX offset <input id="j8-off" type="number" min="300" max="2900" step="10"> Hz</label>
      <span class="dim" id="j8-info"></span>
    </div>
    <p class="ftx-notice" id="j8-notice" hidden></p>
    <div class="js8-main">
      <section class="js8-col">
        <h3 class="blabel">Band activity <span class="ftx-hb"><button type="button" class="ftx-clear" id="j8-clear" title="Empty the lists (for everybody on this radio)">Clear</button></span></h3>
        <div class="js8-list" id="j8-act" role="log" aria-live="off" tabindex="0" aria-label="Everything JS8Call decodes"></div>
      </section>
      <section class="js8-col">
        <h3 class="blabel">Messages and heard stations</h3>
        <div class="js8-list js8-dir" id="j8-dir" role="log" aria-live="off" tabindex="0" aria-label="Directed messages"></div>
        <div class="js8-heard" id="j8-heard" aria-label="Stations heard"></div>
      </section>
    </div>
    <section class="cw-send" id="j8-sendbox">
      <h3 class="blabel">Send <span class="dim" id="j8-sendinfo"></span></h3>
      <p class="ftx-notice" id="j8-sendnote" hidden></p>
      <div class="row cw-ctl">
        <label>To <input id="j8-to" class="js8-to" maxlength="14" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="callsign or @ALLCALL" aria-label="Station the message is for"></label>
        <input id="j8-in" class="cw-in" maxlength="220" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Message, then Send (Enter)" aria-label="Message to send">
        <button type="button" id="j8-send" class="active">Send</button><button type="button" id="j8-halt" class="danger">Halt TX</button>
      </div>
      <div class="row cw-ctl">
        <button type="button" data-m="cq" title="CQ CQ CQ and your grid">CQ</button>
        <button type="button" data-m="hb" title="A heartbeat: tells the stations listening that you are here">Heartbeat</button>
        <button type="button" data-m="snr" title="Ask the selected station how it hears you">SNR?</button>
        <button type="button" data-m="hw" title="Ask the selected station how it copies">HW CPY?</button>
        <button type="button" data-m="73" title="73 to the selected station">73</button>
        <span class="dim" id="j8-qinfo"></span>
      </div>
    </section>
    <div id="j8-log"></div>
    <details class="fdv-edit ftx-set"><summary>JS8 settings</summary>
      <div class="row ftx-acc ftx-me">
        <label title="Level of JS8Call's audio sent to the radio. Keep the radio's ALC at zero: more is not better.">TX level, dB <input id="j8-lvl" type="number" min="-40" max="0" step="1"></label>
        <label title="What this device plays while this screen is open (other devices are not affected). JS8Call works on the Pi's own audio, so it needs no sound here.">Sound here <select id="j8-snd"><option value="off">Off</option><option value="low">Low</option><option value="on">Normal</option></select></label>
        <button type="button" id="j8-save" class="active">Apply</button>
      </div>
      <p class="dim" id="j8-setnote"></p>
    </details>
  </div>`);
  host.append(root);
  const $ = (id) => root.querySelector("#" + id);
  const can = () => !viewer && S.lease.holder?.conn === S.conn;
  const my = () => (info.settings.callsign || "").toUpperCase();
  const sound = soundGuard(ctx, "js8Sound", $("j8-snd"));
  const pwr = createTxPower($("j8-pwr"), ctx);
  const qso = createQsoPanel($("j8-log"), ctx, { mode: "JS8", defaultRst: "" });
  pickCallsign($("j8-dir"), qso, toast);
  pickCallsign($("j8-act"), qso, toast);
  const RMODES = MODES.filter((m) => (S.caps?.modes || []).includes(m));
  $("j8-mode-r").innerHTML = `<option value="">-</option>` + RMODES.map((m) => `<option value="${m}">${m}</option>`).join("");
  const STEPS = [10, 50, 100, 500, 1000];
  $("j8-step").innerHTML = STEPS.map((h) => `<option value="${h}">${h >= 1000 ? h / 1000 + " kHz" : h + " Hz"}</option>`).join("");
  $("j8-step").value = String(pref.get("js8Step", 100));
  const parseHz = (t) => { const v = parseFloat(String(t).replace(",", ".")); return !(v > 0) ? 0 : v < 1000 ? Math.round(v * 1e6) : v < 1e6 ? Math.round(v * 1e3) : Math.round(v); };
  let tracking = false;
  let auto = false;                                  // entering the screen switches JS8 on as soon as the system is ready and this page holds the controls (once; the operator's own Off wins)
  const ready = () => !!info.prereq?.system_ok && !!info.prereq?.app_ok;

  // ------------------------------------------------------------------ top bar and set-up
  function paintTop() {
    const s = S.state;
    $("j8-freq").textContent = fmtDial(s.frequency);
    $("j8-rmode").textContent = s.mode || "";
    const sel = $("j8-band");
    if (sel.dataset.n !== String(info.bands.length)) {
      sel.dataset.n = String(info.bands.length);
      sel.innerHTML = `<option value="">Band...</option>` + info.bands.map((b) => `<option value="${b.hz}">${esc(b.name)}  ${(b.hz / 1e6).toFixed(3)}</option>`).join("");
    }
    const near = info.bands.find((b) => Math.abs(b.hz - (s.frequency || 0)) < 3000);
    sel.value = near ? String(near.hz) : "";
    if (document.activeElement !== $("j8-mode-r")) $("j8-mode-r").value = RMODES.includes(s.mode) ? s.mode : "";
    for (const id of ["j8-fin", "j8-set", "j8-dn", "j8-up", "j8-step", "j8-mode-r", "j8-onoff", "j8-band"]) $(id).disabled = viewer || !can();
    $("j8-onoff").disabled = viewer || !can() || (!info.on && !ready());
    if (tracking && s.frequency && RMODES.includes(s.mode)) {
      const last = pref.get("js8Last", null);
      if (!last || last.hz !== s.frequency || last.mode !== s.mode) pref.set("js8Last", { hz: s.frequency, mode: s.mode });
    }
    if (auto && !info.on && ready() && can() && RMODES.includes(s.mode)) { auto = false; setOn(true); }
    $("j8-onoff").textContent = info.on ? "JS8 on" : "JS8 off";
    $("j8-onoff").classList.toggle("active", !!info.on);
    const chip = $("j8-chip");
    const txt = info.transmitting ? "TX" : info.status === "on" ? "listening" : info.status === "starting" ? "starting..." : info.status === "error" ? "problem" : "off";
    chip.className = "ftx-chip " + (info.transmitting ? "bad" : info.status === "on" ? "ok" : info.status === "error" ? "bad" : "");
    chip.textContent = txt;
    paintSetup(); paintTuning(); paintSend();
    pwr.update(); qso.update();
    const n = $("j8-notice");
    n.hidden = true;
    if (info.status === "error" && info.error) { n.hidden = false; n.textContent = info.error; }
    else if (info.on && s.mode && !MODES.includes(s.mode)) { n.hidden = false; n.textContent = `The radio is in ${s.mode}: JS8 is heard through its audio, so use USB, LSB or a data mode (DATA-U) in the Mode box above.`; }
  }
  function paintSetup() {
    const p = info.prereq || {}, box = $("j8-setup");
    const need = !ready();
    box.hidden = !need && !info.installing;
    if (box.hidden) return;
    let msg = "";
    if (p.platform_ok === false) msg = "JS8 needs a Raspberry Pi 4 (or newer) or a 64-bit Linux PC. It is not available on this system.";
    else if (!p.system_ok) msg = `JS8 needs a one-off preparation of the system: ${(p.missing || []).join(", ")}. An administrator presses "Prepare the system" (it installs a few packages and loads the loopback sound card; it can take a few minutes).`;
    else if (!p.app_ok) msg = "JS8 is done by JS8Call, a separate free program (GPL-3.0) from its own release page. An administrator presses \"Install JS8Call\" to download it (about 60 MB).";
    $("j8-setmsg").textContent = msg;
    $("j8-prep").hidden = p.platform_ok === false || !!p.system_ok;
    $("j8-inst").hidden = p.platform_ok === false || !p.system_ok || !!p.app_ok;
    $("j8-prep").disabled = !admin; $("j8-inst").disabled = !admin || !!info.installing;
    $("j8-setst").textContent = info.installing || (admin ? "" : "Ask an administrator.");
    const lg = $("j8-setlog");
    lg.hidden = !info.setup_log || !!p.system_ok;
    lg.textContent = info.setup_log || "";
  }
  function paintTuning() {
    const sp = $("j8-speed");
    if (sp.dataset.n !== String(info.speeds.length)) { sp.dataset.n = String(info.speeds.length); sp.innerHTML = info.speeds.map((x) => `<option value="${x.n}">${esc(x.name)} (${x.period} s)</option>`).join(""); }
    if (document.activeElement !== sp) sp.value = String(info.speed);
    if (document.activeElement !== $("j8-off")) $("j8-off").value = info.offset;
    const dis = viewer || !can() || !info.api_up || info.transmitting;
    sp.disabled = dis; $("j8-off").disabled = dis;
    $("j8-info").textContent = info.api_up ? `Your signal at ${info.offset} Hz` : "";
  }
  function paintSend() {
    const busy = !!info.sending, on = info.api_up && info.status === "on";
    $("j8-send").disabled = !can() || !on || !$("j8-in").value.trim();
    $("j8-halt").disabled = !can() || !(busy || info.transmitting);
    $("j8-in").disabled = $("j8-to").disabled = !can() || !on;
    for (const b of root.querySelectorAll("[data-m]")) b.disabled = !can() || !on || (!my()) || (["snr", "hw", "73"].includes(b.dataset.m) && !(target && CALL_RE.test(target)));
    $("j8-qinfo").textContent = info.transmitting ? "Transmitting..." : busy ? `Waiting for the next slot: ${info.sending}` : "";
    const note = $("j8-sendnote");
    let msg = "";
    if (!on) msg = info.on ? "JS8Call is starting..." : "Switch JS8 on to send.";
    else if (!my()) msg = "Your callsign is not set: set it in the FT settings of the FT8/FT4 screen.";
    else if (S.state.mode && !MODES.includes(S.state.mode)) msg = `The radio is in ${S.state.mode}: put it in DATA-U or USB to send.`;
    note.hidden = !msg; note.textContent = msg;
    $("j8-sendinfo").textContent = "Sent by JS8Call as audio tones, one message in the next time slot; it is stopped if this page goes away.";
  }

  // ------------------------------------------------------------------ lists
  const act = $("j8-act"), dir = $("j8-dir");
  const addRow = (box, html, cls) => {
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    const r = el(`<div class="js8-row ${cls || ""}">${html}</div>`);
    box.append(r);
    while (box.children.length > 300) box.firstChild.remove();
    if (atEnd) box.scrollTop = box.scrollHeight;
  };
  const actHtml = (a) => `<span class="t">${clock(a.t)}</span><span class="o">${a.offset}</span><span class="s">${a.snr > 0 ? "+" : ""}${a.snr}</span><span class="x">${esc(a.text)}</span>`;
  // JS8Call's TEXT repeats the addressee, the command and the sender ("@ALLCALL CQ G4ABC: @ALLCALL CQ CQ CQ"): show who it is for, the command and then what was said, once
  const bodyOf = (d) => {
    let t = String(d.text || "").trim();
    for (const part of [d.to, d.cmd, d.from ? d.from + ":" : ""]) { if (part && t.toUpperCase().startsWith(part.toUpperCase())) t = t.slice(part.length).trim(); }
    if (d.to && t.toUpperCase().startsWith(d.to.toUpperCase())) t = t.slice(d.to.length).trim();
    if (d.cmd && t.toUpperCase().startsWith(d.cmd.toUpperCase())) t = t.slice(d.cmd.length).trim();
    return t;
  };
  const dirHtml = (d) => `<span class="t">${clock(d.t)}</span><span class="x"><b>${esc(d.from)}</b> &rarr; ${esc(d.to)} ${esc(d.cmd)} ${esc(bodyOf(d))}</span><span class="s">${d.tx ? "TX" : (d.snr > 0 ? "+" : "") + d.snr}</span>`;
  function paintLists() {
    act.replaceChildren(...info.activity.map((a) => el(`<div class="js8-row">${actHtml(a)}</div>`)));
    dir.replaceChildren(...info.directed.map((d) => el(`<div class="js8-row${d.tx ? " tx" : my() && d.to.toUpperCase() === my() ? " mine" : ""}">${dirHtml(d)}</div>`)));
    act.scrollTop = act.scrollHeight; dir.scrollTop = dir.scrollHeight;
    paintHeard();
  }
  function paintHeard() {
    const h = $("j8-heard");
    h.replaceChildren(...info.heard.slice(0, 30).map((x) => {
      const b = el(`<button type="button" class="js8-call${target === x.call ? " sel" : ""}" title="${esc(x.grid ? x.grid + ", " : "")}heard ${clock(x.t)} UTC at ${x.offset || "?"} Hz. Tap to talk to it.">${esc(x.call)} <small>${x.snr > 0 ? "+" : ""}${x.snr}</small></button>`);
      b.onclick = () => { target = x.call; $("j8-to").value = x.call; qso.setCall(x.call); paintHeard(); paintSend(); };
      return b;
    }));
  }

  // ------------------------------------------------------------------ sending
  async function sendText(text) {
    const t = text.trim();
    if (!t) return false;
    if (!can()) { toast("You need control of the radio to transmit: press Take control."); return false; }
    try { await send("js8_send", { text: t }); return true; } catch (e) { toast(e.message); return false; }
  }
  const compose = () => { const to = $("j8-to").value.trim().toUpperCase(), m = $("j8-in").value.trim().toUpperCase(); return m ? (to ? `${to} ${m}` : m) : ""; };
  async function sendBox() { const t = compose(); if (t && await sendText(t)) $("j8-in").value = ""; paintSend(); }
  $("j8-send").onclick = () => { sendBox(); };
  $("j8-in").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); sendBox(); } };
  $("j8-in").oninput = () => { $("j8-in").value = $("j8-in").value.toUpperCase(); paintSend(); };
  $("j8-to").oninput = () => { $("j8-to").value = $("j8-to").value.toUpperCase(); target = $("j8-to").value.trim(); paintHeard(); paintSend(); };
  $("j8-halt").onclick = () => { send("js8_halt").catch((e) => toast(e.message)); };
  const grid4 = () => (info.settings.grid_square || "").slice(0, 4).toUpperCase();
  const MACROS = { cq: () => `CQ CQ CQ ${grid4()}`.trim(), hb: () => `@HB HEARTBEAT ${grid4()}`.trim(), snr: () => `${target} SNR?`, hw: () => `${target} HW CPY?`, 73: () => `${target} 73` };
  for (const b of root.querySelectorAll("[data-m]")) b.onclick = () => { const f = MACROS[b.dataset.m]; if (f) sendText(f()); };

  // ------------------------------------------------------------------ settings and buttons
  async function setOn(on) { try { await send("js8", { on }); } catch (e) { toast(e.message); } }
  $("j8-onoff").onclick = () => { auto = false; setOn(!info.on); };
  $("j8-prep").onclick = async () => {
    try { await api("/api/js8/setup", "POST", {}); toast("Asked the system to prepare itself: this can take a few minutes."); pollSetup(); } catch (e) { toast(e.message); }
  };
  $("j8-inst").onclick = async () => {
    info.installing = "downloading JS8Call"; paintSetup();
    try { info = { ...info, ...(await api("/api/js8/install", "POST", {})) }; toast("JS8Call installed."); } catch (e) { toast(e.message); }
    info.installing = ""; load();
  };
  let poll = 0;
  function pollSetup() {
    clearInterval(poll);
    let n = 0;
    poll = setInterval(async () => { n++; await load(); if (info.prereq?.system_ok || n > 120 || !root.isConnected) { clearInterval(poll); poll = 0; } }, 5000);
  }
  $("j8-speed").onchange = () => { send("js8_set", { speed: +$("j8-speed").value }).catch((e) => { toast(e.message); paintTuning(); }); };
  $("j8-off").onchange = () => { send("js8_set", { offset: Math.round(+$("j8-off").value) }).catch((e) => { toast(e.message); paintTuning(); }); };
  $("j8-clear").onclick = () => { send("js8_clear").catch((e) => toast(e.message)); };
  const setFreq = () => { const hz = parseHz($("j8-fin").value); if (hz) { ctx.tuner.set(hz); $("j8-fin").value = ""; } else toast("Type a frequency, for example 14.078 (MHz)."); };
  $("j8-set").onclick = setFreq;
  $("j8-setf").onsubmit = (e) => { e.preventDefault(); setFreq(); };
  $("j8-fin").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); setFreq(); } };
  $("j8-step").onchange = () => { pref.set("js8Step", +$("j8-step").value); ctx.setStep?.(+$("j8-step").value); };
  $("j8-dn").onclick = () => { ctx.tune(-(+$("j8-step").value)); };
  $("j8-up").onclick = () => { ctx.tune(+$("j8-step").value); };
  $("j8-mode-r").onchange = () => { const m = $("j8-mode-r").value; if (m) send("set_mode", { mode: m }); };
  $("j8-band").onchange = async (e) => {
    const b = info.bands.find((x) => x.hz === +e.target.value);
    if (!b) return;
    try {
      if (!MODES.includes(S.state.mode)) await send("set_mode", { mode: RMODES[0] || "USB" });
      await send("set_frequency", { hz: b.hz });
    } catch (err) { toast(err.message); }
  };
  function paintSettings() {
    if (document.activeElement?.closest?.(".js8 .ftx-set")) return;
    $("j8-lvl").value = info.settings.tx_level_db ?? -14;
    $("j8-lvl").disabled = $("j8-save").disabled = !admin;
    $("j8-setnote").textContent = admin ? "" : "Only an administrator can change the level.";
  }
  $("j8-save").onclick = async () => {
    try { await api("/api/config", "PUT", { js8: { tx_level_db: +$("j8-lvl").value } }); toast("JS8 settings applied."); } catch (e) { toast(e.message); }
  };

  // Entering the screen puts the radio on the last JS8 frequency (20 m, 14.078 MHz, the first time) in DATA-U and switches JS8 on when the system is ready; leaving switches it off and puts back the radio's mode and frequency.
  let away = null;
  ctx.js8Active = async (active) => {
    sound.set(active);
    auto = active;
    if (!can()) return;
    try {
      if (active) {
        await load();
        if (S.state.freedv_on) { await send("freedv", { on: false }); toast("FreeDV switched off: JS8 and FreeDV cannot share the radio."); }
        away = { mode: S.state.mode, hz: S.state.frequency };
        const last = pref.get("js8Last", null);
        const want = last && last.hz && RMODES.includes(last.mode) ? last : { hz: 14078000, mode: RMODES[0] || "USB" };
        const band = info.bands.find((b) => Math.abs(b.hz - (S.state.frequency || 0)) < 3000);
        if (!(band && MODES.includes(S.state.mode))) {
          await send("set_mode", { mode: want.mode });
          await send("set_frequency", { hz: want.hz });
          await ctx.settle?.(want);
        }
        tracking = true;
        if (ready()) { auto = false; await setOn(true); }
      } else {
        tracking = false;
        auto = false;
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
    if (m.k === "status") { info.status = m.status; info.error = m.error || ""; info.api_up = !!m.api_up; paintTop(); }
    else if (m.k === "state") { info.on = m.on; paintTop(); }
    else if (m.k === "tuning") { info.dial = m.dial; info.offset = m.offset; info.speed = m.speed; paintTuning(); }
    else if (m.k === "activity") { info.activity.push(m.a); addRow(act, actHtml(m.a)); }
    else if (m.k === "directed") { info.directed.push(m.d); info.heard = m.heard; addRow(dir, dirHtml(m.d), m.d.tx ? "tx" : my() && m.d.to.toUpperCase() === my() ? "mine" : ""); paintHeard(); }
    else if (m.k === "heard") { info.heard = m.heard; paintHeard(); }
    else if (m.k === "tx") { info.sending = m.sending; info.transmitting = !!m.transmitting; paintTop(); }
    else if (m.k === "error") { toast("JS8: " + m.text); }
    else if (m.k === "install") { info.installing = m.text; paintSetup(); }
    else if (m.k === "state_changed") { load(); }
    else if (m.k === "clear") { info.activity = []; info.directed = []; paintLists(); }
    else if (m.k === "settings") { info.settings = m.settings; paintSettings(); paintSend(); }
  }
  ctx.js8Event = onEvent;

  async function load() {
    try {
      info = { ...info, ...(await api("/api/js8")) };
      paintSettings(); paintTop(); paintLists();
    } catch (e) { toast("JS8: " + e.message); }
  }
  load();
  signal?.addEventListener("abort", () => { clearInterval(poll); if (ctx.js8Event === onEvent) ctx.js8Event = null; ctx.js8Active = null; qso.close(); });
  return { update: paintTop };
}
