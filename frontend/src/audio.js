// Remote audio: RX listening, optional mic for TX, levels. WebRTC/Opus, signalled over REST.
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
  let pc = null, micStream = null, micTrack = null, actx = null, analyser = null, raf = 0, ptt = false;
  // The audio session (one WebRTC connection) is wanted while the user listens OR has the microphone armed: sending needs the session
  // too, so arming the mic alone connects (muted) and Listen only controls whether you hear the radio. Nothing starts by itself on
  // page load (browsers need a tap), and the mic is never armed from an earlier visit.
  let wanted = false, hear = false;
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
        <div class="arow"><span class="alabel"></span><span class="dim aval wide" id="a-devnote"></span></div>
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
    $(id).onchange = () => { pref.set(key, $(id).checked); if (pc && A.mic) restart(); };
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
  selIn.onchange = () => { pref.set("inId", selIn.value); if (pc && A.mic) restart(); };
  navigator.mediaDevices?.addEventListener?.("devicechange", () => listDevices().catch(() => {}));
  applySink();
  listDevices().catch(() => {});

  function status(t) { A.state = t; $("a-status").textContent = t; notify(); }

  // OS-level media controls and "this page is playing audio": helps the browser keep playing while the page is in the background
  function mediaSession(on) {
    if (!("mediaSession" in navigator)) return;
    try {
      if (on) {
        navigator.mediaSession.metadata = new MediaMetadata({ title: "Radio Remote", artist: model });
        navigator.mediaSession.playbackState = "playing";
        navigator.mediaSession.setActionHandler("pause", () => api_toggleListen());
        navigator.mediaSession.setActionHandler("stop", () => api_toggleListen());
      } else {
        navigator.mediaSession.playbackState = "none";
        navigator.mediaSession.metadata = null;
      }
    } catch { /* optional */ }
  }

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
    const old = pc; pc = null;
    if (old) { old.onconnectionstatechange = null; old.close(); }
    releaseMic();
    el.srcObject = null; A.listening = false;
    set("a-rx", 0); set("a-tx", 0);
    status(text);
  }

  function scheduleRetry() {
    clearTimeout(retryTimer);
    if (!wanted) return;
    retry += 1;
    retryTimer = setTimeout(() => { if (wanted && !pc) start(); }, Math.min(15000, 1500 * retry));
  }

  async function start() {
    if (busy || !info.available) return;
    busy = true;
    status("connecting…");
    try {
      const p = new RTCPeerConnection({ iceServers: [] });     // LAN / Tailscale: no STUN/TURN needed
      pc = p;
      p.ontrack = (e) => { el.srcObject = e.streams[0] || new MediaStream([e.track]); el.play().catch(() => {}); };
      p.onconnectionstatechange = () => {
        if (pc !== p) return;
        const s = p.connectionState;
        if (s === "connected") { retry = 0; status((hear ? "listening" : "connected, not listening") + (A.mic ? (micAck ? " (mic armed)" : " (server refused mic)") : "")); }
        else if (s === "failed" || s === "closed") {
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
    else if (!pc) { retry = 0; start(); }
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
    get listening() { return A.listening || (wanted && !!pc); },
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
    resume() { if (wanted && !pc && !busy) { retry = 0; start(); } },
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
