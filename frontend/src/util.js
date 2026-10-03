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
