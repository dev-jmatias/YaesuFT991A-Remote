import { api } from "../api.js";
import { el } from "../util.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// FreeDV digital voice (700D / 700E). The Pi decodes the radio's modem tones into speech for every listener and encodes the operator's
// microphone into modem tones while that connection holds PTT (see docs/freedv.md). This tab: on/off, the mode, the preset channels and the
// transmit level. By the FreeDV convention frequencies below 10 MHz use LSB and above use USB.
export const ssbFor = (hz) => (hz < 10_000_000 ? "LSB" : "USB");
const fmtMHz = (hz) => (hz / 1e6).toFixed(hz % 1000 ? 4 : 3);

export function createFreeDV(host, ctx) {
  const { S, send, toast } = ctx;
  const admin = S.user.role === "admin";
  let info = { channels: [], modes: ["700D", "700E"], tx_level_db: -6 };
  const root = el(`<div class="fdv">
    <h2>FreeDV</h2>
    <div class="fdv-status" aria-live="polite"><i class="dot" id="fdv-dot"></i><span id="fdv-text">FreeDV is off</span></div>
    <div class="row fdv-ctl">
      <button class="led" id="fdv-onoff">Switch FreeDV on</button>
      <label class="fdv-mode">Mode <select id="fdv-mode" aria-label="FreeDV mode"></select></label>
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
        <select class="m" aria-label="Mode">${(info.modes || ["700D", "700E"]).map((m) => `<option ${m === c.mode ? "selected" : ""}>${m}</option>`).join("")}</select>
        <button type="button" class="danger" aria-label="Remove">×</button></div>`);
      r.querySelector("button").onclick = () => r.remove();
      return r;
    }));
  }
  $("fdv-add").onclick = () => {
    info.channels = readRows();
    info.channels.push({ name: "new", hz: 14_236_000, mode: modeSel.value || "700D" });
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
    try { info = await api("/api/freedv"); } catch { return; }
    modeSel.innerHTML = (info.modes || ["700D", "700E"]).map((m) => `<option>${m}</option>`).join("");
    modeSel.value = S.state.freedv_mode || info.mode || "700D";
    $("fdv-lvr").value = info.tx_level_db; $("fdv-lv").textContent = info.tx_level_db;
    $("fdv-lvr").disabled = !admin;
    $("fdv-lvnote").textContent = admin ? "" : "(set by an administrator)";
    $("fdv-edit").hidden = !admin;
    paintChannels();
    if (admin) paintEditor();
    update();
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
  }

  load();
  return { update };
}
