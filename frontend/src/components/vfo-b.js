import { el } from "../util.js";

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
  const dual = !!f.dual_receiver;                      // MAIN / SUB receivers: the copy / swap / split buttons mean something else here, so they are not offered
  const root = el(`<div class="vfob">
    <div class="vfob-read"><span class="dim">${dual ? "SUB" : "VFO B"}</span> <b data-fb>-</b> <small class="dim">MHz</small>
      <span class="vfob-band dim" data-bb></span> <span class="vbadge split" data-splitpill hidden>SPLIT: TX on B</span></div>
    <div class="row vfob-ctl${dual ? " dualctl" : ""}">
      ${dual ? `<button class="led" data-swapms title="Swap the frequencies of the MAIN and SUB receivers (the modes stay as they are)">MAIN &#8596; SUB</button><span class="rxtx" role="group" aria-label="Receive and transmit receivers"><span class="rxtxg" role="group" aria-label="MAIN receiver"><button class="led sm selrx" data-sel="main" aria-pressed="false" title="Operate the MAIN receiver: the radio's dial and keys act on it">MAIN</button><button class="led sm" data-rx="main" aria-pressed="false" title="Listen to the MAIN receiver (on / off)">RX</button><button class="led sm txsel" data-tx="main" aria-pressed="false" title="Transmit on the MAIN receiver's frequency">TX</button></span><span class="rxtxg" role="group" aria-label="SUB receiver"><button class="led sm selrx" data-sel="sub" aria-pressed="false" title="Operate the SUB receiver: the radio's dial and keys act on it">SUB</button><button class="led sm" data-rx="sub" aria-pressed="false" title="Listen to the SUB receiver (on / off)">RX</button><button class="led sm txsel" data-tx="sub" aria-pressed="false" title="Transmit on the SUB receiver's frequency">TX</button></span></span>` : `<button class="led" data-op="a_to_b" title="Copy the A frequency to B">A &rarr; B</button>
      <button class="led" data-op="b_to_a" title="Copy the B frequency to A">B &rarr; A</button>
      <button class="led" data-op="swap" title="Swap VFO A and B">A &#8596; B</button>`}
      ${f.split ? `<button class="led" data-split title="Split: receive on A, transmit on B">Split</button>` : ""}
      ${f.quick_split ? `<button class="led" data-op="quick_split" title="Radio's quick split: sets B from A using the offset in radio menu 035 and turns split on">Quick split</button>` : ""}
      ${dual ? "" : `<form class="row bform" data-form><input inputmode="decimal" placeholder="B MHz" size="7" aria-label="Set the VFO B frequency in MHz"><button>Set B</button></form>`}
    </div></div>`);
  host.append(root);
  root.querySelectorAll("[data-op]").forEach((b) => (b.onclick = () => ctx.send("vfo", { op: b.dataset.op })));
  // MAIN / SUB groups (FTDX101): RX = which receivers are listening (at least one stays on), TX = which one transmits; listening to one and transmitting on the other is split
  const rxBtns = [...root.querySelectorAll("[data-rx]")], txBtns = [...root.querySelectorAll("[data-tx]")];
  rxBtns.forEach((b) => (b.onclick = () => {
    const s = ctx.S.state, main = s.rx_main !== false, sub = !!s.rx_sub;
    const next = b.dataset.rx === "main" ? { main: !main, sub } : { main, sub: !sub };
    if (!next.main && !next.sub) return;
    ctx.send("receivers", next);
  }));
  txBtns.forEach((b) => (b.onclick = () => ctx.send("tx_receiver", { receiver: b.dataset.tx })));
  const selBtns = [...root.querySelectorAll("[data-sel]")];
  selBtns.forEach((b) => (b.onclick = () => ctx.send("active_receiver", { receiver: b.dataset.sel })));
  const swapMs = root.querySelector("[data-swapms]");
  if (swapMs) swapMs.onclick = () => {
    const s = ctx.S.state;
    if (!s.frequency || !s.frequency_b) return;
    ctx.send("set_frequency", { hz: s.frequency_b });
    ctx.send("set_frequency", { hz: s.frequency, vfo: "B" });
  };
  const splitBtn = root.querySelector("[data-split]");
  if (splitBtn) splitBtn.onclick = () => ctx.send("split", { on: !ctx.S.state.split });
  const bForm = root.querySelector("[data-form]");
  if (bForm) bForm.onsubmit = (e) => {
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
      if (dual) {
        const on = { main: s.rx_main !== false, sub: !!s.rx_sub }, tx = s.tx_receiver === "sub" ? "sub" : "main";
        rxBtns.forEach((b) => { b.classList.toggle("on", on[b.dataset.rx]); b.setAttribute("aria-pressed", String(on[b.dataset.rx])); });
        const act = s.active_receiver === "sub" ? "sub" : "main";
        selBtns.forEach((b) => { b.classList.toggle("on", b.dataset.sel === act); b.setAttribute("aria-pressed", String(b.dataset.sel === act)); });
        txBtns.forEach((b) => { b.classList.toggle("on", b.dataset.tx === tx); b.setAttribute("aria-pressed", String(b.dataset.tx === tx)); });
      }
      if (splitBtn) splitBtn.classList.toggle("on", !!s.split);
    },
  };
}
