// The look of the app: a named set of colour values (the CSS in style.css, html[data-skin=...]). Kept in this browser only.
export const SKINS = [["slate", "Slate (default)"], ["yaesu", "Yaesu: dark body, blue display, red lamps"], ["ft710", "FT-710: dark body, amber display"]];
export const LINES = [["soft", "Soft"], ["normal", "Normal (default)"], ["bright", "Bright"]];
const KEY = "rr.skin", LKEY = "rr.lines";

export function getSkin() {
  try { const v = localStorage.getItem(KEY); if (SKINS.some(([k]) => k === v)) return v; } catch { /* storage blocked */ }
  return "slate";
}
export function setSkin(name) {
  const k = SKINS.some(([n]) => n === name) ? name : "slate";
  try { localStorage.setItem(KEY, k); } catch { /* not remembered */ }
  applySkin();
}
export function getLines() {
  try { const v = localStorage.getItem(LKEY); if (LINES.some(([n]) => n === v)) return v; } catch { /* storage blocked */ }
  return "normal";
}
export function setLines(name) {
  try { localStorage.setItem(LKEY, LINES.some(([n]) => n === name) ? name : "normal"); } catch { /* not remembered */ }
  applySkin();
}
export function applySkin() {
  const l = getLines();
  if (l === "normal") delete document.documentElement.dataset.lines; else document.documentElement.dataset.lines = l;
  const k = getSkin();
  if (k === "slate") delete document.documentElement.dataset.skin; else document.documentElement.dataset.skin = k;
  let m = document.querySelector('meta[name="theme-color"]');
  if (m) m.content = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || m.content;
  window.dispatchEvent(new CustomEvent("rr-skin"));              // canvases repaint in the new colours
}
