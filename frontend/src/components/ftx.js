import { api } from "../api.js";
import { answer, fmtReport, onDecode, parseMessage, slots, utcDate, utcTime } from "../ftx-qso.js";
import { el, qrzUrl } from "../util.js";
const pref = {                                            // remembered in this browser only
  get: (k, d) => { try { const v = localStorage.getItem("rr." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem("rr." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// FT8 / FT4 screen (see docs/ft8.md). The Pi decodes the radio's audio in the UTC time slots and sends every decode to the page; the page shows them with a waterfall, follows the
// standard QSO sequence (ftx-qso.js), tells the Pi what to send next (the Pi repeats it in every slot of that parity), keeps the log and uploads it.
const MASK = "********";
const BW = { FT8: 50, FT4: 83.3 };
const hhmmss = (t0) => new Date(t0 * 1000).toISOString().slice(11, 19).replace(/:/g, "");
const fmtDial = (hz) => { const s = String(Math.round(hz || 0)).padStart(8, " "); return `${s.slice(0, -6).trim() || "0"}.${s.slice(-6, -3)}.${s.slice(-3)}`; };

// waterfall colours: 0 = the noise floor, 255 = 42 dB above it
const PALETTE = Array.from({ length: 256 }, (_, i) => {
  const x = i / 255, st = [[0, 0, 0, 24], [0.25, 8, 38, 140], [0.5, 20, 170, 220], [0.75, 250, 220, 60], [1, 255, 70, 40]];
  let k = 1;
  while (k < st.length - 1 && x > st[k][0]) k++;
  const a = st[k - 1], b = st[k], f = (x - a[0]) / (b[0] - a[0]);
  return [0, 1, 2].map((j) => Math.round(a[j + 1] + (b[j + 1] - a[j + 1]) * f));
});

export function createFtx(host, ctx) {
  const { S, send, toast, signal } = ctx;
  const admin = S.user.role === "admin", viewer = S.user.role === "viewer";
  let info = { available: false, on: false, mode: "FT8", bands: { FT8: [], FT4: [] }, settings: {}, history: [], slot: 15, upload: {} };
  let skew = 0;                                             // server clock minus this device's clock, ms
  let qso = null, txEnabled = false, txParity = pref.get("ftxParity", 0), txHz = pref.get("ftxTx", 1500), logRows = [], hold = false;
  let hits = [];                                           // the callsign labels on the waterfall that can be tapped
  let selected = null;                                     // the decode row last tapped: the Answer button works on it
  const seen = [];                                          // the decodes of the last two slots, for the markers on the waterfall
  const root = el(`<div class="ftx">
    <div class="ftx-top">
      <div class="ftx-freq" title="The radio's dial frequency"><b id="fx-freq">0.000.000</b><small id="fx-rmode"></small></div>
      <div class="ftx-seg" role="group" aria-label="Mode"><button type="button" class="led" data-fm="FT8">FT8</button><button type="button" class="led" data-fm="FT4">FT4</button></div>
      <label class="ftx-band">Band <select id="fx-band" aria-label="Band and standard frequency"></select></label>
      <button type="button" id="fx-onoff" class="led">RX off</button>
      <span class="ftx-clock" id="fx-clock" hidden></span>
      <div class="ftx-timebox" id="fx-timebox"><b id="fx-utc" title="UTC time, from the Pi's clock">--:--:--</b><span class="ftx-chip" id="fx-ntp">sync ?</span><span class="ftx-chip" id="fx-dt">DT -</span></div>
    </div>
    <p class="ftx-notice" id="fx-notice" hidden></p>
    <div class="ftx-slotrow"><span id="fx-slotname">idle</span><div class="ftx-bar"><i id="fx-bar"></i></div><b id="fx-sec">0</b></div>
    <div class="ftx-main">
      <section class="ftx-col"><h3 class="blabel">All decodes <span class="ftx-hb"><label class="chk ftx-cq"><input type="checkbox" id="fx-cqonly"> CQ only</label><button type="button" class="ftx-clear" id="fx-clr-all" title="Empty this list">Clear</button></span></h3><div class="ftx-list" id="fx-all" aria-live="polite"></div></section>
      <section class="ftx-col">
        <h3 class="blabel">Directed to me / worked <span class="ftx-hb"><button type="button" class="ftx-clear" id="fx-clr-dir" title="Empty this list">Clear</button></span></h3><div class="ftx-list" id="fx-dir" aria-live="polite"></div>
      </section>
    </div>
    <div class="ftx-under">
      <div class="row ftx-ctl">
          <label>DX <input id="fx-dx" maxlength="13" size="9" autocapitalize="characters" placeholder="callsign"></label>
          <button type="button" id="fx-enable" class="led" title="Nothing is transmitted until this is on. It switches off again when the page is reloaded.">Enable TX</button>
          <button type="button" id="fx-halt" class="danger">Halt TX</button>
          <button type="button" id="fx-answer" class="active" title="Answer the decode you tapped (the same as tapping it twice)">Answer</button>
          <button type="button" id="fx-cq" class="active">Call CQ</button>
          <button type="button" id="fx-reset" title="Stop transmitting, forget the contact in progress and empty both lists and the waterfall">Reset all</button>
          <label class="ftx-par">TX <select id="fx-par" aria-label="Transmit in even or odd slots"><option value="0">even</option><option value="1">odd</option></select></label>
        </div>
        <div class="ftx-status dim" id="fx-txstat"></div>
        <h3 class="blabel">QSO messages <span class="ftx-hb"><button type="button" class="ftx-clear" id="fx-msgreset" title="Forget the DX station and the contact in progress, stop repeating the message being sent and put the six messages back to the start">Reset QSO</button><button type="button" class="ftx-clear" id="fx-qrz" title="Look the DX station up on QRZ.com (opens a new tab)">QRZ Lookup</button></span></h3>
        <div class="ftx-msgs" id="fx-msgs"></div>
    </div>
    <div class="ftx-wfwrap"><canvas id="fx-wf" width="240" height="100" aria-label="Waterfall of the received audio, 0 to 3000 Hz"></canvas><canvas id="fx-ov" aria-hidden="true"></canvas></div>
    <div class="ftx-scale"><span>0</span><span>500</span><span>1000</span><span>1500</span><span>2000</span><span>2500</span><span>3000 Hz</span></div>
    <div class="row ftx-wfbar">
      <label>TX <input id="fx-txhz" inputmode="numeric" size="5" aria-label="Transmit audio frequency in Hz"> Hz</label>
      <span class="dim">tap a callsign to select it (again to call it), or the waterfall to move TX</span>
      <button type="button" id="fx-log">Log QSO</button><button type="button" id="fx-erase">Erase</button>
      <label class="ftx-cmt" title="Your own comment for the next contact logged (sent to the log, QRZ and eQSL). Empty = the automatic one: mode, report sent and received, e.g. FT8 Sent: +06 Rcvd: +01.">Comment <input id="fx-comment" maxlength="80" size="26" placeholder="auto: FT8 Sent: +06 Rcvd: +01" autocomplete="off"></label>
      <label class="chk"><input type="checkbox" id="fx-hold"> Hold TX frequency</label>
    </div>
    <div class="ftx-bottom">
    <details class="fdv-edit ftx-logbox" id="fx-logbox"><summary id="fx-logsum">Log</summary>
      <div class="row"><a id="fx-adif" class="btn" href="/api/ftx/log.adi" download>Export ADIF</a>
        <button type="button" data-up="qrz">Upload to QRZ</button><button type="button" data-up="eqsl">Upload to eQSL</button></div>
      <div class="dim" id="fx-upmsg"></div>
      <div id="fx-logrows" class="ftx-log"></div>
    </details>
    <details class="fdv-edit ftx-set"><summary>FT settings</summary>
      <div class="row ftx-acc ftx-me"><label>Callsign <input id="fs-call" maxlength="13" size="10" autocapitalize="characters"></label>
        <label>Grid square <input id="fs-grid" maxlength="6" size="7" placeholder="IN51"></label>
        <label title="Level of the tones sent to the radio. Keep the radio's ALC at zero: more is not better.">TX level, dB <input id="fs-lvl" type="number" min="-40" max="0" step="1"></label>
        <label title="The same message is repeated this many times unanswered, then transmitting stops by itself.">Repeats <input id="fs-rep" type="number" min="1" max="30" step="1" size="3"></label>
        <label title="What this device plays while this screen is open (other devices are not affected). The decoder works on the Pi's own audio, so it needs no sound.">Sound here <select id="fs-snd"><option value="off">Off</option><option value="low">Low</option></select></label>
        <button type="button" id="fs-save" class="active">Apply</button></div>
      <p class="dim" id="fs-note"></p>
      <div id="fs-up" hidden>
        <h3 class="blabel">Upload accounts (stored on the Pi, never shown again)</h3>
        <div class="row ftx-acc">
          <label>QRZ API key <input id="fu-qrz" type="password" autocomplete="off"></label>
          <label>eQSL user <input id="fu-equser" autocomplete="off"></label><label>eQSL password <input id="fu-eqpw" type="password" autocomplete="off"></label>
        </div>
        <div class="row ftx-acc"><span class="dim">Upload as soon as a contact is logged:</span>
          <span class="ftx-chks"><label class="chk"><input type="checkbox" id="fu-aqrz"> QRZ</label><label class="chk"><input type="checkbox" id="fu-aeq"> eQSL</label></span>
          <button type="button" id="fu-save" class="active">Save accounts</button></div>
      </div>
    </details>
    </div>
  </div>`);
  host.append(root);
  const $ = (id) => root.querySelector("#" + id);
  const my = () => (info.settings.callsign || "").toUpperCase();
  const grid4 = () => (info.settings.grid_square || "").toUpperCase().slice(0, 4);
  const dataMode = () => { const m = S.caps?.modes || []; return m.find((x) => /^(DATA|PKT).*-?U/i.test(x) && !/FM/i.test(x)) || m.find((x) => x === "USB") || "USB"; };      // the radio's own name for data-USB
  const slotLen = () => (info.mode === "FT4" ? 7.5 : 15);
  const nowS = () => (Date.now() + skew) / 1000;
  const can = () => !viewer && S.lease.holder?.conn === S.conn;

  // ------------------------------------------------------------------ top bar
  function paintTop() {
    const s = S.state;
    $("fx-freq").textContent = fmtDial(s.frequency);
    $("fx-rmode").textContent = s.mode || "";
    for (const b of root.querySelectorAll("[data-fm]")) b.classList.toggle("active", b.dataset.fm === info.mode);
    const on = !!info.on;
    $("fx-onoff").textContent = on ? "RX on" : "RX off";
    $("fx-onoff").classList.toggle("active", on);
    $("fx-enable").classList.toggle("active", txEnabled);
    $("fx-enable").textContent = txEnabled ? "TX enabled" : "Enable TX";
    $("fx-dx").value = document.activeElement === $("fx-dx") ? $("fx-dx").value : (qso?.dx || $("fx-dx").value);
    $("fx-qrz").disabled = !qrzUrl(dxCall());
    const bands = info.bands[info.mode] || [];
    const sel = $("fx-band");
    if (sel.dataset.mode !== info.mode + bands.length) {
      sel.dataset.mode = info.mode + bands.length;
      sel.innerHTML = `<option value="">Band...</option>` + bands.map((b) => `<option value="${b.hz}">${esc(b.name)}  ${(b.hz / 1e6).toFixed(b.hz % 1000 ? 4 : 3)}</option>`).join("");
    }
    const near = bands.find((b) => Math.abs(b.hz - (s.frequency || 0)) < 3000);
    sel.value = near ? String(near.hz) : "";
    for (const b of root.querySelectorAll(".ftx-ctl button:not(#fx-reset), .ftx-wfbar button, #fx-onoff, [data-fm], #fx-band, .ftx-msg button")) b.disabled = viewer || !can();      // every command needs the control of the radio
    $("fx-notice").hidden = true;
    if (!info.available) { $("fx-notice").hidden = false; $("fx-notice").textContent = "FT8/FT4 is not available: " + (info.reason || "the library is not installed") + "."; }
    else if (!my()) { $("fx-notice").hidden = false; $("fx-notice").textContent = "Set your callsign and grid square in the FT settings below (an administrator can) before transmitting."; }
    else if (info.on && s.mode && s.mode !== dataMode() && s.mode !== "USB") { $("fx-notice").hidden = false; $("fx-notice").textContent = `The radio is in ${s.mode}: FT8/FT4 needs ${dataMode()} (pick a band above to set it).`; }
    $("fx-txhz").value = document.activeElement === $("fx-txhz") ? $("fx-txhz").value : Math.round(txHz);
    $("fx-par").value = String(txParity);
    paintMsgs();
  }

  // the clock: where we are in the slot, even or odd
  let lastCan = null;
  function tickSlot() {
    const c = can();
    if (c !== lastCan) { lastCan = c; paintTop(); }                                   // control of the radio was taken or handed over
    const t = nowS(), L = slotLen(), into = t % L, idx = Math.floor(t / L);
    $("fx-bar").style.width = (into / L * 100).toFixed(1) + "%";
    $("fx-sec").textContent = String(Math.floor(into));
    const tx = !!S.state.ptt_owner && info.transmitting;
    paintPending(t);
    $("fx-utc").textContent = new Date(t * 1000).toISOString().slice(11, 19);
    $("fx-slotname").textContent = !info.on ? "idle" : tx ? "TX" : (idx % 2 ? "ODD" : "EVEN") + " slot";
    root.classList.toggle("ftx-tx", tx);
  }

  // ------------------------------------------------------------------ decodes
  const isMine = (text) => { const m = parseMessage(text); return !!my() && ((m.to && m.to === my()) || (m.kind === "cq" && m.from === my())); };
  function rowOf(it, rec, mine = false) {
    const m = parseMessage(it.text);
    const cls = ["ftx-row", m.kind === "cq" ? "cq" : "", m.to && m.to === my() ? "me" : "", mine ? "tx" : ""].join(" ").trim();
    const r = el(`<div class="${cls}"><span class="s">${mine ? "TX" : String(it.snr).padStart(3, " ")}</span><span class="d">${mine ? "" : (it.dt >= 0 ? "+" : "") + it.dt.toFixed(1)}</span><span class="f">${it.f}</span><span class="t">${esc(it.text)}</span>${it.c ? `<span class="c" title="Country">${esc(it.c)}</span>` : ""}</div>`);
    r.dataset.text = it.text; r.dataset.snr = it.snr; r.dataset.f = it.f; r.dataset.parity = rec.parity;
    if (mine) return r;
    r.onclick = () => {                                    // one tap selects (and fills the DX box), a second tap on the same row within 0.5 s answers it: also on a touch screen, which does not always send a double-click
      pick(it, rec);
      const again = selected?.row === r && performance.now() - selected.t < 500;
      for (const x of root.querySelectorAll(".ftx-row.sel")) x.classList.remove("sel");
      r.classList.add("sel");
      selected = { row: r, it, rec, t: performance.now() };
      if (again) act(it, rec);
    };
    return r;
  }
  function addSlot(rec) {
    const box = $("fx-all"), cqOnly = $("fx-cqonly").checked;
    const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    box.append(el(`<div class="ftx-sep">${hhmmss(rec.t0)} ${rec.mode} · ${rec.items.length} ${rec.late ? "more " : ""}decoded${rec.parity ? " (odd)" : " (even)"}</div>`));
    for (const it of rec.items) if (!cqOnly || parseMessage(it.text).kind === "cq" || isMine(it.text)) box.append(rowOf(it, rec));
    while (box.children.length > 600) box.firstChild.remove();
    if (stick) box.scrollTop = box.scrollHeight;
    seen.push(rec); while (seen.length > 3) seen.shift();
    drawOverlay();
  }
  function addDirected(node) {
    const box = $("fx-dir");
    box.append(node);
    while (box.children.length > 120) box.firstChild.remove();
    box.scrollTop = box.scrollHeight;
  }
  function onDecodes(rec) {
    addSlot(rec);
    for (const it of rec.items) {
      const m = parseMessage(it.text);
      if (!my() || !(m.to === my())) continue;
      addDirected(rowOf(it, rec));
      // The contact logic (the answers, the log) runs ONLY in the page that has control of the radio: a second page open on the same station (a phone and a computer) must not answer
      // or log the same contact again.
      if (can() && !viewer) follow(onDecode(qso, { text: it.text, snr: it.snr, parity: rec.parity }, my(), grid4()));
    }
  }
  // what the QSO logic decided: update the state, send the next message, log the contact
  function follow(r) {
    if (!r) return;
    if (r.qso !== qso && r.qso) qso = { ...r.qso, tOn: r.qso.tOn || qso?.tOn || new Date(nowS() * 1000) };
    if (r.log) logQso();
    if (r.tx) sendTx(r.tx.text, r.tx.parity, r.tx.repeats);
    else if (r.halt && txEnabled) send("ftx_halt").catch(() => {});                  // the contact is over: stop repeating the last message
    paintTop();
  }
  const gridOf = (m) => (m.kind === "cq" ? m.grid : m.kind === "grid" ? m.payload.toUpperCase() : "");
  // tapping a row of any message selects its SENDER as the DX station and fills the six messages (with the report measured from that decode); a contact under way is left alone
  function pick(it, rec) {
    const m = parseMessage(it.text);
    if (!m.from || m.from === my()) return;
    $("fx-dx").value = m.from;
    if (qso && qso.dx !== m.from && ["tx2", "tx3", "tx4"].includes(qso.stage)) return;
    qso = { dx: m.from, dxGrid: gridOf(m), rstSent: fmtReport(it.snr), rstRcvd: "", stage: "tx1", logged: false, tOn: qso?.dx === m.from && qso.tOn ? qso.tOn : new Date(nowS() * 1000) };
    paintTop();
  }
  function act(it, rec) {                                   // two taps / double click / Answer: start the contact with the sender of that message, or take up a message sent to us
    if (viewer) return;
    const m = parseMessage(it.text);
    if (!my()) return toast("Set your callsign first (FT settings).");
    if (!can()) return toast("You need control of the radio to transmit: press Take control.");
    if (!m.from || m.from === my()) return toast("There is no other station in that message.");
    txEnabled = true;                                      // answering a station is asking to transmit (as in WSJT-X): Enable TX switches on by itself
    if (m.to === my()) follow(onDecode(qso, { text: it.text, snr: it.snr, parity: rec.parity }, my(), grid4()));
    else {                                                  // a CQ, or two other stations talking: call the sender, message 1 in the other slot, on that station's frequency
      const a = answer(my(), grid4(), m.from, gridOf(m), it.snr, rec.parity);
      qso = { ...a.qso, tOn: new Date(nowS() * 1000) };
      if (!hold) { txHz = Math.max(200, Math.min(2900, it.f)); pref.set("ftxTx", txHz); }
      sendTx(a.tx.text, a.tx.parity);
    }
    $("fx-dx").value = m.from;
    paintTop();
  }

  // ------------------------------------------------------------------ transmit
  async function sendTx(text, parity, repeats) {
    if (viewer) return false;
    if (!txEnabled) { toast("Transmitting is not enabled: press Enable TX."); return false; }
    if (!can()) { toast("You need control of the radio to transmit."); return false; }
    try {
      const r = await send("ftx_tx", { text, freq: txHz, parity, ...(repeats ? { repeats } : {}) });
      info.error = "";
      return r;
    } catch (e) { info.error = "Not sent: " + e.message; paintPending(); toast(e.message); return false; }
  }
  function paintMsgs() {
    const box = $("fx-msgs"), list = slots(my() || "NOCALL", grid4() || "AA00", qso);
    if (box.dataset.sig === list.join("|") + info.on) return;
    box.dataset.sig = list.join("|") + info.on;
    queueMicrotask(markMsgs);
    box.replaceChildren(...list.map((text, i) => {
      const r = el(`<div class="ftx-msg"><span class="n">${i + 1}</span><input value="${esc(text)}" maxlength="40" aria-label="Message ${i + 1}" autocapitalize="characters"><button type="button">Send</button></div>`);
      r.querySelector("button").onclick = () => { const t = r.querySelector("input").value.trim(); if (t) { if (i === 5) qso = { stage: "cq" }; sendTx(t, txParity); } };
      return r;
    }));
  }
  // The six messages show where the contact is: done (green tick), the one being sent or about to be sent (highlighted, red while the transmitter is on), the rest plain.
  const STAGE_MSG = { tx1: 0, tx2: 1, tx3: 2, tx4: 3, tx5: 4, done: 4, cq: 5 };
  function markMsgs() {
    const rows = [...$("fx-msgs").children];
    if (!rows.length) return;
    const norm = (x) => String(x || "").trim().toUpperCase().replace(/\s+/g, " ");
    const p = info.pending;
    let cur = p ? rows.findIndex((r) => norm(r.querySelector("input").value) === norm(p.text)) : -1;
    if (cur < 0 && qso?.stage in STAGE_MSG) cur = STAGE_MSG[qso.stage];
    const done = qso?.stage === "done" ? 5 : qso?.stage in STAGE_MSG && qso.stage !== "cq" ? STAGE_MSG[qso.stage] : 0;
    rows.forEach((r, i) => {
      r.classList.toggle("done", i < done && i !== cur && i < 5);
      r.classList.toggle("cur", i === cur && (!!p || qso?.stage === "cq" || qso?.stage in STAGE_MSG));
      r.classList.toggle("txing", i === cur && !!info.transmitting);
    });
  }
  function paintPending(t = nowS()) {
    markMsgs();
    const p = info.pending, el_ = $("fx-txstat");
    if (!p) { el_.textContent = info.error || ""; return; }
    // a message goes out in the next slot of its parity: up to two slots away (30 s for FT8), so say when
    const L = slotLen(), idx = Math.floor(t / L), into = t - idx * L;
    let c = idx; while (c % 2 !== p.parity || (c === idx && into > 0.9)) c++;
    const wait = Math.max(0, Math.ceil(c * L + 0.2 - t));
    el_.textContent = `Sending "${p.text}" at ${Math.round(p.f0)} Hz in ${p.parity ? "odd" : "even"} slots. ` + (info.transmitting ? "Transmitting now. " : `Next transmission in ${wait} s. `) + `(${p.left} left, ${p.sent} sent)`;
  }

  // ------------------------------------------------------------------ log
  let logging = false;
  async function logQso() {
    if (!qso?.dx || viewer || logging || qso.logged) return;
    logging = true;
    const t = new Date(nowS() * 1000), on = qso.tOn || t;
    const q = { call: qso.dx, gridsquare: qso.dxGrid || "", mode: info.mode, rst_sent: qso.rstSent || "", rst_rcvd: qso.rstRcvd || "", freq_mhz: (S.state.frequency || 0) / 1e6,
                qso_date: utcDate(on), time_on: utcTime(on), qso_date_off: utcDate(t), time_off: utcTime(t) };
    const cmt = $("fx-comment").value.trim();
    if (cmt) q.comment = cmt;                               // a custom comment replaces the automatic one
    qso = { ...qso, logged: true };
    try { await api("/api/ftx/log", "POST", { ...q, conn: S.conn }); addDirected(el(`<div class="ftx-row logged"><span class="t">✓ LOGGED ${esc(q.call)}  ${esc(q.mode)}</span></div>`)); $("fx-comment").value = ""; }
    catch (e) { toast("Could not log it: " + e.message); qso = { ...qso, logged: false }; }
    logging = false;
  }
  const chips = (q) => ["qrz", "eqsl"].map((s) => {
    const u = q.uploaded?.[s];
    return `<span class="chip ${u ? (u.ok ? "ok" : "bad") : ""}" title="${esc(u ? u.message : "not uploaded")}">${{ qrz: "QRZ", eqsl: "eQSL" }[s]}${u ? (u.ok ? " ✓" : " ✗") : ""}</span>`;
  }).join("");
  function paintLog() {
    $("fx-logsum").textContent = `Log (${logRows.length} contact${logRows.length === 1 ? "" : "s"})`;
    $("fx-logrows").replaceChildren(...logRows.map((q) => {
      const r = el(`<div class="ftx-lrow"><b>${esc(q.call)}</b><span>${q.qso_date.slice(0, 4)}-${q.qso_date.slice(4, 6)}-${q.qso_date.slice(6)} ${q.time_on.slice(0, 2)}:${q.time_on.slice(2, 4)}</span><span>${esc(q.band)} ${esc(q.mode)}</span><span>${esc(q.rst_sent)}/${esc(q.rst_rcvd)}</span>${chips(q)}<button type="button" class="danger" aria-label="Delete">×</button></div>`);
      r.querySelector("button").onclick = async () => { if (!confirm(`Delete the contact with ${q.call} from the log?`)) return; try { await api(`/api/ftx/log/${q.id}/delete`, "POST", {}); logRows = logRows.filter((x) => x.id !== q.id); paintLog(); } catch (e) { toast(e.message); } };
      return r;
    }));
  }
  async function loadLog() { try { logRows = (await api("/api/ftx/log")).log; paintLog(); } catch { /* not available */ } }
  for (const b of root.querySelectorAll("[data-up]")) {
    b.onclick = async () => {
      const svc = b.dataset.up;
      $("fx-upmsg").textContent = "Uploading...";
      try { const r = await api("/api/ftx/upload", "POST", { service: svc }); $("fx-upmsg").textContent = `${r.sent} sent, ${r.failed} failed${r.left ? `, ${r.left} still waiting` : ""}. ${r.message}`; }
      catch (e) { $("fx-upmsg").textContent = e.message; }
      loadLog();
    };
  }

  // ------------------------------------------------------------------ waterfall
  const wf = $("fx-wf"), wctx = wf.getContext("2d"), ov = $("fx-ov");
  wctx.fillStyle = "#000018"; wctx.fillRect(0, 0, wf.width, wf.height);
  function drawRow(b64) {
    let bin; try { bin = atob(b64); } catch { return; }
    const w = wf.width;
    wctx.drawImage(wf, 0, 0, w, wf.height - 1, 0, 1, w, wf.height - 1);
    const img = wctx.createImageData(w, 1);
    for (let x = 0; x < w && x < bin.length; x++) { const c = PALETTE[bin.charCodeAt(x)]; img.data[x * 4] = c[0]; img.data[x * 4 + 1] = c[1]; img.data[x * 4 + 2] = c[2]; img.data[x * 4 + 3] = 255; }
    wctx.putImageData(img, 0, 0);
  }
  function drawOverlay() {
    const w = ov.clientWidth || 600, h = ov.clientHeight || 100;
    if (ov.width !== w) ov.width = w;
    if (ov.height !== h) ov.height = h;
    const g = ov.getContext("2d");
    g.clearRect(0, 0, w, h);
    const px = (f) => f / 3000 * w;
    const bw = Math.max(3, px(BW[info.mode] || 50));
    g.fillStyle = "rgba(251,146,60,.30)"; g.strokeStyle = "#fb923c"; g.lineWidth = 1;
    g.fillRect(px(txHz), 0, bw, h); g.strokeRect(px(txHz) + .5, .5, bw, h - 1);
    // the callsigns heard in the last two slots: a label on a dark plate, each on the first line where it does not cover another (the lines are 15 px apart)
    const size = w < 520 ? 11 : 12;
    g.font = `700 ${size}px system-ui, sans-serif`; g.textBaseline = "middle";
    const lines = Math.max(2, Math.floor((h - 4) / 15)), taken = Array.from({ length: lines }, () => []);
    const items = [];
    for (const rec of seen.slice(-2)) for (const it of rec.items) {
      const m = parseMessage(it.text);
      if (m.from && m.from !== my()) items.push({ who: m.from, f: it.f, kind: m.to === my() ? "me" : m.kind === "cq" ? "cq" : "", it, rec });
    }
    hits = [];
    items.sort((a, b) => (a.kind === "me" ? -1 : 0) - (b.kind === "me" ? -1 : 0));         // the ones for us get a line first
    const seenWho = new Set();
    for (const it of items) {
      if (seenWho.has(it.who + it.f)) continue;
      seenWho.add(it.who + it.f);
      const x = px(it.f), tw = g.measureText(it.who).width + 8;
      const x0 = Math.max(1, Math.min(x - 2, w - tw - 1));
      const line = taken.findIndex((row) => row.every(([a, b]) => x0 + tw + 2 < a || x0 > b + 2));
      g.fillStyle = it.kind === "me" ? "rgba(251,146,60,.9)" : it.kind === "cq" ? "rgba(52,211,153,.55)" : "rgba(56,189,248,.45)";
      g.fillRect(x, 0, 1, h);                                                              // a thin line down to the signal itself
      if (line < 0) { hits.push({ x: x - 5, y: 0, w: 10, h, it: it.it, rec: it.rec, who: it.who, weak: true }); continue; }          // no room left: the line alone marks it (and can be tapped)
      taken[line].push([x0, x0 + tw]);
      const y = 8 + line * 15;
      g.fillStyle = "rgba(6,10,20,.88)"; g.fillRect(x0, y - 7, tw, 14);
      if (qso?.dx === it.who) { g.strokeStyle = "#22d3ee"; g.lineWidth = 2; g.strokeRect(x0 + 1, y - 6, tw - 2, 12); g.lineWidth = 1; }          // the station we are working
      hits.push({ x: x0 - 3, y: y - 10, w: tw + 6, h: 20, it: it.it, rec: it.rec, who: it.who });                                                  // a little larger than the label: a finger is not a pixel
      g.fillStyle = it.kind === "me" ? "#fed7aa" : it.kind === "cq" ? "#bbf7d0" : "#e0f2fe";
      g.fillText(it.who, x0 + 4, y + .5);
    }
  }
  // a tap on a callsign selects that station (the same as tapping its row in All decodes; a second tap on it calls it); a tap anywhere else moves the transmit frequency
  const hitAt = (e) => {
    const r = ov.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    return hits.find((q) => !q.weak && x >= q.x && x <= q.x + q.w && y >= q.y && y <= q.y + q.h) || hits.find((q) => q.weak && x >= q.x && x <= q.x + q.w);
  };
  wf.parentElement.onclick = (e) => {
    const q = hitAt(e);
    if (q) {
      const again = selected && !selected.row && selected.who === q.who && performance.now() - selected.t < 500;
      pick(q.it, q.rec);
      selected = { row: null, it: q.it, rec: q.rec, who: q.who, t: performance.now() };
      for (const x of root.querySelectorAll(".ftx-row.sel")) x.classList.remove("sel");
      drawOverlay();
      if (again) act(q.it, q.rec);
      return;
    }
    const r = wf.getBoundingClientRect(), f = Math.round(((e.clientX - r.left) / r.width * 3000 - (BW[info.mode] || 50) / 2) / 10) * 10;
    setTxHz(f);
  };
  wf.parentElement.onmousemove = (e) => wf.parentElement.classList.toggle("hot", !!hitAt(e));
  new ResizeObserver(drawOverlay).observe(wf.parentElement);
  function setTxHz(f) {
    txHz = Math.max(200, Math.min(2900, Math.round(+f) || 1500)); pref.set("ftxTx", txHz); $("fx-txhz").value = txHz; drawOverlay();
    if (info.pending && can() && txEnabled) sendTx(info.pending.text, info.pending.parity);        // the message already being sent moves to the new frequency
  }
  $("fx-txhz").onchange = (e) => setTxHz(e.target.value);

  // ------------------------------------------------------------------ buttons
  async function setOn(on, mode) {
    try { await send("ftx", { on, mode: mode || info.mode }); } catch (e) { toast(e.message); }
  }
  $("fx-onoff").onclick = () => setOn(!info.on);
  const bandOf = (mode, hz) => (info.bands[mode] || []).find((b) => Math.abs(b.hz - (hz || 0)) < 3000);
  async function tune(hz) {
    try { await send("set_mode", { mode: dataMode() }); } catch { /* a radio without a data mode: the frequency still moves */ }
    await send("set_frequency", { hz });
    pref.set("ftxHz" + info.mode, hz);
  }
  for (const b of root.querySelectorAll("[data-fm]")) b.onclick = async () => {
    const from = info.mode, to = b.dataset.fm;
    if (from === to) return;
    const was = bandOf(from, S.state.frequency);
    info.mode = to;
    try {
      const now = was && (info.bands[to] || []).find((x) => x.name === was.name);      // the same band, at the other mode's frequency
      if (now) await tune(now.hz);
      if (info.on) await setOn(true, to); else paintTop();
    } catch (e) { toast(e.message); }
  };
  $("fx-band").onchange = async (e) => {
    const hz = +e.target.value; if (!hz) return;
    await tune(hz);
    if (!info.on) await setOn(true, info.mode);
  };
  // Entering this screen puts the radio in the data mode on the last FT8/FT4 frequency and switches decoding on; leaving it switches decoding off and puts back the mode and frequency the radio had.
  let away = null;
  let heard = false, here = false;
  const sound = () => {                                    // this device's sound while on this screen: off (Listen switched off) or low (a fraction of the volume); back as it was afterwards
    const au = ctx.audio?.(); if (!au) return;
    const low = $("fs-snd").value === "low";
    if (here && low) { if (heard && !au.hear) { heard = false; au.toggleListen(); } au.duck(0.12); }
    else if (here) { au.duck(null); if (au.hear) { heard = true; au.toggleListen(); } }
    else { au.duck(null); if (heard) { heard = false; if (!au.hear) au.toggleListen(); } }
  };
  $("fs-snd").value = pref.get("ftxSound", "off");
  $("fs-snd").onchange = () => { pref.set("ftxSound", $("fs-snd").value); sound(); };
  ctx.ftxActive = async (active) => {
    here = active; sound();
    if (!info.available || !can()) return;
    try {
      if (active) {
        if (S.state.freedv_on) { await send("freedv", { on: false }); toast("FreeDV switched off: FT8/FT4 and FreeDV cannot share the radio."); }       // the FT8 tones would otherwise go through the FreeDV modem
        away = { mode: S.state.mode, hz: S.state.frequency };
        const list = info.bands[info.mode] || [];
        const hz = pref.get("ftxHz" + info.mode, 0) || bandOf(info.mode, S.state.frequency)?.hz || list.find((b) => b.name === "20m")?.hz || list[0]?.hz;
        if (hz) await tune(hz);
        await setOn(true, info.mode);
      } else {
        if (info.on) await setOn(false, info.mode);
        if (away) {
          const a = away; away = null;
          if (a.mode) await send("set_mode", { mode: a.mode });
          if (a.hz) await send("set_frequency", { hz: a.hz });
        }
      }
    } catch (e) { toast(e.message); }
  };
  $("fx-enable").onclick = () => { txEnabled = !txEnabled; if (!txEnabled) send("ftx_halt"); paintTop(); };
  $("fx-halt").onclick = () => { txEnabled = false; if (qso) qso = { ...qso, stage: "" }; send("ftx_halt"); paintTop(); };
  $("fx-answer").onclick = () => { if (!selected) return toast("Tap a station in All decodes first."); act(selected.it, selected.rec); };
  $("fx-cq").onclick = async () => {
    if (!my()) return toast("Set your callsign first (FT settings).");
    if (can()) txEnabled = true;                           // pressing Call CQ is asking to transmit (as in WSJT-X)
    qso = { stage: "cq" };
    if (!info.on) await setOn(true, info.mode);
    sendTx(`CQ ${my()} ${grid4()}`.trim(), txParity);
  };
  $("fx-par").onchange = (e) => { txParity = +e.target.value; pref.set("ftxParity", txParity); };
  $("fx-dx").onchange = () => {
    const dx = $("fx-dx").value.trim().toUpperCase();
    if (!dx) { qso = null; return paintTop(); }
    qso = { dx, dxGrid: "", rstSent: "", rstRcvd: "", stage: "tx1", logged: false, tOn: new Date(nowS() * 1000) };
    paintTop();
  };
  $("fx-log").onclick = () => { if (!qso?.dx) return toast("Type the DX callsign first."); qso = { ...qso, logged: false }; logQso(); };
  $("fx-erase").onclick = () => { qso = null; $("fx-dx").value = ""; $("fx-comment").value = ""; paintTop(); };
  // Clear empties a list (and, for All decodes, stays empty after a reload: only decodes newer than the press come back). Reset is all of it at once plus a halt.
  const clearAll = () => { $("fx-all").replaceChildren(); seen.length = 0; pref.set("ftxClearedAt", nowS()); drawOverlay(); };
  const clearDir = () => $("fx-dir").replaceChildren();
  const clearWf = () => { wctx.fillStyle = "#000018"; wctx.fillRect(0, 0, wf.width, wf.height); };
  $("fx-clr-all").onclick = clearAll;
  $("fx-clr-dir").onclick = clearDir;
  $("fx-reset").onclick = () => {
    txEnabled = false; qso = null; $("fx-dx").value = ""; $("fx-comment").value = "";
    send("ftx_halt").catch(() => {});
    clearAll(); clearDir(); clearWf(); paintTop();
  };
  const dxCall = () => (document.activeElement === $("fx-dx") ? $("fx-dx").value : (qso?.dx || $("fx-dx").value)).trim();
  $("fx-qrz").onclick = () => { const u = qrzUrl(dxCall()); if (u) window.open(u, "_blank", "noopener,noreferrer"); else toast("Select a station first (tap it in All decodes), or type the DX callsign."); };
  $("fx-msgreset").onclick = () => {                       // back to the start: no DX station, no contact, nothing being repeated, the messages as they are before a station is chosen
    qso = null; selected = null; $("fx-dx").value = ""; $("fx-comment").value = "";
    for (const x of root.querySelectorAll(".ftx-row.sel")) x.classList.remove("sel");
    send("ftx_halt").catch(() => {});
    $("fx-msgs").dataset.sig = ""; paintMsgs(); paintTop();
  };
  $("fx-hold").onchange = (e) => { hold = e.target.checked; };
  $("fx-cqonly").checked = pref.get("ftxCq", false);
  $("fx-cqonly").onchange = (e) => pref.set("ftxCq", e.target.checked);

  // ------------------------------------------------------------------ settings
  function paintSettings() {
    const s = info.settings;
    if (document.activeElement?.closest?.(".ftx-set")) return;
    $("fs-call").value = s.callsign || ""; $("fs-grid").value = s.grid_square || ""; $("fs-lvl").value = s.tx_level_db ?? -6; $("fs-rep").value = s.max_tx_repeats ?? 6;
    for (const id of ["fs-call", "fs-grid", "fs-lvl", "fs-rep", "fs-save"]) $(id).disabled = !admin;
    $("fs-note").textContent = admin ? "" : "Only an administrator can change these.";
    $("fs-up").hidden = !admin;
  }
  $("fs-save").onclick = async () => {
    try {
      await api("/api/config", "PUT", { ftx: { callsign: $("fs-call").value.trim().toUpperCase(), grid_square: $("fs-grid").value.trim(), tx_level_db: +$("fs-lvl").value, max_tx_repeats: +$("fs-rep").value } });
      toast("FT settings applied.");
    } catch (e) { toast(e.message); }
  };
  async function loadUp() {
    if (!admin) return;
    try {
      const u = (await api("/api/config")).config.upload;
      $("fu-qrz").value = u.qrz_key; $("fu-equser").value = u.eqsl_user; $("fu-eqpw").value = u.eqsl_password;
      $("fu-aqrz").checked = u.auto_qrz; $("fu-aeq").checked = u.auto_eqsl;
    } catch { /* not shown */ }
  }
  $("fu-save").onclick = async () => {
    try {
      await api("/api/config", "PUT", { upload: { qrz_key: $("fu-qrz").value.trim(), eqsl_user: $("fu-equser").value.trim(), eqsl_password: $("fu-eqpw").value,
        auto_qrz: $("fu-aqrz").checked, auto_eqsl: $("fu-aeq").checked } });
      toast("Upload accounts saved."); loadUp();
    } catch (e) { toast(e.message); }
  };

  // ------------------------------------------------------------------ events from the Pi
  function onEvent(m) {
    if (m.k === "dec") { if (m.mode === info.mode) { info.history.push(m); onDecodes(m); noteDt(m); } }
    else if (m.k === "wf") drawRow(m.r);
    else if (m.k === "state") { info.on = m.on; info.mode = m.mode; paintTop(); }
    else if (m.k === "pending") { info.pending = m.pending; if (m.why && !m.pending) { info.error = m.why; toast("FT8/FT4: " + m.why); } else info.error = ""; paintPending(); }
    else if (m.k === "tx") { info.transmitting = true; addDirected(rowOf({ f: m.f, snr: 0, dt: 0, text: m.text, c: m.c }, { parity: m.parity }, true)); }
    else if (m.k === "txdone") { info.transmitting = false; }
    else if (m.k === "settings") { info.settings = m.settings; paintSettings(); paintTop(); }
    else if (m.k === "clock") { info.clock = { ok: m.ok, offset_ms: m.offset_ms, source: m.source }; paintClock(); }
    else if (m.k === "logged") { if (!logRows.some((x) => x.id === m.q.id)) logRows.unshift(m.q); paintLog(); }
    else if (m.k === "uploaded") { const q = logRows.find((x) => x.id === m.id); if (q) { (q.uploaded ||= {})[m.service] = { ok: m.ok, message: m.message }; paintLog(); } }
  }
  // Time matters more here than anywhere: every slot starts on the UTC second. Two readings: what the Pi's clock says about itself (NTP, and its offset from the server), and what
  // the band says (the median DT of the stations just decoded: how far THEY are from OUR slot start; it should stay within +-0.5 s, and a steady value away from zero means this clock
  // or the radio's audio delay is off).
  const dtSeen = [];                                        // [slot, median DT, stations] of the last few slots
  function noteDt(rec) {                                    // the early and the final pass of a slot both report: the last report of a slot replaces the earlier one
    if (rec.dtm == null) return;
    const i = dtSeen.findIndex((x) => x[0] === rec.slot);
    if (i >= 0) dtSeen[i] = [rec.slot, rec.dtm, rec.n]; else dtSeen.push([rec.slot, rec.dtm, rec.n]);
    while (dtSeen.length > 4) dtSeen.shift();
    paintDt();
  }
  function paintClock() {
    const c = $("fx-clock"), k = info.clock || {};
    c.hidden = k.ok !== false;
    c.textContent = "The Pi's clock is not synchronised: decoding and transmitting need it within a fraction of a second";
    const n = $("fx-ntp"), off = k.offset_ms;
    let cls = "", txt = "sync ?", tip = "The Pi cannot say whether its clock is synchronised.";
    if (k.ok === false) { cls = "bad"; txt = "NOT synced"; tip = "The Pi's clock is not synchronised by NTP. On the Pi: sudo timedatectl set-ntp true (it needs internet access)."; }
    else if (k.ok === true) {
      const a = off == null ? 0 : Math.abs(off);
      cls = a > 500 ? "bad" : a > 100 ? "warn" : "ok";
      txt = off == null ? "NTP ✓" : `NTP ${a < 1 ? "<1" : Math.round(a)} ms`;
      tip = `The Pi's clock is synchronised${k.source ? " by " + k.source : ""}${off == null ? "" : `, ${off >= 0 ? "+" : ""}${Math.abs(off) < 1 ? off.toFixed(2) : Math.round(off)} ms from the time server`}.`;
    }
    n.className = "ftx-chip " + cls; n.textContent = txt; n.title = tip;
    paintDt();
  }
  function paintDt() {
    const d = $("fx-dt"), all = dtSeen.flatMap((x) => Array(x[2]).fill(x[1]));
    if (all.length < 3) { d.className = "ftx-chip"; d.textContent = "DT -"; d.title = "The median DT of the stations decoded: shown once a few have been heard."; return; }
    const v = [...all].sort((a, b) => a - b)[Math.floor(all.length / 2)], a = Math.abs(v);
    d.className = "ftx-chip " + (a < 0.5 ? "ok" : a < 1.0 ? "warn" : "bad");
    d.textContent = `DT ${v >= 0 ? "+" : ""}${v.toFixed(1)} s`;
    d.title = `Median DT of the last ${all.length} stations decoded: how far they are from the start of YOUR slot. It should stay within +-0.5 s. A steady value away from zero means the Pi's clock (or the radio's audio delay) is off, and your own signal is then just as late or early at the other end.`;
  }
  ctx.ftxEvent = onEvent;

  async function load() {
    try {
      info = await api("/api/ftx");
      skew = info.now * 1000 - Date.now();
      $("fx-all").replaceChildren(); seen.length = 0;
      const clearedAt = pref.get("ftxClearedAt", 0);
      for (const rec of info.history.filter((r) => r.mode === info.mode && r.t0 > clearedAt)) addSlot(rec);
      dtSeen.length = 0; for (const rec of info.history.filter((r) => r.mode === info.mode).slice(-6)) noteDt(rec);
      paintSettings(); paintTop(); paintPending(); paintClock(); loadLog(); loadUp();
    } catch (e) { toast("FT8/FT4: " + e.message); }
  }
  load();
  const timer = setInterval(() => { if (root.isConnected) tickSlot(); else clearInterval(timer); }, 250);
  signal?.addEventListener("abort", () => { clearInterval(timer); if (ctx.ftxEvent === onEvent) ctx.ftxEvent = null; ctx.ftxActive = null; });
  return { update: paintTop };
}
