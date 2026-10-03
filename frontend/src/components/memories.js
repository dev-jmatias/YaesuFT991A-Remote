import { api } from "../api.js";
import { el } from "../util.js";

const fmt = (hz) => {
  const s = String(Math.round(hz || 0)).padStart(9, "0");
  return `${s.slice(0, 3).replace(/^0+(?=\d)/, "")}.${s.slice(3, 6)}.${s.slice(6)}`;
};

// The radio's stored memory channels: a list to look at and tap. Read from the radio (it takes a few seconds the first time) and
// recalled with the MC command. This program never creates, edits or deletes a memory.
export function openMemories(ctx) {
  const sheet = el(`<div class="sheet memsheet" role="dialog" aria-label="Memory channels"><div class="sheetbox">
    <div class="sheethead"><h2>Memory channels</h2><button class="icon wide" data-x>Close</button></div>
    <div class="memtools">
      <input type="search" data-q placeholder="Filter: name, frequency, mode" aria-label="Filter the memory channels">
      <button data-refresh title="Read the channels from the radio again (after you changed them on the radio)">Re-read</button>
      <button data-vfo title="Leave memory mode and go back to the VFO">Back to VFO</button>
    </div>
    <div class="memnote dim" data-note></div>
    <div class="memlist" data-list></div>
  </div></div>`);
  document.body.append(sheet);
  const q = (s) => sheet.querySelector(s);
  let items = [];

  const close = () => { sheet.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  sheet.addEventListener("click", (e) => { if (e.target === sheet) close(); });
  q("[data-x]").onclick = close;

  function paint() {
    const s = ctx.S.state, filter = q("[data-q]").value.trim().toLowerCase();
    const inMem = s.vfo_memory === "memory";
    q("[data-vfo]").disabled = !inMem;
    const list = q("[data-list]");
    list.replaceChildren();
    const shown = items.filter((m) => !filter || `${m.channel} ${m.tag} ${fmt(m.frequency)} ${m.mode} ${m.band || ""}`.toLowerCase().includes(filter));
    for (const m of shown) {
      const cur = inMem && s.memory_channel === m.channel;
      const b = el(`<button class="memrow${cur ? " cur" : ""}" data-ch="${m.channel}"><span class="mch">${String(m.channel).padStart(3, "0")}</span>`
        + `<span class="mtag"></span><span class="mfq">${fmt(m.frequency)}</span><span class="mmode">${m.mode}</span></button>`);
      b.querySelector(".mtag").textContent = m.tag || "-";               // the tag is text from the radio: never HTML
      b.onclick = () => { ctx.send("memory_select", { channel: m.channel }); close(); };
      list.append(b);
    }
    q("[data-note]").textContent = items.length ? `${shown.length} of ${items.length} stored channels. Tap one to recall it.`
      : "";
  }

  async function load(refresh) {
    q("[data-note]").textContent = "Reading the radio's memory channels (a few seconds)...";
    q("[data-list]").replaceChildren();
    q("[data-refresh]").disabled = true;
    try {
      items = (await api("/api/memories" + (refresh ? "?refresh=1" : ""))).channels || [];
      if (!items.length) q("[data-note]").textContent = "The radio has no stored memory channels (001-099).";
      else paint();
    } catch (e) {
      q("[data-note]").textContent = "Could not read the memories: " + e.message;
    } finally {
      q("[data-refresh]").disabled = false;
    }
  }

  q("[data-q]").oninput = paint;
  q("[data-refresh]").onclick = () => load(true);
  q("[data-vfo]").onclick = () => { ctx.send("memory_vfo"); close(); };
  load(false);
  return { close };
}
