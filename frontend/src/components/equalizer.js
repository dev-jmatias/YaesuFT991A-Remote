import { el } from "../util.js";

// Parametric equalizer for the audio you hear (RX) and the audio you send (TX): 5, 10 or 18 bands, each with a frequency, a gain and a Q, like the parametric EQ of Thetis.
// Drag a node to set its frequency (left/right) and gain (up/down); the mouse wheel over the graph changes the selected band's Q; the boxes below give exact values; double-click a node
// to zero its gain. The settings live on the server and are shared by everyone connected (the radio has one setting). The Radio tab has two buttons (RX EQ, TX EQ) after Memories that
// switch each equalizer on and off. FreeDV's modem tones are never equalized: only speech.
let W, H, X0, X1, Y0, Y1, RD, RH;          // the graph's size: set in createEqualizer (a taller graph with bigger nodes on a phone)
const GRID_HZ = [[50, "50"], [100, "100"], [200, "200"], [500, "500"], [1000, "1k"], [2000, "2k"], [5000, "5k"], [10000, "10k"]];
const DIRS = [["rx", "Receive"], ["tx", "Transmit"]];
// presets: gain at anchor frequencies (Hz -> dB), read at each band's own frequency
const PRESETS = {
  Flat: [[100, 0]],
  Voice: [[63, -12], [125, -8], [250, -3], [500, 0], [1000, 3], [2000, 5], [4000, 3], [8000, -3]],
  "Bass cut": [[63, -12], [125, -9], [250, -5], [500, -2], [1000, 0]],
  "Treble cut": [[2000, 0], [4000, -3], [6000, -7], [8000, -12]],
  "Bass boost": [[63, 8], [125, 6], [250, 3], [500, 0]],
  "Treble boost": [[2000, 0], [4000, 3], [6000, 6], [8000, 9]],
  // decoded RADE speech is bass-heavy and a little muddy: less below 500 Hz, more presence at 1-4 kHz, a touch less at the top (found on a real receiver, 2026-10)
  "FreeDV speech": [[60, -12], [125, -8], [250, -3], [500, 0], [1000, 3], [2000, 5], [3000, 4], [4000, 3], [6000, -0.5], [8000, -3]],
};
const curveAt = (pts, f) => {
  if (f <= pts[0][0]) return pts[0][1];
  for (let i = 1; i < pts.length; i++) {
    if (f <= pts[i][0]) { const [f0, g0] = pts[i - 1], [f1, g1] = pts[i]; return g0 + ((Math.log(f) - Math.log(f0)) / (Math.log(f1) - Math.log(f0))) * (g1 - g0); }
  }
  return pts[pts.length - 1][1];
};
// the same sum of peaking filters the server uses (analogue prototype), in dB
const respDb = (bands, f, maxDb) => {
  let t = 0;
  for (const b of bands) {
    if (!b.g) continue;
    const a = 10 ** (b.g / 40), x = f / b.f, d = (1 - x * x) ** 2;
    t += 10 * Math.log10((d + (x * a / b.q) ** 2) / (d + (x / (a * b.q)) ** 2));
  }
  return Math.max(-2 * maxDb, Math.min(2 * maxDb, t));
};
const cutDb = (cut, f) => {                              // the low-cut and high-cut: Butterworth, 24 dB per octave
  let t = 0;
  if (cut?.low) t -= 10 * Math.log10(1 + (cut.low / f) ** 8);
  if (cut?.high) t -= 10 * Math.log10(1 + (f / cut.high) ** 8);
  return Math.max(t, -80);
};
const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };

export function createEqualizer(host, ctx) {
  const { S, send } = ctx;
  if (!S.audio?.available || !S.state.eq) return { update() {} };
  const lim = S.state.eq.limits, FMIN = lim.f[0], FMAX = lim.f[1], maxDb = lim.g, QMIN = lim.q[0], QMAX = lim.q[1];
  const canEdit = S.user.role !== "viewer";
  const compact = window.innerWidth < 700;
  [W, H, X0, X1, Y0, Y1, RD, RH] = compact ? [360, 270, 34, 352, 8, 242, 11, 22] : [640, 230, 38, 630, 8, 206, 8, 15];
  const xOf = (f) => X0 + (Math.log(f / FMIN) / Math.log(FMAX / FMIN)) * (X1 - X0);
  const fOf = (x) => FMIN * Math.pow(FMAX / FMIN, (x - X0) / (X1 - X0));
  const yOf = (g) => Y0 + ((maxDb - g) / (2 * maxDb)) * (Y1 - Y0);
  const gOf = (y) => maxDb - ((y - Y0) / (Y1 - Y0)) * 2 * maxDb;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const root = el(`<section class="eqcard" aria-label="Equalizer"><h3 class="blabel">Equalizer</h3>
    <p class="dim eqnote">Parametric, shared by everyone connected. <b>Receive</b> shapes the speech you hear (the decoded speech when FreeDV is on); <b>Transmit</b> shapes your voice before it is sent.
      Drag a node to set its frequency and gain; turn the mouse wheel over the graph to change the selected band's <b>Q</b> (width); double-click a node to zero it.
      FreeDV's modem tones are never equalized. It adds about 10 ms of delay. The RX EQ and TX EQ buttons on the Radio tab switch each one on and off.</p></section>`);
  host.append(root);
  const blocks = {};

  for (const [dir, title] of DIRS) {
    const b = el(`<div class="eqblock" data-dir="${dir}">
      <div class="eqhead"><b>${title}</b>
        <button type="button" class="led" data-on>Off</button>
        <label class="eqcount">Bands <select data-count aria-label="Number of bands">${[5, 10, 18].map((n) => `<option value="${n}">${n}</option>`).join("")}</select></label>
        <select data-preset aria-label="Preset"><option value="">Preset…</option>${Object.keys(PRESETS).map((p) => `<option>${p}</option>`).join("")}</select>
        <button type="button" data-reset>Reset</button></div>
      <div class="eqgraph" data-g></div>
      <div class="eqsel"><span data-selname class="dim">Select a node</span>
        <label><small>Freq (Hz)</small><input type="number" data-f min="${FMIN}" max="${FMAX}" step="1" inputmode="numeric"></label>
        <label><small>Gain (dB)</small><input type="number" data-gain min="-${maxDb}" max="${maxDb}" step="0.5" inputmode="decimal"></label>
        <label><small>Q</small><input type="number" data-q min="${QMIN}" max="${QMAX}" step="0.1" inputmode="decimal"></label></div>
      ${true ? `<div class="eqcuts">
        <label class="chk"><input type="checkbox" data-lowon> Low-cut <input type="number" data-lowf min="${lim.low[0]}" max="${lim.low[1]}" step="5" inputmode="numeric"> Hz</label>
        <label class="chk"><input type="checkbox" data-highon> High-cut <input type="number" data-highf min="${lim.high[0]}" max="${lim.high[1]}" step="50" inputmode="numeric"> Hz</label></div>` : ""}
      ${dir === "tx" && S.state.dyn ? `<div class="eqdyn">
        <div class="dynrow"><label class="chk"><input type="checkbox" data-lvon> Leveler</label>
          <label><small>Max (dB)</small><input type="number" data-lvmax step="1" inputmode="decimal"></label>
          <label><small>Target (dB)</small><input type="number" data-lvtarget step="1" inputmode="decimal"></label>
          <label><small>Decay (ms)</small><input type="number" data-lvdecay step="50" inputmode="numeric"></label>
          <span class="dim dynmeter" data-lvmeter></span></div>
        <div class="dynrow"><label class="chk"><input type="checkbox" data-lmon> Limiter</label>
          <label><small>Ceiling (dB)</small><input type="number" data-lmceil step="1" inputmode="decimal"></label>
          <label><small>Rel. (ms)</small><input type="number" data-lmrel step="10" inputmode="numeric"></label>
          <span class="dim dynmeter" data-lmmeter></span></div></div>` : ""}</div>`);
    root.append(b);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "eqsvg", role: "img", "aria-label": `${title} equalizer graph` });
    b.querySelector("[data-g]").append(svg);
    const st = { bands: [], sel: 0, dragging: false, edited: 0, nodes: [], curve: null, timer: 0, cut: { low: 0, high: 0 }, cutTimer: 0, cutDrag: false, cutMem: dir === "tx" ? { low: 150, high: 2800 } : { low: 200, high: 6000 } };
    const cutMarks = {};
    blocks[dir] = { b, svg, st };

    // static grid
    const grid = svgEl("g", { class: "eqgrid" });
    for (let g = -maxDb; g <= maxDb; g += 6) {
      grid.append(svgEl("line", { x1: X0, x2: X1, y1: yOf(g), y2: yOf(g), class: g === 0 ? "zero" : "" }));
      const t = svgEl("text", { x: X0 - 4, y: yOf(g) + 3, "text-anchor": "end" }); t.textContent = g > 0 ? `+${g}` : String(g); grid.append(t);
    }
    for (const [f, lbl] of GRID_HZ) {
      grid.append(svgEl("line", { x1: xOf(f), x2: xOf(f), y1: Y0, y2: Y1 }));
      const t = svgEl("text", { x: xOf(f), y: Y1 + 14, "text-anchor": "middle" }); t.textContent = lbl; grid.append(t);
    }
    svg.append(grid);
    st.curve = svgEl("path", { class: "eqcurve" });
    svg.append(st.curve);

    const pushNow = () => { clearTimeout(st.timer); st.timer = 0; st.edited = performance.now(); send("set_eq", { [dir]: st.bands }); };
    const pushSoon = () => { st.edited = performance.now(); clearTimeout(st.timer); st.timer = setTimeout(pushNow, 90); };
    const redraw = () => {
      const pts = [];
      for (let i = 0; i <= 200; i++) { const f = FMIN * Math.pow(FMAX / FMIN, i / 200); pts.push(`${i ? "L" : "M"}${xOf(f).toFixed(1)},${yOf(clamp(respDb(st.bands, f, maxDb) + cutDb(st.cut, f), -maxDb, maxDb)).toFixed(1)}`); }
      st.curve.setAttribute("d", pts.join(" "));
      st.nodes.forEach((n, i) => {
        const bd = st.bands[i];
        n.g.setAttribute("transform", `translate(${xOf(bd.f).toFixed(1)},${yOf(bd.g).toFixed(1)})`);
        n.g.classList.toggle("sel", i === st.sel);
      });
      for (const k of ["low", "high"]) {
        if (cutMarks[k]) { cutMarks[k].style.display = st.cut[k] ? "" : "none"; if (st.cut[k]) cutMarks[k].setAttribute("transform", `translate(${xOf(st.cut[k]).toFixed(1)},0)`); }
        const on = b.querySelector(`[data-${k}on]`), fi = b.querySelector(`[data-${k}f]`);
        if (on) { on.checked = !!st.cut[k]; if (document.activeElement !== fi) fi.value = st.cut[k] || st.cutMem[k]; }
      }
      const bd = st.bands[st.sel];
      if (bd) {
        b.querySelector("[data-selname]").textContent = `Band ${st.sel + 1}`;
        for (const [sel, v] of [["[data-f]", Math.round(bd.f)], ["[data-gain]", bd.g], ["[data-q]", bd.q]]) { const inp = b.querySelector(sel); if (document.activeElement !== inp) inp.value = v; }
      }
    };
    const buildNodes = () => {
      st.nodes.forEach((n) => n.g.remove());
      st.nodes = st.bands.map((_, i) => {
        const g = svgEl("g", { class: "eqnode", tabindex: "0", "aria-label": `Band ${i + 1}` });
        g.append(svgEl("circle", { r: RH, class: "hit" }), svgEl("circle", { r: RD, class: "dot" }));
        const t = svgEl("text", { y: compact ? 4 : 3.5, "text-anchor": "middle", class: compact ? "big" : "" }); t.textContent = String(i + 1); g.append(t);
        g.addEventListener("pointerdown", (e) => {
          st.sel = i; redraw();
          if (!canEdit) return;
          e.preventDefault(); st.dragging = true;
          try { g.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
        });
        g.addEventListener("pointermove", (e) => {
          if (!st.dragging || st.sel !== i) return;
          const m = svg.getScreenCTM().inverse(), p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m);
          st.bands[i] = { ...st.bands[i], f: Math.round(clamp(fOf(clamp(p.x, X0, X1)), FMIN, FMAX)), g: Math.round(clamp(gOf(p.y), -maxDb, maxDb) * 10) / 10 };
          redraw(); pushSoon();
        });
        const end = () => { if (st.dragging) { st.dragging = false; pushNow(); } };
        g.addEventListener("pointerup", end); g.addEventListener("pointercancel", end);
        g.addEventListener("dblclick", () => { if (!canEdit) return; st.bands[i] = { ...st.bands[i], g: 0 }; redraw(); pushNow(); });
        g.addEventListener("keydown", (e) => {                               // keyboard: arrows move the node, +/- change Q
          if (!canEdit) return;
          const bd = { ...st.bands[i] };
          if (e.key === "ArrowUp") bd.g = Math.min(maxDb, Math.round((bd.g + 0.5) * 10) / 10);
          else if (e.key === "ArrowDown") bd.g = Math.max(-maxDb, Math.round((bd.g - 0.5) * 10) / 10);
          else if (e.key === "ArrowRight") bd.f = Math.min(FMAX, Math.round(bd.f * 1.03));
          else if (e.key === "ArrowLeft") bd.f = Math.max(FMIN, Math.round(bd.f / 1.03));
          else if (e.key === "+" || e.key === "=") bd.q = Math.min(QMAX, Math.round(bd.q * 110) / 100);
          else if (e.key === "-") bd.q = Math.max(QMIN, Math.round(bd.q * 90) / 100);
          else return;
          e.preventDefault(); st.sel = i; st.bands[i] = bd; redraw(); pushSoon();
        });
        svg.append(g);
        return { g };
      });
    };
    svg.addEventListener("wheel", (e) => {                                   // the wheel sets the Q of the selected band
      if (!canEdit || !st.bands[st.sel]) return;
      e.preventDefault();
      const q = clamp(st.bands[st.sel].q * (e.deltaY < 0 ? 1.1 : 1 / 1.1), QMIN, QMAX);
      st.bands[st.sel] = { ...st.bands[st.sel], q: Math.round(q * 20) / 20 };
      redraw(); pushSoon();
    }, { passive: false });
    svg.style.touchAction = "none";
    st.setBands = (bands) => { const rebuild = bands.length !== st.bands.length; st.bands = bands.map((x) => ({ ...x })); if (rebuild) { st.sel = Math.min(st.sel, bands.length - 1); buildNodes(); } redraw(); };

    // low-cut / high-cut (receive and transmit): a draggable vertical marker for each, and a switch with its frequency
    {
      const pushCut = () => { clearTimeout(st.cutTimer); st.cutTimer = 0; st.edited = performance.now(); send("set_eq", { [`${dir}_cut`]: { low: st.cut.low, high: st.cut.high } }); };
      const pushCutSoon = () => { st.edited = performance.now(); clearTimeout(st.cutTimer); st.cutTimer = setTimeout(pushCut, 90); };
      const range = { low: lim.low, high: lim.high }, step = { low: 5, high: 50 };
      const setCut = (k, v) => {
        const [lo, hi] = range[k];
        let f = v ? clamp(Math.round(v / step[k]) * step[k], lo, hi) : 0;
        if (f && k === "low" && st.cut.high && f >= st.cut.high) f = Math.max(lo, st.cut.high - 50);
        if (f && k === "high" && st.cut.low && f <= st.cut.low) f = Math.min(hi, st.cut.low + 50);
        st.cut[k] = f; if (f) st.cutMem[k] = f;
      };
      for (const k of ["low", "high"]) {
        const m = svgEl("g", { class: "eqcutmark", style: "display:none" });
        const tag = svgEl("text", { y: Y0 + 12, "text-anchor": "middle" }); tag.textContent = k === "low" ? "L" : "H";
        m.append(svgEl("line", { y1: Y0, y2: Y1 }), svgEl("rect", { x: -11, y: Y0 - 2, width: 22, height: 18, rx: 5, class: "tab" }), tag);
        m.addEventListener("pointerdown", (e) => { if (!canEdit) return; e.preventDefault(); st.cutDrag = true; try { m.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ } });
        m.addEventListener("pointermove", (e) => {
          if (!st.cutDrag) return;
          const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse());
          setCut(k, fOf(clamp(p.x, X0, X1))); redraw(); pushCutSoon();
        });
        const end = () => { if (st.cutDrag) { st.cutDrag = false; pushCut(); } };
        m.addEventListener("pointerup", end); m.addEventListener("pointercancel", end);
        svg.append(m); cutMarks[k] = m;
        const on = b.querySelector(`[data-${k}on]`), fi = b.querySelector(`[data-${k}f]`);
        on.disabled = fi.disabled = !canEdit;
        on.onchange = () => { setCut(k, on.checked ? st.cutMem[k] : 0); redraw(); pushCut(); };
        fi.onchange = () => { const v = parseFloat(fi.value); if (Number.isFinite(v)) { setCut(k, v); redraw(); pushCut(); } };
      }
    }

    // transmit leveler and limiter: switches and numbers, shared and saved on the server like the equalizer
    if (dir === "tx" && S.state.dyn) {
      const dl = S.state.dyn.limits;
      const q = (s) => b.querySelector(s);
      const lim2 = { "[data-lvmax]": dl.max_gain, "[data-lvtarget]": dl.target, "[data-lvdecay]": dl.decay, "[data-lmceil]": dl.ceiling, "[data-lmrel]": dl.release };
      for (const [sel, [lo, hi]] of Object.entries(lim2)) { q(sel).min = lo; q(sel).max = hi; }
      const sendDyn = () => send("set_dyn", {
        leveler: { on: q("[data-lvon]").checked, max_gain: +q("[data-lvmax]").value, target: +q("[data-lvtarget]").value, decay: +q("[data-lvdecay]").value },
        limiter: { on: q("[data-lmon]").checked, ceiling: +q("[data-lmceil]").value, release: +q("[data-lmrel]").value },
      });
      for (const sel of ["[data-lvon]", "[data-lvmax]", "[data-lvtarget]", "[data-lvdecay]", "[data-lmon]", "[data-lmceil]", "[data-lmrel]"]) { q(sel).disabled = !canEdit; q(sel).onchange = sendDyn; }
      st.paintDyn = () => {
        const d = S.state.dyn;
        if (!d) return;
        const set = (sel, v) => { const e = q(sel); if (document.activeElement !== e) e.value = v; };
        q("[data-lvon]").checked = d.leveler.on; q("[data-lmon]").checked = d.limiter.on;
        set("[data-lvmax]", d.leveler.max_gain); set("[data-lvtarget]", d.leveler.target); set("[data-lvdecay]", d.leveler.decay);
        set("[data-lmceil]", d.limiter.ceiling); set("[data-lmrel]", d.limiter.release);
        const tx = !!S.state.tx;
        q("[data-lvmeter]").textContent = d.leveler.on && tx ? `+${(S.state.tx_lev_db || 0).toFixed(1)} dB` : "";
        q("[data-lmmeter]").textContent = d.limiter.on && tx ? `-${(S.state.tx_lim_db || 0).toFixed(1)} dB` : "";
      };
      st.paintDyn();
    }

    // numbers for the selected band
    const edit = (key, lo, hi, round) => (e) => {
      const v = parseFloat(e.target.value);
      if (!Number.isFinite(v)) return;
      st.bands[st.sel] = { ...st.bands[st.sel], [key]: round(clamp(v, lo, hi)) };
      redraw(); pushNow();
    };
    b.querySelector("[data-f]").onchange = edit("f", FMIN, FMAX, Math.round);
    b.querySelector("[data-gain]").onchange = edit("g", -maxDb, maxDb, (v) => Math.round(v * 10) / 10);
    b.querySelector("[data-q]").onchange = edit("q", QMIN, QMAX, (v) => Math.round(v * 20) / 20);
    for (const sel of ["[data-f]", "[data-gain]", "[data-q]"]) b.querySelector(sel).disabled = !canEdit;

    b.querySelector("[data-on]").onclick = () => send("set_eq", { [`${dir}_on`]: !S.state.eq?.[dir]?.on });
    const count = b.querySelector("[data-count]"), preset = b.querySelector("[data-preset]");
    for (const n of [b.querySelector("[data-on]"), count, preset, b.querySelector("[data-reset]")]) n.disabled = !canEdit;
    count.onchange = () => { st.sel = 0; send("set_eq", { [dir]: S.state.eq.layouts[count.value] }); };
    preset.onchange = () => {
      const pts = PRESETS[preset.value];
      preset.value = "";
      if (!pts) return;
      send("set_eq", { [dir]: st.bands.map((x) => ({ ...x, g: Math.round(clamp(curveAt(pts, x.f), -maxDb, maxDb) * 2) / 2 })) });
    };
    b.querySelector("[data-reset]").onclick = () => send("set_eq", { [dir]: st.bands.map((x) => ({ ...x, g: 0 })) });
    st.setBands(S.state.eq[dir].bands);
    if (S.state.eq[dir].cut) { st.cut = { ...S.state.eq[dir].cut }; redraw(); }
  }

  return {
    update() {
      const eq = S.state.eq;
      if (!eq) return;
      for (const [dir] of DIRS) {
        const { b, st } = blocks[dir];
        const cur = eq[dir];
        const on = b.querySelector("[data-on]");
        on.classList.toggle("on", !!cur.on);
        on.textContent = cur.on ? "On" : "Off";
        b.querySelector("[data-count]").value = String(cur.bands.length);
        st.paintDyn?.();
        if (st.dragging || st.cutDrag || performance.now() - st.edited < 700 || st.timer || st.cutTimer) continue;          // do not fight the hand that is moving a node
        if (cur.cut) st.cut = { ...cur.cut };
        st.setBands(cur.bands);
      }
    },
  };
}

// The two buttons on the Radio tab, after Memories: switch the receive and the transmit equalizer on and off (the settings are on the Audio tab).
export function createEqButtons(host, ctx) {
  const { S, send } = ctx;
  if (!S.audio?.available || !S.state.eq) return { update() {} };
  const mk = (dir, label) => {
    const b = el(`<button class="led eqbtn" title="${label} equalizer on or off. Its settings are on the Audio tab.">${dir.toUpperCase()} EQ</button>`);
    b.onclick = () => send("set_eq", { [`${dir}_on`]: !S.state.eq?.[dir]?.on });
    b.disabled = S.user.role === "viewer";
    host.append(b);
    return b;
  };
  const rx = mk("rx", "Receive"), tx = mk("tx", "Transmit");
  return {
    update() {
      const eq = S.state.eq;
      if (!eq) return;
      rx.classList.toggle("on", !!eq.rx.on);
      tx.classList.toggle("on", !!eq.tx.on);
    },
  };
}
