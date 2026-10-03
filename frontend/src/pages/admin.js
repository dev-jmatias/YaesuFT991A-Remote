import { api } from "../api.js";
import { el } from "../util.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "");
const ROLES = ["viewer", "operator", "admin"];

// Full-screen sheet: Account (everyone) + Users / Clients / Config / Diagnostics / Audit (admins).
export function openSheet(root, { user, tab, sock }) {
  const admin = user.role === "admin";
  const tabs = [...(admin ? [["users", "Users"], ["clients", "Clients"], ["config", "Config"], ["diag", "Diagnostics"], ["audit", "Audit"]] : []), ["account", "Account"]];
  const sheet = el(`<div class="sheet" role="dialog" aria-label="Settings"><div class="sheetbox">
      <div class="sheethead"><strong>${admin ? "Administration" : "Account"}</strong><button id="x" aria-label="Close">Close</button></div>
      <div class="sheettabs"></div><div class="sheetbody"></div></div></div>`);
  root.append(sheet);
  const body = sheet.querySelector(".sheetbody"), bar = sheet.querySelector(".sheettabs");
  let timer = 0, current = tab;
  const close = () => { clearInterval(timer); sheet.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", onKey);
  sheet.querySelector("#x").onclick = close;
  // NOTE: an inline-property handler that returns false cancels the click's default action (it once blocked every form submit
  // and checkbox inside the sheet). Use a statement body so nothing is returned.
  sheet.addEventListener("click", (e) => { if (e.target === sheet) close(); });

  const note = (msg, bad = false) => { const n = body.querySelector(".note"); if (n) { n.textContent = msg; n.className = "note " + (bad ? "bad" : "ok"); n.scrollIntoView({ block: "nearest", behavior: "smooth" }); } };
  const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { note(e.message, true); } };

  const views = {
    async users() {
      const { users } = await api("/api/users");
      body.innerHTML = `<div class="note"></div>
        <table><thead><tr><th>User</th><th>Role</th><th title="Takes control immediately, without asking the current holder">Trusted</th><th>Created</th><th></th></tr></thead><tbody>
        ${users.map((u) => `<tr data-id="${u.id}"><td>${esc(u.username)}</td>
          <td><select data-role>${ROLES.map((r) => `<option ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
          <td><input type="checkbox" data-trusted ${u.trusted ? "checked" : ""} ${u.role === "viewer" ? "disabled" : ""} aria-label="Trusted: takes control without asking"></td>
          <td class="dim">${when(u.created)}</td>
          <td class="row"><button data-reset>Reset password</button><button class="danger" data-del>Delete</button></td></tr>`).join("")}
        </tbody></table>
        <h3 class="mt14">Add user</h3>
        <form id="add" class="row"><input name="username" placeholder="Username" required autocomplete="off">
          <input name="password" type="password" placeholder="Password (min 10)" required autocomplete="new-password">
          <select name="role">${ROLES.map((r) => `<option ${r === "operator" ? "selected" : ""}>${r}</option>`).join("")}</select>
          <label class="chk"><input type="checkbox" name="trusted"> Trusted</label><button class="active">Add</button></form>
        <p class="dim">Roles: <b>viewer</b> watches and listens; <b>operator</b> can take control and transmit; <b>admin</b> also manages users and settings.
        <b>Trusted</b> users take control immediately instead of asking the current holder (never while a transmission is in progress); everyone else is asked and, if the holder does not answer, gets control after the timeout set in Config. Changing a role or password signs that user out.</p>`;
      body.querySelectorAll("tr[data-id]").forEach((tr) => {
        const id = tr.dataset.id;
        tr.querySelector("[data-role]").onchange = guard(async (e) => { await api(`/api/users/${id}`, "PATCH", { role: e.target.value }); note("Role updated; the user was signed out."); });
        tr.querySelector("[data-trusted]").onchange = guard(async (e) => {
          await api(`/api/users/${id}`, "PATCH", { trusted: e.target.checked });
          note(e.target.checked ? "Trusted: this user now takes control immediately." : "No longer trusted: this user must ask the current holder.");
        });
        tr.querySelector("[data-reset]").onclick = guard(async () => {
          const pw = prompt("New password for this user (min 10 characters):");
          if (pw) { await api(`/api/users/${id}`, "PATCH", { password: pw }); note("Password reset; the user was signed out."); }
        });
        tr.querySelector("[data-del]").onclick = guard(async () => { if (confirm("Delete this user?")) { await api(`/api/users/${id}`, "DELETE"); views.users(); } });
      });
      body.querySelector("#add").onsubmit = guard(async (e) => {
        e.preventDefault();
        const f = e.target;
        await api("/api/users", "POST", { username: f.username.value, password: f.password.value, role: f.role.value, trusted: f.trusted.checked });
        views.users();
      });
    },

    async clients() {
      const { clients, lease } = await api("/api/clients");
      body.innerHTML = `<div class="note"></div><p>Control: <b>${lease.holder ? esc(lease.holder.user) : "nobody"}</b>${lease.pending ? ` (request from ${esc(lease.pending.user)})` : ""}</p>
        <table><thead><tr><th>User</th><th>Role</th><th>Address</th><th>Since</th><th>Status</th><th></th></tr></thead><tbody>
        ${clients.map((c) => `<tr><td>${esc(c.user)}</td><td>${c.role}</td><td class="dim">${esc(c.ip)}</td><td class="dim">${when(c.since)}</td>
          <td>${c.holder ? "<b>control</b> " : ""}${c.ptt ? '<b class="txt">TX</b>' : ""}</td>
          <td><button class="danger" data-kick="${c.conn}">Disconnect</button></td></tr>`).join("")}</tbody></table>`;
      body.querySelectorAll("[data-kick]").forEach((b) => (b.onclick = guard(async () => { await api(`/api/clients/${b.dataset.kick}/kick`, "POST"); views.clients(); })));
    },

    async config() {
      const info = await api("/api/config");
      const c = info.config, ed = info.editable;
      const opt = (list, v) => list.map((x) => `<option ${String(x) === String(v) ? "selected" : ""}>${x}</option>`).join("");
      body.innerHTML = `<div class="note"></div>
        ${info.writable ? "" : '<p class="note bad">No config file in use: start the service with --config to enable editing.</p>'}
        <form id="cfg" class="cfg">
          <fieldset><legend>Radio</legend>
            <label>Model<select name="radio.model">${opt(info.models, c.radio.model)}</select></label>
            <label>Serial port<input name="radio.serial_port" list="ports" value="${esc(c.radio.serial_port)}"><datalist id="ports"><option value="auto">${info.serial_ports.map((p) => `<option value="${esc(p.device)}">${esc(p.description)}</option>`).join("")}</datalist></label>
            <label>CAT baud<select name="radio.baud">${opt([4800, 9600, 19200, 38400, 115200], c.radio.baud)}</select></label>
            <label>Hamlib model<input name="radio.hamlib_model" type="number" min="0" value="${c.radio.hamlib_model}"></label></fieldset>
          <fieldset><legend>Audio</legend>
            <label class="chk"><input name="audio.enabled" type="checkbox" ${c.audio.enabled ? "checked" : ""}> Enabled</label>
            <label>Backend<select name="audio.backend">${opt(["auto", "alsa", "test"], c.audio.backend)}</select></label>
            <label>Radio -> Pi (capture): ALSA name, empty = auto<input name="audio.input_device" value="${esc(c.audio.input_device)}" placeholder="auto, e.g. plughw:CARD=CODEC,DEV=0"></label>
            <label>Pi -> radio (playback): ALSA name, empty = auto<input name="audio.output_device" value="${esc(c.audio.output_device)}" placeholder="auto, e.g. plughw:CARD=CODEC,DEV=0"></label>
            <label>RX gain dB<input name="audio.rx_gain_db" type="number" step="0.5" min="-30" max="30" value="${c.audio.rx_gain_db}"></label>
            <label>TX gain dB<input name="audio.tx_gain_db" type="number" step="0.5" min="-30" max="30" value="${c.audio.tx_gain_db}"></label>
            <label>Opus bitrate<input name="audio.opus_bitrate" type="number" min="6000" max="128000" step="1000" value="${c.audio.opus_bitrate}"></label>
            <label>Max listeners<input name="audio.max_peers" type="number" min="1" max="8" value="${c.audio.max_peers}"></label></fieldset>
          <fieldset><legend>Operation</legend>
            <label>TX timeout (s)<input name="safety.tx_timeout_s" type="number" min="5" max="600" value="${c.safety.tx_timeout_s}"></label>
            <label>Control request timeout (s)<input name="safety.control_request_timeout_s" type="number" min="3" max="120" value="${c.safety.control_request_timeout_s}"></label>
            <label>PTT heartbeat timeout (s)<input name="safety.ptt_heartbeat_timeout_s" type="number" step="0.1" min="0.3" max="5" value="${c.safety.ptt_heartbeat_timeout_s}"></label>
            <label>Tuning steps (Hz, comma separated)<input name="ui.tuning_steps_hz" value="${c.ui.tuning_steps_hz.join(", ")}"></label>
            <label>Log level<select name="logging.level">${opt(["DEBUG", "INFO", "WARNING", "ERROR"], c.logging.level)}</select></label></fieldset>
          <fieldset><legend>Meters (calibration, provisional)</legend>
            <label>ALC full-scale (raw 10-255)<input name="ui.meter_alc_full" type="number" min="10" max="255" value="${c.ui.meter_alc_full}" title="Raw ALC value at which the radio's own ALC meter is full"></label>
            <label>COMP full-scale (raw 10-255)<input name="ui.meter_comp_full" type="number" min="10" max="255" value="${c.ui.meter_comp_full}" title="Raw compression value at which the radio's own meter is full"></label></fieldset>
          <button class="active" ${info.writable ? "" : "disabled"}>Save</button>
        </form>
        <p class="dim"><b>Not editable here, on purpose:</b> ${info.locked.map(esc).join(", ")}.
        PTT can only be enabled by editing <code>safety.allow_ptt</code> in the config file on the Pi.</p>
        <div id="restart"></div>`;
      body.querySelector("#cfg").onsubmit = guard(async (e) => {
        e.preventDefault();
        const out = {};
        for (const inp of e.target.elements) {
          if (!inp.name) continue;
          const [s, k] = inp.name.split(".");
          let v = inp.type === "checkbox" ? inp.checked : inp.type === "number" ? Number(inp.value) : inp.value;
          if (k === "baud" || k === "tx_timeout_s") v = Number(v);
          if (k === "tuning_steps_hz") v = String(inp.value).split(",").map((x) => parseInt(x, 10)).filter((x) => x > 0);
          if (JSON.stringify(c[s][k]) !== JSON.stringify(v)) (out[s] ??= {})[k] = v;
        }
        if (!Object.keys(out).length) return note("Nothing changed.");
        const r = await api("/api/config", "PUT", out);
        for (const [s, ks] of Object.entries(out)) Object.assign(c[s], ks);
        note(r.restart_required ? "Saved. Restart the service to apply." : "Saved and applied.");
        if (r.restart_required) {
          const b = el(`<button class="danger">Restart service now</button>`);
          b.onclick = guard(async () => { if (confirm("Restart now? The radio connection and audio drop for a few seconds.")) { await api("/api/admin/restart", "POST", { confirm: true }); note("Restarting…"); } });
          body.querySelector("#restart").replaceChildren(b);
        }
      });
    },

    async diag() {
      const d = await api("/api/diagnostics");
      const kv = (o) => Object.entries(o).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object").map(([k, v]) => `<div><span class="dim">${esc(k)}</span> ${esc(v)}</div>`).join("");
      const sysd = d.system, mem = sysd.memory;
      body.innerHTML = `<div class="diag">
        <div class="card2"><h3>App</h3>${kv(d.app)}${kv(d.versions)}</div>
        <div class="card2"><h3>Host</h3>${kv(sysd)}${mem ? `<div><span class="dim">memory</span> ${mem.used_pct}% of ${mem.total_mb} MB</div>` : ""}</div>
        <div class="card2"><h3>Radio / CAT</h3>${kv(d.radio)}${d.radio.port ? kv(d.radio.port) : ""}${d.radio.stats ? kv(d.radio.stats) : ""}</div>
        <div class="card2"><h3>PTT</h3>${kv(d.ptt)}</div>
        <div class="card2"><h3>Audio</h3>${d.audio ? kv(d.audio) : "n/a"}</div>
        <div class="card2"><h3>Clients (${d.clients.length})</h3>${d.clients.map((c) => `<div>${esc(c.user)} <span class="dim">${esc(c.ip)}</span> ${c.holder ? "<b>control</b>" : ""}</div>`).join("")}</div></div>
        <h3 class="mt14">Recent log</h3><pre class="log">${esc(d.log.join("\n"))}</pre>`;
    },

    async audit() {
      const { events } = await api("/api/audit?limit=300");
      body.innerHTML = `<table><thead><tr><th>Time</th><th>Event</th><th>User</th><th>Address</th><th>Detail</th></tr></thead><tbody>
        ${events.map((e) => `<tr><td class="dim">${when(e.ts)}</td><td>${esc(e.event)}</td><td>${esc(e.user)}</td><td class="dim">${esc(e.ip)}</td><td>${esc(e.detail)}</td></tr>`).join("")}</tbody></table>`;
    },

    async account() {
      body.innerHTML = `<div class="note"></div><p>Signed in as <b>${esc(user.name)}</b> (${user.role}).</p>
        <form id="pw" class="cfg"><fieldset><legend>Change password</legend>
          <label>Current password<input name="current" type="password" autocomplete="current-password" required></label>
          <label>New password (min 10)<input name="new" type="password" autocomplete="new-password" required></label></fieldset>
          <button class="active">Change password</button></form>
        <p class="dim">Your other signed-in devices are signed out when you change it.</p>`;
      body.querySelector("#pw").onsubmit = guard(async (e) => {
        e.preventDefault();
        await api("/api/me/password", "POST", { current: e.target.current.value, new: e.target.new.value });
        e.target.reset(); note("Password changed.");
      });
    },
  };

  async function show(name) {
    current = name; clearInterval(timer);
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.t === name));
    try { await views[name](); } catch (e) { body.innerHTML = `<div class="note bad">${esc(e.message)}</div>`; }
    if (name === "diag" || name === "clients") timer = setInterval(() => current === name && views[name]().catch(() => {}), 3000);
  }
  bar.replaceChildren(...tabs.map(([k, label]) => { const b = el(`<button data-t="${k}">${label}</button>`); b.onclick = () => show(k); return b; }));
  show(tab);
}
