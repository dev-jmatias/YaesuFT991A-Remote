import { el } from "../util.js";
import { createFreqDisplay } from "./freq-display.js";
import { createSMeter, createTxMeters } from "./meters.js";
import { getLights } from "../prefs.js";

// One VFO block: VFO A (main, receiving) with the signal meter and the TX meters, and VFO B as a smaller tab attached below.
// VFO B shows its frequency and whether it is the transmit VFO (split). The CAT commands of this radio give no signal strength
// or mode for VFO B, so the panel says so instead of drawing a made-up bar. On phones only the panel in use is shown (CSS).
const SVG = (body) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICON_SPK = SVG(`<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4z" fill="currentColor" stroke="none"/><path d="M15.5 9a4 4 0 0 1 0 6"/><path d="M18 6.5a7.5 7.5 0 0 1 0 11"/>`);
const ICON_MIC = SVG(`<rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" stroke="none"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>`);

export function createVfoPanels(host, ctx) {
  const hasB = !!ctx.S.caps.features.vfo_b;
  const root = el(`<div class="vfopanels">
    <section class="vfopanel a" aria-label="VFO A">
      <header><span class="vl">VFO-A</span><span class="vbadge rxb on" data-rx>RX</span><span class="vbadge txb" data-atx>TX</span><span class="rxchips" data-chips></span><span class="audbtns">
          <button class="aud" data-aud="listen" aria-pressed="false">${ICON_SPK}</button>
          <button class="aud" data-aud="mic" aria-pressed="false">${ICON_MIC}</button>
        </span></header>
      <div class="freqrow"><div class="freqhost" data-fa></div><span class="vl vl2" aria-hidden="true">VFO-A</span></div>
      <div class="vmode"><span id="mode">-</span><span class="vband" id="band">-</span><span class="vbadge fdvb" data-fdv hidden></span><span class="vbadge memb" data-memb hidden></span></div>
      <div class="smeterhost" data-sm></div>
    </section>
    <div class="txmhost" data-txm></div>
    ${hasB ? `<section class="vfopanel b" aria-label="VFO B">
      <header><span class="vl">VFO-B</span><span class="vbadge txb" data-btx>TX</span><span class="vbadge split" data-split hidden>SPLIT</span><span class="vmodeb" data-mb></span><span class="vband" data-bb></span></header>
      <div class="freqhost" data-fb></div>
      <div class="nodata dim">The signal meter is only available for VFO A (not in the CAT commands)</div>
    </section>` : ""}
  </div>`);
  host.append(root);
  const parts = [
    createFreqDisplay(root.querySelector("[data-fa]"), ctx, { vfo: "A" }),
    createSMeter(root.querySelector("[data-sm]"), ctx),
    createTxMeters(root.querySelector("[data-txm]"), ctx),            // power / SWR / ALC / COMP right under the signal meter
  ];
  if (hasB) parts.push(createFreqDisplay(root.querySelector("[data-fb]"), ctx, { vfo: "B" }));
  const q = (s) => root.querySelector(s);

  // Receive-path status lights next to RX/TX/MEM: ONE light for the preamp that shows which setting is in use (IPO, AMP1 or AMP2),
  // then ATT, ONE for the AGC naming its setting (AGC FAST / MID / SLOW / AUTO; dark when OFF) and the antenna tuner. Green = active,
  // dark = inactive; the tuner light blinks amber while a tune is running. They only show what the radio reports (the buttons that
  // change these settings are in the Receiver block).
  const feat = ctx.S.caps.features;
  const CHIPS = [
    ...(feat.ipo ? [["ipo", "IPO", "Preamp (IPO = off, direct path; AMP1; AMP2)"]] : []),
    ...(feat.att || feat.att_levels ? [["att", "ATT", "ATT: attenuator"]] : []),
    ...(feat.agc ? [["agc", "AGC", "AGC: automatic gain control (OFF, FAST, MID, SLOW, AUTO)"]] : []),
    ...(feat.tuner ? [["tuner", "TUNER", "Antenna tuner"]] : []),
  ];
  const chipHost = q("[data-chips]");
  const chipEls = CHIPS.map(([key, label, title]) => {
    const c = el(`<span class="schip" role="img" aria-label="${label}: inactive" title="${title}: inactive">${label}</span>`);
    chipHost.append(c);
    return { key, label, title, c };
  });
  // which lights are shown is a per-device choice (Account > Display); hidden lights cost nothing and the gap closes
  const showLights = () => {
    const want = getLights();
    for (const { key, c } of chipEls) c.hidden = want[key] === false;
    chipHost.hidden = chipEls.every(({ c }) => c.hidden);
  };
  showLights();
  window.addEventListener("rr-lights", showLights, { signal: ctx.signal });
  const chipOn = (key, s) => key === "ipo" ? !!s.ipo
    : key === "att" ? (s.att === true || (typeof s.att_level === "string" && s.att_level !== "OFF"))
    : key === "agc" ? (!!s.agc && s.agc !== "OFF")                    // AGC OFF is a setting too: shown, but dark
    : !!s.tuner;
  const paintChips = (s) => {
    for (const { key, label, title, c } of chipEls) {
      const on = chipOn(key, s), tuning = key === "tuner" && !!s.tuning;
      // the preamp and AGC lights name the setting that is in use
      const text = key === "ipo" ? (s.ipo || "IPO") : key === "agc" ? (s.agc ? `AGC ${s.agc}` : "AGC") : label;
      if (c.textContent !== text) c.textContent = text;
      c.classList.toggle("on", on && !tuning);
      c.classList.toggle("tuning", tuning);
      const value = key === "ipo" ? s.ipo : key === "agc" ? s.agc : null;
      const st = value !== null ? (value ? `${value}${on ? " (active)" : ""}` : "unknown") : tuning ? "tuning" : on ? "active" : "inactive";
      c.setAttribute("aria-label", `${key === "ipo" ? "Preamp" : label}: ${st}`);
      c.title = `${title}: ${st}`;
    }
  };

  // Speaker / microphone switches (top right of the VFO area). Teal = on. Only the user switches them.
  const au = ctx.audio();
  const bL = q("[data-aud=listen]"), bM = q("[data-aud=mic]");
  bL.onclick = () => au.toggleListen();
  bM.onclick = () => au.toggleMic();
  const paintAudio = () => {
    const on = au.hear, live = au.listening && /^(listening|connected)/.test(au.state);
    bL.classList.toggle("on", on); bL.classList.toggle("wait", on && !live);
    bL.setAttribute("aria-pressed", String(on)); bL.disabled = !au.availableListen;
    bL.title = !au.availableListen ? "Audio is not available" : on ? `Listening${live ? "" : " (connecting...)"}. Tap to switch off.` : "Listen: tap to hear the radio";
    const m = au.mic && au.availableMic;
    bM.classList.toggle("on", m); bM.classList.toggle("wait", m && !live); bM.setAttribute("aria-pressed", String(m)); bM.disabled = !au.availableMic || !!ctx.S.state.tx;
    bM.title = !au.availableMic ? "Microphone needs HTTPS and an operator account" : m ? "Microphone armed, radio input set to REAR (it only transmits while you hold PTT). Tap to switch off and return the radio to MIC." : "Microphone: tap to arm it for transmitting (sets the radio input to REAR)";
  };
  // Microphone armed = radio menu 106 on REAR (audio from the USB port); disarmed = back to MIC. A change that fails (for example during
  // a transmission, which the server refuses) is reported by the normal error toast.
  const hasMicSel = ctx.S.caps.controls.some((c) => c.name === "mic_select");
  if (hasMicSel) au.onMicChange((on) => ctx.send("set_control", { name: "mic_select", value: on ? "REAR" : "MIC" }));
  au.subscribe(paintAudio);
  paintAudio();
  const [panelA, panelB] = [q(".vfopanel.a"), q(".vfopanel.b")];

  return {
    update() {
      const s = ctx.S.state;
      paintAudio();                                                    // the mic button is locked during a transmission
      const aTx =!!s.tx && !s.split, bTx = !!s.tx && !!s.split;       // in split, transmit happens on VFO B
      root.dataset.active = bTx ? "b" : "a";                           // phones show only this panel (CSS): the frequency in use
      q("#band").textContent = s.band || "-";
      q("#mode").textContent = s.mode || "-";
      const fdv = q("[data-fdv]");                                       // FreeDV indicator under the frequency, before the MEM tag
      fdv.hidden = !s.freedv_on;
      if (s.freedv_on) {
        fdv.textContent = `FreeDV ${s.freedv_mode || ""}`.trim();
        fdv.classList.toggle("on", s.freedv_sync === 1);
        fdv.title = s.freedv_sync === 1 ? `FreeDV ${s.freedv_mode}: locked on a signal (SNR ${(+s.freedv_snr || 0).toFixed(1)} dB)` : `FreeDV ${s.freedv_mode}: on, no signal locked`;
      }
      const memb = q("[data-memb]"), inMem = s.vfo_memory === "memory";
      memb.hidden = !inMem;
      if (inMem) memb.textContent = `MEM ${String(s.memory_channel ?? "").padStart(3, "0")}`;
      paintChips(s);
      q("[data-rx]").classList.toggle("on", !aTx);
      q("[data-atx]").classList.toggle("on", aTx);
      panelA.classList.toggle("keyed", aTx);
      if (hasB) {
        q("[data-bb]").textContent = s.band_b || "";
        q("[data-mb]").textContent = s.mode_b || "";
        q("[data-btx]").classList.toggle("on", bTx);
        q("[data-split]").hidden = !s.split;
        panelB.classList.toggle("keyed", bTx);
        panelB.classList.toggle("txvfo", !!s.split);
      }
      paintAudio();
      parts.forEach((p) => p.update?.());
    },
  };
}
