import { digitsOf, el } from "../util.js";

// Big LCD-style frequency readout.
// VFO A: click/tap a digit to make its place value the tuning step; the mouse wheel over a digit tunes by that place.
// VFO B: read-only display (VFO B is changed with the Set B box and the A/B tools).
export function createFreqDisplay(host, ctx, { vfo = "A" } = {}) {
  const readOnly = vfo === "B";
  const root = el(`<div class="freq ${readOnly ? "ro" : ""}" role="group" aria-label="VFO ${vfo} frequency"></div>`);
  host.append(root);
  const spans = [];
  for (let i = 0; i < 9; i++) {
    const place = 10 ** (8 - i);
    const d = el(`<span class="digit" data-place="${place}">0</span>`);
    if (!readOnly) {
      d.onclick = () => ctx.setStep(place);
      d.onwheel = (e) => { e.preventDefault(); ctx.tune((e.deltaY < 0 ? 1 : -1) * place); };
    }
    spans.push(d);
    root.append(d);
    if (i === 2 || i === 5) root.append(el(`<span class="sep">.</span>`));
  }
  root.append(el(`<small class="unit">MHz</small>`));

  return {
    update() {
      const hz = readOnly ? ctx.S.state.frequency_b || 0 : ctx.freq();
      const ds = digitsOf(hz);
      const lead = ds.findIndex((x) => x !== 0);
      const first = Math.min(lead < 0 ? 8 : lead, 2);                 // always show the MHz digit
      const stepPlace = 10 ** Math.floor(Math.log10(ctx.ui.step));
      spans.forEach((s, i) => {
        s.textContent = ds[i];
        s.classList.toggle("lead", i < first);
        if (!readOnly) s.classList.toggle("cur", Number(s.dataset.place) === stepPlace);
      });
      if (!readOnly) root.classList.toggle("pending", ctx.tuner.target !== null);
      root.setAttribute("aria-label", `VFO ${vfo} ${hz} hertz`);
    },
  };
}
