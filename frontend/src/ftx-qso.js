// FT8 / FT4 message parsing and the QSO sequence (the standard one, as the WSJT-X documentation lays it out). Pure functions, no page, no network: the FT8 tab (components/ftx.js)
// feeds them what was decoded and sends what they answer.
//
//   Calling CQ:   CQ me grid  ->  (they)  me them grid        ->  them me -rpt     ->  (they)  me them R-rpt   ->  them me RR73  ->  (they) me them 73
//   Answering:    them me grid  ->  (they)  me them -rpt       ->  them me R-rpt    ->  (they)  me them RR73    ->  them me 73
// The server repeats the message it was given in every slot of the chosen parity until the next one replaces it, so each step only has to say what to send next.

export const GRID = /^[A-R]{2}[0-9]{2}([A-X]{2})?$/i;
const REPORT = /^[+-][0-9]{2}$/;
const unbracket = (c) => c.replace(/^<|>$/g, "");

// "CQ DX K1ABC FN30" / "K1FM WV1W -10" -> { kind, to, from, payload, grid, ... }
export function parseMessage(text) {
  const t = String(text || "").trim().toUpperCase().split(/\s+/).filter(Boolean);
  if (!t.length) return { kind: "other" };
  if (t[0] === "CQ") {
    let i = 1;
    if (t.length >= 3 && /^[A-Z]{1,4}$/.test(t[1]) && t[1] !== t[2]) i = 2;            // CQ DX ..., CQ NA ..., CQ POTA ...
    const from = unbracket(t[i] || "");
    const last = t[t.length - 1];
    return { kind: "cq", from, mod: i === 2 ? t[1] : "", grid: t.length > i + 1 && GRID.test(last) ? last : "" };
  }
  if (t.length >= 3) {
    const to = unbracket(t[0]), from = unbracket(t[1]), payload = t.slice(2).join(" ");
    let kind = "text";
    if (payload === "RR73") kind = "rr73";                                            // RR73 looks like a grid square (RR is a valid field): it is tested first
    else if (GRID.test(payload)) kind = "grid";
    else if (REPORT.test(payload)) kind = "report";
    else if (/^R[+-][0-9]{2}$/.test(payload)) kind = "rreport";
    else if (payload === "RRR") kind = "rrr";
    else if (payload === "73") kind = "73";
    return { kind, to, from, payload, report: kind === "report" ? payload : kind === "rreport" ? payload.slice(1) : "" };
  }
  return { kind: "other" };
}

export const fmtReport = (snr) => {
  const n = Math.max(-30, Math.min(30, Math.round(snr)));
  return (n < 0 ? "-" : "+") + String(Math.abs(n)).padStart(2, "0");
};

// The messages of the QSO as the six slots of the screen show them
export function slots(my, grid, qso) {
  const dx = qso?.dx || "";
  const rpt = qso?.rstSent || "-10";
  return [
    dx ? `${dx} ${my} ${grid}` : "",
    dx ? `${dx} ${my} ${rpt}` : "",
    dx ? `${dx} ${my} R${rpt}` : "",
    dx ? `${dx} ${my} RR73` : "",
    dx ? `${dx} ${my} 73` : "",
    `CQ ${my} ${grid}`,
  ];
}

// A new QSO that we start: by answering a CQ (we send message 1) or by typing a callsign
export function answer(my, grid, dx, dxGrid, snr, decodeParity) {
  const qso = { dx, dxGrid: dxGrid || "", rstSent: snr == null ? "" : fmtReport(snr), rstRcvd: "", stage: "tx1", logged: false };
  return { qso, tx: { text: `${dx} ${my} ${grid}`, parity: 1 - decodeParity }, log: false };
}

// One decode addressed to us (or, while we call CQ, any answer). Returns { qso, tx, log, halt } where tx = { text, parity } is the next message (or null: nothing to change),
// log = true the moment the contact is complete for us (we send RR73 or 73, or the last message arrives) and halt = true when there is nothing left to send. `qso` may be null (calling CQ or idle).
export function onDecode(qso, d, my, grid) {
  const m = parseMessage(d.text);
  if (!m.to || m.to !== my || !m.from) return { qso, tx: null, log: false };
  const parity = 1 - d.parity;
  const rpt = fmtReport(d.snr);
  const say = (q, text, log = false) => ({ qso: q, tx: { text, parity }, log: log && !q.logged });
  const calling = !qso || qso.stage === "cq";
  if (calling) {
    const q = { dx: m.from, dxGrid: "", rstSent: rpt, rstRcvd: "", stage: "", logged: false };
    if (m.kind === "grid") { q.dxGrid = m.payload.toUpperCase(); q.stage = "tx2"; return say(q, `${m.from} ${my} ${rpt}`); }
    if (m.kind === "report") { q.rstRcvd = m.report; q.stage = "tx3"; return say(q, `${m.from} ${my} R${rpt}`); }
    if (m.kind === "rreport") { q.rstRcvd = m.report; q.stage = "tx4"; return say(q, `${m.from} ${my} RR73`, true); }
    return { qso, tx: null, log: false };
  }
  if (m.from !== qso.dx) return { qso, tx: null, log: false };
  const q = { ...qso, rstSent: qso.rstSent || rpt };
  if (m.kind === "grid") { q.dxGrid = q.dxGrid || m.payload.toUpperCase(); return say(q, qso.stage === "tx1" ? `${m.from} ${my} ${grid}` : `${m.from} ${my} ${rpt}`); }
  if (m.kind === "report") { q.rstRcvd = m.report; q.rstSent = rpt; q.stage = "tx3"; return say(q, `${m.from} ${my} R${rpt}`); }
  if (m.kind === "rreport") { q.rstRcvd = m.report; q.stage = "tx4"; return say(q, `${m.from} ${my} RR73`, true); }
  if (m.kind === "rr73" || m.kind === "rrr") {
    if (qso.stage === "tx4" || qso.stage === "tx5") return { qso: { ...q, stage: "done" }, tx: null, log: !qso.logged, halt: true };
    return { qso: { ...q, stage: "done" }, tx: { text: `${m.from} ${my} 73`, parity, repeats: 1 }, log: !qso.logged };
  }
  if (m.kind === "73") return { qso: { ...q, stage: "done" }, tx: null, log: !qso.logged, halt: true };       // nothing more to send: stop repeating
  return { qso, tx: null, log: false };
}

// "20261017" / "101500" in UTC from a Date
export const utcDate = (d) => d.toISOString().slice(0, 10).replace(/-/g, "");
export const utcTime = (d) => d.toISOString().slice(11, 19).replace(/:/g, "");
