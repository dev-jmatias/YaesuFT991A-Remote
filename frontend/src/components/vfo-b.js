import { el } from "../util.js";
import { openMemories } from "./memories.js";

const fmt = (hz) => {
  const s = String(Math.max(0, Math.round(hz || 0))).padStart(9, "0");
  return `${s.slice(0, 3).replace(/^0+(?=\d)/, "")}.${s.slice(3, 6)}.${s.slice(6)}`;
};

// VFO tools: copy / swap / split / set B.
// The FT-991A has no "select VFO" CAT command: the documented ones are swap (SV), A to B (AB), B to A (BA), set/read B (FB),
// quick split (QS) and the transmit-VFO selector (FT: FT3 = transmit on B = SPLIT, FT2 = transmit on A; verified on hardware).
// On phones the second VFO panel is hidden, so a compact "VFO B" line is shown here instead (CSS decides which).
export function createVfoB(host, ctx) {
  const f = ctx.S.caps.features;
  const root = el(`<div class="vfob">
    <div class="vfob-read"><span class="dim">VFO B</span> <b data-fb>-</b> <small class="dim">MHz</small>
      <span class="vfob-band dim" data-bb></span> <span class="vbadge split" data-splitpill hidden>SPLIT: TX on B</span></div>
    <div class="row vfob-ctl">
      <button class="led" data-op="a_to_b" title="Copy the A frequency to B">A &rarr; B</button>
      <button class="led" data-op="b_to_a" title="Copy the B frequency to A">B &rarr; A</button>
      <button class="led" data-op="swap" title="Swap VFO A and B">A &#8596; B</button>
      ${f.split ? `<button class="led" data-split title="Split: receive on A, transmit on B">Split</button>` : ""}
      ${f.memories ? `<button class="led" data-mem title="The memory channels stored in the radio: look at them and recall one">Memories</button><button class="led" data-tovfo hidden title="Leave memory mode and go back to the VFO">Back to VFO</button>` : ""}
      ${f.quick_split ? `<button class="led" data-op="quick_split" title="Radio's quick split: sets B from A using the offset in radio menu 035 and turns split on">Quick split</button>` : ""}
      <form class="row bform" data-form><input inputmode="decimal" placeholder="B MHz" size="7" aria-label="Set the VFO B frequency in MHz"><button>Set B</button></form>
    </div></div>`);
  host.append(root);
  root.querySelectorAll("[data-op]").forEach((b) => (b.onclick = () => ctx.send("vfo", { op: b.dataset.op })));
  const memBtn = root.querySelector("[data-mem]"), toVfo = root.querySelector("[data-tovfo]");
  if (memBtn) { memBtn.onclick = () => openMemories(ctx); toVfo.onclick = () => ctx.send("memory_vfo"); }
  const splitBtn = root.querySelector("[data-split]");
  if (splitBtn) splitBtn.onclick = () => ctx.send("split", { on: !ctx.S.state.split });
  root.querySelector("[data-form]").onsubmit = (e) => {
    e.preventDefault();
    const inp = e.target.querySelector("input"), v = parseFloat(inp.value.replace(",", "."));
    if (v > 0) ctx.send("set_frequency", { hz: Math.round(v * 1e6), vfo: "B" });
    inp.value = "";
  };
  const fb = root.querySelector("[data-fb]"), bb = root.querySelector("[data-bb]"), pill = root.querySelector("[data-splitpill]");
  return {
    update() {
      const s = ctx.S.state;
      fb.textContent = s.frequency_b ? fmt(s.frequency_b) : "-";
      bb.textContent = s.band_b ? `(${s.band_b})` : "";
      pill.hidden = !s.split;
      if (splitBtn) splitBtn.classList.toggle("on", !!s.split);
      if (memBtn) { const inMem = s.vfo_memory === "memory"; memBtn.classList.toggle("on", inMem); toVfo.hidden = !inMem; }
    },
  };
}
