import { el } from "../util.js";

// Hold-to-transmit. The client sends a heartbeat every 250 ms while the button is held; the SERVER un-keys on
// silence, disconnect or timeout, so nothing here is a safety net - it only mirrors what the server enforces.
export function createPtt(host, ctx) {
  const { S, send, sock, signal } = ctx;
  const hasMic = !!S.caps.controls.some((c) => c.name === "mic_select");
  const root = el(`<div class="pttbox">
    ${hasMic ? `<div class="micsel"><span class="blabel" title="Radio menu 106 SSB MIC SELECT">TX mic input</span>
      <span class="row"><button class="led" data-mic="REAR" title="Rear data/USB input: use this for remote operation">REAR (remote)</button><button class="led" data-mic="MIC" title="Front microphone: use this when operating at the radio">MIC (local)</button></span>
      <span class="micwarn" hidden>Radio is set to MIC: browser audio will not be transmitted.</span></div>` : ""}
    <button class="ptt" aria-label="Press and hold to transmit">HOLD TO TRANSMIT</button>
    <div class="txtimer" hidden><div class="bar"><i></i></div><span></span></div>
    <div class="err" role="alert"></div>
  </div>`);
  host.append(root);
  const btn = root.querySelector(".ptt"), tt = root.querySelector(".txtimer"), bar = tt.querySelector("i"), lbl = tt.querySelector("span");
  const canPtt = S.user.role !== "viewer" && S.caps.features.ptt;
  const blocked = !canPtt || !S.safety.ptt_permitted;            // viewers / PTT disabled in the config: nothing to lock
  btn.disabled = blocked;
  if (S.user.role === "viewer") btn.textContent = "VIEW ONLY";
  else if (!S.safety.ptt_permitted) btn.textContent = "PTT DISABLED IN CONFIG";

  // PTT lock: a switch above the button, next to the mic input choice. While it is on the PTT button (and TUNE) cannot be used, so a
  // stray touch or click does not transmit. It is a convenience of THIS device (remembered in the browser); the server's own TX
  // safeguards (permission, control, heartbeat, time limit) are unchanged.
  const ICON_LOCK = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" stroke="none"/><path class="shackle" d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>`;
  const lockBtn = el(`<button class="lockbtn" aria-pressed="false">${ICON_LOCK}<span>Lock PTT</span></button>`);
  lockBtn.hidden = blocked;
  const micsel = root.querySelector(".micsel");
  if (micsel) micsel.append(lockBtn); else root.insertBefore(el(`<div class="lockrow"></div>`), btn).append(lockBtn);
  lockBtn.onclick = () => { ctx.setPttLock(!ctx.ui.pttLock); if (ctx.ui.pttLock) release(); };

  const micBtns = [...root.querySelectorAll("[data-mic]")], micWarn = root.querySelector(".micwarn");
  micBtns.forEach((b) => (b.onclick = () => send("set_control", { name: "mic_select", value: b.dataset.mic })));

  let hb = null, held = false, txStart = 0, tick = 0;
  const err = (m) => { root.querySelector(".err").textContent = m || ""; if (m) setTimeout(() => (root.querySelector(".err").textContent = ""), 4000); };

  const release = () => {
    if (!held) return;
    held = false;
    ctx.audio()?.setPtt(false);
    clearInterval(hb); hb = null;
    send("ptt", { on: false });
  };
  btn.onpointerdown = async (e) => {
    e.preventDefault();
    if (btn.disabled || held) return;
    held = true;
    try { btn.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    try {
      await sock.send("ptt", { on: true });
      if (!held) { send("ptt", { on: false }); return; }      // released while the request was in flight
      ctx.audio()?.setPtt(true);
      hb = setInterval(() => sock.ping("ptt_hb"), 250);
    } catch (x) { held = false; err(x.message); }
  };
  btn.onpointerup = release; btn.onpointercancel = release; btn.onlostpointercapture = release;
  btn.oncontextmenu = (e) => e.preventDefault();
  window.addEventListener("blur", release, { signal });
  document.addEventListener("visibilitychange", () => document.hidden && release(), { signal });

  return {
    update() {
      const s = S.state, tx = !!s.tx, locked = !!ctx.ui.pttLock;
      btn.classList.toggle("keyed", tx);
      btn.classList.toggle("locked", locked && !blocked);
      btn.disabled = blocked || (locked && !tx);                     // a transmission that is already running can still be released
      lockBtn.classList.toggle("on", locked);
      lockBtn.setAttribute("aria-pressed", String(locked));
      lockBtn.querySelector("span").textContent = locked ? "PTT locked" : "Lock PTT";
      lockBtn.title = locked ? "PTT and TUNE are locked on this device. Tap to unlock." : "Lock PTT and TUNE on this device so a stray touch cannot transmit";
      for (const b of micBtns) { b.classList.toggle("on", b.dataset.mic === s.mic_select); b.disabled = tx; }
      if (micWarn) micWarn.hidden = s.mic_select !== "MIC";
      if (!blocked) btn.textContent = tx ? (s.tx_source === "radio" ? "TX (radio keyed)" : "TRANSMITTING") : locked ? "PTT LOCKED" : "HOLD TO TRANSMIT";
      if (tx && !tick) {
        txStart = performance.now();
        tick = setInterval(() => {
          const t = (performance.now() - txStart) / 1000, max = S.safety.tx_timeout_s || 120;
          tt.hidden = false; bar.style.width = Math.min(100, (t / max) * 100) + "%";
          lbl.textContent = `${t.toFixed(0)} s / ${max} s limit`;
        }, 200);
      } else if (!tx && tick) { clearInterval(tick); tick = 0; tt.hidden = true; }
    },
    error: err,
  };
}
