import { el } from "../util.js";

// Hold-to-transmit, with a hands-free option: while holding, slide the finger (or mouse) UP past the lock point and let go; the
// transmission stays on until the button is tapped again. The client sends a heartbeat every 250 ms while the button is held; the SERVER un-keys on
// silence, disconnect or timeout, so nothing here is a safety net - it only mirrors what the server enforces.
export function createPtt(host, ctx) {
  const { S, send, sock, signal } = ctx;
  const root = el(`<div class="pttbox">
    <div class="pttrow"><span class="pttcue" hidden></span><button class="ptt" aria-label="Press and hold to transmit">HOLD TO TRANSMIT</button></div>
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

  // PTT lock: a round switch beside the button. While it is on the PTT button (and TUNE) cannot be used, so a
  // stray touch or click does not transmit. It is a convenience of THIS device (remembered in the browser); the server's own TX
  // safeguards (permission, control, heartbeat, time limit) are unchanged.
  const ICON_LOCK = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" stroke="none"/><path class="shackle" d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>`;
  const lockBtn = el(`<button class="lockbtn" aria-pressed="false" aria-label="Lock PTT">${ICON_LOCK}</button>`);
  lockBtn.hidden = blocked;
  root.querySelector(".pttrow").append(lockBtn);
  lockBtn.onclick = () => { ctx.setPttLock(!ctx.ui.pttLock); if (ctx.ui.pttLock) release(); };      // (release is hoisted below)

  let hb = null, held = false, txStart = 0, tick = 0;
  // hands-free latch: slide up LATCH_PX while holding. Everything the server enforces still applies (heartbeat while latched, time
  // limit, un-key on disconnect); a hidden page, the lock button or the end of the transmission also end it.
  const LATCH_PX = 70, cue = root.querySelector(".pttcue");
  let latched = false, startY = 0, sawTx = false;
  const showCue = (on, text) => { cue.hidden = !on; if (on) cue.textContent = text; };
  const err = (m) => { root.querySelector(".err").textContent = m || ""; if (m) setTimeout(() => (root.querySelector(".err").textContent = ""), 4000); };

  const release = () => {
    latched = false; sawTx = false; showCue(false); btn.classList.remove("latched");
    if (!held) return;
    held = false;
    ctx.audio()?.setPtt(false);
    clearInterval(hb); hb = null;
    send("ptt", { on: false });
  };
  btn.onpointerdown = async (e) => {
    e.preventDefault();
    if (latched) { release(); return; }                      // a tap on the latched button ends the transmission
    if (btn.disabled || held) return;
    held = true; startY = e.clientY;
    showCue(true, "▲ slide up to lock transmit");
    try { btn.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    try {
      await sock.send("ptt", { on: true });
      if (!held) { send("ptt", { on: false }); return; }      // released while the request was in flight
      ctx.audio()?.setPtt(true);
      hb = setInterval(() => sock.ping("ptt_hb"), 250);
    } catch (x) { held = false; latched = false; showCue(false); err(x.message); }
  };
  btn.onpointermove = (e) => {
    if (!held || latched) return;
    if (startY - e.clientY >= LATCH_PX) {
      latched = true;
      btn.classList.add("latched");
      showCue(true, "Locked on - tap the button to stop");
    }
  };
  const letGo = () => { showCue(latched, "Locked on - tap the button to stop"); if (!latched) release(); };   // finger up: latched stays on
  btn.onpointerup = letGo; btn.onpointercancel = letGo; btn.onlostpointercapture = letGo;
  btn.oncontextmenu = (e) => e.preventDefault();
  window.addEventListener("blur", () => { if (!latched) release(); }, { signal });
  document.addEventListener("visibilitychange", () => document.hidden && release(), { signal });

  return {
    update() {
      const s = S.state, tx = !!s.tx, locked = !!ctx.ui.pttLock;
      btn.classList.toggle("keyed", tx);
      if (tx) sawTx = true;
      else if (latched && sawTx) release();                        // the transmission ended (time limit, radio, lost control): drop the latch
      btn.classList.toggle("locked", locked && !blocked);
      btn.disabled = blocked || (locked && !tx);                     // a transmission that is already running can still be released
      lockBtn.classList.toggle("on", locked);
      lockBtn.setAttribute("aria-pressed", String(locked));
      lockBtn.title = locked ? "PTT and TUNE are locked on this device. Tap to unlock." : "Lock PTT and TUNE on this device so a stray touch cannot transmit";
      if (!blocked) btn.textContent = tx ? (s.tx_source === "radio" ? "TX (radio keyed)" : latched ? "TRANSMITTING - TAP TO STOP" : "TRANSMITTING") : locked ? "PTT LOCKED" : "HOLD TO TRANSMIT";
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
