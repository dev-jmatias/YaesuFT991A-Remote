import { api } from "../api.js";
import { el } from "../util.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// FreeDV digital voice (1600 / 700D / 700E with codec2, RADE with its own optional library). The Pi decodes the radio's modem tones into speech for every listener and encodes the operator's
// microphone into modem tones while that connection holds PTT (see docs/freedv.md). This tab: on/off, the mode, the preset channels and the
// transmit level. By the FreeDV convention frequencies below 10 MHz use LSB and above use USB.
export const ssbFor = (hz) => (hz < 10_000_000 ? "LSB" : "USB");
const fmtMHz = (hz) => (hz / 1e6).toFixed(hz % 1000 ? 4 : 3);

export function createFreeDV(host, ctx) {
  const { S, send, toast } = ctx;
  const admin = S.user.role === "admin";
  let info = { channels: [], modes: ["1600", "700D", "700E"], tx_level_db: -6 };
  const root = el(`<div class="fdv">
    <h2>FreeDV</h2>
    <div class="fdv-status" aria-live="polite"><i class="dot" id="fdv-dot"></i><span id="fdv-text">FreeDV is off</span></div>
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
    </div>
    <p class="dim fdv-note" id="fdv-note" hidden></p>
    <div class="row fdv-rade" id="fdv-rade-row" hidden>
      <button type="button" id="fdv-rade-install" class="active" hidden>Install RADE</button>
      <span class="dim" id="fdv-rade-msg"></span>
    </div>
    <h3 class="blabel">Channels</h3>
    <div class="fdv-ch" id="fdv-ch"></div>
    <div class="fdv-level"><label>Transmit level of the modem tones <b id="fdv-lv"></b> dB
      <input type="range" id="fdv-lvr" min="-40" max="0" step="1" aria-label="FreeDV transmit level"></label>
      <span class="dim" id="fdv-lvnote"></span></div>
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

  const on = () => !!S.state.freedv_on;
  const hz = () => S.state.frequency || 0;

  // Administrators always see where RADE stands: the Install / Reinstall button on a 64-bit ARM Pi, otherwise why it is not offered
  function paintRadeRow() {
    const have = !info.unavailable?.RADE, b = $("fdv-rade-install"), msg = $("fdv-rade-msg");
    $("fdv-rade-row").hidden = !admin;
    if (info.rade_installable) {
      b.hidden = false;
      b.textContent = have ? "Reinstall RADE" : "Install RADE";
      msg.textContent = have ? `RADE is installed (${info.rade_path || "system library"}).`
        : "RADE is not installed. The button downloads the library (about 22 MB) from the project's release page and starts using it at once.";
    } else {
      b.hidden = true;
      msg.textContent = `RADE cannot be installed from here: it needs a 64-bit ARM system (Raspberry Pi OS 64-bit); this one reports "${info.arch || "unknown"}".`;
    }
  }

  function paintChannels() {
    const box = $("fdv-ch");
    box.replaceChildren(...info.channels.map((c) => {
      const b = el(`<button class="led fdv-c" title="${esc(c.name)}: ${fmtMHz(c.hz)} MHz ${ssbFor(c.hz)}, FreeDV ${c.mode}">${esc(c.name)}<small>${fmtMHz(c.hz)} MHz · ${c.mode}</small></button>`);
      b.dataset.hz = c.hz; b.dataset.mode = c.mode;
      b.onclick = async () => {
        await send("set_mode", { mode: ssbFor(c.hz) });
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
        <select class="m" aria-label="Mode">${(info.all_modes || info.modes || ["1600", "700D", "700E", "RADE"]).map((m) => `<option ${m === c.mode ? "selected" : ""}>${m}</option>`).join("")}</select>
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
        : had ? "RADE reinstalled. The new copy is used after the next restart of the service." : "RADE installed. Choose RADE in the mode list.";
    } catch (e) { result = `Could not install RADE: ${e.message}`; }
    b.disabled = false;
    toast(result);
    await load();
    msg.textContent = result;
  };
  $("fdv-add").onclick = () => {
    info.channels = readRows();
    info.channels.push({ name: "new", hz: 14_236_000, mode: modeSel.value || "700D" });          // (the editor lists every mode, installed or not)
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
    if (!["USB", "LSB"].includes(S.state.mode)) await send("set_mode", { mode: ssbFor(hz()) });
    await send("freedv", { on: true, mode: modeSel.value });
  };
  modeSel.onchange = () => { if (on()) send("freedv", { on: true, mode: modeSel.value }); };

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
    modeSel.innerHTML = (info.modes || ["1600", "700D", "700E"]).map((m) => `<option>${m}</option>`).join("");
    modeSel.value = S.state.freedv_mode || info.mode || "700D";
    if (!(info.modes || []).includes(modeSel.value)) modeSel.value = (info.modes || [])[0] || "";
    const missing = Object.entries(info.unavailable || {});                  // modes whose library is not installed, with the reason
    $("fdv-note").hidden = !missing.length;
    $("fdv-note").textContent = missing.map(([m, why]) => `${m}: ${why}`).join("  ");
    paintRadeRow();
    $("fdv-lvr").value = info.tx_level_db; $("fdv-lv").textContent = info.tx_level_db;
    $("fdv-lvr").disabled = !admin;
    $("fdv-lvnote").textContent = admin ? "" : "(set by an administrator)";
    $("fdv-edit").hidden = !admin;
    paintChannels();
    if (admin) paintEditor();
    update();
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
    const b = $("fdv-onoff");
    b.textContent = onNow ? "Switch FreeDV off" : "Switch FreeDV on";
    b.classList.toggle("active", onNow);
    if (onNow && document.activeElement !== modeSel && s.freedv_mode) modeSel.value = s.freedv_mode;
    $("fdv-dot").className = "dot " + (!onNow ? "" : locked ? "ok" : "warn");
    const au = ctx.audio?.(), streaming = !!au && (au.hear || au.mic);          // the Pi only decodes while someone is listening
    $("fdv-text").textContent = !onNow ? "FreeDV is off"
      : !streaming ? `FreeDV ${s.freedv_mode} is on. Tap the speaker icon (Listen) to hear and decode.`
      : !["USB", "LSB"].includes(s.mode) ? `FreeDV ${s.freedv_mode} is on, but the radio is in ${s.mode}: use USB (LSB below 10 MHz)`
      : locked ? `FreeDV ${s.freedv_mode} locked on a signal (SNR ${(+s.freedv_snr || 0).toFixed(1)} dB)` : `FreeDV ${s.freedv_mode} listening, no signal locked yet`;
    for (const c of $("fdv-ch").querySelectorAll(".fdv-c")) c.classList.toggle("active", onNow && Math.abs(hz() - +c.dataset.hz) < 500 && s.freedv_mode === c.dataset.mode);
    paintTune();
  }

  load();
  return { update };
}
