import { el } from "../util.js";

// Two transmit buttons: HOLD TO TRANSMIT (transmits while it is held) and TX LOCK (one tap starts a hands-free transmission, a tap on either button stops it). The client sends a
// heartbeat every 250 ms while transmitting; the SERVER un-keys on silence, disconnect or timeout, so nothing here is a safety net - it only mirrors what the server enforces.
export function createPtt(host, ctx) {
  const { S, send, sock, signal } = ctx;
  const root = el(`<div class="pttbox">
    <div class="pttrow"><span class="pttcue" hidden></span><button class="ptt" aria-label="Press and hold to transmit">HOLD TO TRANSMIT</button><button class="ptt pttlatch" aria-pressed="false" aria-label="Transmit hands-free: tap once to start, tap again to stop">TX LOCK</button></div>
    <div class="txtimer" hidden><div class="bar"><i></i></div><span></span></div>
    <div class="err" role="alert"></div>
  </div>`);
  host.append(root);
  const btn = root.querySelector(".ptt"), tt = root.querySelector(".txtimer"), bar = tt.querySelector("i"), lbl = tt.querySelector("span");
  const canPtt = S.user.role !== "viewer" && S.caps.features.ptt;
  const blocked = !canPtt || !S.safety.ptt_permitted;            // viewers / PTT disabled in the config: nothing to lock
  const latchBtn = root.querySelector(".pttlatch");
  btn.disabled = latchBtn.disabled = blocked;
  latchBtn.hidden = blocked;                                     // viewers / PTT disabled in the config: nothing to lock on
  if (S.user.role === "viewer") btn.textContent = "VIEW ONLY";
  else if (!S.safety.ptt_permitted) btn.textContent = "PTT DISABLED IN CONFIG";

  // PTT lock: a round switch beside the button. While it is on the PTT button (and TUNE) cannot be used, so a
  // stray touch or click does not transmit. It is a convenience of THIS device (remembered in the browser); the server's own TX
  // safeguards (permission, control, heartbeat, time limit) are unchanged.
  const ICON_LOCK = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" stroke="none"/><path class="shackle" d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>`;
  const lockBtn = el(`<button class="lockbtn" aria-pressed="false" aria-label="Lock the screen">${ICON_LOCK}</button>`);
  lockBtn.hidden = blocked;
  root.querySelector(".pttrow").append(lockBtn);
  lockBtn.onclick = () => { ctx.setPttLock(!ctx.ui.pttLock); if (ctx.ui.pttLock) release(); };      // (release is hoisted below)

  let hb = null, held = false, txStart = 0, tick = 0;
  // hands-free latch (the TX LOCK button): everything the server enforces still applies (heartbeat while latched, time limit, un-key on disconnect);
  // a hidden page, the screen lock button or the end of the transmission also end it.
  const cue = root.querySelector(".pttcue");
  let latched = false, sawTx = false;
  const showCue = (on, text) => { cue.hidden = !on; if (on) cue.textContent = text; };
  const err = (m) => { root.querySelector(".err").textContent = m || ""; if (m) setTimeout(() => (root.querySelector(".err").textContent = ""), 4000); };

  let sendingClip = false;
  const release = () => {
    sendingClip = false;
    latched = false; sawTx = false; showCue(false); btn.classList.remove("latched"); latchBtn.classList.remove("latched");
    if (!held) return;
    held = false;
    ctx.audio()?.setPtt(false);
    clearInterval(hb); hb = null;
    send("ptt", { on: false });
  };
  // ask the server to key and start the heartbeat; the caller has already set `held`
  const begin = async () => {
    try {
      await sock.send("ptt", ctx.ui.levelTest ? { on: true, test: true } : { on: true });          // test: the FreeDV level test tone instead of the voice
      if (!held) { send("ptt", { on: false }); return; }      // released while the request was in flight
      ctx.audio()?.setPtt(true);
      hb = setInterval(() => sock.ping("ptt_hb"), 250);
    } catch (x) { held = false; latched = false; btn.classList.remove("latched"); latchBtn.classList.remove("latched"); showCue(false); err(x.message); }
  };
  btn.onpointerdown = async (e) => {
    e.preventDefault();
    if (latched) { release(); return; }                      // a tap on the button of a latched transmission ends it
    if (btn.disabled || held) return;
    held = true;
    try { btn.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    await begin();
  };
  const letGo = () => { if (!latched) release(); };          // finger up: a latched transmission stays on
  btn.onpointerup = letGo; btn.onpointercancel = letGo; btn.onlostpointercapture = letGo;
  btn.oncontextmenu = (e) => e.preventDefault();
  // TX LOCK: one tap starts a hands-free transmission, the next tap (on this button or on HOLD TO TRANSMIT) stops it
  const startLatched = () => {
    held = true; latched = true; sawTx = false;
    btn.classList.add("latched"); latchBtn.classList.add("latched");
    showCue(true, "Locked on - tap TX LOCK or the button to stop");
    begin();
  };
  latchBtn.onclick = () => {
    if (held) { release(); return; }
    if (latchBtn.disabled || btn.disabled) return;
    startLatched();
  };
  latchBtn.oncontextmenu = (e) => e.preventDefault();
  window.addEventListener("blur", () => { if (!latched) release(); }, { signal });
  document.addEventListener("visibilitychange", () => document.hidden && release(), { signal });

  // Voice keyer: send a recording over the air. The page keys PTT like a latched transmission (heartbeat, time limit, lock and the rest apply), the server plays the clip on its own
  // clock and releases PTT when it ends; a tap on the PTT button or on Stop ends it earlier.
  async function sendClip(id) {
    if (btn.disabled || held || !id) return;
    held = true; latched = true; sendingClip = true; sawTx = false;
    btn.classList.add("latched"); latchBtn.classList.add("latched");
    showCue(true, "Sending the recording - tap the button to stop");
    try {
      await sock.send("ptt", { on: true, play: id });
      hb = setInterval(() => sock.ping("ptt_hb"), 250);
    } catch (x) { held = false; latched = false; sendingClip = false; btn.classList.remove("latched"); latchBtn.classList.remove("latched"); showCue(false); err(x.message); }
  }
  // An external button (a Bluetooth PTT button, a key) learned on the Audio tab: down/up = hold to talk, toggle = press to start, press again to stop.
  // The same rules apply as for the screen button (lock, permission, heartbeat, time limit).
  // each action returns a short text saying what happened (the Audio tab shows it under "External PTT button")
  const ext = {
    down() {
      if (btn.disabled) return `ignored: the PTT button is disabled (${blocked ? "PTT not permitted for this account or disabled in the config" : "screen lock is on"})`;
      if (held) return "ignored: already transmitting";
      held = true; showCue(true, "External button held"); begin(); return "transmit requested (hold)";
    },
    up() { if (held && !latched) { release(); return "released"; } return "up ignored (not holding)"; },
    toggle() {
      if (held) { release(); return "stopped"; }
      if (btn.disabled) return `ignored: the PTT button is disabled (${blocked ? "PTT not permitted for this account or disabled in the config" : "screen lock is on"})`;
      held = true; latched = true; sawTx = false; btn.classList.add("latched"); latchBtn.classList.add("latched");
      showCue(true, "Locked on - press the button again to stop");
      begin(); return "transmit requested (latched)";
    },
  };
  ctx.audio()?.setExtPtt(ext);
  signal.addEventListener("abort", () => ctx.audio()?.setExtPtt(null), { once: true });
  ctx.pttApi = { sendClip, stop: release, get sending() { return sendingClip; } };

  return {
    update() {
      const s = S.state, tx = !!s.tx, locked = !!ctx.ui.pttLock;
      btn.classList.toggle("keyed", tx);
      if (tx) sawTx = true;
      else if (latched && sawTx) release();                        // the transmission ended (time limit, radio, lost control): drop the latch
      btn.classList.toggle("locked", locked && !blocked);
      btn.disabled = blocked || (locked && !tx);                     // a transmission that is already running can still be released
      latchBtn.disabled = btn.disabled;
      latchBtn.classList.toggle("keyed", tx);
      latchBtn.classList.toggle("locked", locked && !blocked);
      latchBtn.setAttribute("aria-pressed", String(latched));
      latchBtn.textContent = latched ? "STOP" : "TX LOCK";
      latchBtn.title = latched ? "Stop the hands-free transmission" : "Transmit hands-free: tap once to start, tap again to stop";
      lockBtn.classList.toggle("on", locked);
      lockBtn.setAttribute("aria-pressed", String(locked));
      lockBtn.title = locked ? "The screen is locked on this device (PTT, TUNE and every control). Tap to unlock." : "Lock the whole screen on this device so a stray touch changes nothing and cannot transmit";
      if (!blocked) btn.textContent = tx ? (s.tx_source === "radio" ? "TX (radio keyed)" : latched ? "TRANSMITTING - TAP TO STOP" : "TRANSMITTING") : locked ? "SCREEN LOCKED" : ctx.ui.levelTest && s.freedv_mode ? "HOLD FOR LEVEL TEST TONE" : "HOLD TO TRANSMIT";
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
