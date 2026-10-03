import { el } from "../util.js";

// Everything here is generated from the radio's capability snapshot: a control the radio profile does not
// mark as supported never exists in the DOM.

// TUNE: the radio transmits a carrier while the antenna tuner matches. Server-side it is guarded like PTT. Lives next to the tuning step.
export function createTuneButton(host, ctx) {
  if (!ctx.S.caps.features.tuner_tune) return { update() {} };
  const tune = el(`<button class="led tune" title="Start the antenna tuner. The radio transmits a carrier for a few seconds.">Tune</button>`);
  tune.onclick = () => {
    if (ctx.S.state.tuning) { ctx.send("tune_stop"); return; }
    if (ctx.ui.pttLock) return;                                    // PTT lock is on: TUNE also transmits, so it is blocked too
    if (confirm("TUNE makes the radio TRANSMIT a carrier for a few seconds.\n\nIs an antenna or dummy load connected?")) ctx.send("tune");
  };
  host.append(tune);
  return {
    update() {
      const s = ctx.S.state;
      const locked = !!ctx.ui.pttLock && !s.tuning;                  // a running tune can always be stopped
      tune.classList.toggle("tuning", !!s.tuning);
      tune.disabled = locked;
      tune.title = locked ? "Locked: switch off the PTT lock to use TUNE (it transmits a carrier)" : "Start the antenna tuner. The radio transmits a carrier for a few seconds.";
      tune.textContent = s.tuning ? "Tuning... (tap to stop)" : "Tune";
    },
  };
}

// C4FM RX/TX DG-ID cannot be set over CAT (bench-verified: no EX menu item changes when it is changed on the radio),
// so only a hint is shown, in C4FM mode. The backend "dgid" control (menu 153, WIRES DG-ID) stays but is not offered here.
export function createDgHint(host, ctx) {
  if (!ctx.S.caps.controls.some((c) => c.name === "dgid")) return { update() {} };
  const dg = el(`<span class="dghint" hidden title="Not available over CAT">DG-ID: set on the radio (hold GM)</span>`);
  host.append(dg);
  return { update() { dg.hidden = ctx.S.state.mode !== "C4FM"; } };
}
// Main level sliders (RF / MIC / TX power ...): one row each, label | slider | value, aligned in a column.
export function createLevels(host, ctx) {
  // AF gain is the radio's own speaker volume: irrelevant when listening through the browser, so it is not offered here.
  const levels = Object.entries(ctx.S.caps.levels).filter(([k]) => k !== "af_gain");
  const root = el(`<div class="levels"></div>`);
  host.append(root);
  const rows = levels.map(([k, r]) => {
    const w = el(`<div class="arow"><span class="alabel">${r.label}</span><input type="range" min="${r.min}" max="${r.max}" aria-label="${r.label}"><span class="aval" data-v></span></div>`);
    const inp = w.querySelector("input"), v = w.querySelector("[data-v]");
    inp.oninput = () => (v.textContent = inp.value);
    inp.onchange = () => ctx.send("set_level", { name: k, value: +inp.value });
    root.append(w);
    return { k, inp, v };
  });
  return {
    update() {
      for (const { k, inp, v } of rows) {
        const val = ctx.S.state[k];
        if (val !== undefined && document.activeElement !== inp) { inp.value = val; v.textContent = val; }
      }
    },
  };
}

// Full filter / DSP panel, grouped in tabs. Every control is one row with the same three columns: [button or label | bar or select | value].
export function createFilters(host, ctx) {
  const controls = ctx.S.caps.controls.filter((x) => !x.quick);      // quick ones (DG-ID) live next to the mode row
  if (!controls.length) { host.append(el(`<div class="dim">This radio exposes no adjustable filter controls.</div>`)); return { update() {} }; }
  const root = el(`<div class="filters tabbed"></div>`);
  host.append(root);
  const groups = [...new Set(controls.map((x) => x.group))];
  const tabs = el(`<div class="subtabs" role="tablist" aria-label="Filter and DSP groups"></div>`);
  root.append(tabs);
  // an on/off button sits on the same row as its adjustment bar
  const PAIR = { nr: "nr_level", nb: "nb_level", notch: "notch_freq", contour: "contour_freq", processor: "processor_level", monitor: "monitor_level" };
  const fmt = (s, v) => (s.unit && s.name !== "clarifier_hz" ? `${v} ${s.unit}` : s.name === "clarifier_hz" ? `${v > 0 ? "+" : ""}${v} Hz` : `${v}`);

  function toggle(s) {
    const b = el(`<button class="tog led" data-c="${s.name}">${s.label}</button>`);
    b.onclick = () => ctx.send("set_control", { name: s.name, value: !ctx.S.state[s.name] });
    return b;
  }
  // the slider is "display: contents": its input and value take the 2nd and 3rd column of the row; the label only shows when it stands alone
  function slider(s, withLabel) {
    const w = el(`<div class="slider" data-c="${s.name}">${withLabel ? `<span class="alabel">${s.label}</span>` : ""}<input type="range" min="${s.min}" max="${s.max}" step="${s.step}" aria-label="${s.label}"><span class="aval" data-v></span></div>`);
    const inp = w.querySelector("input"), v = w.querySelector("[data-v]");
    if (s.default !== undefined) { inp.value = s.default; v.textContent = fmt(s, s.default); }       // shown until the radio reports its own value
    inp.oninput = () => (v.textContent = fmt(s, +inp.value));
    inp.onchange = () => ctx.send("set_control", { name: s.name, value: +inp.value });
    if (s.name === "clarifier_hz") { inp.title = "Double-click to clear the offset"; inp.ondblclick = () => { inp.value = 0; v.textContent = fmt(s, 0); ctx.send("set_control", { name: s.name, value: 0 }); }; }
    return w;
  }
  function select(s) {
    const w = el(`<label class="sel" data-c="${s.name}"><span class="alabel">${s.label}</span><select></select></label>`);
    const sel = w.querySelector("select");
    if (s.kind === "enum") sel.innerHTML = s.choices.map((x) => `<option>${x}</option>`).join("");
    sel.onchange = () => ctx.send("set_control", { name: s.name, value: s.kind === "width" ? +sel.value : sel.value });
    return w;
  }

  const sections = {};
  for (const group of groups) {
    const sec = el(`<div class="ctlgroup" role="tabpanel"><h3>${group}</h3><div class="ctls"></div></div>`);
    sections[group] = sec;
    const box = sec.querySelector(".ctls");
    const inGroup = controls.filter((x) => x.group === group), used = new Set(), by = (n) => inGroup.find((x) => x.name === n);
    const row = (...kids) => { const r = el(`<div class="crow"></div>`); r.append(...kids); box.append(r); return r; };
    for (const s of inGroup) {
      if (used.has(s.name)) continue;
      used.add(s.name);
      if (s.name === "rit" && by("clarifier_hz")) {              // RIT and XIT share the one clarifier offset: both buttons, one bar
        const two = el(`<div class="twobtn"></div>`);
        two.append(toggle(s));
        if (by("xit")) { used.add("xit"); two.append(toggle(by("xit"))); }
        used.add("clarifier_hz");
        row(two, slider(by("clarifier_hz"), false));
      } else if (PAIR[s.name] && by(PAIR[s.name])) {
        used.add(PAIR[s.name]);
        row(toggle(s), slider(by(PAIR[s.name]), false));
      } else if (s.kind === "bool") row(toggle(s));
      else if (s.kind === "int") row(slider(s, true));
      else row(select(s));
    }
    root.append(sec);
  }
  // One group at a time, as tabs. The last tab used is remembered (per browser).
  const KEY = "rr.filterTab";
  let active = "";
  try { active = localStorage.getItem(KEY) || ""; } catch { /* storage unavailable */ }
  if (!groups.includes(active)) active = groups[0];
  const show = (g) => {
    active = g;
    try { localStorage.setItem(KEY, g); } catch { /* storage unavailable */ }
    for (const name of groups) sections[name].hidden = name !== g;
    tabs.querySelectorAll("button").forEach((b) => { const on = b.dataset.g === g; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); });
  };
  for (const g of groups) {
    const b = el(`<button role="tab" data-g="${g}">${g}</button>`);
    b.onclick = () => show(g);
    tabs.append(b);
  }
  show(active);
  return {
    update() {
      const st = ctx.S.state;
      for (const w of root.querySelectorAll("[data-c]")) {
        const n = w.dataset.c, val = st[n], spec = controls.find((x) => x.name === n);
        if (w.tagName === "BUTTON") w.classList.toggle("on", !!val);
        else if (spec.kind === "int") {
          const inp = w.querySelector("input");
          if (document.activeElement !== inp && val !== undefined) { inp.value = val; w.querySelector("[data-v]").textContent = fmt(spec, val); }
        } else {
          const sel = w.querySelector("select");
          if (spec.kind === "width") {
            const opts = st.width_options || [];
            (w.closest(".crow") || w).hidden = !opts.length;
            // Rebuilding the options on every meter update closes an open dropdown (it flickered and could not be used on phones):
            // only touch the list when it really changed, and never while the user has it open.
            const sig = JSON.stringify([opts, val]);
            if (document.activeElement !== sel && w.dataset.sig !== sig) {
              w.dataset.sig = sig;
              sel.innerHTML = opts.map((o) => `<option value="${o.hz}">${o.hz} Hz</option>`).join("");
              if (val) { if (![...sel.options].some((o) => +o.value === val)) sel.insertAdjacentHTML("afterbegin", `<option value="${val}">${val} Hz</option>`); sel.value = val; }
            }
          } else if (val !== undefined && document.activeElement !== sel) sel.value = val;
        }
      }
    },
  };
}