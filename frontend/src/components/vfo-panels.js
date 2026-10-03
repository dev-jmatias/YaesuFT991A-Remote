import { el } from "../util.js";
import { createFreqDisplay } from "./freq-display.js";
import { createSMeter, createTxMeters } from "./meters.js";

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
      <header><span class="vl">VFO-A</span><span class="vbadge rxb on" data-rx>RX</span><span class="vbadge txb" data-atx>TX</span><span class="vbadge memb" data-memb hidden></span><span class="audbtns">
          <button class="aud" data-aud="listen" aria-pressed="false">${ICON_SPK}</button>
          <button class="aud" data-aud="mic" aria-pressed="false">${ICON_MIC}</button>
        </span></header>
      <div class="freqhost" data-fa></div>
      <div class="vmode"><span id="mode">-</span><span class="vband" id="band">-</span></div>
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
    bM.classList.toggle("on", m); bM.classList.toggle("wait", m && !live); bM.setAttribute("aria-pressed", String(m)); bM.disabled = !au.availableMic;
    bM.title = !au.availableMic ? "Microphone needs HTTPS and an operator account" : m ? "Microphone armed (it only transmits while you hold PTT). Tap to switch off." : "Microphone: tap to arm it for transmitting";
  };
  au.subscribe(paintAudio);
  paintAudio();
  const [panelA, panelB] = [q(".vfopanel.a"), q(".vfopanel.b")];

  return {
    update() {
      const s = ctx.S.state;
      const aTx = !!s.tx && !s.split, bTx = !!s.tx && !!s.split;       // in split, transmit happens on VFO B
      root.dataset.active = bTx ? "b" : "a";                           // phones show only this panel (CSS): the frequency in use
      q("#band").textContent = s.band || "-";
      q("#mode").textContent = s.mode || "-";
      const memb = q("[data-memb]"), inMem = s.vfo_memory === "memory";
      memb.hidden = !inMem;
      if (inMem) memb.textContent = `MEM ${String(s.memory_channel ?? "").padStart(3, "0")}`;
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
