import { api } from "../api.js";
import { el } from "../util.js";

// The Recorder block on the Radio tab, under Mode: four buttons.
//  REC        records what you hear (decoded speech when FreeDV is on, otherwise the radio's audio; before the equalizer) into a clip of at most two minutes;
//  PLAY       plays the last recording here, in the browser;
//  SEND       sends the last recording over the air like a voice keyer: PTT is keyed, the clip plays on the server's clock, and PTT is released by itself when it ends;
//  RECORDINGS opens the last five (play, download, delete, send one).
// The Pi keeps five clips: a new recording replaces the oldest.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MODE_LABEL = { RADE: "RADE V1", RADE2: "RADE V2" };
const pad = (n) => String(n).padStart(2, "0");
const clock = (s) => `${Math.floor(s / 60)}:${pad(Math.floor(s % 60))}`;
const when = (t0) => { const d = new Date(t0 * 1000); return `${d.getDate()} ${d.toLocaleString(undefined, { month: "short" })} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const label = (c) => [when(c.t0), c.frequency ? `${(c.frequency / 1e6).toFixed(4)} MHz` : null, c.mode, c.freedv ? MODE_LABEL[c.freedv] || c.freedv : null, `${c.seconds} s`].filter(Boolean).join(" · ");
const newest = (S) => [...(S.state.recordings || [])].sort((a, b) => b.t0 - a.t0);
const url = (c) => `/api/recordings/${c.id}.wav`;
const rawurl = (c) => `/api/recordings/${c.id}.raw.wav`;

export function createRecBar(host, ctx) {
  const { S, send } = ctx;
  if (!S.audio?.available || !S.state.rec) return { update() {} };
  const canEdit = S.user.role !== "viewer";
  const row = el(`<div class="recbar">
    <button type="button" class="led recb" data-rec title="Record what you hear (up to 2 minutes). The last 5 recordings are kept.">● REC</button>
    <button type="button" class="led" data-play title="Play the last recording here">▶ Play</button>
    <button type="button" class="led" data-send title="Send the last recording over the air: PTT is keyed and released by itself, like a voice keyer">Send</button>
    <button type="button" class="led" data-list title="The last 5 recordings">Recordings</button></div>`);
  host.append(row);
  const $ = (s) => row.querySelector(s);
  const bRec = $("[data-rec]"), bPlay = $("[data-play]"), bSend = $("[data-send]"), bList = $("[data-list]");
  let player = null, started = 0, timer = 0;

  const stopPlay = () => { if (player) { player.pause(); player = null; } paint(); };
  bRec.disabled = !canEdit;
  bRec.onclick = () => send("rec", { on: !S.state.rec?.on });
  bPlay.onclick = () => {
    if (player) { stopPlay(); return; }
    const c = newest(S)[0];
    if (!c) return;
    player = new Audio(url(c));
    player.onended = player.onerror = () => { player = null; paint(); };
    player.play().catch(() => { player = null; paint(); });
    paint();
  };
  bSend.onclick = () => {
    if (ctx.pttApi?.sending) { ctx.pttApi.stop(); return; }
    const c = newest(S)[0];
    if (c) ctx.pttApi?.sendClip(c.id);
  };
  bList.onclick = () => openRecordings(ctx);

  const tick = () => { bRec.textContent = `■ ${clock((performance.now() - started) / 1000)}`; };
  function paint() {
    const on = !!S.state.rec?.on, last = newest(S)[0], sending = !!ctx.pttApi?.sending;
    bRec.classList.toggle("on", on);
    if (on && !timer) { started = performance.now(); tick(); timer = setInterval(tick, 500); }
    else if (!on) { if (timer) { clearInterval(timer); timer = 0; } bRec.textContent = "● REC"; }
    bPlay.textContent = player ? "■ Stop" : "▶ Play";
    bPlay.classList.toggle("on", !!player);
    bPlay.disabled = !last;
    bSend.textContent = sending ? "Stop" : "Send";
    bSend.classList.toggle("on", sending);
    bSend.disabled = !sending && (!last || !canEdit || !S.caps.features.ptt || !S.safety.ptt_permitted || !!S.state.tx || !!S.state.tuning);
    bSend.title = last ? `Send the last recording (${label(last)}) over the air: PTT is keyed and released by itself, like a voice keyer` : "Record something first";
  }
  paint();
  return { update: paint };
}

// The last five recordings: a player, Download, Send and Delete for each. Refreshes itself while it is open.
export function openRecordings(ctx) {
  const { S, toast } = ctx;
  const canEdit = S.user.role !== "viewer";
  const sheet = el(`<div class="sheet recsheet" role="dialog" aria-label="Recordings"><div class="sheetbox">
    <div class="sheethead"><h2>Recordings</h2><button class="icon wide" data-x>Close</button></div>
    <p class="dim recnote">The last <b>5</b> recordings of what you heard (before the equalizer; nothing is recorded while you transmit). A new recording replaces the oldest.
      With FreeDV on, the radio's own audio (before decoding) is saved too: <b>Download raw radio audio</b> is the file to analyse a badly decoded station.
      <b>Send</b> keys PTT, plays the clip and releases PTT by itself when it ends: send only audio you may retransmit, such as the other station's own signal back to them as an audio report, and give your callsign.</p>
    <div class="reclist" data-list></div></div></div>`);
  document.body.append(sheet);
  const listEl = sheet.querySelector("[data-list]");
  let shown = null;
  const close = () => { clearInterval(iv); sheet.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  sheet.addEventListener("click", (e) => { if (e.target === sheet) close(); });
  sheet.querySelector("[data-x]").onclick = close;
  const paint = () => {
    const items = newest(S);
    const key = JSON.stringify(items.map((c) => c.id));
    if (key === shown) return;
    if ([...listEl.querySelectorAll("audio")].some((a) => !a.paused)) return;                                  // do not rebuild under a playing clip
    shown = key;
    listEl.replaceChildren(...(items.length ? items.map((c) => {
      const r = el(`<div class="recrow"><div class="reclabel">${esc(label(c))}</div>
        <audio controls preload="none" src="${url(c)}"></audio>
        <div class="row"><a class="btnlink" href="${url(c)}?download=1" download>Download</a>
          ${c.raw ? `<a class="btnlink" href="${rawurl(c)}?download=1" download title="The radio's own audio before FreeDV decoding: the file to analyse why a station decodes badly">Download raw radio audio</a>` : ""}
          <button type="button" class="active" data-send>Send</button>
          <button type="button" class="danger" data-del>Delete</button></div></div>`);
      const sb = r.querySelector("[data-send]");
      sb.disabled = !canEdit || !S.caps.features.ptt || !S.safety.ptt_permitted || !!S.state.tx;
      sb.onclick = () => { close(); ctx.pttApi?.sendClip(c.id); };
      r.querySelector("[data-del]").disabled = !canEdit;
      r.querySelector("[data-del]").onclick = async () => {
        if (!confirm("Delete this recording?")) return;
        try { await api(`/api/recordings/${c.id}/delete`, "POST", {}); } catch (e) { toast(e.message); }
      };
      return r;
    }) : [el(`<p class="dim">No recordings yet. Press REC on the Radio tab while you listen to a station.</p>`)]));
  };
  const iv = setInterval(paint, 700);
  paint();
}
