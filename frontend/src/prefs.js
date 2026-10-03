// Per-device display preferences (kept in this browser's localStorage; never sent to the server).
// Status lights in the VFO A header: each can be hidden for a cleaner header. Default: all shown.
const KEY = "rr.lights";
export const LIGHTS = [["ipo", "Preamp (IPO / AMP1 / AMP2)"], ["att", "ATT"], ["agc", "AGC"], ["tuner", "Tuner"]];

export function getLights() {
  const all = Object.fromEntries(LIGHTS.map(([k]) => [k, true]));
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
    for (const k of Object.keys(all)) if (saved[k] === false) all[k] = false;
  } catch { /* storage blocked or damaged: show everything */ }
  return all;
}

export function setLight(name, shown) {
  const now = getLights();
  now[name] = !!shown;
  try { localStorage.setItem(KEY, JSON.stringify(now)); } catch { /* not remembered */ }
  window.dispatchEvent(new CustomEvent("rr-lights"));          // open pages repaint at once
}
