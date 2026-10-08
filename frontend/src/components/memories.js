import { api } from "../api.js";
import { el } from "../util.js";

const fmt = (hz) => {
  const s = String(Math.round(hz || 0)).padStart(9, "0");
  return `${s.slice(0, 3).replace(/^0+(?=\d)/, "")}.${s.slice(3, 6)}.${s.slice(6)}`;
};

// What a channel can store besides frequency, mode and name (FT-991A): the shift direction, the tone MODE and the tone frequency (CTCSS) or DCS code. The repeater offset
// (e.g. 600 kHz) is a radio menu setting that CAT cannot store per channel, so it is not offered here. The tables are the ones of the radio's CAT manual (the server checks them too).
const CTCSS = [67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1];
const DCS = ["023", "025", "026", "031", "032", "036", "043", "047", "051", "053", "054", "065", "071", "072", "073", "074", "114", "115", "116", "122", "125", "131", "132", "134", "143", "145", "152", "155", "156", "162", "165", "172", "174", "205", "212", "223", "225", "226", "243", "244", "245", "246", "251", "252", "255", "261", "263", "265", "266", "271", "274", "306", "311", "315", "325", "331", "332", "343", "346", "351", "356", "364", "365", "371", "411", "412", "413", "423", "431", "432", "445", "446", "452", "454", "455", "462", "464", "465", "466", "503", "506", "516", "523", "526", "532", "546", "565", "606", "612", "624", "627", "631", "632", "654", "662", "664", "703", "712", "723", "731", "732", "734", "743", "754"];
const MODES = ["LSB", "USB", "CW-U", "CW-L", "AM", "FM", "RTTY-L", "RTTY-U", "DATA-L", "DATA-U", "DATA-FM", "FM-N", "AM-N", "C4FM"];
const SHIFTS = { simplex: "Simplex (no shift)", plus: "Plus shift (+)", minus: "Minus shift (-)" };
const SHIFT_MARK = { simplex: "", plus: "+", minus: "-" };
const TONES = { off: "Tone off", ctcss_enc: "CTCSS encode (TX tone)", ctcss_encdec: "CTCSS encode + decode (tone squelch)", dcs_enc: "DCS encode", dcs_encdec: "DCS encode + decode" };
const TONE_MARK = { off: "", ctcss_enc: "T", ctcss_encdec: "TSQ", dcs_enc: "DCS", dcs_encdec: "DCS" };
const csvCell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;

// The radio's stored memory channels: a list to look at and tap, recalled with the MC command. Administrators of a radio that supports it (FT-991A) can also add and edit a channel
// (MT write: frequency, mode, name, shift, tone mode); the server proves every write by reading the whole list back. "Export" saves the list as a CSV file first.
export function openMemories(ctx) {
  const canEdit = !!ctx.S.caps.features.memory_edit && ctx.S.user?.role === "admin";
  const sheet = el(`<div class="sheet memsheet" role="dialog" aria-label="Memory channels"><div class="sheetbox">
    <div class="sheethead"><h2>Memory channels</h2><button class="icon wide" data-x>Close</button></div>
    <div data-view="list">
      <div class="memtools">
        <input type="search" data-q placeholder="Filter: name, frequency, mode" aria-label="Filter the memory channels">
        <button data-refresh title="Read the channels from the radio again (after you changed them on the radio)">Re-read</button>
        <button data-vfo title="Leave memory mode and go back to the VFO">Back to VFO</button>
        <button data-export title="Save the list as a CSV file (a backup, or to open in a spreadsheet)">Export</button>
        ${canEdit ? `<button data-add class="active" title="Store a new channel in the radio">Add</button>` : ""}
      </div>
      <div class="memnote dim" data-note></div>
      <div class="memlist" data-list></div>
    </div>
    <form data-view="edit" class="memedit" hidden>
      <h3 data-etitle></h3>
      <div class="memfields">
        <label>Channel<input data-f="channel" type="number" min="1" max="99" inputmode="numeric" required></label>
        <label>Name (up to 12 letters)<input data-f="name" maxlength="12" autocapitalize="characters" autocomplete="off" spellcheck="false"></label>
        <label>Frequency (MHz)<input data-f="freq" inputmode="decimal" placeholder="e.g. 145.600" required></label>
        <label>Mode<select data-f="mode">${MODES.map((m) => `<option>${m}</option>`).join("")}</select></label>
        <label>Shift<select data-f="shift">${Object.entries(SHIFTS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label>Tone<select data-f="tone">${Object.entries(TONES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label data-tonebox hidden>Tone frequency<select data-f="tonehz">${CTCSS.map((t) => `<option value="${t}">${t.toFixed(1)} Hz</option>`).join("")}</select></label>
        <label data-dcsbox hidden>DCS code<select data-f="dcs">${DCS.map((c) => `<option value="${c}">${c}</option>`).join("")}</select></label>
      </div>
      <p class="dim memhint">The repeater offset (for example 600 kHz) is set in the radio's menus; here you choose the direction. The name may use letters, digits and
        ordinary punctuation. <b>Saving overwrites the channel stored in the radio</b> and takes about 30 seconds: the radio's front is borrowed for a moment to store the tone, and the program
        reads all channels back to check the result. Use <b>Export</b> first if you want a backup.</p>
      <div class="memnote" data-enote></div>
      <div class="row"><button type="submit" class="active" data-save>Save to the radio…</button><button type="button" data-cancel>Cancel</button>
        <button type="button" class="danger" data-delete title="Empty this channel in the radio" hidden>Delete channel…</button></div>
    </form>
  </div></div>`);
  document.body.append(sheet);
  const q = (s) => sheet.querySelector(s);
  const f = (n) => sheet.querySelector(`[data-f="${n}"]`);
  let items = [], busy = false;

  const close = () => { sheet.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape" && !busy) { if (!q('[data-view="edit"]').hidden) showList(); else close(); } };
  document.addEventListener("keydown", onKey);
  sheet.addEventListener("click", (e) => { if (e.target === sheet && !busy) close(); });
  q("[data-x]").onclick = () => { if (!busy) close(); };

  const syncTone = () => {
    const t = f("tone").value;
    q("[data-tonebox]").hidden = !t.startsWith("ctcss");
    q("[data-dcsbox]").hidden = !t.startsWith("dcs");
  };
  f("tone").onchange = syncTone;

  const tagOf = (m) => `${SHIFT_MARK[m.shift || "simplex"]}${TONE_MARK[m.tone_mode || "off"] ? (SHIFT_MARK[m.shift || "simplex"] ? " " : "") + TONE_MARK[m.tone_mode || "off"] : ""}`;

  function paint() {
    const s = ctx.S.state, filter = q("[data-q]").value.trim().toLowerCase();
    const inMem = s.vfo_memory === "memory";
    q("[data-vfo]").disabled = !inMem;
    q("[data-export]").disabled = !items.length;
    const list = q("[data-list]");
    list.replaceChildren();
    const shown = items.filter((m) => !filter || `${m.channel} ${m.tag} ${fmt(m.frequency)} ${m.mode} ${m.band || ""}`.toLowerCase().includes(filter));
    for (const m of shown) {
      const cur = inMem && s.memory_channel === m.channel;
      const b = el(`<button class="memrow${cur ? " cur" : ""}" data-ch="${m.channel}"><span class="mch">${String(m.channel).padStart(3, "0")}</span>`
        + `<span class="mtag"></span><span class="mfq">${fmt(m.frequency)}</span><span class="mmode">${m.mode} <small class="dim">${tagOf(m)}</small></span></button>`);
      b.querySelector(".mtag").textContent = m.tag || "-";               // the tag is text from the radio: never HTML
      b.title = `${SHIFTS[m.shift || "simplex"]}, ${TONES[m.tone_mode || "off"]}`;
      b.onclick = () => { ctx.send("memory_select", { channel: m.channel }); close(); };
      if (canEdit) {
        const e = el(`<button class="memedit-btn" aria-label="Edit channel ${m.channel}" title="Edit this channel">Edit</button>`);
        e.onclick = () => showEdit(m);
        const wrap = el(`<div class="memitem"></div>`);
        wrap.append(b, e);
        list.append(wrap);
      } else list.append(b);
    }
    q("[data-note]").textContent = items.length ? `${shown.length} of ${items.length} stored channels. Tap one to recall it.` : "";
  }

  async function load(refresh) {
    q("[data-note]").textContent = "Reading the radio's memory channels (a few seconds)...";
    q("[data-list]").replaceChildren();
    q("[data-refresh]").disabled = true;
    try {
      items = (await api("/api/memories" + (refresh ? "?refresh=1" : ""))).channels || [];
      if (!items.length) q("[data-note]").textContent = "The radio has no stored memory channels (001-099).";
      paint();
    } catch (e) {
      q("[data-note]").textContent = "Could not read the memories: " + e.message;
    } finally {
      q("[data-refresh]").disabled = false;
    }
  }

  // ---- export: a CSV the user keeps (also what to put back by hand if a write ever went wrong)
  function exportCsv() {
    const head = ["channel", "name", "frequency_hz", "frequency_mhz", "mode", "shift", "tone_mode"];
    const rows = items.map((m) => [m.channel, m.tag, m.frequency, (m.frequency / 1e6).toFixed(6), m.mode, m.shift || "simplex", m.tone_mode || "off"]);
    const csv = [head, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = el(`<a download="radio-memories-${new Date().toISOString().slice(0, 10)}.csv" hidden></a>`);
    a.href = url;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  // ---- add / edit
  const showList = () => { q('[data-view="edit"]').hidden = true; q('[data-view="list"]').hidden = false; };
  function showEdit(m) {
    const isNew = !m;
    const used = new Set(items.map((x) => x.channel));
    let free = 1; while (used.has(free) && free < 99) free++;
    q("[data-etitle]").textContent = isNew ? "Add a memory channel" : `Edit channel ${String(m.channel).padStart(3, "0")}`;
    f("channel").value = isNew ? free : m.channel; f("channel").readOnly = !isNew;
    f("name").value = isNew ? "" : m.tag || "";
    f("freq").value = isNew ? (ctx.S.state.frequency ? (ctx.S.state.frequency / 1e6).toFixed(6).replace(/0+$/, "").replace(/\.$/, "") : "") : (m.frequency / 1e6).toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
    f("mode").value = isNew ? (MODES.includes(ctx.S.state.mode) ? ctx.S.state.mode : "FM") : (MODES.includes(m.mode) ? m.mode : "FM");
    f("shift").value = isNew ? "simplex" : m.shift || "simplex";
    f("tone").value = isNew ? "off" : m.tone_mode || "off";
    f("tonehz").value = "88.5"; f("dcs").value = "023";
    syncTone();
    q("[data-delete]").hidden = isNew;
    q("[data-delete]").dataset.ch = isNew ? "" : String(m.channel);
    q("[data-save]").disabled = false;
    q("[data-enote]").textContent = isNew ? "A new channel starts from the radio's current frequency and mode." : "";
    q('[data-view="list"]').hidden = true; q('[data-view="edit"]').hidden = false;
    f(isNew ? "channel" : "name").focus();
    if (!isNew && m.tone_mode && m.tone_mode !== "off") loadTone(m.channel);       // the channel's own tone frequency: without it a save would reset it
  }

  // The tone frequency of a stored channel can only be read by recalling it, so the server does that for a moment and puts the radio back.
  async function loadTone(ch) {
    const note = q("[data-enote]"), save = q("[data-save]");
    save.disabled = true;
    note.textContent = "Reading the channel's tone from the radio…";
    try {
      const t = await api(`/api/memories/${ch}/tone`);
      if (t.tone_hz != null) f("tonehz").value = String(t.tone_hz);
      if (t.dcs_code != null) f("dcs").value = t.dcs_code;
      note.textContent = "";
      save.disabled = false;
    } catch (e) {
      note.textContent = "Could not read this channel's tone, so it cannot be edited safely now: " + e.message;
    }
  }

  q('[data-view="edit"]').onsubmit = async (ev) => {
    ev.preventDefault();
    if (busy) return;
    const ch = parseInt(f("channel").value, 10), mhz = parseFloat(f("freq").value.replace(",", "."));
    const name = f("name").value.trim();
    const note = q("[data-enote]");
    if (!(ch >= 1 && ch <= 99)) { note.textContent = "The channel must be 1 to 99."; return; }
    if (!(mhz >= 0.03 && mhz <= 470)) { note.textContent = "The frequency must be between 0.03 and 470 MHz."; return; }
    if (!/^[\x20-\x7e]*$/.test(name) || name.includes(";")) { note.textContent = "The name may only use ordinary letters, digits and punctuation (no ;)."; return; }
    const hz = Math.round(mhz * 1e6);
    const old = items.find((x) => x.channel === ch);
    const what = old ? `Channel ${ch} now holds "${old.tag || "-"}" ${fmt(old.frequency)} ${old.mode}. It will be OVERWRITTEN in the radio with "${name || "-"}" ${fmt(hz)} ${f("mode").value}.`
      : `Channel ${ch} is empty. It will be written in the radio as "${name || "-"}" ${fmt(hz)} ${f("mode").value}.`;
    if (!confirm(`${what}\n\nContinue?`)) return;
    busy = true;
    for (const b of sheet.querySelectorAll("button")) b.disabled = true;
    note.textContent = "Writing to the radio and checking every channel... about 30 seconds. Please wait.";
    try {
      const tone = f("tone").value;
      await api(`/api/memories/${ch}`, "POST", { frequency: hz, mode: f("mode").value, shift: f("shift").value, tone_mode: tone, name, confirm: true,
        ...(tone.startsWith("ctcss") ? { tone_hz: parseFloat(f("tonehz").value) } : {}), ...(tone.startsWith("dcs") ? { dcs_code: f("dcs").value } : {}) });
      ctx.toast?.(`Channel ${ch} stored and checked`);
      busy = false;
      for (const b of sheet.querySelectorAll("button")) b.disabled = false;
      showList();
      await load(false);                              // the server already re-read the list: its cache is current
    } catch (e) {
      busy = false;
      for (const b of sheet.querySelectorAll("button")) b.disabled = false;
      note.textContent = "Not stored: " + e.message;
    }
  };
  q("[data-cancel]").onclick = () => { if (!busy) showList(); };
  q("[data-delete]").onclick = async () => {
    const ch = parseInt(q("[data-delete]").dataset.ch, 10), old = items.find((x) => x.channel === ch);
    if (busy || !ch) return;
    if (!confirm(`Channel ${ch}${old ? ` ("${old.tag || "-"}" ${fmt(old.frequency)} ${old.mode})` : ""} will be EMPTIED in the radio. This cannot be undone (use Export first for a backup).\n\nContinue?`)) return;
    busy = true;
    for (const b of sheet.querySelectorAll("button")) b.disabled = true;
    q("[data-enote]").textContent = "Deleting the channel and checking every channel... about 20 seconds. Please wait.";
    try {
      await api(`/api/memories/${ch}/delete`, "POST", { confirm: true });
      ctx.toast?.(`Channel ${ch} deleted`);
      busy = false;
      for (const b of sheet.querySelectorAll("button")) b.disabled = false;
      showList();
      await load(false);
    } catch (e) {
      busy = false;
      for (const b of sheet.querySelectorAll("button")) b.disabled = false;
      q("[data-enote]").textContent = "Not deleted: " + e.message;
    }
  };

  q("[data-q]").oninput = paint;
  q("[data-refresh]").onclick = () => load(true);
  q("[data-vfo]").onclick = () => { ctx.send("memory_vfo"); close(); };
  q("[data-export]").onclick = exportCsv;
  if (canEdit) q("[data-add]").onclick = () => showEdit(null);
  load(false);
  return { close };
}


// Memories / Back to VFO buttons, placed beside TUNE in the tuning step row (they used to sit in the VFO tools row, which ran off small screens).
export function createMemoryButtons(host, ctx) {
  if (!ctx.S.caps.features.memories) return { update() {} };
  const mem = el(`<button class="led" data-mem title="The memory channels stored in the radio: look at them and recall one">Memories</button>`);
  const back = el(`<button class="led" data-tovfo hidden title="Leave memory mode and go back to the VFO">Back to VFO</button>`);
  mem.onclick = () => openMemories(ctx);
  back.onclick = () => ctx.send("memory_vfo");
  host.append(mem, back);
  return {
    update() {
      const inMem = ctx.S.state.vfo_memory === "memory";
      mem.classList.toggle("on", inMem);
      back.hidden = !inMem;
    },
  };
}
