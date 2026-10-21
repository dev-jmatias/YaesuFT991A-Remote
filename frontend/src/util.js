export const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
export const fmtStep = (s) => (s >= 1000 ? `${s / 1000} kHz` : `${s} Hz`);
export const buzz = (ms = 4) => { try { navigator.vibrate?.(ms); } catch { /* unsupported */ } };

// Frequency -> "14.200.000" parts (9 digits: 100 MHz .. 1 Hz)
export function digitsOf(hz) {
  return String(Math.max(0, Math.round(hz))).padStart(9, "0").split("").map(Number);
}

// QRZ.com lookup of a callsign. A portable or mobile suffix (K1ABC/P, /QRP, /M...) is dropped, and for a prefix form (EA8/DL1ABC) the longer part, the real callsign, is looked up.
const NOT_A_CALL = new Set(["P", "M", "MM", "AM", "QRP", "LH", "A", "B", "T", "R", "J", "AA", "AG", "AE", "KT"]);
export function baseCall(call) {
  const parts = String(call || "").trim().toUpperCase().replace(/[<>]/g, "").split("/").filter((p) => p && !NOT_A_CALL.has(p) && !/^\d$/.test(p));
  return parts.sort((a, b) => b.length - a.length)[0] || "";
}
export const qrzUrl = (call) => { const c = baseCall(call); return /^[A-Z0-9]{3,10}$/.test(c) && /\d/.test(c) && /[A-Z]/.test(c) ? "https://www.qrz.com/db/" + c : ""; };       // a callsign has a digit and a letter

// remembered in this browser only (keys are prefixed "rr.")
export const pref = {
  get: (k, d) => { try { const v = localStorage.getItem("rr." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem("rr." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
