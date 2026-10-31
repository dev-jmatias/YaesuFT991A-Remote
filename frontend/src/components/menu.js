import { api } from "../api.js";
import { el } from "../util.js";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// The Menu tab (see docs/menu.md): the radio's own menu, every item the CAT manual lists. The table of items comes from the Pi (GET /api/menu); the values are read from the radio a group at a time
// (POST /api/menu/read); a change is sent as a command, written to the radio, read back and checked. Items that would cut the link or change how the radio is keyed are shown but read only.
export function createMenu(host, ctx) {
  const { S, send, toast, signal } = ctx;
  const viewer = S.user.role === "viewer", admin = S.user.role === "admin";
  let data = null;                                         // { model, groups, items }
  const byN = new Map();
  const first = new Map();                                 // the value of each item when this page first saw it: "changed" is measured from that
  let group = "", query = "", loading = false;
  const root = el(`<div class="menu">
    <div class="row menu-top">
      <label class="menu-q">Search <input id="mn-q" type="search" placeholder="name or number, e.g. beep or 026" autocomplete="off" aria-label="Search the menu"></label>
      <label>Group <select id="mn-g" aria-label="Menu group"></select></label>
      <button type="button" id="mn-all" title="Read every item from the radio (about 10 seconds)">Read all</button>
      <a id="mn-bk" class="btn" href="/api/menu/backup.json" download title="Read the whole menu and save it as a file">Back up menu</a>
      <button type="button" id="mn-pre" class="active" title="Set the radio's menu items the app needs (the audio-over-USB settings); you see what changes first">Set up for the app</button>
      <label class="btn" id="mn-rsw" title="Write a saved menu back to the radio (administrators)">Restore...<input type="file" id="mn-rs" accept="application/json,.json" hidden></label>
      <span class="dim" id="mn-st"></span>
    </div>
    <p class="ftx-notice" id="mn-note" hidden></p>
    <div class="menu-rows" id="mn-rows" role="list"></div>
    <div id="mn-rep" class="menu-rep" hidden></div>
    <div id="mn-pr" class="menu-rep" hidden></div>
  </div>`);
  host.append(root);
  const $ = (id) => root.querySelector("#" + id);
  const can = () => !viewer && S.lease.holder?.conn === S.conn;

  const show = (it) => {
    if (it.raw == null) return "";
    return it.label || it.raw;
  };
  function mine() {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.items.filter((it) => {
      if (q) return it.name.toLowerCase().includes(q) || (it.num || String(it.n).padStart(3, "0")).includes(q) || it.group.toLowerCase().includes(q);
      return !group || it.group === group;
    });
  }
  function control(it) {
    const dis = !can();
    if (it.locked || it.kind === "none" || it.kind === "raw") return el(`<span class="menu-val dim" title="${esc(it.locked || "")}">${esc(show(it)) || "-"} <small>read only</small></span>`);
    if (it.raw == null) return el(`<span class="dim">${loading && !it.error ? "reading..." : it.error ? "not read: " + esc(it.error) : "not read yet"}</span>`);
    if (it.kind === "enum") {
      const sel = el(`<select aria-label="${esc(it.name)}">${it.options.map((o) => `<option value="${o.v}">${esc(o.label)}</option>`).join("")}${it.ok ? "" : `<option value="">${esc(show(it))}</option>`}</select>`);
      sel.value = it.ok ? String(it.value) : "";
      sel.disabled = dis;
      sel.onchange = () => { if (sel.value !== "") set(it, +sel.value); };
      return sel;
    }
    const wrap = el(`<span class="menu-int"></span>`);
    const inp = el(`<input type="number" min="${it.lo}" max="${it.hi}" step="${it.step}" aria-label="${esc(it.name)}">`);
    inp.value = it.ok ? it.value : "";
    inp.disabled = dis;
    wrap.append(inp, el(`<small class="dim">${esc(it.ok ? show(it) : "? " + it.raw)}</small>`));
    if ((it.hi - it.lo) / it.step <= 200 && it.ok) {
      const r = el(`<input type="range" min="${it.lo}" max="${it.hi}" step="${it.step}" aria-label="${esc(it.name)} slider">`);
      r.value = it.value; r.disabled = dis;
      r.oninput = () => { inp.value = r.value; };
      r.onchange = () => { set(it, +r.value); };
      wrap.prepend(r);
    }
    inp.onchange = () => { const v = Math.round(+inp.value); if (Number.isFinite(v)) set(it, v); };
    return wrap;
  }
  function row(it) {
    const was = first.get(it.n);
    const changed = it.raw != null && was != null && was !== it.raw;
    const r = el(`<div class="menu-row${changed ? " changed" : ""}${it.locked ? " locked" : ""}" role="listitem" data-n="${it.n}">
      <span class="menu-n">${esc(it.num || String(it.n).padStart(3, "0"))}</span>
      <span class="menu-name" title="${esc(it.doc || "")}">${esc(it.name)}${it.caution ? `<small class="menu-caut">${esc(it.caution)}</small>` : ""}${it.locked ? `<small class="menu-caut">${esc(it.locked)}</small>` : ""}</span>
      <span class="menu-ctl"></span>
      <span class="menu-flag"></span>
    </div>`);
    r.querySelector(".menu-ctl").append(control(it));
    if (changed) {
      const u = el(`<button type="button" class="ftx-clear" title="Put back the value this page first read (${esc(was)})">Undo</button>`);
      u.disabled = !can();
      u.onclick = () => { const d = data && byN.get(it.n); if (d) { const dec = decodeLocal(d, was); if (dec != null) set(d, dec); } };
      r.querySelector(".menu-flag").append(el(`<span class="chip">changed</span>`), u);
    }
    return r;
  }
  // the raw text -> the value to send (the Pi checked it when it was first read)
  function decodeLocal(it, raw) {
    if (it.kind === "enum") { const v = parseInt(raw, 10); return it.options.some((o) => o.v === v) ? v : null; }
    if (it.kind === "int") { const v = parseInt(raw, 10); return Number.isFinite(v) ? v : null; }
    return null;
  }
  function paint() {
    if (!data) return;
    const list = mine();
    $("mn-rows").replaceChildren(...(list.length ? list.map(row) : [el(`<div class="dim menu-empty">No menu item matches.</div>`)]));
    const unread = data.items.filter((i) => i.raw == null && i.kind !== "none").length;
    $("mn-st").textContent = `${list.length} item${list.length === 1 ? "" : "s"} shown${unread ? `, ${unread} of ${data.items.length} not read yet` : ", all read"}`;
    $("mn-rsw").hidden = !admin;
    $("mn-bk").classList.toggle("disabled", false);
    const n = $("mn-note");
    n.hidden = can() || viewer;
    n.textContent = "You are only looking: press Take control to change menu items.";
  }
  function absorb(items) {
    for (const it of items) {
      if (it.error) { const d = byN.get(it.n); if (d) d.error = it.error; continue; }
      const d = byN.get(it.n);
      if (!d) continue;
      Object.assign(d, it);
      if (!first.has(it.n) && it.raw != null) first.set(it.n, it.raw);
    }
  }
  async function readItems(items) {
    const nums = items.filter((i) => i.raw == null && i.kind !== "none" && !i.error).map((i) => i.n);
    for (let i = 0; i < nums.length; i += 40) {
      $("mn-st").textContent = `Reading from the radio... ${Math.min(i + 40, nums.length)} of ${nums.length}`;
      try { absorb((await api("/api/menu/read", "POST", { numbers: nums.slice(i, i + 40) })).items); }
      catch (e) { toast("Menu: " + e.message); break; }
      paint();
    }
    paint();
  }
  async function readShown() {
    if (loading || !data) return;
    loading = true;
    try { await readItems(mine()); } finally { loading = false; }              // everything shown (the whole menu is about 200 items, 20 seconds), not only the first 80
  }
  async function set(it, value) {
    try {
      const item = await send("menu_set", { n: it.n, value });
      if (item) { absorb([item]); }
    } catch (e) { toast(e.message); }
    paint();
  }

  // ------------------------------------------------------------------ buttons
  $("mn-q").oninput = () => { query = $("mn-q").value; paint(); clearTimeout(timer); timer = setTimeout(() => { readShown(); }, 400); };
  let timer = 0;
  $("mn-g").onchange = () => { group = $("mn-g").value; paint(); readShown(); };
  $("mn-all").onclick = async () => {
    if (loading || !data) return;
    loading = true;
    try { await readItems(data.items); } finally { loading = false; }
  };
  // ---- "Set up for the app": what the radio needs so that the app's audio works (server: menu.py APP_PRESET). Shown first, applied by an administrator, checked by reading back, and undoable.
  async function preset() {
    const box = $("mn-pr");
    box.hidden = false;
    box.textContent = "Reading the radio...";
    let plan;
    try { plan = await api("/api/menu/preset"); } catch (e) { box.textContent = "Could not read the radio: " + e.message; return; }
    const rows = plan.items.map((r) => `<li><b>${esc(r.num)} ${esc(r.name)}</b>: ${r.error ? `<span class="bad">${esc(r.error)}</span>` : r.change ? `${esc(r.now_label)} &rarr; <b>${esc(r.want_label)}</b>` : `${esc(r.now_label)} (already right)`} <small class="dim">${esc(r.why)}</small></li>`).join("");
    box.innerHTML = `<b>Set up for the app</b>` + (plan.items.length
      ? `<p class="dim">${plan.pending ? `${plan.pending} item${plan.pending === 1 ? "" : "s"} to change. Nothing else is touched; each value is read back and checked.` : "The radio already has everything the app needs."}</p><ul>${rows}</ul>`
      : `<p class="dim">This radio needs no menu change for the app: the microphone button chooses the USB audio source by itself.</p>`)
      + `<div class="row">${plan.pending && admin ? `<button type="button" class="active" data-go>Apply</button>` : ""}${plan.undo && admin ? `<button type="button" data-undo>Undo the last preset</button>` : ""}<button type="button" data-close>Close</button></div>`
      + (plan.pending && !admin ? `<p class="dim">An administrator applies it.</p>` : "");
    box.querySelector("[data-close]").onclick = () => { box.hidden = true; };
    const run = async (body, what) => {
      box.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      try {
        const res = await api("/api/menu/preset", "POST", body);
        toast(res.changed.length ? `${what}: ${res.changed.length} item${res.changed.length === 1 ? "" : "s"}` : "Nothing needed changing");
        if (res.failed?.length) toast(res.failed.map((f) => `${f.name}: ${f.why}`).join("; "));
      } catch (e) { toast(e.message); }
      preset();
    };
    box.querySelector("[data-go]")?.addEventListener("click", () => { if (confirm("Change these radio menu items now?")) run({ confirm: true }, "Set"); });
    box.querySelector("[data-undo]")?.addEventListener("click", () => { if (confirm("Put back what the last preset found?")) run({ confirm: true, undo: true }, "Put back"); });
  }
  $("mn-pre").onclick = () => { if (!S.state.connected) { toast("The radio is not connected."); return; } preset(); };
  $("mn-bk").onclick = (e) => {
    if (!S.state.connected) { e.preventDefault(); toast("The radio is not connected."); return; }
    toast("Reading the whole menu: the file downloads when it is ready (about 10 seconds).");
  };
  $("mn-rs").onchange = async () => {
    const f = $("mn-rs").files[0];
    $("mn-rs").value = "";
    if (!f) return;
    let j;
    try { j = JSON.parse(await f.text()); } catch { toast("That file is not a menu backup."); return; }
    const n = Object.keys(j.values || {}).length;
    if (!n || !confirm(`Write this backup (${n} items, taken ${j.taken || "at an unknown time"}) to the radio? Only items that differ are written; the items that cannot be changed here are skipped.`)) return;
    $("mn-st").textContent = "Writing to the radio...";
    try {
      const rep = await api("/api/menu/restore", "POST", j);
      const rp = $("mn-rep");
      rp.hidden = false;
      rp.innerHTML = `<b>Restore finished:</b> ${rep.changed.length} changed, ${rep.same} already the same, ${rep.skipped.length} skipped, ${rep.errors.length} errors.` +
        (rep.changed.length ? `<ul>${rep.changed.map((c) => `<li>${String(c.n).padStart(3, "0")} ${esc(c.name)}: ${esc(c.from)} to ${esc(c.to)}</li>`).join("")}</ul>` : "") +
        (rep.errors.length ? `<p class="bad">${rep.errors.map((c) => `${String(c.n).padStart(3, "0")} ${esc(c.name)}: ${esc(c.why)}`).join("<br>")}</p>` : "");
      for (const it of data.items) { it.raw = null; }
      await load(true);
    } catch (e) { toast("Restore: " + e.message); }
  };

  // ------------------------------------------------------------------ events and loading
  ctx.menuEvent = (m) => {
    if (m.k === "item" && data) { absorb([m.item]); paint(); }
  };
  async function load(keepGroup) {
    try {
      data = await api("/api/menu");
      byN.clear();
      for (const it of data.items) { byN.set(it.n, it); if (it.raw != null && !first.has(it.n)) first.set(it.n, it.raw); }
      const secs = new Map();                                       // "Section: Group" -> one optgroup per section (the other radios' menus have sections)
      for (const g of data.groups) { const [sec, ...rest] = g.split(": "); const k = rest.length ? sec : ""; if (!secs.has(k)) secs.set(k, []); secs.get(k).push([g, rest.length ? rest.join(": ") : g]); }
      $("mn-g").innerHTML = `<option value="">All groups</option>` + [...secs].map(([sec, gs]) => {
        const opts = gs.map(([g, label]) => `<option value="${esc(g)}">${esc(label)}</option>`).join("");
        return sec ? `<optgroup label="${esc(sec)}">${opts}</optgroup>` : opts;
      }).join("");
      if (!keepGroup || !group && !query) group = group || data.groups[0];
      $("mn-g").value = group;
      paint();
      readShown();
    } catch (e) { toast("Menu: " + e.message); }
  }
  signal?.addEventListener("abort", () => { ctx.menuEvent = null; clearTimeout(timer); });
  let wasVisible = false;
  return {
    update() {
      const visible = ctx.ui.tab === "menu";
      if (visible && !wasVisible) load(true);                  // every time the tab is opened the table and the values read so far are fetched again
      if (visible && data) $("mn-note").hidden = can() || viewer;
      wasVisible = visible;
    },
  };
}
