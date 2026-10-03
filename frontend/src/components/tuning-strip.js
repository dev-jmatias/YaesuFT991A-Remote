import { buzz, el, fmtStep } from "../util.js";

const PX_PER_STEP = 9;     // finger travel per tuning step
const FRICTION = 0.93;     // momentum decay per animation frame
const MIN_LABEL_PX = 84;   // closest spacing between two numbers on the scale

// "Nice" label spacing (1, 2 or 5 x 10^k Hz) so that the numbers are never closer than MIN_LABEL_PX.
function labelSpan(pxPerHz) {
  for (let k = 0; k < 10; k++) for (const m of [1, 2, 5]) if (m * 10 ** k * pxPerHz >= MIN_LABEL_PX) return m * 10 ** k;
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
    const cs = getComputedStyle(cv), dim = cs.getPropertyValue("--seg-text").trim() || "#a9b7d3", mark = "#9fb2d6", minor = "#4b587a";
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const step = stepHz(), pxPerHz = PX_PER_STEP / step;
    const centre = (ctx.freq() || 0) + (acc * step) / PX_PER_STEP;
    const span = labelSpan(pxPerHz), minorSpan = span / 5;
    const lo = centre - (W / 2 + 40) / pxPerHz, hi = centre + (W / 2 + 40) / pxPerHz;
    const xOf = (f) => W / 2 + (f - centre) * pxPerHz;
    const tall = Math.min(17, Math.round(H * 0.28)), short = Math.round(tall / 2);          // tick heights follow the strip height
    c.lineWidth = 1;
    c.strokeStyle = minor; c.beginPath();
    for (let f = Math.ceil(lo / minorSpan) * minorSpan; f <= hi; f += minorSpan) {
      if (Math.round(f / minorSpan) % 5 === 0) continue;
      const x = Math.round(xOf(f)) + 0.5; c.moveTo(x, H - short); c.lineTo(x, H);
    }
    c.stroke();
    c.strokeStyle = mark; c.fillStyle = dim; c.font = "11px ui-monospace, Consolas, monospace"; c.textBaseline = "alphabetic"; c.textAlign = "center";
    c.beginPath();
    for (let f = Math.ceil(lo / span) * span; f <= hi; f += span) {
      const x = Math.round(xOf(f)) + 0.5; c.moveTo(x, H - tall); c.lineTo(x, H);
    }
    c.stroke();
    for (let f = Math.ceil(lo / span) * span; f <= hi; f += span) c.fillText(labelText(Math.round(f), span), xOf(f), H - tall - 5);
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

  return {
    update() {
      stepTxt.textContent = `${fmtStep(stepHz())} per tick`;
      coarseBtn.classList.toggle("on", !!ctx.ui.coarse);
      coarseBtn.setAttribute("aria-pressed", String(!!ctx.ui.coarse));
      draw();
    },
  };
}
