import { buzz, el, fmtStep } from "../util.js";

const PX_PER_STEP = 9;     // finger travel per tuning step
const FRICTION = 0.93;     // momentum decay per animation frame

// "Nice" label spacing (1, 2 or 5 x 10^k Hz) so that the numbers (font size fs px: "14.195" or, for finer spacing, "14.195.500") never touch.
function labelSpan(pxPerHz, fs) {
  for (let k = 0; k < 10; k++) for (const m of [1, 2, 5]) {
    const span = m * 10 ** k;
    if (span * pxPerHz >= (span >= 1000 ? 6 : 10) * fs * 0.62 + 18) return span;
  }
  return 1e9;
}

// 14195000 -> "14.195" (spacing of a kHz or more) or "14.195.500" (finer)
function labelText(hz, span) {
  const mhz = Math.floor(hz / 1e6), khz = Math.floor((hz % 1e6) / 1e3), rest = hz % 1e3;
  const base = `${mhz}.${String(khz).padStart(3, "0")}`;
  return span >= 1000 ? base : `${base}.${String(rest).padStart(3, "0")}`;
}

// Sliding tuning scale (used on all screens; there is no round dial). Drag / swipe left = frequency up, right = down: the scale
// moves under your finger like a real tuning dial and shows frequency numbers whose spacing follows the tuning step (the span
// that is visible grows with a bigger step or the x10 switch). A quick flick keeps gliding. Uses the shared tuning step and Coarse switch.
export function createTuningStrip(host, ctx) {
  const root = el(`<div class="strip" aria-label="Tuning strip: swipe sideways to tune" role="slider" tabindex="0">
    <canvas class="strip-ruler" data-ruler></canvas>
    <div class="strip-needle"></div>
    <div class="strip-info"><span data-step></span></div>
    <button class="strip-coarse" data-coarse aria-pressed="false">&times;10</button>
  </div>`);
  host.append(root);
  const cv = root.querySelector("[data-ruler]"), stepTxt = root.querySelector("[data-step]"), coarseBtn = root.querySelector("[data-coarse]");
  let acc = 0, last = null, lastT = 0, vel = 0, raf = 0, down = false, W = 0, H = 0;
  const stepHz = () => ctx.ui.step * (ctx.ui.coarse ? 10 : 1);

  function size() {
    const dpr = window.devicePixelRatio || 1;
    W = cv.clientWidth; H = cv.clientHeight;
    if (!W || !H) return;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    draw();
  }
  new ResizeObserver(size).observe(cv);

  // Absolute-frequency scale: position of frequency f is  W/2 + (f - centre) * PX_PER_STEP / stepHz.
  // "centre" includes the part of a step the finger has already travelled, so the scale moves smoothly between steps.
  function draw() {
    if (!W) return;
    const dpr = window.devicePixelRatio || 1, c = cv.getContext("2d");
    const cs = getComputedStyle(cv);
    const glow = cs.getPropertyValue("--dial-glow").trim() || "#4ade80";                            // the colour of the scale: light that glows (dark looks) or printed ink (paper look)
    const halo = parseFloat(cs.getPropertyValue("--dial-halo")), fade = parseFloat(cs.getPropertyValue("--dial-fade"));
    const lit = () => {                                                                             // a glow around the strokes, or (paper) a hair of light under them like printed ink
      if (halo > 0) { c.shadowColor = glow; c.shadowBlur = halo; c.shadowOffsetY = 0; }
      else { c.shadowColor = "rgba(255,250,232,.7)"; c.shadowBlur = 0; c.shadowOffsetY = 1; }
    };
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const step = stepHz(), pxPerHz = PX_PER_STEP / step;
    const centre = (ctx.freq() || 0) + (acc * step) / PX_PER_STEP;
    const fs = Math.max(14, Math.min(26, Math.round(H * 0.3)));
    const span = labelSpan(pxPerHz, fs), minorSpan = span / 5;
    const lo = centre - (W / 2 + 60) / pxPerHz, hi = centre + (W / 2 + 60) / pxPerHz;
    const xOf = (f) => W / 2 + (f - centre) * pxPerHz;
    // like the lit glass scale of a vintage radio: the numbers and ticks are brightest in the middle (under the lamp) and fade towards the edges
    const light = (x) => fade + (1 - fade) * Math.exp(-(((x - W / 2) / (W * 0.36)) ** 2));
    const yb = Math.round(H * 0.72), tall = Math.round(H * 0.30), short = Math.round(tall / 2), below = Math.round(H * 0.1);
    c.lineCap = "round"; c.fillStyle = c.strokeStyle = glow;
    // the scale line, with its halo
    c.save();
    c.globalAlpha = 0.9; lit(); c.lineWidth = 2;
    const g = c.createLinearGradient(0, 0, W, 0);
    g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(0.18, glow); g.addColorStop(0.82, glow); g.addColorStop(1, "rgba(0,0,0,0)");
    c.strokeStyle = g; c.beginPath(); c.moveTo(0, yb + 0.5); c.lineTo(W, yb + 0.5); c.stroke();
    c.restore();
    c.lineWidth = 1.5;
    for (let f = Math.ceil(lo / minorSpan) * minorSpan; f <= hi; f += minorSpan) {            // the fine ticks
      if (Math.round(f / minorSpan) % 5 === 0) continue;
      const x = Math.round(xOf(f)) + 0.5;
      c.globalAlpha = light(x) * 0.8; c.beginPath(); c.moveTo(x, yb - short); c.lineTo(x, yb); c.moveTo(x, yb + 3); c.lineTo(x, yb + 3 + below); c.stroke();
    }
    lit(); c.lineWidth = 2.5;
    c.font = `700 ${fs}px "Segoe UI", system-ui, sans-serif`; c.textBaseline = "alphabetic"; c.textAlign = "center";
    for (let f = Math.ceil(lo / span) * span; f <= hi; f += span) {                          // the numbered ticks
      const x = Math.round(xOf(f)) + 0.5;
      c.globalAlpha = light(x);
      c.beginPath(); c.moveTo(x, yb - tall); c.lineTo(x, yb + 3 + below); c.stroke();
      c.fillText(labelText(Math.round(f), span), xOf(f), yb - tall - 4);
    }
    c.globalAlpha = 1; c.shadowBlur = 0; c.shadowOffsetY = 0;
  }

  function emit(dx) {                              // dx > 0 means the scale moved right (frequency goes down)
    acc -= dx;
    const n = Math.trunc(acc / PX_PER_STEP);
    if (n) { acc -= n * PX_PER_STEP; ctx.tune(n * stepHz()); buzz(3); }
    draw();
  }
  function glide() {
    vel *= FRICTION;
    if (Math.abs(vel) < 0.15 || down) { raf = 0; return; }
    emit(vel);
    raf = requestAnimationFrame(glide);
  }
  root.onpointerdown = (e) => {
    if (e.target === coarseBtn) return;
    cancelAnimationFrame(raf); raf = 0; down = true; vel = 0;
    try { root.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    last = e.clientX; lastT = performance.now(); acc = 0;
  };
  root.onpointermove = (e) => {
    if (!down || last === null) return;
    const dx = e.clientX - last, now = performance.now();
    vel = dx / Math.max(1, now - lastT) * 16;        // px per frame
    last = e.clientX; lastT = now;
    emit(dx);
  };
  const end = () => { down = false; last = null; if (Math.abs(vel) > 2 && !raf) raf = requestAnimationFrame(glide); };
  root.onpointerup = end; root.onpointercancel = end; root.onlostpointercapture = end;
  root.onwheel = (e) => { e.preventDefault(); emit(-(Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY)); };
  root.onkeydown = (e) => {
    const k = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
    if (k) { e.preventDefault(); ctx.tune(k * stepHz()); }
  };
  coarseBtn.onclick = () => { ctx.ui.coarse = !ctx.ui.coarse; ctx.setStep(ctx.ui.step); };
  // the look of the scale (theme = the app colours, then paper = an old meter face, then a lit old white, green, amber): double-click the scale to try the next one; remembered in this browser
  const LOOKS = ["theme", "paper", "white", "green", "amber"];
  const look = (v) => { root.dataset.dial = v; draw(); };
  root.dataset.dial = "theme";
  try { const v = localStorage.getItem("rr.dial"); if (LOOKS.includes(v)) root.dataset.dial = v; } catch { /* storage blocked */ }
  root.ondblclick = (e) => {
    if (e.target === coarseBtn) return;
    const v = LOOKS[(LOOKS.indexOf(root.dataset.dial) + 1) % LOOKS.length];
    look(v);
    try { localStorage.setItem("rr.dial", v); } catch { /* storage blocked */ }
  };

  return {
    update() {
      stepTxt.textContent = `${fmtStep(stepHz())} per tick`;
      coarseBtn.classList.toggle("on", !!ctx.ui.coarse);
      coarseBtn.setAttribute("aria-pressed", String(!!ctx.ui.coarse));
      draw();
    },
  };
}
