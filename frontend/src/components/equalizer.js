import { el } from "../util.js";

// 8-band equalizer for the audio you hear (RX) and the audio you send (TX). The settings live on the server and are shared by everyone connected (the radio has one setting);
// the Audio tab has the sliders, the Radio tab has two on/off buttons (RX EQ, TX EQ) after Memories. FreeDV modem tones are never equalized: only speech.
const PRESETS = {
  Flat: [0, 0, 0, 0, 0, 0, 0, 0],
  Voice: [-12, -8, -3, 0, 3, 5, 3, -3],
  "Bass cut": [-12, -9, -5, -2, 0, 0, 0, 0],
  "Treble cut": [0, 0, 0, 0, 0, -3, -7, -12],
  "Bass boost": [8, 6, 3, 0, 0, 0, 0, 0],
  "Treble boost": [0, 0, 0, 0, 0, 3, 6, 9],
};
const fmtHz = (hz) => (hz >= 1000 ? `${hz / 1000}k` : String(hz));
const fmtDb = (v) => (v > 0 ? `+${v}` : String(v));
const DIRS = [["rx", "Receive (what you hear)"], ["tx", "Transmit (what you send)"]];

export function createEqualizer(host, ctx) {
  const { S, send } = ctx;
  if (!S.audio?.available || !S.state.eq) return { update() {} };
  const bands = S.state.eq.bands, maxDb = S.state.eq.max_db || 12;
  const canEdit = S.user.role !== "viewer";
  const root = el(`<section class="eqcard" aria-label="Equalizer"><h3 class="blabel">Equalizer</h3>
    <p class="dim eqnote">Shared by everyone connected. <b>Receive</b> shapes the speech you hear (the decoded speech when FreeDV is on); <b>Transmit</b> shapes your voice before it is sent.
      FreeDV's modem tones are never equalized. The equalizer adds about 10 ms of delay. The RX EQ and TX EQ buttons on the Radio tab switch each one on and off.</p></section>`);
  host.append(root);
  const blocks = {};
  for (const [dir, title] of DIRS) {
    const b = el(`<div class="eqblock" data-dir="${dir}">
      <div class="eqhead"><b>${title}</b>
        <button type="button" class="led" data-on>Off</button>
        <select data-preset aria-label="Preset"><option value="">Preset…</option>${Object.keys(PRESETS).map((p) => `<option>${p}</option>`).join("")}</select>
        <button type="button" data-reset>Reset</button></div>
      <div class="eqbands">${bands.map((hz, i) => `<div class="eqband"><span class="eqval" data-v="${i}">0</span>
        <div class="eqslide"><input type="range" min="-${maxDb}" max="${maxDb}" step="0.5" value="0" data-b="${i}" aria-label="${dir === "rx" ? "Receive" : "Transmit"} ${fmtHz(hz)} Hz"></div>
        <span class="eqhz">${fmtHz(hz)}</span></div>`).join("")}</div></div>`);
    root.append(b);
    const sliders = [...b.querySelectorAll("input[data-b]")];
    const gains = () => sliders.map((s) => +s.value);
    let timer = 0, edited = 0;
    const push = () => { clearTimeout(timer); timer = setTimeout(() => send("set_eq", { [dir]: gains() }), 120); };
    sliders.forEach((s, i) => {
      s.oninput = () => { b.querySelector(`[data-v="${i}"]`).textContent = fmtDb(+s.value); edited = performance.now(); push(); };
      s.disabled = !canEdit;
    });
    b.querySelector("[data-on]").onclick = () => send("set_eq", { [`${dir}_on`]: !S.state.eq?.[dir]?.on });
    b.querySelector("[data-on]").disabled = !canEdit;
    const preset = b.querySelector("[data-preset]");
    preset.disabled = !canEdit;
    preset.onchange = () => {
      const g = PRESETS[preset.value];
      preset.value = "";
      if (!g) return;
      send("set_eq", { [dir]: g });
    };
    b.querySelector("[data-reset]").onclick = () => send("set_eq", { [dir]: PRESETS.Flat });
    b.querySelector("[data-reset]").disabled = !canEdit;
    blocks[dir] = { b, sliders, edited: () => edited };
  }
  return {
    update() {
      const eq = S.state.eq;
      if (!eq) return;
      for (const [dir] of DIRS) {
        const { b, sliders, edited } = blocks[dir];
        const st = eq[dir];
        const on = b.querySelector("[data-on]");
        on.classList.toggle("on", !!st.on);
        on.textContent = st.on ? "On" : "Off";
        if (performance.now() - edited() < 600 || sliders.includes(document.activeElement)) continue;          // do not fight the hand that is moving a slider
        sliders.forEach((s, i) => { s.value = st.gains[i]; b.querySelector(`[data-v="${i}"]`).textContent = fmtDb(st.gains[i]); });
        b.classList.toggle("flat", !st.gains.some((g) => g));
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
