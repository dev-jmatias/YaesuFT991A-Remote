import { api, apiRaw } from "../api.js";
import { el, pref } from "../util.js";
import { soundGuard } from "./digital-sound.js";
import { createSquelch } from "./controls.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// SSTV screen (see docs/sstv.md). The Pi decodes the radio's audio into pictures and sends them to the page line by line; a picture the operator prepares here (chosen file, the mode's
// size, the callsign and a message printed on it) is sent to the Pi as raw RGB bytes and transmitted through the usual PTT protection.
const fmtDial = (hz) => { const s = String(Math.round(hz || 0)).padStart(8, " "); return `${s.slice(0, -6).trim() || "0"}.${s.slice(-6, -3)}.${s.slice(-3)}`; };
const fmtDur = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);
const when = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export function createSstv(host, ctx) {
  const { S, send, toast, signal } = ctx;
  const admin = S.user.role === "admin", viewer = S.user.role === "viewer";
  let info = { on: false, modes: [], bands: [], settings: {}, cur: null, images: [], max_tx_s: 120, transmitting: false };
  let rxMeta = null;                                       // the picture being received: name, size, rows so far, when it started
  let txStart = 0, txInfo = null, src = null;
  const root = el(`<div class="sstv">
    <div class="ftx-top">
      <div class="ftx-freq" title="The radio's dial frequency"><b id="sv-freq">0.000.000</b><small id="sv-rmode"></small></div>
      <label class="ftx-band">Band <select id="sv-band" aria-label="SSTV frequency"></select></label>
      <button type="button" id="sv-onoff" class="led">RX off</button>
      <div class="ftx-timebox"><span class="ftx-chip" id="sv-chip">idle</span></div>
    </div>
    <div class="row sstv-tune">
      <form class="sstv-setf" id="sv-setf"><label>Frequency <input id="sv-fin" inputmode="decimal" placeholder="437.550" autocomplete="off" aria-label="Frequency in MHz (437.550), kHz (437550) or Hz"></label><button type="button" id="sv-set">Set</button></form>
      <span class="sstv-step"><button type="button" id="sv-dn" title="Tune down by one step" aria-label="Tune down">&minus;</button><select id="sv-step" aria-label="Tuning step"></select><button type="button" id="sv-up" title="Tune up by one step" aria-label="Tune up">+</button></span>
      <label>Mode <select id="sv-mode-r" aria-label="The radio's mode"></select></label>
      <span class="sstv-sql" id="sv-sql"></span>
    </div>
    <p class="ftx-notice" id="sv-notice" hidden></p>
    <div class="sstv-main">
      <section class="sstv-col">
        <h3 class="blabel">Receiving <span class="ftx-hb"><button type="button" class="ftx-clear" id="sv-clear" title="Clear the picture area (the saved pictures stay)">Clear</button></span></h3>
        <div class="sstv-view"><canvas id="sv-rx" width="320" height="240" aria-label="The picture being received"></canvas><div class="sstv-empty" id="sv-empty">Waiting for a picture. Tune to an SSTV frequency; the picture appears line by line.</div></div>
        <div class="sstv-prog"><div class="ftx-bar"><i id="sv-bar"></i></div><span class="dim" id="sv-stat"></span></div>
      </section>
      <section class="sstv-col">
        <h3 class="blabel">Transmit</h3>
        <div class="row sstv-ctl">
          <label class="btn sstv-file">Choose a picture<input type="file" id="sv-file" accept="image/*" hidden></label>
          <label>Mode <select id="sv-mode" aria-label="SSTV mode"></select></label>
          <span class="dim" id="sv-dur"></span>
        </div>
        <div class="sstv-view tx"><canvas id="sv-tx" width="320" height="240" aria-label="The picture to transmit"></canvas></div>
        <div class="row sstv-ctl">
          <label>Fit <select id="sv-fit" aria-label="How the picture fills the frame"><option value="cover">Fill (crop)</option><option value="contain">Whole picture (bars)</option></select></label>
          <label class="chk"><input type="checkbox" id="sv-call"> My callsign</label>
          <label class="sstv-msg">Message <input id="sv-text" maxlength="40" placeholder="e.g. CQ SSTV   or   Name, QTH" autocomplete="off"></label>
          <label>Text at <select id="sv-pos" aria-label="Where the text goes"><option value="bottom">bottom</option><option value="top">top</option></select></label>
        </div>
        <div class="row sstv-ctl">
          <button type="button" id="sv-send" class="active">Send picture</button><button type="button" id="sv-halt" class="danger">Halt TX</button>
          <span class="dim" id="sv-txstat"></span>
        </div>
        <div class="ftx-bar sstv-txbar" id="sv-txbarw" hidden><i id="sv-txbar"></i></div>
      </section>
    </div>
    <h3 class="blabel">Received pictures <span class="dim" id="sv-count"></span></h3>
    <div class="sstv-gallery" id="sv-gallery"></div>
    <details class="fdv-edit ftx-set"><summary>SSTV settings</summary>
      <div class="row ftx-acc ftx-me">
        <label title="Level of the tones sent to the radio. Keep the radio's ALC at zero: more is not better.">TX level, dB <input id="sv-lvl" type="number" min="-40" max="0" step="1"></label>
        <label title="What this device plays while this screen is open (other devices are not affected). The decoder works on the Pi's own audio, so it needs no sound.">Sound here <select id="sv-snd"><option value="off">Off</option><option value="low">Low</option></select></label>
        <button type="button" id="sv-save" class="active">Apply</button>
      </div>
      <p class="dim" id="sv-setnote"></p>
    </details>
  </div>`);
  host.append(root);
  const $ = (id) => root.querySelector("#" + id);
  const can = () => !viewer && S.lease.holder?.conn === S.conn;
  const my = () => (info.settings.callsign || "").toUpperCase();
  const mode = () => info.modes.find((m) => m.key === $("sv-mode").value) || info.modes[0] || { key: "robot36", name: "Robot 36", w: 320, h: 240, seconds: 36 };
  const radioMode = (k) => { const m = S.caps?.modes || []; const d = { USB: "DATA-U", LSB: "DATA-L" }[k]; return d && m.includes(d) ? d : k; };      // a data mode skips the radio's microphone processing
  const sound = soundGuard(ctx, "sstvSound", $("sv-snd"));
  const sql = createSquelch($("sv-sql"), ctx);                                       // the radio's squelch: it must be open (0) for FM or the audio is muted between the signals
  const MODES = ["USB", "LSB", "FM", "FM-N", "AM", "AM-N", "DATA-U", "DATA-L", "DATA-FM"].filter((m) => (S.caps?.modes || []).includes(m));
  $("sv-mode-r").innerHTML = `<option value="">-</option>` + MODES.map((m) => `<option value="${m}">${m}</option>`).join("");
  const STEPS = [100, 500, 1000, 2500, 5000, 10000];
  $("sv-step").innerHTML = STEPS.map((h) => `<option value="${h}">${h >= 1000 ? h / 1000 + " kHz" : h + " Hz"}</option>`).join("");
  $("sv-step").value = String(pref.get("sstvStep", 2500));
  // "437.550" = MHz, "437550" = kHz, "437550000" = Hz
  const parseHz = (t) => { const v = parseFloat(String(t).replace(",", ".")); return !(v > 0) ? 0 : v < 1000 ? Math.round(v * 1e6) : v < 1e6 ? Math.round(v * 1e3) : Math.round(v); };
  let tracking = false;                                                               // the screen has tuned the radio: from now on its frequency and mode are remembered for the next visit

  // ------------------------------------------------------------------ top bar
  function paintTop() {
    const s = S.state;
    $("sv-freq").textContent = fmtDial(s.frequency);
    $("sv-rmode").textContent = s.mode || "";
    const sel = $("sv-band");
    if (sel.dataset.n !== String(info.bands.length)) {
      sel.dataset.n = String(info.bands.length);
      sel.innerHTML = `<option value="">Band...</option>` + info.bands.map((b) => `<option value="${b.hz}">${esc(b.name)}  ${(b.hz / 1e6).toFixed(b.hz % 1000 ? 4 : 3)}</option>`).join("");
    }
    const near = info.bands.find((b) => Math.abs(b.hz - (s.frequency || 0)) < (b.mode === "FM" ? 15000 : 3000));      // an FM preset is still "near" while the Doppler shift moves the signal
    sel.value = near ? String(near.hz) : "";
    if (document.activeElement !== $("sv-mode-r")) $("sv-mode-r").value = MODES.includes(s.mode) ? s.mode : "";
    for (const id of ["sv-fin", "sv-set", "sv-dn", "sv-up", "sv-step", "sv-mode-r"]) $(id).disabled = viewer || !can();
    if (tracking && s.frequency && MODES.includes(s.mode)) {                           // remember where the radio is (also when it is tuned with its own knob)
      const last = pref.get("sstvLast", null);
      if (!last || last.hz !== s.frequency || last.mode !== s.mode) pref.set("sstvLast", { hz: s.frequency, mode: s.mode });
    }
    sql.update();
    $("sv-onoff").textContent = info.on ? "RX on" : "RX off";
    $("sv-onoff").classList.toggle("active", !!info.on);
    for (const b of root.querySelectorAll("#sv-onoff, #sv-band, #sv-send, #sv-halt")) b.disabled = viewer || !can();
    const m = mode(), tooLong = m.seconds + 0.7 > info.max_tx_s;
    $("sv-send").disabled = viewer || !can() || !src || tooLong || !!info.transmitting;
    $("sv-dur").textContent = `Takes ${fmtDur(m.seconds)}` + (tooLong ? `: more than the transmit time limit (${Math.round(info.max_tx_s)} s)` : "");
    $("sv-dur").classList.toggle("bad", tooLong);
    const call = $("sv-call");
    call.disabled = !my();
    $("sv-notice").hidden = true;
    if (info.on && s.mode && !["USB", "LSB", "FM", "FM-N", "AM", "AM-N", "DATA-U", "DATA-L", "DATA-FM"].includes(s.mode)) { $("sv-notice").hidden = false; $("sv-notice").textContent = `The radio is in ${s.mode}: SSTV needs USB or LSB (or FM); pick a band above to set it.`; }
    else if (!my() && !viewer) { $("sv-notice").hidden = false; $("sv-notice").textContent = "Your callsign is not set, so it cannot be printed on the pictures: set it in the FT settings of the FT8/FT4 screen."; }
    paintChip();
  }
  function paintChip() {
    const c = $("sv-chip");
    let cls = "", txt = info.on ? "waiting" : "off";
    if (info.transmitting) { cls = "bad"; txt = "TX"; }
    else if (rxMeta) { cls = "ok"; txt = `${Math.round(100 * rxMeta.rows / rxMeta.h)} %`; }
    c.className = "ftx-chip " + cls; c.textContent = txt;
  }

  // ------------------------------------------------------------------ receiving
  const rxc = $("sv-rx"), rctx = rxc.getContext("2d");
  function newPicture(w, h, name, seconds) {
    rxc.width = w; rxc.height = h;
    rctx.fillStyle = "#000"; rctx.fillRect(0, 0, w, h);
    rxMeta = { w, h, name, rows: 0, seconds, t0: performance.now() };
    root.classList.add("sstv-has");
    $("sv-empty").hidden = true;
    paintProgress();
  }
  function drawRows(y0, n, b64) {
    let bin; try { bin = atob(b64); } catch { return; }
    const w = rxc.width, rgba = new Uint8ClampedArray(w * n * 4);
    for (let i = 0, j = 0; i < w * n; i++) { rgba[j++] = bin.charCodeAt(i * 3); rgba[j++] = bin.charCodeAt(i * 3 + 1); rgba[j++] = bin.charCodeAt(i * 3 + 2); rgba[j++] = 255; }
    rctx.putImageData(new ImageData(rgba, w, n), 0, y0);
    if (rxMeta) { rxMeta.rows = Math.max(rxMeta.rows, y0 + n); paintProgress(); }
  }
  function paintProgress() {
    const m = rxMeta;
    $("sv-bar").style.width = m ? (100 * m.rows / m.h).toFixed(1) + "%" : "0%";
    if (m) {
      const left = Math.max(0, Math.round(m.seconds * (1 - m.rows / m.h)));
      $("sv-stat").textContent = `Receiving ${m.name}: line ${m.rows} of ${m.h}, about ${fmtDur(left)} left`;
    }
    paintChip();
  }
  function endPicture(text) {
    rxMeta = null;
    $("sv-bar").style.width = "0%";
    $("sv-stat").textContent = text;
    paintChip();
  }
  $("sv-clear").onclick = () => {
    rxMeta = null; root.classList.remove("sstv-has"); $("sv-empty").hidden = false; $("sv-bar").style.width = "0%"; $("sv-stat").textContent = "";
    rctx.clearRect(0, 0, rxc.width, rxc.height); paintChip();
  };

  // ------------------------------------------------------------------ pictures
  function paintGallery() {
    const box = $("sv-gallery");
    $("sv-count").textContent = info.images.length ? `(${info.images.length})` : "";
    if (!info.images.length) { box.replaceChildren(el(`<div class="dim">Nothing received yet.</div>`)); return; }
    box.replaceChildren(...info.images.map((im) => {
      const f = el(`<figure class="sstv-th"><button type="button" class="sstv-open" title="Open this picture"><img loading="lazy" alt="${esc(im.mode_name)}" src="/api/sstv/images/${esc(im.name)}"></button>
        <figcaption>${esc(when(im.t))}<br>${esc(im.mode_name)} · ${(im.khz / 1000).toFixed(3)} MHz${im.partial ? " · partial" : ""}</figcaption></figure>`);
      f.querySelector("button").onclick = () => openPicture(im);
      return f;
    }));
  }
  function openPicture(im) {
    const box = el(`<div class="sstv-lb" role="dialog" aria-modal="true" aria-label="Received picture"><div class="sstv-lbc">
      <img alt="${esc(im.mode_name)}" src="/api/sstv/images/${esc(im.name)}">
      <div class="row"><a class="btn" href="/api/sstv/images/${esc(im.name)}?download=1" download>Download PNG</a>
        <button type="button" data-a="del" class="danger"${viewer ? " hidden" : ""}>Delete</button><button type="button" data-a="close" class="active">Close</button></div>
      <p class="dim">${esc(when(im.t))} · ${esc(im.mode_name)} · ${(im.khz / 1000).toFixed(3)} MHz${im.partial ? " · partial picture" : ""}</p></div></div>`);
    const close = () => { document.removeEventListener("keydown", onKey, true); box.remove(); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    box.addEventListener("click", (e) => { if (e.target === box) close(); });
    box.querySelector('[data-a="close"]').onclick = close;
    box.querySelector('[data-a="del"]').onclick = async () => {
      if (!confirm("Delete this picture from the Pi?")) return;
      try { await api(`/api/sstv/images/${im.name}/delete`, "POST", {}); close(); } catch (e) { toast(e.message); }
    };
    document.addEventListener("keydown", onKey, true);
    document.body.append(box);
  }

  // ------------------------------------------------------------------ transmit
  const txc = $("sv-tx"), tctx = txc.getContext("2d", { willReadFrequently: true });
  function drawTx() {
    const m = mode();
    if (txc.width !== m.w || txc.height !== m.h) { txc.width = m.w; txc.height = m.h; }
    tctx.fillStyle = "#000"; tctx.fillRect(0, 0, m.w, m.h);
    if (src) {
      const k = $("sv-fit").value === "cover" ? Math.max(m.w / src.width, m.h / src.height) : Math.min(m.w / src.width, m.h / src.height);
      const dw = src.width * k, dh = src.height * k;
      tctx.drawImage(src, (m.w - dw) / 2, (m.h - dh) / 2, dw, dh);
    } else {
      tctx.fillStyle = "#1c2a44"; tctx.font = `600 ${Math.round(m.h / 12)}px system-ui, sans-serif`; tctx.textAlign = "center"; tctx.textBaseline = "middle";
      tctx.fillText("Choose a picture", m.w / 2, m.h / 2); tctx.textAlign = "left";
    }
    const parts = [];
    if ($("sv-call").checked && my()) parts.push(my());
    const msg = $("sv-text").value.trim();
    if (msg) parts.push(msg);
    if (parts.length && src) {
      const text = parts.join("   ");
      let size = Math.round(m.h / 11);
      tctx.textBaseline = "alphabetic"; tctx.lineJoin = "round";
      tctx.font = `700 ${size}px system-ui, sans-serif`;
      while (size > 9 && tctx.measureText(text).width > m.w - 16) { size -= 1; tctx.font = `700 ${size}px system-ui, sans-serif`; }
      const y = $("sv-pos").value === "top" ? size + 6 : m.h - 8;
      tctx.lineWidth = Math.max(2, size / 5); tctx.strokeStyle = "#000"; tctx.strokeText(text, 8, y);
      tctx.fillStyle = "#fff"; tctx.fillText(text, 8, y);
    }
    paintTop();
  }
  $("sv-file").onchange = async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    try { src?.close?.(); src = await createImageBitmap(f); } catch { src = null; toast("That file is not a picture this browser can read."); }
    drawTx();
  };
  $("sv-mode").onchange = () => { pref.set("sstvMode", $("sv-mode").value); drawTx(); };
  for (const id of ["sv-fit", "sv-pos"]) $(id).onchange = drawTx;
  $("sv-call").onchange = () => { pref.set("sstvCall", $("sv-call").checked); drawTx(); };
  $("sv-text").oninput = drawTx;
  $("sv-send").onclick = async () => {
    const m = mode();
    if (!src) { toast("Choose a picture first."); return; }
    if (!can()) { toast("You need control of the radio to transmit: press Take control."); return; }
    if (!info.on) { toast("Switch SSTV on first (RX on)."); return; }
    if (!confirm(`Transmit this picture as ${m.name}? It takes ${fmtDur(m.seconds)}.`)) return;
    const d = tctx.getImageData(0, 0, m.w, m.h).data, rgb = new Uint8Array(m.w * m.h * 3);
    for (let i = 0, j = 0; i < d.length; i += 4) { rgb[j++] = d[i]; rgb[j++] = d[i + 1]; rgb[j++] = d[i + 2]; }
    $("sv-txstat").textContent = "Sending the picture to the Pi...";
    try { await apiRaw(`/api/sstv/tx?mode=${encodeURIComponent(m.key)}&conn=${encodeURIComponent(S.conn)}`, rgb); }
    catch (e) { toast(e.message); $("sv-txstat").textContent = "Not sent: " + e.message; }
  };
  $("sv-halt").onclick = () => { send("sstv_halt").catch(() => {}); };
  function paintTx() {
    const on = !!info.transmitting && txInfo;
    $("sv-txbarw").hidden = !on;
    if (!on) return;
    const el_ = (performance.now() - txStart) / 1000;
    $("sv-txbar").style.width = Math.min(100, 100 * el_ / txInfo.seconds).toFixed(1) + "%";
    $("sv-txstat").textContent = `Transmitting ${txInfo.name}: ${fmtDur(Math.min(el_, txInfo.seconds))} of ${fmtDur(txInfo.seconds)}`;
  }

  // ------------------------------------------------------------------ buttons
  async function setOn(on) {
    try { await send("sstv", { on }); } catch (e) { toast(e.message); }
  }
  $("sv-onoff").onclick = () => { setOn(!info.on); };
  // tuning: a typed frequency, and steps with the same rate-limited tuner as the Radio tab (clicks in a row add up while the radio catches up: a Doppler shift can be followed by tapping)
  const setFreq = () => { const hz = parseHz($("sv-fin").value); if (hz) { ctx.tuner.set(hz); $("sv-fin").value = ""; } else toast("Type a frequency, for example 437.550 (MHz)."); };
  $("sv-set").onclick = setFreq;
  $("sv-setf").onsubmit = (e) => { e.preventDefault(); setFreq(); };
  $("sv-fin").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); setFreq(); } };
  $("sv-step").onchange = () => { pref.set("sstvStep", +$("sv-step").value); ctx.setStep?.(+$("sv-step").value); };     // the arrow keys of the page tune by the same step
  $("sv-dn").onclick = () => { ctx.tune(-(+$("sv-step").value)); };
  $("sv-up").onclick = () => { ctx.tune(+$("sv-step").value); };
  $("sv-mode-r").onchange = () => { const m = $("sv-mode-r").value; if (m) send("set_mode", { mode: m }); };
  async function tune(b) {
    try { await send("set_mode", { mode: radioMode(b.mode) }); } catch { /* the frequency still moves */ }
    await send("set_frequency", { hz: b.hz });
  }
  $("sv-band").onchange = async (e) => {
    const b = info.bands.find((x) => x.hz === +e.target.value);
    if (!b) return;
    try { await tune(b); if (!info.on) await setOn(true); } catch (err) { toast(err.message); }
  };
  // Entering this screen puts the radio on the last SSTV frequency and switches reception on; leaving it switches reception off and puts back the mode and frequency the radio had.
  let away = null;
  ctx.sstvActive = async (active) => {
    sound.set(active);
    if (!can()) return;
    try {
      if (active) {
        if (S.state.freedv_on) { await send("freedv", { on: false }); toast("FreeDV switched off: SSTV and FreeDV cannot share the radio."); }
        away = { mode: S.state.mode, hz: S.state.frequency };
        const last = pref.get("sstvLast", null);                                     // the frequency and mode of the last visit, whatever they were (a preset, a typed frequency, the radio's knob)
        if (last && last.hz && MODES.includes(last.mode)) {
          await send("set_mode", { mode: last.mode });
          await send("set_frequency", { hz: last.hz });
          await ctx.settle?.(last);
        } else {
          const b = info.bands.find((x) => Math.abs(x.hz - (S.state.frequency || 0)) < 3000) || info.bands.find((x) => x.name === "20m") || info.bands[0];
          if (b) { await tune(b); await ctx.settle?.({ mode: radioMode(b.mode), hz: b.hz }); }
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
    if (document.activeElement?.closest?.(".sstv .ftx-set")) return;
    $("sv-lvl").value = info.settings.tx_level_db ?? -6;
    $("sv-lvl").disabled = $("sv-save").disabled = !admin;
    $("sv-setnote").textContent = admin ? "" : "Only an administrator can change the level.";
  }
  $("sv-save").onclick = async () => {
    try { await api("/api/config", "PUT", { sstv: { tx_level_db: +$("sv-lvl").value } }); toast("SSTV settings applied."); } catch (e) { toast(e.message); }
  };

  // ------------------------------------------------------------------ events from the Pi
  function onEvent(m) {
    if (m.k === "start") { info.cur = m; newPicture(m.w, m.h, m.name, m.seconds); }
    else if (m.k === "rows") drawRows(m.y0, m.n, m.d);
    else if (m.k === "done") {
      const name = rxMeta?.name || "picture";
      endPicture(`${m.complete ? "Received" : "Ended after " + m.rows + " lines:"} ${name}${m.complete ? "" : m.image ? " (kept as a partial picture)" : " (too short to keep)"}`);
      info.cur = null;
      if (m.image && m.image.name) { info.images = [m.image, ...info.images.filter((x) => x.name !== m.image.name)]; paintGallery(); }
    }
    else if (m.k === "vis") toast(`SSTV header for a mode this program does not know (code ${m.code}).`);
    else if (m.k === "state") { info.on = m.on; paintTop(); }
    else if (m.k === "tx") { info.transmitting = true; txInfo = m; txStart = performance.now(); $("sv-txstat").textContent = ""; paintTop(); paintTx(); }
    else if (m.k === "txdone") { info.transmitting = false; txInfo = null; paintTx(); paintTop(); $("sv-txstat").textContent = m.error ? "Stopped: " + m.error : "Transmission finished."; }
    else if (m.k === "settings") { info.settings = m.settings; paintSettings(); drawTx(); }
    else if (m.k === "deleted") { info.images = info.images.filter((x) => x.name !== m.name); paintGallery(); }
  }
  ctx.sstvEvent = onEvent;

  async function load() {
    try {
      info = await api("/api/sstv");
      const sel = $("sv-mode");
      sel.innerHTML = info.modes.map((m) => `<option value="${m.key}">${esc(m.name)} (${m.w}×${m.h}, ${fmtDur(m.seconds)})</option>`).join("");
      sel.value = info.modes.some((m) => m.key === pref.get("sstvMode", "robot36")) ? pref.get("sstvMode", "robot36") : "robot36";
      $("sv-call").checked = pref.get("sstvCall", true);
      paintSettings(); paintGallery(); drawTx(); paintTop();
      if (info.cur) {                                       // joined while a picture is coming in: show what there is so far
        newPicture(info.cur.w, info.cur.h, info.cur.name, info.cur.seconds);
        rxMeta.rows = info.cur.rows;
        const im = new Image();
        im.onload = () => { rctx.drawImage(im, 0, 0); paintProgress(); };
        im.src = "/api/sstv/current.png?t=" + Date.now();
      }
    } catch (e) { toast("SSTV: " + e.message); }
  }
  load();
  const timer = setInterval(() => { if (root.isConnected) paintTx(); else clearInterval(timer); }, 500);
  signal?.addEventListener("abort", () => { clearInterval(timer); if (ctx.sstvEvent === onEvent) ctx.sstvEvent = null; ctx.sstvActive = null; });
  return { update: paintTop };
}
