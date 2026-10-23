import { api } from "../api.js";
import { el, qrzUrl } from "../util.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const utcDate = (t) => `${t.getUTCFullYear()}${pad(t.getUTCMonth() + 1)}${pad(t.getUTCDate())}`;
const utcTime = (t) => `${pad(t.getUTCHours())}${pad(t.getUTCMinutes())}${pad(t.getUTCSeconds())}`;
export const CALL_RE = /^(?:[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](?:\/[A-Z0-9]{1,4})?|[A-Z0-9]{1,4}\/[A-Z0-9]{3,7})$/;

// The "Log a contact" panel of the CW and RTTY screens (the FT8/FT4 screen logs from its own QSO; this one is filled in by hand, the callsign can be picked from the decoded text).
// The contacts go into the same log as FT8/FT4 (/api/ftx/log): one ADIF file for all modes, and the same QRZ / eQSL upload.
export function createQsoPanel(host, ctx, { mode, defaultRst = "599" }) {
  const { S, toast } = ctx;
  const viewer = S.user.role === "viewer";
  let rows = [], tOn = null, logging = false;
  const root = el(`<section class="qsop">
    <h3 class="blabel">Log a contact <span class="dim" id="qp-hint"></span></h3>
    <div class="row qsop-form">
      <label>Call <input id="qp-call" class="qsop-call" maxlength="14" autocomplete="off" autocapitalize="characters" spellcheck="false" aria-label="Callsign of the other station"></label>
      <label>RST sent <input id="qp-rs" class="qsop-rst" maxlength="4" autocomplete="off" aria-label="Report you sent"></label>
      <label>RST rcvd <input id="qp-rr" class="qsop-rst" maxlength="4" autocomplete="off" aria-label="Report you received"></label>
      <label>Name <input id="qp-name" maxlength="40" autocomplete="off" aria-label="Name of the other operator"></label>
      <label class="qsop-cmt">Comment <input id="qp-cmt" maxlength="80" autocomplete="off" aria-label="Comment"></label>
      <button type="button" id="qp-log" class="active">Log QSO</button><button type="button" id="qp-clr">Clear</button>
      <a id="qp-qrz" class="btn" target="_blank" rel="noopener" hidden>QRZ</a>
    </div>
    <details class="fdv-edit ftx-logbox"><summary id="qp-sum">Log</summary>
      <div class="row"><a class="btn" href="/api/ftx/log.adi" download>Export ADIF</a><button type="button" data-up="qrz">Upload to QRZ</button><button type="button" data-up="eqsl">Upload to eQSL</button></div>
      <div class="dim" id="qp-upmsg"></div>
      <div id="qp-rows" class="ftx-log"></div>
    </details>
  </section>`);
  const pre = `qp${mode.toLowerCase()}-`;                      // the CW and RTTY screens both live in the page: their ids must differ
  for (const n of root.querySelectorAll("[id^='qp-']")) n.id = pre + n.id.slice(3);
  host.append(root);
  const $ = (id) => root.querySelector("#" + pre + id.slice(3));
  const can = () => !viewer && S.lease.holder?.conn === S.conn;
  $("qp-rs").value = $("qp-rr").value = defaultRst;
  $("qp-hint").textContent = `Double-click a callsign in the text to fill it in. Logged as ${mode}.`;
  const call = () => $("qp-call").value.trim().toUpperCase();
  function paintForm() {
    const c = call(), ok = CALL_RE.test(c);
    $("qp-log").disabled = !can() || !ok || logging;
    const q = ok ? qrzUrl(c) : "";
    $("qp-qrz").hidden = !q; if (q) $("qp-qrz").href = q;
    for (const b of root.querySelectorAll("#qp-clr, [data-up]")) b.disabled = viewer;
  }
  $("qp-call").oninput = () => { $("qp-call").value = $("qp-call").value.toUpperCase(); if (!tOn && call()) tOn = new Date(); if (!call()) tOn = null; paintForm(); };
  function clearForm() {
    for (const id of ["qp-call", "qp-name", "qp-cmt"]) $(id).value = "";
    $("qp-rs").value = $("qp-rr").value = defaultRst;
    tOn = null; paintForm();
  }
  $("qp-clr").onclick = clearForm;
  async function logIt() {
    if (!can() || logging) return;
    const c = call();
    if (!CALL_RE.test(c)) { toast("That callsign does not look right."); return; }
    logging = true; paintForm();
    const t = new Date(), on = tOn || t;
    const q = { call: c, mode, rst_sent: $("qp-rs").value.trim(), rst_rcvd: $("qp-rr").value.trim(), freq_mhz: (S.state.frequency || 0) / 1e6,
                qso_date: utcDate(on), time_on: utcTime(on), qso_date_off: utcDate(t), time_off: utcTime(t), name: $("qp-name").value.trim(), comment: $("qp-cmt").value.trim() };
    try {
      const r = await api("/api/ftx/log", "POST", { ...q, conn: S.conn });
      toast(r.duplicate ? `${c} is already in the log (same band and mode a few minutes ago).` : `Logged ${c}.`);
      if (!r.duplicate) { clearForm(); if (r.q && !rows.some((x) => x.id === r.q.id)) { rows.unshift(r.q); paintRows(); } }
    } catch (e) { toast("Could not log it: " + e.message); }
    logging = false; paintForm();
  }
  $("qp-log").onclick = () => { logIt(); };
  for (const id of ["qp-call", "qp-rs", "qp-rr", "qp-name", "qp-cmt"]) $(id).onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); logIt(); } };

  const chips = (q) => ["qrz", "eqsl"].map((s) => {
    const u = q.uploaded?.[s];
    return `<span class="chip ${u ? (u.ok ? "ok" : "bad") : ""}" title="${esc(u ? u.message : "not uploaded")}">${{ qrz: "QRZ", eqsl: "eQSL" }[s]}${u ? (u.ok ? " ✓" : " ✗") : ""}</span>`;
  }).join("");
  function paintRows() {
    const mine = rows.filter((q) => q.mode === mode);
    $("qp-sum").textContent = `Log (${mine.length} ${mode} contact${mine.length === 1 ? "" : "s"}; ${rows.length} in all)`;
    $("qp-rows").replaceChildren(...mine.slice(0, 40).map((q) => {
      const r = el(`<div class="ftx-lrow"><b>${esc(q.call)}</b><span>${q.qso_date.slice(0, 4)}-${q.qso_date.slice(4, 6)}-${q.qso_date.slice(6)} ${q.time_on.slice(0, 2)}:${q.time_on.slice(2, 4)}</span><span>${esc(q.band)} ${esc(q.mode)}</span><span>${esc(q.rst_sent)}/${esc(q.rst_rcvd)}</span>${chips(q)}<button type="button" class="ftx-clear" title="Delete this contact from the log">Delete</button></div>`);
      r.querySelector("button").disabled = viewer;
      r.querySelector("button").onclick = async () => {
        if (!confirm(`Delete the contact with ${q.call} from the log?`)) return;
        try { await api(`/api/ftx/log/${q.id}/delete`, "POST", {}); rows = rows.filter((x) => x.id !== q.id); paintRows(); } catch (e) { toast(e.message); }
      };
      return r;
    }));
  }
  async function load() { try { rows = (await api("/api/ftx/log")).log; paintRows(); } catch { /* the log is not available */ } }
  for (const b of root.querySelectorAll("[data-up]")) {
    b.onclick = async () => {
      $("qp-upmsg").textContent = "Uploading...";
      try { const r = await api("/api/ftx/upload", "POST", { service: b.dataset.up }); $("qp-upmsg").textContent = `${r.sent} sent, ${r.failed} failed${r.left ? `, ${r.left} still waiting` : ""}. ${r.message}`; }
      catch (e) { $("qp-upmsg").textContent = e.message; }
      load();
    };
  }
  // the server tells every page when a contact is logged or uploaded (the FT8/FT4 screen, another browser, ...)
  const listener = (m) => {
    if (m.k === "logged") { if (!rows.some((x) => x.id === m.q.id)) rows.unshift(m.q); paintRows(); }
    else if (m.k === "uploaded") { const q = rows.find((x) => x.id === m.id); if (q) { (q.uploaded ||= {})[m.service] = { ok: m.ok, message: m.message }; paintRows(); } }
  };
  (ctx.qsoListeners ||= new Set()).add(listener);
  load(); paintForm();
  return {
    update: paintForm,
    setCall(c) { $("qp-call").value = String(c).toUpperCase(); $("qp-call").dispatchEvent(new Event("input")); $("qp-call").scrollIntoView?.({ block: "nearest" }); },
    close() { ctx.qsoListeners?.delete(listener); },
  };
}

// Double-clicking a word of a text box fills the call box when the word looks like a callsign.
export function pickCallsign(box, panel, toast) {
  box.ondblclick = () => {
    const w = (getSelection()?.toString() || "").trim().toUpperCase().replace(/[^A-Z0-9/]/g, "");
    if (CALL_RE.test(w)) panel.setCall(w); else if (w) toast?.(`"${w}" does not look like a callsign.`);
  };
}
