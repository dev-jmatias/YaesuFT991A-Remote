import { RadioSocket, api } from "../api.js";
import { createAudioPanel } from "../audio.js";
import { createVfoPanels } from "../components/vfo-panels.js";
import { createVfoB } from "../components/vfo-b.js";
import { createTuningStrip } from "../components/tuning-strip.js";
import { createDgHint, createFilters, createLevels, createSquelch, createTuneButton } from "../components/controls.js";
import { createScanButton } from "../components/scan.js";
import { createEqButtons, createEqualizer } from "../components/equalizer.js";
import { createRecBar } from "../components/recordings.js";
import { createPtt } from "../components/ptt.js";
import { createFreeDV } from "../components/freedv.js";
import { createFtx } from "../components/ftx.js";
import { createSstv } from "../components/sstv.js";
import { createMemoryButtons } from "../components/memories.js";
import { createTuner } from "../tuner.js";
import { el, fmtStep, pref } from "../util.js";
import { openSheet } from "./admin.js";

// top-bar icons (phones show these instead of the words)
const svg = (d) => `<svg class="ti" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const TI = {
  wake: svg(`<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>`),
  full: svg(`<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>`),
  help: svg(`<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01"/>`),
  power: svg(`<path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>`),
  admin: svg(`<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/>`),
  user: svg(`<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>`),
  out: svg(`<path d="M9 4H5v16h4M16 8l4 4-4 4M20 12H9"/>`),
};

// Four tabs on phones and tablets: Levels and Audio share one, and FreeDV, FT8/FT4 and SSTV share the Digital tab (a switch at its top chooses the screen).
const TABS = [["radio", "Radio"], ["filters", "Filters"], ["audio", "Audio & Levels"]];
const DIGITAL = { freedv: ["FreeDV", "freedvActive"], ftx: ["FT8/FT4", "ftxActive"], sstv: ["SSTV", "sstvActive"] };      // screen -> label, and the hook of its component that retunes the radio on entering / leaving

export function renderRadio(root, { onLogout, onAuthLost }) {
  const S = { state: {}, caps: null, user: null, safety: {}, ui: { steps: [100, 1000, 10000] }, audio: null, conn: "",
              lease: { holder: null, pending: null } };
  // pttLock: "lock PTT" switch (this device only, remembered): blocks the PTT button and TUNE so a stray touch cannot transmit
  let savedLock = false;
  try { savedLock = localStorage.getItem("rr.pttLock") === "1"; } catch { /* storage blocked: unlocked */ }
  const ui = { step: 1000, tab: "radio", dig: "", coarse: false, pttLock: savedLock };
  let parts = [], audio = null, abort = null, wake = null;
  const view = el(`<div class="app" data-tab="radio">
      <header class="topbar">
        <strong class="brand">Radio Remote</strong>
        <span class="row">
          <button class="icon wide" id="wake" title="Keep the screen on" aria-label="Keep the screen on" aria-pressed="false">${TI.wake}<span class="tl">Awake</span></button>
          <button class="icon wide" id="full" title="Full screen" aria-label="Full screen">${TI.full}<span class="tl">Full</span></button>
          <button class="icon wide" id="help" title="The manual (works offline, from this Pi)" aria-label="Help">${TI.help}<span class="tl">Help</span></button>
          <button id="pwr" hidden title="Switch the radio off (standby)" aria-label="Power off">${TI.power}<span class="tl">Power off</span></button>
          <button id="adm" hidden title="Administration" aria-label="Admin">${TI.admin}<span class="tl">Admin</span></button>
          <button id="acct" title="Your account" aria-label="Account">${TI.user}<span class="tl">Account</span></button>
          <button id="logout" title="Sign out" aria-label="Sign out">${TI.out}<span class="tl">Sign out</span></button>
        </span>
      </header>
      <div class="radiobar"><span class="rtab"><i class="dot" id="dot" role="img" aria-label="connecting"></i><span id="model"></span></span><span class="digbar" id="digbar" role="group" aria-label="Digital screen" hidden></span><button id="ftxgo" class="ftxgo" hidden title="The FT8 / FT4 screen">FT8/FT4</button><button id="sstvgo" class="ftxgo" hidden title="The SSTV screen">SSTV</button><div id="ctlbar" class="ctlbar"></div></div>
      <div id="mock" class="mockbanner" hidden>SIMULATED RADIO - nothing is connected or transmitting.</div>
      <div id="exp" class="mockbanner" hidden></div>
      <div id="offline" class="offline" hidden><span>Radio offline - waiting for it to come back. Controls are paused.</span><button class="active" id="poweron" hidden title="Wake the radio from standby (PS1). It needs a few seconds to start.">Power on radio</button></div>
      <div id="updbar" class="updbar" role="status" hidden><span>A new version was installed on the radio server.</span><button class="active" id="reload">Reload now</button></div>
      <div id="newver" class="updbar" role="status" hidden></div>
      <div id="lreq" class="dialog" role="alertdialog" aria-live="assertive" hidden></div>
      <main class="layout" id="layout"></main>
      <nav class="tabs" aria-label="Sections"></nav>
      <div class="toast" id="toast" role="status"></div>
    </div>`);
  root.innerHTML = "";
  root.append(view);
  const $ = (id) => view.querySelector("#" + id);

  const toast = (m) => { const t = $("toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 3500); };
  const send = (t, a) => sock.send(t, a).catch((e) => { toast(e.message); });

  // --- shared context handed to every component
  const tuner = createTuner({
    send, getFreq: () => S.state.frequency, getRanges: () => S.caps.frequency_ranges, onChange: () => repaint(),
  });
  const ctx = {
    S, ui, send, toast, tuner, sock: null,
    freq: () => tuner.target ?? S.state.frequency ?? 0,
    tune: (d) => tuner.nudge(d),
    setStep: (hz) => { ui.step = hz; repaint(); },
    setPttLock: (on) => {
      ui.pttLock = !!on;
      if (on && S.state.tuning) send("tune_stop");                  // locking the screen must not leave a tune running (its button is locked too)
      try { localStorage.setItem("rr.pttLock", on ? "1" : "0"); } catch { /* not remembered */ }
      repaint();
    },
    audio: () => audio,
    // After a screen put the radio back (mode / frequency), wait until the radio reports it, so the next screen remembers the right "before" (the state reaches the page a moment after the reply)
    settle: async (t) => {
      const end = performance.now() + 1500;
      while (performance.now() < end) {
        if ((!t.mode || S.state.mode === t.mode) && (!t.hz || S.state.frequency === t.hz)) return;
        await new Promise((r) => setTimeout(r, 40));
      }
    },
  };

  let lastBand = null, stepBeforeAir = null;
  function repaint() { for (const p of parts) p.update?.(); paintChrome(); applyScreenLock(); }

  // The lock switch beside the PTT button locks the WHOLE screen, not only PTT and TUNE: everything except the PTT area (which holds the lock switch itself) becomes inert, so a stray
  // touch, click, wheel turn or key press changes nothing. Scrolling still works. (An "inert" subtree takes no input at all.)
  function applyScreenLock() {
    const lock = !!ui.pttLock;
    for (const n of [view.querySelector("header.topbar"), view.querySelector(".radiobar"), view.querySelector("nav.tabs"), ...view.querySelectorAll("[data-pane]:not([data-pane=ptt])")]) n?.toggleAttribute("inert", lock);
    document.querySelectorAll(".sheet").forEach((n) => n.toggleAttribute("inert", lock));
    view.classList.toggle("screen-locked", lock);
  }

  function paintChrome() {
    const s = S.state;
    // the dot alone shows the link: green = radio online, red = radio offline, yellow = (re)connecting to the server
    const d = $("dot"), up = s.connected === true;
    d.className = "dot " + (up ? "ok" : s.connected === false ? "bad" : "warn");
    d.title = d.ariaLabel = up ? "Radio online" : s.connected === false ? "Radio offline" : "Connecting…";
    $("offline").hidden = s.connected !== false;
    $("poweron").hidden = !(S.caps?.features.power_on_cat && S.user?.role === "admin");
    view.classList.toggle("is-tx", !!s.tx);
    view.classList.toggle("is-offline", s.connected === false);
    for (const b of view.querySelectorAll("[data-m]")) b.classList.toggle("active", b.dataset.m === s.mode);
    for (const b of view.querySelectorAll("[data-b]")) b.classList.toggle("active", b.dataset.b === s.band);
    const st = view.querySelector("#stepsel");
    if (s.band !== lastBand) {
      // The AIR band is channelled every 25 kHz: the tuning step follows it when the band is entered (from the page or from the radio's own AIR key, which also puts the radio
      // in AM by itself) and goes back to the previous step when the band is left.
      const prev = lastBand;
      lastBand = s.band;
      const has25 = st && [...st.options].some((o) => +o.value === 25000);
      if (s.band === "AIR" && prev !== "AIR" && has25) { stepBeforeAir = ui.step; ui.step = 25000; }
      else if (prev === "AIR" && s.band !== "AIR" && stepBeforeAir) { ui.step = stepBeforeAir; stepBeforeAir = null; }
    }
    if (st && +st.value !== ui.step) st.value = ui.step;
    paintLease();
  }

  // ---- new release notice (administrators only; the server asks GitHub once a day, see Admin > Config > Updates). Shown once per
  // version: "Dismiss" remembers it in this browser. Nothing is installed from here.
  let verChecked = false;
  async function checkNewVersion() {
    if (verChecked || S.user?.role !== "admin") return;
    verChecked = true;
    try {
      const u = await api("/api/admin/update");
      let seen = "";
      try { seen = localStorage.getItem("rr.updateSeen") || ""; } catch { /* no storage: shown every time */ }
      if (!u.enabled || !u.newer || seen === u.latest) return;
      const bar = $("newver");
      const msg = el(`<span></span>`);
      msg.append(`Radio Remote `, Object.assign(document.createElement("b"), { textContent: u.latest }),
        ` is available (this one is ${u.current}). `);
      if (u.url) msg.append(Object.assign(document.createElement("a"), { href: u.url, target: "_blank", rel: "noopener", textContent: "What's new" }));
      const how = el(`<button class="active">How to update</button>`), skip = el(`<button>Dismiss</button>`);
      how.onclick = () => openSheet(root, { user: S.user, tab: "config", sock });
      skip.onclick = () => { bar.hidden = true; try { localStorage.setItem("rr.updateSeen", u.latest); } catch { /* not remembered */ } };
      bar.replaceChildren(msg, how, skip);
      bar.hidden = false;
    } catch { /* no answer is fine: the notice is a convenience */ }
  }

  // ---- control lease: many can watch, one controls
  const hasControl = () => !!S.lease.holder && S.lease.holder.conn === S.conn;

  function paintLease() {
    if (!S.user) return;
    const l = S.lease, mine = hasControl(), role = S.user.role, bar = $("ctlbar");
    view.classList.toggle("no-control", !mine);
    let html, cls = "ctlbar";
    if (role === "viewer") { html = `<span>Listen-only account: you can watch but not control.</span>`; cls += " watch"; }
    else if (mine) {
      html = `<span><b>You have control.</b>${l.pending ? ` ${l.pending.user} has asked for it.` : ""}</span><button data-a="release">Release</button>`;
      cls += " mine";
    } else if (!l.holder) html = `<span>Nobody has control.</span><button class="active" data-a="request">Take control</button>`;
    else {
      const wait = l.pending && l.pending.conn === S.conn;
      html = `<span><b>${l.holder.user}</b> has control. You are watching.</span>`
        + `<button data-a="request" ${wait ? "disabled" : ""}>${wait ? "Request sent…" : (S.user.trusted ? "Take control now" : "Request control")}</button>`
        + (role === "admin" ? `<button data-a="force" class="danger" title="Take control now (un-keys the transmitter)">Force</button>` : "");
      cls += " watch";
    }
    if (bar.dataset.sig !== html + cls) {
      bar.dataset.sig = html + cls; bar.className = cls; bar.innerHTML = html;
      bar.querySelector('[data-a="request"]')?.addEventListener("click", async () => {
        try { const r = await sock.send("request_control"); toast(r === "pending" ? "Request sent. The holder has 10 s to answer." : "You have control."); } catch (e) { toast(e.message); }
      });
      bar.querySelector('[data-a="release"]')?.addEventListener("click", () => send("release_control"));
      bar.querySelector('[data-a="force"]')?.addEventListener("click", () => confirm("Take control from " + l.holder.user + " now?") && send("force_control"));
    }
  }

  let lreqTimer = 0;
  function showLeaseRequest(from, timeoutS) {
    const d = $("lreq");
    d.hidden = false;
    d.innerHTML = `<div><b>${from}</b> wants control of the radio.<div class="dim" id="lcount"></div></div>
      <div class="row"><button class="active" data-r="1">Hand over</button><button data-r="0">Keep control</button></div>`;
    let left = timeoutS;
    const tick = () => { const c = d.querySelector("#lcount"); if (c) c.textContent = `Control passes automatically in ${left} s if you do not answer.`; if (left-- <= 0) hide(); };
    const hide = () => { clearInterval(lreqTimer); d.hidden = true; };
    clearInterval(lreqTimer); tick(); lreqTimer = setInterval(tick, 1000);
    d.querySelectorAll("[data-r]").forEach((b) => (b.onclick = () => { send("respond_control", { accept: b.dataset.r === "1" }); hide(); }));
  }

  // A labelled group of controls inside a pane (small grey caps label, like a hardware panel section)
  function block(host, title, cls = "") {
    const b = el(`<section class="block ${cls}"><h3 class="blabel">${title}</h3><div class="bbody"></div></section>`);
    host.append(b);
    return b.querySelector(".bbody");
  }

  function pane(name, title, cls = "") {
    const p = el(`<section class="card pane ${cls}" data-pane="${name}">${title ? `<h2>${title}</h2>` : ""}</section>`);
    $("layout").append(p);
    return p;
  }

  function build() {
    abort?.abort(); abort = new AbortController();
    ctx.signal = abort.signal;
    parts = [];
    const c = S.caps;
    // the audio engine survives UI rebuilds (a reconnect rebuilds everything): Listen is only ever switched off by the user
    if (!audio) audio = createAudioPanel({ conn: () => S.conn, info: S.audio, model: c.model.name });
    else audio.setInfo(S.audio, c.model.name);
    $("layout").replaceChildren();
    $("model").textContent = c.model.name;
    $("mock").hidden = !c.mock;
    $("exp").hidden = !c.experimental;
    $("exp").textContent = `EXPERIMENTAL PROFILE: the ${c.model.name} driver was written from its CAT manual and has not been tested on a real radio. Check each function before relying on it.`;

    // VFO area: VFO A (+ signal and TX meters, VFO B as an attached tab), sliding tuning strip (all screens), then labelled blocks:
    // [VFO tools | Tuning step], Band select, Mode
    const vfo = pane("vfo", "", "vfo");
    parts.push(createVfoPanels(vfo, ctx));
    parts.push(createTuningStrip(vfo, ctx));
    const toolRow = el(`<div class="toolrow"></div>`);
    vfo.append(toolRow);
    if (c.features.vfo_b) parts.push(createVfoB(block(toolRow, "VFO tools"), ctx));

    const stepBox = block(toolRow, "Tuning step", "tuning");
    const stepRow = el(`<div class="row steprow"><button class="led" id="dn" aria-label="Step down">&minus;</button><select id="stepsel" aria-label="Tuning step"></select><button class="led" id="up" aria-label="Step up">+</button></div>`);
    stepBox.append(stepRow);
    const baseSteps = S.ui.steps?.length ? S.ui.steps : [100, 1000, 10000];
    const steps = [...new Set([...baseSteps, ...(c.bands.includes("AIR") ? [25000] : [])])].sort((a, b) => a - b);          // 25 kHz: the airband channel step
    if (!steps.includes(ui.step)) ui.step = steps.includes(1000) ? 1000 : steps[0];
    const sel = stepRow.querySelector("#stepsel");
    sel.innerHTML = steps.map((s) => `<option value="${s}">${fmtStep(s)}</option>`).join("");
    sel.value = ui.step;
    sel.onchange = () => ctx.setStep(+sel.value);
    stepRow.querySelector("#up").onclick = () => ctx.tune(ui.step);
    stepRow.querySelector("#dn").onclick = () => ctx.tune(-ui.step);
    parts.push(createTuneButton(stepRow, ctx));                       // TUNE (antenna tuner) right next to the tuning step
    parts.push(createScanButton(stepRow, ctx));                       // SCAN / Stop scan beside TUNE
    parts.push(createMemoryButtons(stepRow, ctx));                    // Memories / Back to VFO beside TUNE
    parts.push(createEqButtons(stepRow, ctx));                        // RX EQ / TX EQ on-off after Memories (settings: Audio tab)

    const bandBox = block(vfo, "Band select");
    const bands = c.bands.filter((b) => (b !== "2m" || c.features.vhf) && (b !== "70cm" || c.features.uhf));
    const bandRow = el(`<div class="row bandrow"></div>`);
    for (const b of bands) { const x = el(`<button class="led" data-b="${b}">${b}</button>`); x.onclick = () => send("set_band", { band: b }); bandRow.append(x); }
    const setf = el(`<form class="setfreq" id="ff"><label class="blabel" for="fin">Set frequency</label><div class="row"><input id="fin" inputmode="decimal" placeholder="e.g. 14.195 or 14195000" aria-label="Enter a frequency in MHz or Hz"><button>Set</button></div></form>`);
    setf.onsubmit = (e) => {
      e.preventDefault();
      const inp = setf.querySelector("#fin"), v = parseFloat(inp.value.replace(",", "."));
      if (v > 0) tuner.set(v >= 100000 ? Math.round(v) : Math.round(v * 1e6));       // 14195000 = Hz, 14.195 = MHz
      inp.value = "";
    };
    const bandWrap = el(`<div class="bandwrap"></div>`);
    bandWrap.append(bandRow, setf);
    if (c.features.dual_receiver && c.features.vfo_b) {                       // FTDX101: the SUB receiver's frequency box sits right beside the MAIN one
      setf.querySelector("label").textContent = "Set MAIN frequency";
      const subf = el(`<form class="setfreq setsub" id="fsub"><label class="blabel" for="fsubin">Set SUB frequency</label><div class="row"><input id="fsubin" inputmode="decimal" placeholder="e.g. 7.074 or 7074000" aria-label="Enter the SUB receiver frequency in MHz or Hz"><button>Set</button></div></form>`);
      subf.onsubmit = (e) => {
        e.preventDefault();
        const inp = subf.querySelector("#fsubin"), v = parseFloat(inp.value.replace(",", "."));
        if (v > 0) send("set_frequency", { hz: v >= 100000 ? Math.round(v) : Math.round(v * 1e6), vfo: "B" });
        inp.value = "";
      };
      setf.after(subf);
    }
    bandBox.append(bandWrap);

    const modeBox = block(vfo, "Mode");
    const modeRow = el(`<div class="row moderow"></div>`);
    for (const m of c.modes) { const x = el(`<button class="led" data-m="${m}">${m}</button>`); x.onclick = () => send("set_mode", { mode: m }); modeRow.append(x); }
    modeBox.append(modeRow);
    parts.push(createDgHint(modeBox, ctx));
    parts.push(createSquelch(modeBox, ctx));

    // Recorder (under Mode): REC / Play / Send (voice keyer) / the last five recordings
    if (S.audio?.available && S.state.rec) parts.push(createRecBar(block(vfo, "Recorder"), ctx));


    // PTT
    const pp = pane("ptt", "", "pttpane");
    parts.push(createPtt(pp, ctx));

    // Levels
    const lp = pane("levels", "Levels");
    parts.push(createLevels(lp, ctx));

    // Filters / DSP
    const fp = pane("filters", "Filters & DSP");
    parts.push(createFilters(fp, ctx));

    // Audio
    const ap = pane("audio", "");
    audio.mount(ap);
    parts.push({ update: () => audio.update(S.state) });
    parts.push(createEqualizer(ap, ctx));                              // the 8-band equalizers, under the audio controls

    // The digital screens: FreeDV (when the RADE library is installed, or can be installed from its button; docs/freedv.md), FT8/FT4 (docs/ft8.md) and SSTV (docs/sstv.md). On phones and
    // tablets they share the Digital tab; on a wide desktop layout FreeDV is a card of the radio page and FT8/FT4 and SSTV have a button in the bar above.
    const fdOk = !!(S.audio?.freedv?.show ?? S.audio?.freedv?.available);
    const fxOk = !!S.audio?.ftx?.available;
    const svOk = !!S.audio?.sstv?.available;
    const screens = [fdOk && "freedv", fxOk && "ftx", svOk && "sstv"].filter(Boolean);
    const tabs = [...TABS];
    $("layout").classList.toggle("has-fd", fdOk);
    if (fdOk) parts.push(createFreeDV(pane("freedv", ""), ctx));
    if (fxOk) parts.push(createFtx(pane("ftx", ""), ctx));
    if (svOk) parts.push(createSstv(pane("sstv", ""), ctx));
    const sw = screens.length ? pane("digsw", "", "digsw") : null;
    if (screens.length) tabs.push(["digital", "Digital"]);
    if (!screens.includes(ui.dig)) ui.dig = screens.includes(pref.get("digital", "")) ? pref.get("digital", "") : screens[0] || "";
    if (!tabs.some(([k]) => k === ui.tab)) ui.tab = "radio";

    // Tab bar. A screen is a tab, or (Digital) one of the digital screens; entering and leaving a digital screen retunes the radio: the old screen puts things back before the new one takes over.
    const nav = view.querySelector(".tabs");
    const screenOf = (tab, dig) => (tab === "digital" ? dig : tab);
    const go = (tab, dig = ui.dig) => {
      const from = screenOf(ui.tab, ui.dig), to = screenOf(tab, dig);
      if (from !== to) ui.hooks = (ui.hooks || Promise.resolve()).then(async () => { await ctx[DIGITAL[from]?.[1]]?.(false); await ctx[DIGITAL[to]?.[1]]?.(true); });
      ui.tab = tab; ui.dig = dig;
      if (dig) pref.set("digital", dig);
      view.dataset.tab = tab; view.dataset.dig = dig;
      nav.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.t === tab));
      view.querySelectorAll(".digsw button, .digbar button").forEach((x) => x.classList.toggle("active", x.dataset.d === dig));
      for (const [id, key] of [["ftxgo", "ftx"], ["sstvgo", "sstv"]]) {                 // the desktop layout's buttons
        const b = $(id), on = tab === "digital" && dig === key;
        b.textContent = on ? "Back to radio" : DIGITAL[key][0]; b.classList.toggle("active", on);
      }
    };
    nav.replaceChildren(...tabs.map(([k, label]) => {
      const b = el(`<button data-t="${k}">${label}</button>`);
      b.onclick = () => go(k);
      return b;
    }));
    // the same switch twice: a thin row at the top of the Digital tab on phones, and in the radio bar (beside the radio's name) on tablets and computers; the stylesheet shows the right one
    const swButtons = () => screens.map((k) => { const b = el(`<button type="button" class="led" data-d="${k}">${DIGITAL[k][0]}</button>`); b.onclick = () => go("digital", k); return b; });
    sw?.append(...swButtons());
    $("digbar").replaceChildren(...swButtons());
    $("digbar").hidden = !screens.length;
    for (const [id, key, ok] of [["ftxgo", "ftx", fxOk], ["sstvgo", "sstv", svOk]]) {
      $(id).hidden = !ok;
      $(id).onclick = () => { if (ui.tab === "digital" && ui.dig === key) go("radio"); else go("digital", key); };
    }
    go(ui.tab, ui.dig);

    $("adm").hidden = S.user.role !== "admin";
    $("adm").onclick = () => openSheet(root, { user: S.user, tab: "users", sock });
    $("acct").onclick = () => openSheet(root, { user: S.user, tab: "account", sock });

    // Power off (admin, only when the radio profile supports it)
    const pb = $("pwr");
    pb.hidden = !(c.features.power_off_cat && S.user.role === "admin");
    pb.onclick = () => { if (confirm("Switch the radio OFF? It cannot be switched on again from here.")) send("power_off", { confirm: true }); };

    // Keyboard: arrows tune (when no form control has focus), PageUp/PageDown = x10
    window.addEventListener("keydown", (e) => {
      if (ui.pttLock) return;                                      // the screen is locked
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName) || document.activeElement?.closest?.(".dial")) return;
      const k = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 10, PageDown: -10 }[e.key];
      if (k) { e.preventDefault(); ctx.tune(k * ui.step); }
    }, { signal: abort.signal });

    repaint();
  }

  // --- chrome buttons
  $("logout").onclick = onLogout;
  $("reload").onclick = () => location.reload();
  $("help").onclick = () => window.open("/docs/", "_blank", "noopener");
  $("poweron").onclick = () => { sock.send("power_on").then(() => toast("Power-on sent. The radio needs a few seconds to start.")).catch((e) => toast(e.message)); };
  $("full").onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.().catch(() => {}));
  $("wake").onclick = async () => {
    const b = $("wake");
    try {
      if (wake) { await wake.release(); wake = null; b.setAttribute("aria-pressed", "false"); b.classList.remove("on"); }
      else { wake = await navigator.wakeLock.request("screen"); b.setAttribute("aria-pressed", "true"); b.classList.add("on"); wake.addEventListener("release", () => { wake = null; b.classList.remove("on"); }); }
    } catch { toast("Keep-awake is not available in this browser"); }
  };
  if (!("wakeLock" in navigator)) $("wake").hidden = true;
  if (!document.documentElement.requestFullscreen) $("full").hidden = true;

  const sock = new RadioSocket({
    onStatus: (up) => { if (!up) { const d = $("dot"); d.className = "dot warn"; d.title = d.ariaLabel = "Reconnecting…"; } },
    onAuthLost,
    onKicked: (why) => { const d = $("dot"); d.className = "dot bad"; d.title = d.ariaLabel = why || "Disconnected by administrator"; toast(d.title); view.classList.add("is-offline"); },
    onMessage: (m) => {
      if (m.t === "hello") {
        if (m.build && window.__loadedBuild && m.build !== window.__loadedBuild) $("updbar").hidden = false;   // an update was installed under this open page
        Object.assign(S, { caps: m.caps, user: m.user, safety: m.safety, state: m.state, ui: m.ui, audio: m.audio, conn: m.conn, lease: m.lease });
        build();
        audio.resume();                                   // reconnected: bring the audio back if Listen is on
        checkNewVersion();
      } else if (m.t === "lease") {
        S.lease = m.d;
        paintLease();
        if (!S.lease.pending || S.lease.holder?.conn !== S.conn) $("lreq").hidden = true;
      } else if (m.t === "lease_request") {
        showLeaseRequest(m.from, m.timeout_s);
      } else if (m.t === "lease_taken") {
        toast(`${m.by} took control (trusted user).`);
      } else if (m.t === "lease_denied") {
        toast(`${m.by} kept control.`);
      } else if (m.t === "safety") {
        S.safety = { ...S.safety, ...m.d };                  // the administrator switched transmitting on or off
        build();
      } else if (m.t === "ftx") {
        ctx.ftxEvent?.(m);
      } else if (m.t === "sstv") {
        ctx.sstvEvent?.(m);
      } else if (m.t === "patch" || m.t === "meters") {
        Object.assign(S.state, m.d);
        repaint();
      }
    },
  });
  ctx.sock = sock;
  const closeSock = sock.close.bind(sock);
  sock.close = () => { audio?.stop(); closeSock(); };     // signing out / losing the session ends the audio too
  return sock;
}
