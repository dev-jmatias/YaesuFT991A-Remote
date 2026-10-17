// Remote audio: RX listening, optional mic for TX, levels. WebRTC/Opus, signalled over REST; if WebRTC (UDP) cannot connect it falls back to
// the same Opus audio over a WebSocket (TCP), see startWs().
// The mic track stays muted except while PTT is held; the server ALSO gates it (that is the real safety).
//
// The engine outlives the page rebuilds (a reconnect rebuilds the whole UI): Listen is switched on and off ONLY by the user.
// If the connection drops (screen locked, app in the background, server restart) the engine reconnects by itself while Listen is on.
import { api } from "./api.js";

const pref = {
  get: (k, d) => { try { const v = localStorage.getItem("rr." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem("rr." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

export function createAudioPanel({ conn, info: initialInfo, model = "Radio" }) {
  let info = initialInfo || {};
  const secure = window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
  const micOk = () => !!info.mic && secure;
  let lastFrames = 0;
  let wasTx = false;
  const raw = { alc: 0, comp: 0 };                             // peak hold while keyed, for calibration
  let pc = null, W = null, micStream = null, micTrack = null, actx = null, analyser = null, raf = 0, ptt = false;
  // The audio session (one WebRTC connection) is wanted while the user listens OR has the microphone armed: sending needs the session
  // too, so arming the mic alone connects (muted) and Listen only controls whether you hear the radio. Nothing starts by itself on
  // page load (browsers need a tap), and the mic is never armed from an earlier visit.
  let wanted = false, hear = false;
  const live = () => !!(pc || W);                                  // an audio session (WebRTC or WebSocket) exists
  let rtcTimer = 0;
  let retry = 0, retryTimer = 0, busy = false, micAck = false;       // micAck: the server accepted our microphone track
  const A = { listening: false, mic: false, state: "" };
  const listeners = new Set();
  let micHook = null, lastMic = false;
  const notify = () => {
    listeners.forEach((f) => { try { f(); } catch { /* a stale listener must not break audio */ } });
    if (A.mic !== lastMic) { lastMic = A.mic; try { micHook?.(A.mic); } catch { /* the radio link is gone: nothing to switch */ } }
  };

  const root = document.createElement("div");
  root.className = "audiopanel";
  root.innerHTML = `
    <div class="ctlgroup"><h3>Audio</h3>
      <div class="dim" id="a-status"></div>
      <div class="audiorows">
        <div class="arow"><span class="alabel">Volume</span><input type="range" id="a-vol" min="0" max="100" aria-label="Listening volume"><span class="aval" id="a-volv"></span></div>
        <div class="arow"><span class="alabel">RX audio</span><div class="bar"><i id="a-rx"></i></div><span class="aval"></span></div>
        <div class="arow"><span class="alabel">TX audio (to radio)</span><div class="bar"><i id="a-tx"></i></div><span class="aval"></span></div>
        <div class="arow"><span class="alabel">Your mic</span><div class="bar"><i id="a-mic-l"></i></div><span class="aval"></span></div>
        <div class="arow"><span class="alabel">Speaker / output</span><select id="a-out" aria-label="Audio output device"></select></div>
        <div class="arow"><span class="alabel">Microphone / input</span><select id="a-in" aria-label="Microphone device"></select></div>
        <div class="arow"><span class="alabel">Connection</span><select id="a-trans" aria-label="Audio connection type"><option value="auto">Automatic</option><option value="webrtc">WebRTC (UDP)</option><option value="websocket">WebSocket (TCP)</option></select></div>
        <div class="arow"><span class="alabel"></span><span class="dim aval wide" id="a-devnote"></span></div>
        <div class="arow"><span class="alabel">PTT button</span><button type="button" id="a-blearn" title="Press this, then press the button on your Bluetooth headset or PTT (or a key). It is remembered in this browser.">Bind PTT</button><button type="button" id="a-bserial" hidden title="Only while binding. For a Bluetooth speaker-mic or PTT button that sends its button over a serial (Serial Port Profile) connection: pair it with this computer first, then pick its port. Chrome or Edge only.">Add Bluetooth serial device</button><select id="a-bmode" hidden aria-label="How the external button works"><option value="hold">Hold to talk</option><option value="toggle">Press to start / press again to stop</option></select><button type="button" id="a-bclear" hidden>Clear</button><span class="dim aval wide" id="a-bnote"></span><a id="a-bhelp" class="aval wide" hidden target="_blank" rel="noopener"></a></div>
        <div class="arow"><span class="alabel">Radio meters (raw)</span><div class="rawm" id="a-raw" title="Raw 0-255 values from the radio, for calibrating the ALC / COMP / S-meter scales"></div></div>
        <div class="arow"><span class="alabel">To radio</span><span class="dim aval wide" id="a-txf" title="Counts 20 ms audio frames the server has handed to the radio's USB sound card. It only rises while you hold PTT.">0 frames</span></div>
      </div>      <div class="row dim audio-opts">
        <label><input type="checkbox" id="a-ec"> echo cancel</label>
        <label><input type="checkbox" id="a-ns"> noise suppress</label>
        <label><input type="checkbox" id="a-agc"> auto gain</label>
      </div>
      <div class="dim" id="a-note"></div>
    </div>`;
  const el = document.createElement("audio");
  el.autoplay = true; el.setAttribute("playsinline", "");
  root.append(el);
  const $ = (id) => root.querySelector("#" + id);
  const set = (id, v) => { $(id).style.width = Math.max(0, Math.min(100, v)) + "%"; };

  $("a-vol").value = pref.get("vol", 80); el.volume = $("a-vol").value / 100; $("a-volv").textContent = $("a-vol").value + "%";
  $("a-vol").oninput = (e) => { el.volume = e.target.value / 100; $("a-volv").textContent = e.target.value + "%"; pref.set("vol", +e.target.value); };
  for (const [id, key, def] of [["a-ec", "ec", true], ["a-ns", "ns", false], ["a-agc", "agc", false]]) {
    $(id).checked = pref.get(key, def);
    $(id).onchange = () => { pref.set(key, $(id).checked); if (live() && A.mic) restart(); };
  }
  let txError = "";
  function paintNote() {
    $("a-note").textContent = txError ? `Audio to the radio failed: ${txError}. Admin > Config > Pi -> radio (playback) must be empty (auto) or a real ALSA name such as plughw:CARD=CODEC,DEV=0.`
      : !info.available ? "Audio unavailable: " + (info.reason || "not configured")
      : !info.mic ? "Listen-only account."
      : !secure ? "Microphone needs HTTPS (or localhost). Listening still works."
      : "";
  }
  paintNote();

  // ---- choose the audio devices of THIS browser (headset, Bluetooth, car hands-free kit, ...). Remembered per browser.
  const selOut = $("a-out"), selIn = $("a-in");
  const canSink = typeof el.setSinkId === "function";
  async function applySink() {
    if (!canSink) return;
    try { await el.setSinkId(pref.get("outId", "")); } catch { el.setSinkId("").catch(() => {}); }
  }
  async function listDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) { $("a-devnote").textContent = "This browser cannot list audio devices."; selOut.disabled = selIn.disabled = true; return; }
    const devs = await navigator.mediaDevices.enumerateDevices();
    const fill = (sel, kind, saved, word) => {
      const list = devs.filter((d) => d.kind === kind && d.deviceId !== "default" && d.deviceId !== "communications");
      sel.innerHTML = `<option value="">System default</option>` + list.map((d, i) => `<option value="${d.deviceId}">${d.label || `${word} ${i + 1}`}</option>`).join("");
      sel.value = list.some((d) => d.deviceId === saved) ? saved : "";
      return list.some((d) => d.label);
    };
    const named = fill(selOut, "audiooutput", pref.get("outId", ""), "Output") | fill(selIn, "audioinput", pref.get("inId", ""), "Input");
    selOut.disabled = !canSink;
    $("a-devnote").textContent = !canSink ? "This browser cannot choose the output: pick the speaker or Bluetooth device in the system sound settings."
      : !named ? "Device names appear after the microphone has been allowed once (tap the microphone icon)." : "";
  }
  selOut.onchange = () => { pref.set("outId", selOut.value); applySink(); };
  selIn.onchange = () => { pref.set("inId", selIn.value); if (live() && A.mic) restart(); };
  $("a-trans").value = pref.get("transport", "auto");
  $("a-trans").onchange = (e) => { pref.set("transport", e.target.value); pref.set("autoWsUntil", 0); if (live()) restart(); };
  navigator.mediaDevices?.addEventListener?.("devicechange", () => listDevices().catch(() => {}));
  applySink();
  listDevices().catch(() => {});

  function status(t) { A.state = t; $("a-status").textContent = t; notify(); }

  // A near-silent looping track (a 20 Hz tone at about -55 dBFS: inaudible, yet above the level under which a browser counts a page as silent). Browsers (Chrome on Windows especially) send a headset's play/pause button only to a page they consider a media player,
  // and live WebRTC audio does not count, so while media buttons matter this near-silent real file keeps the page "playing" and the media session active.
  let keeper = null;
  function keeperOn(on) {
    try {
      if (on) {
        if (!keeper) { keeper = new Audio(new URL("../silence.wav", import.meta.url).href); keeper.loop = true; keeper.setAttribute("playsinline", ""); }
        keeper.play().catch(() => {});
      } else if (keeper) keeper.pause();
    } catch { /* optional */ }
  }

  // OS-level media controls and "this page is playing audio": helps the browser keep playing while the page is in the background
  function mediaSession(on) {
    if (!("mediaSession" in navigator)) return;
    try {
      keeperOn(on);
      if (on) {
        navigator.mediaSession.metadata = new MediaMetadata({ title: "Radio Remote", artist: model });
        navigator.mediaSession.playbackState = "playing";
        navigator.mediaSession.setActionHandler("pause", () => api_toggleListen());
        navigator.mediaSession.setActionHandler("stop", () => api_toggleListen());
        applyBindingMedia();                                         // a learned media button takes over its action (and play/pause as a pair)
      } else {
        navigator.mediaSession.playbackState = "none";
        navigator.mediaSession.metadata = null;
      }
    } catch { /* optional */ }
  }

  // media-session actions a headset or speaker-mic button can arrive as (browsers only deliver them to a page that counts as a media player)
  const BT_ACTIONS = ["play", "pause", "stop", "nexttrack", "previoustrack", "seekforward", "seekbackward", "seekto", "hangup", "togglemicrophone", "togglecamera"];

  // ---- external PTT button (Bluetooth speaker-mic, headset or a key). Devices differ, so it is LEARNED: press "Bind PTT", then the button.
  // A key gives down and up = hold to talk. A media button (play/pause, next...) arrives as ONE event per press = press to start, press again to stop.
  // Stored per browser; the PTT component supplies the actions (setExtPtt) and still enforces lock, permission, heartbeat and time limit.
  let ext = null, learning = false, learnTimer = 0, learnAbort = null, bind = pref.get("pttBtn", null);
  const PAIR = { play: ["play", "pause"], pause: ["play", "pause"] };      // a headset sends "pause" while we play and "play" while paused: one button
  const typing = (e) => /^(INPUT|SELECT|TEXTAREA)$/.test(e.target?.tagName || "") || e.target?.isContentEditable;
  const modeOf = (b) => b.mode || (b.t === "media" || /^(Media|Audio|Launch|Browser)/.test(b.key || "") ? "toggle" : "hold");
  const what = (b) => b.t === "key" ? `Key "${b.key}"${b.code ? " (" + b.code + ")" : b.kc ? " (key code " + b.kc + ")" : ""}`
    : b.t === "pad" ? `Game controller button ${b.index}${b.id ? " (" + b.id.slice(0, 40) + ")" : ""}`
    : b.t === "bridge" ? "Helper button" + (bridgeUp ? " (connected: " + bridgeDevice + ")" : bridgeWs ? " (helper running, but no device connected: switch the device on)" : " (helper not running: start it on this " + (/Android/.test(navigator.userAgent) ? "phone" : "Mac") + ")")
    : b.t === "serial" ? "Bluetooth serial button" + (serPorts.size ? " (connected)" : serialOk ? " (not connected: switch the device on, it reconnects by itself)" : " (this browser cannot read serial ports)")
    : `Media button "${b.action}"`;
  const bindText = (b) => !b ? "Not set. Press Bind PTT, then press the button on your device."
    : `${what(b)}: ${modeOf(b) === "hold" ? "hold to talk" : "press to start, press again to stop"}`;
  let lastFire = "", learnMsg = "", serialDiag = "", seen = [];                              // seen: what the browser received while binding (shown live, to find out what a device sends)
  const seenAdd = (t) => { if (seen[seen.length - 1] !== t) seen.push(t); seen = seen.slice(-4); paintBind(); };
  function fired(what, res) {
    lastFire = `${new Date().toLocaleTimeString()}  ${what} -> ${res || "no PTT component (page still loading?)"}`;
    paintBind();
  }
  function paintBind() {
    $("a-bnote").textContent = learning ? "Press the button on your device now (" + (serialDiag ? "30" : "15") + " s)..." + (serialDiag ? "\n" + serialDiag : "") + (seen.length ? "\nReceived: " + seen.join("; ") : "") : learnMsg ? learnMsg : bindText(bind) + (lastFire ? "\nLast press: " + lastFire : "");
    $("a-blearn").textContent = learning ? "Cancel" : bind ? "Bind again" : "Bind PTT";
    $("a-bclear").hidden = !bind || learning;
    const help = $("a-bhelp"), droid = /Android/.test(navigator.userAgent);
    help.hidden = !(bridgeOk && !bridgeUp && (bind?.t === "bridge" || (!learning && learnMsg)));          // only when the helper is what is missing
    if (!help.hidden) {
      help.href = "https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest/download/" + (droid ? "radio-remote-ptt-android.apk" : "radio-remote-ptt-mac.zip");
      help.textContent = droid ? "Download the Radio Remote PTT app for Android (install it, choose your device, Start)" : "Download Radio Remote PTT for Mac (unzip, open it once, pair the device in Bluetooth settings)";
    }
    $("a-bserial").hidden = !(learning && serialOk && !bridgeOk && !serialDiag);          // only while binding, and not on a Mac (there the helper does it)
    $("a-bmode").hidden = !bind || learning || bind.t === "media";             // a media button only ever sends one event per press
    if (bind) $("a-bmode").value = modeOf(bind);
  }
  // A media KEY (Next, Previous, Play/Pause, Stop) reaches the page as a key press only while the browser has no media session; with Listen on
  // the browser turns it into the matching media-session action instead, so a learned media key is also bound to that action.
  const KEY2ACTION = { MediaTrackNext: ["nexttrack"], MediaTrackPrevious: ["previoustrack"], MediaPlayPause: ["play", "pause"], MediaPlay: ["play", "pause"], MediaPause: ["play", "pause"], MediaStop: ["stop"] };
  const actionsOf = (b) => !b ? [] : b.t === "media" ? (PAIR[b.action] || [b.action]) : modeOf(b) === "toggle" ? (KEY2ACTION[b.key] || []) : [];
  function applyBindingMedia() {
    if (!bind || !("mediaSession" in navigator) || learning) return;
    for (const a of actionsOf(bind)) {
      try {
        navigator.mediaSession.setActionHandler(a, () => {
          const now = performance.now(); if (now - lastToggle < 400) return; lastToggle = now;
          fired('media "' + a + '"', ext?.toggle());
        });
      } catch { /* not supported */ }
    }
  }
  let lastToggle = 0;
  // Identify a key by its code, else its name, else (Android hardware keys often arrive as "Unidentified") its numeric key code.
  const goodCode = (c) => !!c && c !== "Unidentified" && c !== "0";
  const goodName = (k) => !!k && k !== "Unidentified" && k !== "Process";
  const sameKey = (e, b) => goodCode(b.code) ? e.code === b.code : goodName(b.key) ? e.key === b.key : !!b.kc && e.keyCode === b.kc;
  // back to the normal state: listening = full media session (and the silent keeper), otherwise only the learned button's handler
  // A learned media button must keep working with Listen off too, so then only the silent keeper + "playing" state stay on (no pause/stop = Listen toggles).
  function refreshMedia() {
    if (live()) mediaSession(true);
    else if (actionsOf(bind).length && "mediaSession" in navigator) {
      keeperOn(true);
      try { navigator.mediaSession.metadata = new MediaMetadata({ title: "Radio Remote", artist: model + " PTT button" }); navigator.mediaSession.playbackState = "playing"; } catch { /* optional */ }
      applyBindingMedia();
    } else { mediaSession(false); applyBindingMedia(); }
  }
  // browsers only start sound after a tap or key: when the page is opened with a media button already learned, arm it on the first one
  const armOnGesture = () => { document.removeEventListener("pointerdown", armOnGesture, true); document.removeEventListener("keydown", armOnGesture, true); if (!learning) refreshMedia(); };
  if (actionsOf(bind).length) { document.addEventListener("pointerdown", armOnGesture, true); document.addEventListener("keydown", armOnGesture, true); }
  // Game controllers: some Bluetooth buttons show up as a gamepad. Polled (the Gamepad API has no button events); a gamepad gives real press and release.
  let padTimer = 0, padDown = false, padKeyed = false;      // padDown: the button is physically down; padKeyed: we sent a transmit request for it
  const pads = () => { try { return Array.from(navigator.getGamepads?.() || []).filter(Boolean); } catch { return []; } };
  function padTick() {
    if (learning) {
      for (const g of pads()) for (let i = 0; i < g.buttons.length; i++) if (g.buttons[i].pressed) { const b = { t: "pad", index: i, id: g.id }; learnStop(); setBind(b); padDown = true; return; }      // still held: it must be released before it can transmit
      return;
    }
    if (!bind || bind.t !== "pad" || !ext) return;
    const now = pads().some((g) => (!bind.id || g.id === bind.id) && g.buttons[bind.index]?.pressed);
    if (now === padDown) return;
    padDown = now;
    if (modeOf(bind) === "toggle") { if (now) fired("controller button " + bind.index, ext.toggle()); return; }
    if (now) { padKeyed = true; fired("controller button " + bind.index + " down", ext.down()); }
    else if (padKeyed) { padKeyed = false; fired("controller button " + bind.index + " up", ext.up()); }
  }
  function padSync() {
    const need = learning || (bind && bind.t === "pad");
    if (need && !padTimer) padTimer = setInterval(padTick, 40);
    else if (!need && padTimer) { clearInterval(padTimer); padTimer = 0; padDown = false; if (padKeyed) { padKeyed = false; ext?.up(); } }
  }
  window.addEventListener("gamepadconnected", (e) => { if (learning) seenAdd("game controller connected: " + e.gamepad.id.slice(0, 40) + " (press one of its buttons)"); });
  window.addEventListener("gamepaddisconnected", () => { padDown = false; if (padKeyed) { padKeyed = false; ext?.up(); } });
  // Bluetooth serial buttons. Many speaker-mics (the kind sold for Zello and similar apps) are NOT keyboards: the PTT button is sent as text over the Bluetooth
  // Serial Port Profile (RFCOMM). The B01 type sends "+PTT=P" when pressed and "+PTT=R" when released, which gives true hold to talk. Chrome and Edge (117 or later) can
  // read such a port with the Web Serial API once the device is paired with this computer and the user has picked its port once; the page then reopens it by itself.
  const serialOk = typeof navigator !== "undefined" && "serial" in navigator && window.isSecureContext;
  const SERIAL_PROTOS = { "ptt-at": { down: "+PTT=P", up: "+PTT=R" } };
  const showBytes = (t) => t.replace(/[^\x20-\x7e]/g, (c) => "\\x" + c.charCodeAt(0).toString(16).padStart(2, "0"));
  const serPorts = new Map();                                             // open port -> handle
  let serKeyed = false;
  const withTimeout = (promise, ms, what) => Promise.race([promise, new Promise((_, rej) => setTimeout(() => rej(new Error(what + " did not answer within " + Math.round(ms / 1000) + " s")), ms))]);
  function serialOpen(port, onToken, onRaw, onClosed) {
    const opening = port.open({ baudRate: 9600 });
    return withTimeout(opening, 25000, "the port").catch((e) => { opening.then(() => port.close()).catch(() => {}); throw e; }).then(async () => {
      try { const r = port.setSignals({ dataTerminalReady: true, requestToSend: true }); if (r && r.catch) r.catch(() => {}); } catch { /* not supported by this port */ }          // like a terminal program: some devices send nothing until DTR is up
      const reader = port.readable.getReader(), dec = new TextDecoder("latin1");
      let alive = true, buf = "";
      const loop = (async () => {
        try {
          while (alive) {
            const { value, done } = await reader.read();
            if (done) break;
            if (!value || !value.length) continue;
            const text = dec.decode(value, { stream: true });
            onRaw?.(text);
            buf += text;
            for (;;) {                                                    // the stream has no separators and tokens can arrive split or glued together
              let best = -1, tok = null;
              for (const [proto, t] of Object.entries(SERIAL_PROTOS)) for (const kind of ["down", "up"]) {
                const i = buf.indexOf(t[kind]);
                if (i >= 0 && (best < 0 || i < best)) { best = i; tok = { proto, kind, len: t[kind].length }; }
              }
              if (!tok) break;
              buf = buf.slice(best + tok.len);
              onToken(tok);
            }
            if (buf.length > 16) buf = buf.slice(-8);
          }
        } catch { /* the device went away */ }
        try { reader.releaseLock(); } catch { /* already released */ }
        onClosed?.();
      })();
      return { stop: async () => { alive = false; try { await reader.cancel(); } catch { /* closed */ } await loop; try { await port.close(); } catch { /* closed */ } } };
    });
  }
  function serialFire(tok) {
    if (!bind || (bind.t !== "serial" && bind.t !== "bridge") || !ext) return;
    const via = bind.t === "bridge" ? "helper button" : "serial button";
    if (modeOf(bind) === "toggle") {
      if (tok.kind !== "down") return;
      const now = performance.now(); if (now - lastToggle < 400) return; lastToggle = now;
      fired(via, ext.toggle());
      return;
    }
    if (tok.kind === "down") { if (serKeyed) return; serKeyed = true; fired(via + " down", ext.down()); }
    else if (serKeyed) { serKeyed = false; fired(via + " up", ext.up()); }
  }
  function serialReleased() { if (serKeyed) { serKeyed = false; ext?.up(); } }          // the device went away while the button was down: never leave the radio keyed
  async function serialSync() {
    if (!serialOk) return;
    if (!bind || bind.t !== "serial") {
      for (const h of Array.from(serPorts.values())) await h.stop();
      serPorts.clear(); serialReleased(); paintBind();
      return;
    }
    if (learning) return;
    let ports = [];
    try { ports = await navigator.serial.getPorts(); } catch { return; }
    for (const port of ports) {
      if (serPorts.has(port)) continue;
      try {
        serPorts.set(port, null);                                             // claim it so a second sync does not open it twice
        const h = await serialOpen(port, serialFire, null, () => { serPorts.delete(port); serialReleased(); paintBind(); });
        serPorts.set(port, h); paintBind();
      } catch { serPorts.delete(port); }                                      // not connected right now: the next sync tries again
    }
  }
  if (serialOk) {
    navigator.serial.addEventListener("connect", () => serialSync());
    navigator.serial.addEventListener("disconnect", () => { serialReleased(); setTimeout(serialSync, 500); });
    setInterval(() => { if (bind && bind.t === "serial" && !learning && !serPorts.size) serialSync(); }, 5000);
  }
  // Local helper (tools/ptt-bridge-mac, tools/ptt-bridge-android): a small program that reads the Bluetooth serial button natively and passes it on over a local WebSocket, because
  // Chrome on a Mac cannot open such a port. One way only: it sends {"t":"hello","connected":bool,"device":name} and {"t":"ptt","down":bool}.
  const BRIDGE_URL = "ws://127.0.0.1:17991";
  const bridgeOk = typeof WebSocket !== "undefined" && (/Android/.test(navigator.userAgent) || /Macintosh/.test(navigator.userAgent) && !(navigator.maxTouchPoints > 1));          // Mac and Android have a helper
  let bridgeWs = null, bridgeUp = false, bridgeDevice = "", bridgeLearn = null;
  function bridgeOpen() {
    if (bridgeWs) return;
    let ws;
    try { ws = new WebSocket(BRIDGE_URL); } catch { return; }
    bridgeWs = ws;
    ws.onmessage = (e) => {
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.t === "hello") { bridgeUp = !!m.connected; bridgeDevice = m.device || ""; if (!bridgeUp) serialReleased(); if (learning && bridgeUp) seenAdd("helper sees " + bridgeDevice); else paintBind(); }
      else if (m.t === "ptt") {
        if (bridgeLearn) { if (m.down) bridgeLearn(); }
        else serialFire({ proto: "bridge", kind: m.down ? "down" : "up" });
      }
    };
    ws.onclose = () => { if (bridgeWs === ws) { bridgeWs = null; bridgeUp = false; bridgeDevice = ""; serialReleased(); paintBind(); } };
    ws.onerror = () => {};
  }
  function bridgeSync() {
    if (!bridgeOk) return;
    if (learning && bridgeLearn || bind && bind.t === "bridge") bridgeOpen();
    else if (bridgeWs) { const w = bridgeWs; bridgeWs = null; bridgeUp = false; bridgeDevice = ""; serialReleased(); try { w.close(); } catch { /* closed */ } }
  }
  if (bridgeOk) setInterval(() => { if (bind && bind.t === "bridge" && !bridgeWs) bridgeOpen(); }, 3000);          // the helper may be started after the page
  // while binding, "Bind PTT" listens to everything at once: keys, media buttons, controllers, the Mac helper and serial ports this browser may already open
  let learnPorts = [];
  function bridgeLearnOn() {
    if (!bridgeOk) return;
    bridgeLearn = () => { learnStop(); setBind({ t: "bridge" }); };
    bridgeOpen();
  }
  async function serialLearnOn() {
    if (!serialOk || bridgeOk) return;
    let ports = [];
    try { ports = await navigator.serial.getPorts(); } catch { return; }
    for (const port of ports) {
      if (serPorts.has(port)) continue;
      const mine = { port, h: null, stopped: false };
      learnPorts.push(mine);
      serialOpen(port, (tok) => { if (learning && tok.kind === "down") { learnStop(); setBind({ t: "serial", proto: tok.proto }); } }, null, () => {})
        .then((h) => { mine.h = h; if (mine.stopped || !learning) { mine.stopped = true; h.stop(); } }).catch(() => {});
    }
  }
  function learnPortsOff() {
    for (const m of learnPorts) { m.stopped = true; m.h?.stop(); }
    learnPorts = [];
  }
  async function serialLearn() {
    let port;
    try { port = await navigator.serial.requestPort({ allowedBluetoothServiceClassIds: ["00001101-0000-1000-8000-00805f9b34fb"] }); }
    catch (e) {
      if (e && e.name !== "NotFoundError") { learnMsg = "The serial port chooser could not be opened: " + (e.message || e.name); paintBind(); }
      return;                                                                 // NotFoundError = the person closed the chooser
    }
    if (learning) learnStop();                                                // leave the general listening: this one watches the chosen port
    for (const h of Array.from(serPorts.values())) await h?.stop();           // a port that is already open for the old binding must be free
    serPorts.clear();
    learnMsg = ""; seen = []; serialDiag = "Opening the port..."; learning = true; paintBind();
    let h = null, done = false, bytes = 0, tick = 0, opened = false, busy = false, info = "?";
    const t0 = Date.now();
    const finish = async (b, msg) => {
      if (done) return;
      done = true;
      learnAbort = null; clearTimeout(learnTimer); clearInterval(tick);
      if (b && h) serPorts.set(port, h);                                     // keep THIS connection: some devices accept only one serial connection per power-on, so closing and reopening could silence them
      else { try { await h?.stop(); } catch { /* closed */ } }
      const had = seen.slice();
      const diag = serialDiag; serialDiag = "";
      learnStop(); seen = had;
      if (b) setBind(b); else { learnMsg = msg + (diag ? "\n" + diag : ""); paintBind(); }
    };
    learnAbort = () => { if (!done) { done = true; clearInterval(tick); clearTimeout(learnTimer); serialDiag = ""; h?.stop(); } };
    const report = async () => {
      if (done || busy) return;
      if (!opened) { serialDiag = `Opening the port... ${Math.round((Date.now() - t0) / 1000)} s (a Bluetooth port can take several seconds)`; paintBind(); return; }
      busy = true;
      let sig = "";
      try { const g = await withTimeout(port.getSignals(), 800, "getSignals"); sig = ` DCD=${+g.dataCarrierDetect} DSR=${+g.dataSetReady} CTS=${+g.clearToSend}`; } catch { sig = " (signals not available on this kind of port)"; }
      busy = false;
      if (!done) { serialDiag = `Port open ${info}: ${bytes} byte(s) received so far,${sig}`; paintBind(); }
    };
    tick = setInterval(report, 1000); report();
    try {
      h = await serialOpen(port, (tok) => { if (done) serialFire(tok); else if (tok.kind === "down") finish({ t: "serial", proto: tok.proto }); },
        (text) => { if (done) return; bytes += text.length; seenAdd("serial data " + showBytes(text).slice(0, 60)); },
        () => { serPorts.delete(port); serialReleased(); paintBind(); });
    } catch (e) {
      finish(null, "That port could not be opened: " + (e.message || e.name) + ". Is the device switched on and paired with this computer? Another program may also have it open.");
      return;
    }
    if (done) { try { await h.stop(); } catch { /* closed */ } return; }          // cancelled while it was opening
    opened = true;
    try { info = JSON.stringify(port.getInfo()); } catch { info = "?"; }
    report();
    learnTimer = setTimeout(() => finish(null, seen.length
      ? "Received on the serial port, but not a PTT message this page knows: " + seen.join("; ") + ". Send that line to the developer and it can often be added."
      : "Nothing received on that serial port in 30 seconds. Check that the device is switched on and paired with this computer, then press its PTT button. If the list showed two entries for the device, try the other one."), 30000);
  }
  const bindKey = (e) => {
    if (!bind || bind.t !== "key" || learning || !sameKey(e, bind) || typing(e) || !ext) return;
    e.preventDefault();
    if (modeOf(bind) === "toggle") {                                         // one press = one action; key repeat and the key-up are ignored
      if (e.type !== "keydown" || e.repeat) return;
      const now = performance.now(); if (now - lastToggle < 400) return; lastToggle = now;
      fired("key " + e.key, ext.toggle());
      return;
    }
    if (e.type === "keydown") { if (!e.repeat) fired("key " + e.key + " down", ext.down()); } else fired("key " + e.key + " up", ext.up());
  };
  window.addEventListener("keydown", bindKey, true); window.addEventListener("keyup", bindKey, true);
  function setBind(b) {
    learnMsg = "";
    bind = b; pref.set("pttBtn", b);
    if ("mediaSession" in navigator) for (const a of BT_ACTIONS) { try { navigator.mediaSession.setActionHandler(a, null); } catch { /* ignore */ } }
    refreshMedia();
    padSync();
    serialSync();
    bridgeSync();
    paintBind();
  }
  function learnStop() {
    learning = false; clearTimeout(learnTimer);
    const abort = learnAbort; learnAbort = null; abort?.();
    bridgeLearn = null; learnPortsOff(); bridgeSync();
    window.removeEventListener("keydown", learnKey, true);
    if ("mediaSession" in navigator) for (const a of BT_ACTIONS) { try { navigator.mediaSession.setActionHandler(a, null); } catch { /* ignore */ } }
    refreshMedia();
    padSync();
    paintBind();
  }
  function learnKey(e) {
    if (["Shift", "Control", "Alt", "Meta"].includes(e.key)) return;
    e.preventDefault(); e.stopPropagation();
    const b = { t: "key", key: e.key === " " ? "Space" : e.key, code: e.code, kc: e.keyCode || 0 };
    if (!goodCode(b.code) && !goodName(b.key) && (!b.kc || b.kc === 229)) {      // 229 = text composition of an on-screen keyboard
      seenAdd(`a key press the browser cannot tell apart (key "${e.key}", code "${e.code}", key code ${e.keyCode})`);
      return;
    }
    learnStop(); setBind(b);
  }
  function learnStart() {
    learnMsg = ""; seen = []; learning = true; paintBind(); mediaSession(true); padSync();                       // the page must count as a media player or the browser keeps the button
    window.addEventListener("keydown", learnKey, true);
    bridgeLearnOn(); serialLearnOn();
    if ("mediaSession" in navigator) {
      for (const a of BT_ACTIONS) { try { navigator.mediaSession.setActionHandler(a, () => { const b = { t: "media", action: a }; learnStop(); setBind(b); }); } catch { /* not supported */ } }
    }
    learnTimer = setTimeout(() => {
      const had = seen.slice(); learnStop(); seen = had;
      learnMsg = seen.length ? "Received, but it cannot be used as a PTT button: " + seen.join("; ") + ". See the guide, \"Devices that do not work\"."
        : "Nothing received. The browser did not get the button. If the headset's own microphone is the Microphone / input, choose another microphone: the headset's call mode hides its buttons. Many Bluetooth speaker-mics and POC radios send a private signal that no web page can read (see the guide, \"Devices that do not work\"). Also check the device is connected, close other players (Spotify, YouTube), reload the page and try again." + (bridgeOk && !bridgeUp ? " On a Mac or an Android phone, a Bluetooth serial PTT button needs the Radio Remote PTT helper running (see the guide)." : serialOk && !bridgeOk ? " For a Bluetooth serial PTT button press Bind PTT again and use Add Bluetooth serial device." : "");
      paintBind();
    }, 15000);
  }
  $("a-blearn").onclick = () => (learning ? learnStop() : learnStart());
  $("a-bclear").onclick = () => setBind(null);
  $("a-bserial").onclick = () => serialLearn();
  $("a-bmode").onchange = () => { if (bind) { bind.mode = $("a-bmode").value; setBind(bind); } };
  paintBind(); applyBindingMedia(); padSync(); serialSync(); bridgeSync();

  async function getMic() {
    const base = { echoCancellation: $("a-ec").checked, noiseSuppression: $("a-ns").checked, autoGainControl: $("a-agc").checked, channelCount: 1 };
    const id = pref.get("inId", "");
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: id ? { ...base, deviceId: { exact: id } } : base });
    } catch (e) {
      if (!id || e.name === "NotAllowedError") throw e;           // the chosen device is gone (unplugged / out of range): use the default
      micStream = await navigator.mediaDevices.getUserMedia({ audio: base });
    }
    listDevices().catch(() => {});                                // device names are available now
    micTrack = micStream.getAudioTracks()[0];
    micTrack.enabled = ptt;                               // muted unless PTT is held
    actx = new AudioContext(); analyser = actx.createAnalyser(); analyser.fftSize = 512;
    actx.createMediaStreamSource(micStream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const tick = () => {
      analyser.getFloatTimeDomainData(buf);
      let s = 0; for (const v of buf) s += v * v;
      const db = 20 * Math.log10(Math.sqrt(s / buf.length) || 1e-6);
      set("a-mic-l", ((db + 60) / 60) * 100);
      raf = requestAnimationFrame(tick);
    };
    tick();
  }

  function releaseMic() {
    cancelAnimationFrame(raf); set("a-mic-l", 0);
    micStream?.getTracks().forEach((t) => t.stop());
    actx?.close().catch(() => {});
    micStream = micTrack = actx = analyser = null;
  }

  function waitIce(p) {
    return new Promise((res) => {
      if (p.iceGatheringState === "complete") return res();
      const t = setTimeout(res, 2000);
      p.addEventListener("icegatheringstatechange", () => { if (p.iceGatheringState === "complete") { clearTimeout(t); res(); } });
    });
  }

  // tear the connection down without changing what the user asked for
  function drop(text = "") {
    clearTimeout(rtcTimer);
    const old = pc; pc = null;
    if (old) { old.onconnectionstatechange = null; old.close(); }
    const w = W; W = null;
    if (w) {
      w.sock.onclose = w.sock.onmessage = w.sock.onerror = null;
      for (const f of [() => w.sock.close(), () => w.dec?.close(), () => w.enc?.close(), () => w.node?.disconnect(), () => w.cap?.close(), () => w.ctx?.close()]) { try { f(); } catch { /* already closed */ } }
    }
    releaseMic();
    el.srcObject = null; A.listening = false;
    set("a-rx", 0); set("a-tx", 0);
    status(text);
  }

  function scheduleRetry() {
    clearTimeout(retryTimer);
    if (!wanted) return;
    retry += 1;
    retryTimer = setTimeout(() => { if (wanted && !live()) start(); }, Math.min(15000, 1500 * retry));
  }

  // ---- which transport: "auto" tries WebRTC and falls back to the WebSocket when it cannot connect (remembered for 6 hours)
  const wsPossible = () => typeof AudioDecoder === "function" && typeof WebSocket === "function" && typeof AudioContext === "function";
  const wsMicPossible = () => typeof AudioEncoder === "function" && typeof AudioData === "function" && !!window.AudioWorkletNode;
  function useWs() {
    const t = pref.get("transport", "auto");
    if (t === "websocket") return wsPossible();
    if (t === "webrtc") return false;
    return wsPossible() && Date.now() < pref.get("autoWsUntil", 0);
  }
  function start() { return useWs() ? startWs() : startRtc(); }
  function rtcGaveUp(why) {                                    // auto mode: WebRTC did not work, switch to the WebSocket
    if (pref.get("transport", "auto") !== "auto" || !wsPossible()) return false;
    pref.set("autoWsUntil", Date.now() + 6 * 3600 * 1000);
    drop(why + " - switching to WebSocket audio");
    setTimeout(() => { if (wanted && !live()) start(); }, 0);
    return true;
  }

  async function startRtc() {
    if (busy || !info.available) return;
    busy = true;
    status("connecting…");
    try {
      const p = new RTCPeerConnection({ iceServers: [] });     // LAN / Tailscale: no STUN/TURN needed
      pc = p;
      clearTimeout(rtcTimer);
      rtcTimer = setTimeout(() => { if (pc === p && p.connectionState !== "connected") rtcGaveUp("WebRTC did not connect"); }, 9000);
      p.ontrack = (e) => { el.srcObject = e.streams[0] || new MediaStream([e.track]); el.play().catch(() => {}); };
      p.onconnectionstatechange = () => {
        if (pc !== p) return;
        const s = p.connectionState;
        if (s === "connected") { retry = 0; clearTimeout(rtcTimer); status((hear ? "listening" : "connected, not listening") + (A.mic ? (micAck ? " (mic armed)" : " (server refused mic)") : "")); }
        else if (s === "failed" || s === "closed") {
          if (s === "failed" && rtcGaveUp("WebRTC failed")) return;
          drop(wanted ? "audio connection lost, reconnecting…" : "audio connection lost");
          scheduleRetry();
        } else if (s === "disconnected") {                    // often transient: give it a few seconds to recover
          status("audio interrupted…");
          setTimeout(() => { if (pc === p && p.connectionState === "disconnected") { drop("audio connection lost, reconnecting…"); scheduleRetry(); } }, 4000);
        } else status(s);
      };
      if (A.mic && micOk()) { await getMic(); p.addTrack(micTrack, micStream); } else p.addTransceiver("audio", { direction: "recvonly" });
      await p.setLocalDescription(await p.createOffer());
      await waitIce(p);
      const ans = await api("/api/audio/offer", "POST", { sdp: p.localDescription.sdp, type: "offer", conn: conn() });
      if (pc !== p) return;                                     // switched off while connecting
      await p.setRemoteDescription({ type: ans.type, sdp: ans.sdp });
      micAck = !!ans.mic;
      if (A.mic && !micAck) status((hear ? "listening" : "connected") + " (server refused mic)");
      A.listening = true;
      mediaSession(true);
    } catch (e) {
      const denied = e.name === "NotAllowedError";
      drop(denied ? "microphone permission denied" : e.message);
      if (denied) { A.mic = false; wanted = hear; if (wanted) setTimeout(start, 0); }        // keep listening, without the mic
      else scheduleRetry();
    } finally {
      busy = false;
      notify();
    }
  }

  // ---- WebSocket audio (fallback for networks that block WebRTC/UDP): 20 ms Opus packets both ways over /ws/audio, encoded and
  // decoded with the browser's WebCodecs. Received audio is queued on an AudioContext (about 100 ms cushion; anything more than half a
  // second behind is dropped) and played through the same <audio> element as WebRTC, so volume, mute and the output device still work.
  function playPacket(w, d) {
    const n = d.numberOfFrames, f = new Float32Array(n);
    d.copyTo(f, { planeIndex: 0, format: "f32-planar" });
    d.close();
    const c = w.ctx, now = c.currentTime;
    if (w.next < now + 0.02) w.next = now + 0.1;
    else if (w.next > now + 0.5) return;
    const b = c.createBuffer(1, n, 48000);
    b.copyToChannel(f, 0);
    const s = c.createBufferSource();
    s.buffer = b; s.connect(w.dest); s.start(w.next);
    w.next += b.duration;
  }

  async function startWsMic(w) {
    await getMic();                                                // permission, level meter, device choice (shared with WebRTC)
    w.cap = new AudioContext({ sampleRate: 48000 });
    await w.cap.audioWorklet.addModule("/src/worklets/capture.js");
    const node = w.node = new AudioWorkletNode(w.cap, "rr-capture", { numberOfOutputs: 0 });
    w.cap.createMediaStreamSource(micStream).connect(node);
    const fail = (e) => { if (W === w) { drop("microphone encoder failed: " + e.message); scheduleRetry(); } };
    w.enc = new AudioEncoder({
      output: (c) => { if (ptt && w.sock.readyState === 1) { const u = new Uint8Array(c.byteLength); c.copyTo(u); w.sock.send(u); } },
      error: fail,
    });
    const cfg = { codec: "opus", sampleRate: 48000, numberOfChannels: 1, bitrate: 32000 };
    try { w.enc.configure({ ...cfg, opus: { application: "voip", frameDuration: 20000 } }); } catch { w.enc.configure(cfg); }
    let fill = 0, ts = 0;
    const buf = new Float32Array(960);                              // 20 ms at 48 kHz
    node.port.onmessage = (e) => {
      if (!ptt) { fill = 0; return; }                               // nothing is encoded or sent unless PTT is held (the server gates it too)
      const x = e.data;
      for (let i = 0; i < x.length;) {
        const take = Math.min(960 - fill, x.length - i);
        buf.set(x.subarray(i, i + take), fill); fill += take; i += take;
        if (fill === 960) {
          const ad = new AudioData({ format: "f32-planar", sampleRate: 48000, numberOfFrames: 960, numberOfChannels: 1, timestamp: ts, data: buf });
          ts += 20000; fill = 0;
          try { w.enc.encode(ad); } finally { ad.close(); }
        }
      }
    };
  }

  async function startWs() {
    if (busy || !info.available) return;
    busy = true;
    status("connecting (WebSocket)…");
    try {
      if (!wsPossible()) throw new Error("this browser cannot play WebSocket audio");
      const w = W = { next: 0, seq: 0 };
      w.ctx = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
      w.ctx.resume().catch(() => {});
      w.dest = w.ctx.createMediaStreamDestination();
      el.srcObject = w.dest.stream;
      el.play().catch(() => {});
      w.dec = new AudioDecoder({ output: (d) => playPacket(w, d), error: (e) => { if (W === w) { drop("audio decoder failed: " + e.message); scheduleRetry(); } } });
      w.dec.configure({ codec: "opus", sampleRate: 48000, numberOfChannels: 1 });
      const sock = w.sock = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/audio?conn=${encodeURIComponent(conn())}`);
      sock.binaryType = "arraybuffer";
      const ack = await new Promise((res, rej) => {
        const t = setTimeout(() => rej(new Error("the audio WebSocket did not answer")), 8000);
        sock.onerror = () => { clearTimeout(t); rej(new Error("the audio WebSocket could not connect")); };
        sock.onclose = () => { clearTimeout(t); rej(new Error("the audio WebSocket was closed")); };
        sock.onmessage = (e) => {
          if (typeof e.data === "string") { clearTimeout(t); res(JSON.parse(e.data)); return; }
        };
      });
      if (W !== w) return;                                          // switched off while connecting
      if (!ack.ok) throw new Error(ack.error || "the server refused the audio WebSocket");
      sock.onerror = null;
      sock.onmessage = (e) => {
        if (typeof e.data === "string" || w.dec.state !== "configured") return;
        w.dec.decode(new EncodedAudioChunk({ type: "key", timestamp: (w.seq++) * 20000, data: e.data }));
      };
      sock.onclose = () => { if (W === w) { drop(wanted ? "audio connection lost, reconnecting…" : "audio connection lost"); scheduleRetry(); } };
      micAck = !!ack.mic;
      if (A.mic && micOk() && micAck && wsMicPossible()) await startWsMic(w);
      else if (A.mic) micAck = false;
      if (W !== w) return;
      retry = 0;
      status((hear ? "listening" : "connected, not listening") + " (WebSocket)" + (A.mic ? (micAck ? " (mic armed)" : " (mic not available over WebSocket)") : ""));
      A.listening = true;
      mediaSession(true);
    } catch (e) {
      const denied = e.name === "NotAllowedError";
      drop(denied ? "microphone permission denied" : e.message);
      if (denied) { A.mic = false; wanted = hear; if (wanted) setTimeout(start, 0); }
      else scheduleRetry();
    } finally {
      busy = false;
      notify();
    }
  }

  function stop() {                                             // end everything (logout, OS media control)
    hear = false; A.mic = false; wanted = false; retry = 0; clearTimeout(retryTimer);
    drop("");
    mediaSession(false);
    notify();
  }
  const restart = async () => { drop(""); await start(); };
  // bring the session in line with what the user asked for
  function sync(micChanged = false) {
    wanted = hear || A.mic;
    el.muted = !hear;
    if (!wanted) { retry = 0; clearTimeout(retryTimer); drop(""); mediaSession(false); }
    else if (!live()) { retry = 0; start(); }
    else if (micChanged) restart();
    else if (hear) el.play().catch(() => {});
    notify();
  }
  function api_toggleListen() { hear = !hear; sync(); }

  return {
    root,
    mount(host) { host.append(root); },                         // re-attach after the UI was rebuilt
    subscribe(fn) { listeners.clear(); listeners.add(fn); },
    setInfo(i, m) { info = i || {}; if (m) model = m; paintNote(); notify(); },
    setExtPtt(x) { ext = x; },                                   // the PTT component registers the actions an external button triggers
    get listening() { return A.listening || (wanted && live()); },
    get wanted() { return wanted; },
    get hear() { return hear; },
    get mic() { return A.mic; },
    get availableListen() { return !!info.available; },
    get availableMic() { return !!info.available && micOk(); },
    get state() { return A.state; },
    toggleListen: api_toggleListen,
    toggleMic() { A.mic = !A.mic; sync(true); },
    // the page that owns the radio link registers here; it is told whenever the mic is armed or disarmed, however that happened
    // (button, logout, server refusal), so the radio's input menu follows: armed = REAR (USB audio), disarmed = MIC
    onMicChange(fn) { micHook = fn; },
    // after the websocket came back: start again if the user has Listen on but the audio link is gone
    resume() { if (wanted && !live() && !busy) { retry = 0; start(); } },
    update(state) {
      if ((state.audio_tx_error || "") !== txError) { txError = state.audio_tx_error || ""; paintNote(); }
      set("a-rx", state.audio_rx_level || 0); set("a-tx", state.audio_tx_level || 0);
      if (state.tx && !wasTx) { raw.alc = 0; raw.comp = 0; }                      // a new transmission starts a new peak
      wasTx = !!state.tx;
      if (state.tx) { raw.alc = Math.max(raw.alc, state.alc || 0); raw.comp = Math.max(raw.comp, state.comp || 0); }
      $("a-raw").innerHTML = [`ALC ${state.alc ?? 0} (peak ${raw.alc})`, `COMP ${state.comp ?? 0} (peak ${raw.comp})`, `PO ${state.po_raw ?? 0}`, `SWR ${state.swr_raw ?? 0}`, `S ${state.smeter ?? 0}`].map((x) => `<span>${x}</span>`).join("");
      const n = state.audio_tx_frames || 0, sending = n > lastFrames;
      lastFrames = n;
      const t = $("a-txf");
      t.textContent = `${n} frames` + (sending ? " (sending)" : "");
      t.classList.toggle("sending", sending);
    },
    setPtt(on) { ptt = on; if (micTrack) micTrack.enabled = on; },
    stop,
  };
}
