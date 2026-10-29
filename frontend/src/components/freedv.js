import { api } from "../api.js";
import { pct } from "../meter-cal.js";
import { el, pref, qrzUrl } from "../util.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// FreeDV digital voice (RADE V1 and RADE V2, both from the optional RADE library). The Pi decodes the radio's modem tones into speech for every listener and encodes the operator's
// microphone into modem tones while that connection holds PTT (see docs/freedv.md). This tab: on/off, the mode, the preset channels and the
// transmit level. By the FreeDV convention frequencies below 10 MHz use LSB and above use USB.
export const ssbFor = (hz) => (hz < 10_000_000 ? "LSB" : "USB");
const MODE_LABEL = { RADE: "RADE V1", RADE2: "RADE V2" };
const modeName = (m) => MODE_LABEL[m] || m;
const fmtMHz = (hz) => (hz / 1e6).toFixed(hz % 1000 ? 4 : 3);

export function createFreeDV(host, ctx) {
  const { S, send, toast, signal } = ctx;
  const admin = S.user.role === "admin";
  let info = { channels: [], modes: ["RADE"], tx_level_db: -3, data_mode: false };
  // The radio mode FreeDV uses: by default plain USB/LSB (LSB below 10 MHz, the FreeDV convention); the operator can choose one (USB, LSB, DATA-U, DATA-L, FM), remembered in this browser.
  const RMODES = ["USB", "LSB", "DATA-U", "DATA-L", "FM"].filter((m) => (S.caps?.modes || []).includes(m));
  const chosen = () => { const m = pref.get("freedvRadioMode", ""); return RMODES.includes(m) ? m : ""; };       // "" = automatic
  const radioModeFor = (hz) => chosen() || ssbFor(hz);
  const modeOk = (m) => (chosen() ? m === chosen() : ["USB", "LSB"].includes(m));
  const root = el(`<div class="fdv">
    <h2>FreeDV</h2>
    <div class="fdv-status" aria-live="polite"><i class="dot" id="fdv-dot"></i><span id="fdv-text">FreeDV is off</span></div>
    <div class="fdv-heard dim" id="fdv-heard" hidden></div>
    <div class="fdv-tune" id="fdv-tune">
      <canvas id="fdv-spec" height="72" aria-label="Spectrum of the received audio"></canvas>
      <div class="fdv-scale"><span>0</span><span>1 kHz</span><span>2 kHz</span><span>3 kHz</span><span>4 kHz</span></div>
      <div id="fdv-tunetext" class="fdv-tunetext"></div>
      <div class="row fdv-fine">
        <span class="dim">Dial:</span>
        <button type="button" data-df="-100">−100 Hz</button><button type="button" data-df="-10">−10</button><button type="button" data-df="10">+10</button><button type="button" data-df="100">+100 Hz</button>
        <button type="button" id="fdv-centre" hidden title="Move the radio dial by the offset the software is correcting, so the signal sits where the modem expects it">Centre the dial</button>
        <button type="button" id="fdv-again" title="Forget the tuning found so far and search again">Search again</button>
      </div>
    </div>
    <div class="row fdv-ctl">
      <button class="led" id="fdv-onoff">Switch FreeDV on</button>
      <label class="fdv-mode">Mode <select id="fdv-mode" aria-label="FreeDV mode"></select></label>
      <label class="fdv-mode" title="The mode the radio is put in when you pick a channel or switch FreeDV on. Automatic is LSB below 10 MHz and USB above, which is what the other FreeDV stations use. A DATA mode or FM needs the radio set up for it (see the FreeDV help).">Radio mode <select id="fdv-rmode" aria-label="The radio's mode for FreeDV"></select></label>
    </div>
    <p class="dim fdv-note" id="fdv-note" hidden></p>
    <p class="fdv-widthwarn" id="fdv-alcwarn" hidden>The radio's <b>ALC</b> is above its mark while you transmit: the radio is compressing the FreeDV signal, and a compressed RADE signal cannot be decoded at the other station. Lower the <b>Transmit level of the modem tones</b> (or the radio's power) until the ALC just touches.</p>
    <p class="fdv-widthwarn" id="fdv-widthwarn" hidden><span id="fdv-widthtext"></span> <button type="button" id="fdv-widen" class="active">Widen</button></p>
    <label class="chk fdv-autow" id="fdv-autow-row" hidden title="RADE V1 occupies about 750 to 2250 Hz of the audio. A radio in a narrow filter (a DATA mode starts at 500 Hz on an FT-991A) can never decode it. With this on, a too-narrow filter is opened to about 2000 Hz (DATA modes) or 2400 Hz (USB/LSB) when FreeDV is switched on or the mode changes.">
      <input type="checkbox" id="fdv-autow"> Open the radio's filter automatically (RADE V1 needs about 1500 Hz of audio)</label>
    <div class="row fdv-rade" id="fdv-rade-row" hidden>
      <button type="button" id="fdv-rade-install" class="active" hidden>Install RADE</button>
      <span class="dim" id="fdv-rade-msg"></span>
    </div>
    <h3 class="blabel">Channels</h3>
    <div class="fdv-ch" id="fdv-ch"></div>
    <h3 class="blabel">On the air now (FreeDV Reporter)</h3>
    <div class="fdv-rep" id="fdv-rep"><div class="dim" id="fdv-rep-status"></div><div id="fdv-rep-list"></div></div>
    <div class="fdv-level"><label>Transmit level of the modem tones <b id="fdv-lv"></b> dB
      <input type="range" id="fdv-lvr" min="-40" max="3" step="1" aria-label="FreeDV transmit level"></label>
      <span class="dim" id="fdv-lvnote"></span>
      <label class="chk" id="fdv-test-row" hidden title="While this is ticked the PTT button sends a steady 1500 Hz tone at this level (for up to 15 s) instead of your voice.">
        <input type="checkbox" id="fdv-test"> Level test: the PTT button sends a steady tone instead of my voice</label>
      <p class="dim fdv-note" id="fdv-test-note" hidden>Use a dummy load or a free frequency. Hold the PTT button and raise the level above until the radio's <b>ALC</b> just starts to move,
        then lower it a step: more is not better, an overdriven signal is distorted. The <b>power (PO)</b> should then be close to the radio's set power (the tone reads about 2 dB higher than the voice signal).
        The tone stops by itself after 15 seconds. Untick this when you are done.</p></div>
    <details class="fdv-edit" id="fdv-repset" hidden><summary>FreeDV Reporter settings</summary>
      <p class="dim">Announce this station on <b>qso.freedv.org</b> while FreeDV is on (callsign, grid square, frequency, mode, whether you are transmitting) and list who else is on the air.
        Off by default. <b>Your callsign and grid square are shown publicly on that site.</b> The Pi connects only while FreeDV is switched on.</p>
      <label class="chk"><input type="checkbox" id="rp-enabled"> Switch the link to FreeDV Reporter on</label>
      <label class="chk"><input type="checkbox" id="rp-announce"> Announce this station</label>
      <label class="chk"><input type="checkbox" id="rp-watch"> Show who is on the air</label>
      <div class="row fdv-rpf"><label>Callsign <input id="rp-call" maxlength="15" autocapitalize="characters" size="10"></label>
        <label>Grid square <input id="rp-grid" maxlength="8" size="8" placeholder="IO91wm"></label>
        <label>Message <input id="rp-msg" maxlength="100" size="24" placeholder="optional, e.g. CQ FreeDV"></label>
        <button type="button" id="rp-save" class="active">Apply</button></div>
      <div class="row fdv-rpf"><label title="Sent at the end of every RADE V1 transmission, so other stations' FreeDV programs show who is calling. Empty = the callsign above (first 8 characters).">Callsign sent with every transmission (RADE V1) <input id="rp-txcall" maxlength="8" autocapitalize="characters" size="10" placeholder="same as above"></label></div>
    </details>
    <details class="fdv-edit" id="fdv-edit" hidden><summary>Edit the channel list</summary>
      <div id="fdv-rows"></div>
      <div class="row"><button type="button" id="fdv-add">Add a channel</button><button type="button" id="fdv-save" class="active">Save the list</button></div>
      <p class="dim">Frequency in MHz (the dial frequency). Below 10 MHz the radio is put in LSB, above in USB.</p>
    </details>
    <p class="dim fdv-help">Receive: switch FreeDV on, tune the signal until the status says it is locked, the speech is played in your browser. Transmit: arm the microphone
      (microphone button), hold PTT and talk; the Pi turns your voice into modem tones. Keep the radio's ALC barely moving (lower the level above if it moves a lot) and the speech
      processor off. Details in the manual (FreeDV).</p>
  </div>`);
  host.append(root);
  const $ = (id) => root.querySelector("#" + id);
  const modeSel = $("fdv-mode");
  const rmodeSel = $("fdv-rmode");
  rmodeSel.innerHTML = `<option value="">Automatic (LSB / USB by band)</option>` + RMODES.map((m) => `<option value="${m}">${m}</option>`).join("");
  rmodeSel.value = chosen();
  rmodeSel.onchange = async () => {
    pref.set("freedvRadioMode", rmodeSel.value);
    paintChannels();                                                         // the channel buttons' tooltips name the radio mode
    const canSet = S.user.role !== "viewer" && S.lease.holder?.conn === S.conn;
    if (canSet && S.state.frequency) { try { await send("set_mode", { mode: radioModeFor(S.state.frequency) }); } catch (e) { toast(e.message); } }
    update();
  };

  const on = () => !!S.state.freedv_on;
  const hz = () => S.state.frequency || 0;

  // Administrators always see where RADE stands: the Install / Reinstall button on a 64-bit system, otherwise why it is not offered
  function paintRadeRow() {
    const have = !info.unavailable?.RADE, b = $("fdv-rade-install"), msg = $("fdv-rade-msg");
    $("fdv-rade-row").hidden = !admin;
    if (info.rade_installable) {
      b.hidden = false;
      b.textContent = have ? "Reinstall RADE" : "Install RADE";
      msg.textContent = have ? `RADE is installed (${info.rade_path || "system library"}).${info.tx_bpf === false ? " It is an older build: its V1 transmit signal is not band-limited (about 7 % of the power falls outside the signal's band, and the other station may see it wider than it should be). Reinstall RADE for the filtered signal." : ""}${info.text === false ? " It is an older build: it cannot send your callsign in the end-of-over frame or read other stations'. Reinstall RADE to get that." : ""}`
        : "RADE is not installed. The button downloads the library (about 22 MB) from the project's release page and starts using it at once.";
    } else {
      b.hidden = true;
      msg.textContent = `RADE cannot be installed from here: it needs a 64-bit Linux system (Raspberry Pi OS 64-bit, or Debian on a 64-bit PC); this one reports "${info.arch || "unknown"}".`;
    }
  }

  function paintChannels() {
    const box = $("fdv-ch");
    box.replaceChildren(...info.channels.map((c) => {
      const b = el(`<button class="led fdv-c" title="${esc(c.name)}: ${fmtMHz(c.hz)} MHz ${radioModeFor(c.hz)}, FreeDV ${modeName(c.mode)}">${esc(c.name)}<small>${fmtMHz(c.hz)} MHz · ${modeName(c.mode)}</small></button>`);
      b.dataset.hz = c.hz; b.dataset.mode = c.mode;
      b.onclick = async () => {
        await send("set_mode", { mode: radioModeFor(c.hz) });
        await send("set_frequency", { hz: c.hz });
        await send("freedv", { on: true, mode: c.mode });
      };
      return b;
    }));
    if (!info.channels.length) box.textContent = "No channels are defined.";
  }

  function paintEditor() {
    const rows = $("fdv-rows");
    rows.replaceChildren(...info.channels.map((c) => {
      const r = el(`<div class="row fdv-row"><input class="n" maxlength="40" aria-label="Channel name" value="${esc(c.name)}">
        <input class="f" inputmode="decimal" aria-label="Frequency in MHz" value="${(c.hz / 1e6).toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}">
        <select class="m" aria-label="Mode">${(info.all_modes || info.modes || ["RADE", "RADE2"]).map((m) => `<option value="${m}" ${m === c.mode ? "selected" : ""}>${modeName(m)}</option>`).join("")}</select>
        <button type="button" class="danger" aria-label="Remove">×</button></div>`);
      r.querySelector("button").onclick = () => r.remove();
      return r;
    }));
  }
  $("fdv-rade-install").onclick = async () => {
    if (!confirm("Download the RADE library (about 22 MB) from the project's GitHub release page and install it? The Pi needs internet access for this.")) return;
    const b = $("fdv-rade-install"), msg = $("fdv-rade-msg"), had = !info.unavailable?.RADE;
    b.disabled = true; msg.textContent = "Installing… this takes about half a minute.";
    let result;
    try {
      const r = await api("/api/admin/rade/install", "POST", { confirm: true });
      result = !r.ok ? (r.reason || "RADE was installed but could not be loaded.")
        : had ? "RADE reinstalled. The new copy is used the next time FreeDV is switched on (if RADE V2 is still missing from the list, restart the service)." : "RADE installed. Choose RADE in the mode list.";
    } catch (e) { result = `Could not install RADE: ${e.message}`; }
    b.disabled = false;
    toast(result);
    await load();
    msg.textContent = result;
  };
  $("fdv-add").onclick = () => {
    info.channels = readRows();
    info.channels.push({ name: "new", hz: 14_236_000, mode: modeSel.value || "RADE" });          // (the editor lists every mode, installed or not)
    paintEditor();
  };
  function readRows() {
    return [...$("fdv-rows").querySelectorAll(".fdv-row")].map((r) => ({
      name: r.querySelector(".n").value.trim().replace(/\|/g, "/"), hz: Math.round(parseFloat(r.querySelector(".f").value.replace(",", ".")) * 1e6), mode: r.querySelector(".m").value,
    }));
  }
  $("fdv-save").onclick = async () => {
    const chans = readRows();
    if (chans.some((c) => !c.name || !(c.hz >= 10_000))) { toast("Every channel needs a name and a frequency in MHz"); return; }
    try {
      await api("/api/config", "PUT", { freedv: { channels: chans.map((c) => `${c.name}|${c.hz}|${c.mode}`) } });
      await load();
      toast("Channel list saved");
    } catch (e) { toast(e.message); }
  };

  $("fdv-onoff").onclick = async () => {
    if (on()) { await send("freedv", { on: false }); return; }
    if (!modeOk(S.state.mode)) await send("set_mode", { mode: radioModeFor(hz()) });
    await send("freedv", { on: true, mode: modeSel.value });
  };
  modeSel.onchange = () => { if (on()) send("freedv", { on: true, mode: modeSel.value }); };

  // RADE V1 needs about 1500 Hz (audio 750-2250 Hz). DATA modes: the filter is centred on 1500 Hz, so 1700 Hz is the least and about 2000 Hz comfortable; USB/LSB: the passband starts near 300 Hz, so about 2100 Hz.
  const widthRule = () => (/^DATA-[LU]$/.test(S.state.mode) ? { need: 1700, want: 2000 } : { need: 2100, want: 2400 });
  const widest = () => { const { want } = widthRule(); return Math.min(Infinity, ...((S.state.width_options || []).map((o) => o.hz).filter((h) => h >= want && h <= 3000))); };
  $("fdv-widen").onclick = () => { const t = widest(); if (Number.isFinite(t)) send("set_control", { name: "width", value: t }); };
  $("fdv-autow").onchange = async (e) => {
    try { await api("/api/config", "PUT", { freedv: { auto_width: e.target.checked } }); info.auto_width = e.target.checked; } catch (x) { toast(x.message); e.target.checked = !e.target.checked; }
  };
  $("fdv-test").onchange = (e) => { ctx.ui.levelTest = e.target.checked; $("fdv-test-note").hidden = !e.target.checked; };
  let lvTimer = 0;
  $("fdv-lvr").oninput = (e) => {
    $("fdv-lv").textContent = e.target.value;
    clearTimeout(lvTimer);
    lvTimer = setTimeout(async () => {
      try { await api("/api/config", "PUT", { freedv: { tx_level_db: +e.target.value } }); info.tx_level_db = +e.target.value; } catch (x) { toast(x.message); }
    }, 400);
  };

  async function load() {
    try { info = await api("/api/freedv"); } catch (e) {
      $("fdv-note").hidden = false;
      $("fdv-note").textContent = `Could not read the FreeDV settings from the server: ${e.message}`;
      return;
    }
    modeSel.innerHTML = (info.modes || ["RADE"]).map((m) => `<option value="${m}">${modeName(m)}</option>`).join("");
    modeSel.value = S.state.freedv_mode || info.mode || "RADE";
    if (!(info.modes || []).includes(modeSel.value)) modeSel.value = (info.modes || [])[0] || "";
    const missing = Object.entries(info.unavailable || {});                  // modes whose library is not installed, with the reason
    $("fdv-note").hidden = !missing.length;
    $("fdv-note").textContent = missing.map(([m, why]) => `${modeName(m)}: ${why}`).join("  ");
    paintRadeRow();
    $("fdv-autow-row").hidden = !admin;
    $("fdv-autow").checked = info.auto_width !== false;
    $("fdv-lvr").value = info.tx_level_db; $("fdv-lv").textContent = info.tx_level_db;
    $("fdv-lvr").disabled = !admin;
    $("fdv-test-row").hidden = S.user.role === "viewer" || !S.caps.features.ptt;
    $("fdv-lvnote").textContent = admin ? "" : "(set by an administrator)";
    $("fdv-edit").hidden = !admin;
    paintChannels();
    if (admin) paintEditor();
    update();
  }

  // ---- FreeDV Reporter: who is on the air (and the settings for the announcement, administrators only)
  let rep = null, repTimer = 0;
  const tuneTo = async (st) => {
    await send("set_mode", { mode: radioModeFor(st.freq) });
    await send("set_frequency", { hz: st.freq });
    await send("freedv", { on: true, mode: st.mode });
  };
  // a callsign opens its QRZ.com page in a new tab (nothing is sent to QRZ until it is clicked)
  const callLink = (call) => { const u = qrzUrl(call); return u ? `<a class="call" href="${u}" target="_blank" rel="noopener noreferrer" title="Look up ${esc(call)} on QRZ.com">${esc(call)}</a>` : `<b>${esc(call)}</b>`; };
  function paintReporter() {
    const box = $("fdv-rep-list"), line = $("fdv-rep-status");
    if (!rep) { line.textContent = "FreeDV Reporter: not available."; box.replaceChildren(); return; }
    const mine = rep.role === "report" || rep.role === "report_wo";
    line.textContent = !rep.enabled ? "FreeDV Reporter is off. An administrator can switch it on in the settings below."
      : !S.state.freedv_on ? "FreeDV Reporter is on and connects when FreeDV is switched on."
      : rep.connected ? `Connected to qso.freedv.org${mine ? " as an announced station" : " (viewing only)"}. ${rep.total} stations listed${rep.near ? `, ${rep.near} within 5 kHz of your frequency` : ""}.`
      : rep.error ? `Cannot reach FreeDV Reporter: ${rep.error}` : "Connecting to FreeDV Reporter…";
    if (rep.needs_callsign) line.textContent += " To be listed yourself, set a callsign and grid square in the settings.";
    box.replaceChildren(...rep.stations.map((st) => {
      const r = el(`<div class="fdv-rep-row${st.near ? " near" : ""}">${callLink(st.callsign)} <span class="dim">${esc(st.grid)}</span>
        <span class="fdv-rf">${(st.freq / 1e6).toFixed(4)} MHz</span> <span class="fdv-rm">${esc(modeName(st.mode))}</span>${st.tx ? `<span class="fdv-txb">TX</span>` : ""}${st.listening ? `<span class="dim"> (listening)</span>` : ""}
        ${st.message ? `<span class="dim fdv-rmsg">${esc(st.message)}</span>` : ""}</div>`);
      if (st.tunable) { const b = el(`<button type="button" class="fdv-tune-btn" title="Tune to this station: ${esc(st.callsign)} on ${(st.freq / 1e6).toFixed(4)} MHz, ${esc(modeName(st.mode))}">Tune</button>`); b.onclick = () => tuneTo(st); r.append(b); }
      return r;
    }));
  }
  async function loadReporter() {
    try { rep = await api("/api/freedv/reporter"); } catch { rep = null; }
    paintReporter();
  }
  repTimer = setInterval(loadReporter, 4000);
  signal?.addEventListener("abort", () => clearInterval(repTimer));
  if (admin) {
    $("fdv-repset").hidden = false;
    $("rp-save").onclick = async () => {
      const g = $("rp-grid").value.trim();
      const v = { enabled: $("rp-enabled").checked, announce: $("rp-announce").checked, watch: $("rp-watch").checked,
                  callsign: $("rp-call").value.trim().toUpperCase(), grid_square: g.slice(0, 2).toUpperCase() + g.slice(2, 4) + g.slice(4, 6).toLowerCase() + g.slice(6),
                  message: $("rp-msg").value.trim() };
      try { await api("/api/config", "PUT", { reporter: v, freedv: { callsign: $("rp-txcall").value.trim().toUpperCase() } }); toast("FreeDV settings applied."); } catch (e) { toast(e.message); }
      await loadReporter();
    };
    api("/api/config").then((c) => {
      const r = c.config.reporter || {};
      $("rp-enabled").checked = !!r.enabled; $("rp-announce").checked = r.announce !== false; $("rp-watch").checked = r.watch !== false;
      $("rp-call").value = r.callsign || ""; $("rp-grid").value = r.grid_square || ""; $("rp-msg").value = r.message || "";
      $("rp-txcall").value = (c.config.freedv || {}).callsign || "";
    }).catch(() => {});
  }

  // ---- tuning aid: spectrum of the received audio with the modem's expected band, level check, what the software corrected, fine dial steps
  const sideband = () => (["LSB", "CW-L", "DATA-L", "RTTY-L"].includes(S.state.mode) ? -1 : 1);
  for (const b of root.querySelectorAll("[data-df]")) b.onclick = () => send("set_frequency", { hz: Math.max(1, hz() + +b.dataset.df) });
  $("fdv-again").onclick = () => send("freedv", { afc: "reset" });
  $("fdv-centre").onclick = async () => {
    const off = +S.state.freedv_offset || 0, d = Math.round((off * sideband()) / 10) * 10;
    if (d) await send("set_frequency", { hz: Math.max(1, hz() + d) });
  };
  function drawSpectrum() {
    const cv = $("fdv-spec"), s = S.state, bands = s.freedv_spec || [];
    const w = Math.max(200, Math.floor(cv.clientWidth * (window.devicePixelRatio || 1))), h = cv.height;
    if (cv.width !== w) cv.width = w;
    const g = cv.getContext("2d"), css = getComputedStyle(root), col = (n, d) => css.getPropertyValue(n).trim() || d;
    g.clearRect(0, 0, w, h);
    g.fillStyle = "rgba(10,16,30,.9)"; g.fillRect(0, 0, w, h);
    const hzx = (f) => (f / 4000) * w;
    const tn = info.tune?.[s.freedv_mode];
    if (tn && s.freedv_on) {                                                // where the modem expects its signal, moved to where it is being received
      const c = tn.centre + (+s.freedv_offset || 0);
      g.fillStyle = s.freedv_afc === "locked" ? "rgba(52,211,153,.18)" : "rgba(148,163,184,.15)";
      g.fillRect(hzx(c - tn.width / 2), 0, hzx(tn.width), h);
    }
    const bw = w / Math.max(1, bands.length);
    g.fillStyle = col("--cyan", "#22d3ee");
    bands.forEach((v, i) => { const bh = (v / 100) * (h - 4); g.fillRect(i * bw + 0.5, h - bh, Math.max(1, bw - 1), bh); });
    if (s.freedv_hint != null && tn) {                                      // the spectrum's own guess where the signal is
      g.strokeStyle = "rgba(251,191,36,.9)"; g.lineWidth = 2;
      const x = hzx(tn.centre + s.freedv_hint); g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
    }
    g.strokeStyle = "rgba(148,163,184,.35)"; g.lineWidth = 1;
    for (const f of [1000, 2000, 3000]) { g.beginPath(); g.moveTo(hzx(f), 0); g.lineTo(hzx(f), h); g.stroke(); }
  }
  function tuneText() {
    const s = S.state, au = ctx.audio?.(), streaming = !!au && (au.hear || au.mic), off = Math.round(+s.freedv_offset || 0);
    const parts = [];
    if (!s.freedv_on) return "Switch FreeDV on to see the received audio and to let the Pi find the tuning for you.";
    if (!streaming) return "Tap the speaker icon (Listen): the Pi only analyses the audio while someone is listening.";
    const lv = s.freedv_level;
    if (s.freedv_clip) parts.push("Audio level: TOO LOUD, it clips. Lower the radio's USB output level (menu 107) or the receive gain.");
    else if (lv != null && lv < -55) parts.push(`Audio level: very low (${lv} dBFS). Raise the radio's USB output level (menu 107) or the receive gain.`);
    else if (lv != null) parts.push(`Audio level: ${lv} dBFS (fine).`);
    if (s.freedv_afc === "locked") {
      const dir = off === 0 ? "exactly in place" : `${Math.abs(off)} Hz ${off > 0 ? "above" : "below"} its normal place`;
      parts.push(`Locked: the signal is ${dir}${off ? "; the Pi is correcting it by itself" : ""}. SNR ${(+s.freedv_snr || 0).toFixed(1)} dB.`);
    } else if (s.freedv_afc === "searching") {
      parts.push(`No signal locked yet: the Pi is searching up to ±450 Hz around the dial frequency${s.freedv_hint != null ? `; something that looks like the signal is about ${s.freedv_hint > 0 ? "+" : ""}${s.freedv_hint} Hz off` : ""}.`);
    }
    return parts.join(" ");
  }
  function paintTune() {
    const on = !!S.state.freedv_on;
    $("fdv-tune").classList.toggle("off", !on);
    $("fdv-tunetext").textContent = tuneText();
    $("fdv-centre").hidden = !(on && S.state.freedv_afc === "locked" && Math.abs(+S.state.freedv_offset || 0) >= 30);
    drawSpectrum();
  }

  function update() {
    const s = S.state, onNow = on(), locked = s.freedv_sync === 1;
    if (onNow) used = true;
    const b = $("fdv-onoff");
    b.textContent = onNow ? "Switch FreeDV off" : "Switch FreeDV on";
    b.classList.toggle("active", onNow);
    if (onNow && document.activeElement !== modeSel && s.freedv_mode) modeSel.value = s.freedv_mode;
    if (document.activeElement !== rmodeSel) rmodeSel.value = chosen();
    rmodeSel.disabled = S.user.role === "viewer";
    rmodeSel.parentElement.hidden = !RMODES.length;
    $("fdv-dot").className = "dot " + (!onNow ? "" : locked ? "ok" : "warn");
    const heard = onNow && s.freedv_heard ? s.freedv_heard : "";                  // the callsign in the end-of-over frame of the last over (RADE V1)
    $("fdv-heard").hidden = !heard;
    if (heard) $("fdv-heard").innerHTML = `Last heard: ${callLink(heard)}` + (s.freedv_heard_snr != null ? `, SNR ${s.freedv_heard_snr > 0 ? "+" : ""}${(+s.freedv_heard_snr).toFixed(1)} dB` : "") + (s.freedv_heard_at ? ` (${new Date(s.freedv_heard_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })})` : "");
    const au = ctx.audio?.(), streaming = !!au && (au.hear || au.mic);          // the Pi only decodes while someone is listening
    $("fdv-text").textContent = !onNow ? "FreeDV is off"
      : !streaming ? `FreeDV ${modeName(s.freedv_mode)} is on. Tap the speaker icon (Listen) to hear and decode.`
      : !modeOk(s.mode) ? `FreeDV ${modeName(s.freedv_mode)} is on, but the radio is in ${s.mode}: use ${chosen() || "USB (LSB below 10 MHz)"}`
      : locked ? `FreeDV ${modeName(s.freedv_mode)} locked on a signal (SNR ${(+s.freedv_snr || 0).toFixed(1)} dB)` : `FreeDV ${modeName(s.freedv_mode)} listening, no signal locked yet`;
    const w = +s.width || 0, rule = widthRule(), narrowNow = onNow && w > 0 && w < rule.need && !s.narrow && Number.isFinite(widest());          // the radio's filter is too narrow for RADE
    $("fdv-widthwarn").hidden = !narrowNow;
    $("fdv-alcwarn").hidden = !(onNow && s.tx && s.tx_source !== "radio" && pct(+s.alc || 0, S.ui?.meter?.alc_full || 160) >= 55);          // above the mark (50 %) where the radio's ALC zone begins
    if (narrowNow) { $("fdv-widthtext").textContent = `The radio's ${s.mode} filter is only ${w} Hz wide: RADE V1 needs about 1500 Hz of the audio (750 to 2250 Hz), so it cannot decode.`; $("fdv-widen").textContent = `Widen to ${widest()} Hz`; }
    for (const c of $("fdv-ch").querySelectorAll(".fdv-c")) c.classList.toggle("active", onNow && Math.abs(hz() - +c.dataset.hz) < 500 && s.freedv_mode === c.dataset.mode);
    paintTune();
  }

  // Entering this screen remembers the radio's mode and frequency. Leaving it does NOT switch FreeDV off: it stays on until the user switches it off. Once it is off (and was used
  // during the visit), leaving the screen puts the radio back on the mode and frequency it had before.
  let away = null, used = false;
  const canTune = () => S.user.role !== "viewer" && S.lease.holder?.conn === S.conn;
  ctx.freedvActive = async (active) => {
    if (active) { away = { mode: S.state.mode, hz: S.state.frequency }; used = on(); return; }
    const a = away; away = null;
    if (on() || !used || !canTune()) return;               // still on: leave everything as it is
    used = false;
    try {
      if (a?.mode) await send("set_mode", { mode: a.mode });
      if (a?.hz) await send("set_frequency", { hz: a.hz });
      if (a) await ctx.settle?.(a);
    } catch (e) { toast(e.message); }
  };
  signal?.addEventListener("abort", () => { ctx.freedvActive = null; });

  load();
  loadReporter();
  return { update };
}
