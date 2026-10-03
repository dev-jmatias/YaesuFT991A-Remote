import { el } from "../util.js";
import { S_TICKS, dbToPos, levelColor, pct, powerColor, rawToDb, sColor, sText, swrColor } from "../meter-cal.js";

// Segmented bar meter on <canvas>: coloured by position, fast attack, slow decay, peak marker.
// The animation loop only runs while something is moving.
class SegBar {
  constructor(host, { label, ticks = [], colorAt, segs = 36, small = false, big = false }) {
    this.ticks = ticks; this.colorAt = colorAt; this.segs = segs;
    this.cur = 0; this.target = 0; this.peak = 0; this.peakAt = 0; this.raf = 0;
    this.root = el(`<div class="seg ${small ? "small" : ""} ${big ? "big" : ""}"><div class="seglbl"><span>${label}</span><b>-</b></div><canvas></canvas></div>`);
    host.append(this.root);
    this.cv = this.root.querySelector("canvas");
    this.val = this.root.querySelector("b");
    new ResizeObserver(() => this.size()).observe(this.cv);
    this.size();
  }

  size() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.cv.clientWidth, h = this.cv.clientHeight;
    if (!w || !h) return;
    this.cv.width = Math.round(w * dpr); this.cv.height = Math.round(h * dpr);
    this.dpr = dpr; this.w = w; this.h = h;
    this.draw();
  }

  setAlarm(on) {
    if (this.alarm === on) return;
    this.alarm = on;
    this.root.classList.toggle("alarm", on);
    this.draw();
  }

  // frac: 0..1 position along the bar; text: readout
  set(frac, text) {
    this.target = Math.max(0, Math.min(1, frac));
    this.val.textContent = text;
    if (!this.raf) this.raf = requestAnimationFrame((t) => this.frame(t));
  }

  frame(now) {
    this.raf = 0;
    this.cur += (this.target - this.cur) * (this.target > this.cur ? 0.55 : 0.12);
    if (this.target >= this.peak) { this.peak = this.target; this.peakAt = now; }
    else if (now - this.peakAt > 900) this.peak = Math.max(this.target, this.peak - 0.012);
    this.draw();
    const moving = Math.abs(this.cur - this.target) > 0.002 || this.peak > this.target + 0.002;
    if (moving && !document.hidden) this.raf = requestAnimationFrame((t) => this.frame(t));
  }

  draw() {
    // the bitmap must match the displayed size; the resize observer alone was not enough (a bar created while its column was still
    // narrow stayed blurry / empty), so check on every draw
    if (this.cv.clientWidth !== this.w || this.cv.clientHeight !== this.h) { this.size(); return; }
    if (!this.w) return;
    const c = this.cv.getContext("2d");
    const { w, h, dpr, segs } = this;
    const cs = getComputedStyle(this.cv), off = cs.getPropertyValue("--seg-off").trim() || "#1b2438", txt = cs.getPropertyValue("--seg-text").trim() || cs.getPropertyValue("--dim").trim();
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    // the small TX meters (power/SWR/ALC/COMP) drop their scale numbers on phones: bar only, the label row above has the readout
    const bare = !!this.root.closest(".txmhost") && window.matchMedia("(max-width: 699px)").matches;
    const fontPx = Math.max(10, Math.round(h * 0.28)), barH = bare ? h : Math.max(8, h - fontPx - 6), gap = 2, sw = (w - gap * (segs - 1)) / segs;
    for (let i = 0; i < segs; i++) {
      const f = (i + 0.5) / segs, lit = i / segs < this.cur;
      c.fillStyle = lit ? (this.alarm ? "#f43f5e" : this.colorAt(f)) : off;       // alarm: the whole lit bar turns red
      c.globalAlpha = lit ? 1 : 0.85;
      c.fillRect(i * (sw + gap), 0, sw, barH);
    }
    c.globalAlpha = 1;
    const pi = Math.min(segs - 1, Math.floor(this.peak * segs));
    if (this.peak > 0.02) { c.fillStyle = "#fff"; c.fillRect(pi * (sw + gap), 0, Math.max(2, sw * 0.5), barH); }
    c.fillStyle = txt; c.font = `${fontPx}px system-ui, sans-serif`; c.textBaseline = "top";
    for (const [pos, label] of bare ? [] : this.ticks) {
      const x = pos * w;
      c.textAlign = pos < 0.04 ? "left" : pos > 0.96 ? "right" : "center";
      c.fillRect(Math.min(w - 1, Math.max(0, x)), barH + 1, 1, 3);
      c.fillText(label, x, barH + 4);
    }
  }
}

const PCT_TICKS = [[0, "0"], [0.25, "25"], [0.5, "50"], [0.75, "75"], [1, "100%"]];

// S-meter (receive). Lives inside the VFO-A panel.
export function createSMeter(host, ctx) {
  const bar = new SegBar(host, { label: "SIGNAL <small class=\"dim\">(approx. S scale)</small>", ticks: S_TICKS, colorAt: sColor, segs: 40, big: true });
  return {
    update() {
      const db = rawToDb(ctx.S.state.smeter || 0);
      bar.set(dbToPos(db), sText(db));
      bar.root.classList.toggle("dimmed", !!ctx.S.state.tx);
    },
  };
}

// Transmit meters: power, SWR, ALC, COMP. Dimmed while receiving.
export function createTxMeters(host, ctx) {
  const cal = ctx.S.ui?.meter || { alc_full: 50, comp_full: 255 };
  const root = el(`<div class="meters"></div>`);
  host.append(root);
  const tx = el(`<div class="txmeters"></div>`);
  root.append(tx);
  const po = new SegBar(tx, { label: "Power", small: true, segs: 24, colorAt: powerColor, ticks: [[0, "0"], [0.5, "50"], [1, "100 W"]] });
  const swr = new SegBar(tx, { label: "SWR", small: true, segs: 24, colorAt: swrColor, ticks: [[0, "1"], [0.25, "1.5"], [0.5, "2"], [1, "3+"]] });
  const alc = new SegBar(tx, { label: "ALC", small: true, segs: 24, colorAt: levelColor, ticks: PCT_TICKS });
  const comp = new SegBar(tx, { label: "COMP", small: true, segs: 24, colorAt: levelColor, ticks: PCT_TICKS });
  const f = ctx.S.caps.features;
  if (!f.swr_meter) swr.root.hidden = true;
  if (!f.alc_meter) alc.root.hidden = true;
  if (!f.comp_meter) comp.root.hidden = true;

  // SWR warning (Admin > Config > Meters): while transmitting at or above the limit the SWR bar turns red and a message names the
  // ratio. Radios that report a ratio are compared directly; for the others (FT-991A: raw 0..255) the ratio is ESTIMATED with a line
  // through 1:1 = 0 and 3:1 = swr_raw_at_3 (provisional calibration). The message stays 5 s after the transmission so it is not missed.
  const warnAt = Number(cal.swr_warn) || 0, rawAt3 = Number(cal.swr_raw_at_3) || 100;
  const warn = el(`<div class="swrwarn" role="alert" hidden></div>`);
  root.append(warn);
  let latchUntil = 0, worst = 0, wasTx = false, timer = 0;
  const swrNow = (st) => (st.swr !== undefined ? Number(st.swr) : st.swr_raw > 0 ? 1 + (2 * st.swr_raw) / rawAt3 : null);

  const update = () => {
    const st = ctx.S.state, now = performance.now();
    root.classList.toggle("is-tx", !!st.tx);
    if (st.tx && !wasTx) { worst = 0; latchUntil = 0; }                // a new transmission starts a new reading
    wasTx = !!st.tx;
    const ratio = swrNow(st);
    const over = warnAt > 0 && !!st.tx && ratio !== null && ratio >= warnAt;
    if (over) { worst = Math.max(worst, ratio); latchUntil = now + 5000; }
    const show = over || now < latchUntil;
    swr.setAlarm(show);
    warn.hidden = !show;
    if (show) warn.textContent = `High SWR: ${st.swr !== undefined ? "" : "about "}${worst.toFixed(1)}:1 - check the antenna and the cable`;
    clearTimeout(timer);
    if (show && !over) timer = setTimeout(update, latchUntil - now + 50);   // hide it again when the 5 s are over
    if (st.rf_power_out !== undefined) po.set(st.rf_power_out / 100, st.rf_power_out + " W");
    else po.set((st.po_raw || 0) / 255, "");
    if (st.swr !== undefined) swr.set((st.swr - 1) / 2, Number(st.swr).toFixed(2) + ":1");
    else swr.set(((st.swr_raw || 0) / 255) * 0.5, "");
    const a = pct(st.alc || 0, cal.alc_full), cp = pct(st.comp || 0, cal.comp_full);
    alc.set(a / 100, Math.round(a) + " %");
    comp.set(cp / 100, Math.round(cp) + " %");
  };

  return { update };
}
