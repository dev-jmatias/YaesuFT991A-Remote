import { api } from "../api.js";
import { el } from "../util.js";
import { LIGHTS, getLights, setLight } from "../prefs.js";

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
      const upd = await api("/api/admin/update").catch(() => null);
      let ustat = await api("/api/admin/update/status").catch(() => null);
      const rig = await api("/api/admin/rigctl").catch(() => null);
      let fdv = await api("/api/freedv").catch(() => null);
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
            <label>COMP full-scale (raw 10-255)<input name="ui.meter_comp_full" type="number" min="10" max="255" value="${c.ui.meter_comp_full}" title="Raw compression value at which the radio's own meter is full"></label>
            <label>SWR warning at ratio (0 = off)<input name="ui.swr_warn" type="number" min="0" max="10" step="0.1" value="${c.ui.swr_warn}" title="While transmitting at or above this SWR the SWR bar turns red and a warning appears. Typical: 3"></label>
            <label>SWR raw value at 3:1 (10-255)<input name="ui.swr_raw_at_3" type="number" min="10" max="255" value="${c.ui.swr_raw_at_3}" title="Only for radios that report a raw SWR value (FT-991A): the raw reading at which the radio's own meter shows 3:1. The ratio is estimated on a straight line from 1:1. Provisional."></label></fieldset>
          <button class="active" ${info.writable ? "" : "disabled"}>Save</button>
        </form>
        <div class="card2 pttcard"><h3>Transmitting (PTT)</h3>
          ${info.ptt.enabled
            ? `<p><b class="txt">Enabled</b>: operators can key the transmitter from the web page.</p><button id="ptt-off" class="danger" ${info.writable ? "" : "disabled"}>Disable transmitting</button>`
            : `<p><b>Disabled</b>: nobody can transmit.${info.ptt.mock ? " (Test radio: the PTT button works anyway.)" : ""}</p>
               <button id="ptt-ask" ${info.writable ? "" : "disabled"}>Enable transmitting…</button>
               <form id="ptt-form" class="row" hidden>
                 <span class="dim">This lets operators key your transmitter from this page. Test with a dummy load first, and check that remote operation is allowed on your licence.</span>
                 <input name="password" type="password" placeholder="Your password" autocomplete="current-password" required>
                 <button class="danger">Enable now</button></form>`}
        </div>
        <div class="card2 backupcard"><h3>Settings backup</h3>
          <p class="dim">Save the settings (radio, audio, meters, limits) to a file and restore them later, for example after changing the SD card.
            User accounts are not in it; the Pi also keeps daily backups of accounts and settings in <code>/var/backups/radio-remote</code>.
            A restore never changes the listener address, the storage folder or the transmit permission.</p>
          <div class="row"><button type="button" id="bk-dl">Download settings</button>
            <button type="button" id="bk-up" ${info.writable ? "" : "disabled"}>Restore from file…</button>
            <input type="file" id="bk-file" accept=".toml,text/plain" hidden></div>
        </div>
        <div class="card2 updcard"><h3>Updates</h3>
          <p data-updline></p>
          <label class="chk"><input type="checkbox" id="upd-on" ${c.updates?.check ? "checked" : ""} ${info.writable ? "" : "disabled"}> Check for updates once a day
            <span class="dim">(asks GitHub if a newer release exists; nothing is downloaded or installed automatically)</span></label>
          <div class="row"><button type="button" id="upd-now">Check now</button>
            <button type="button" id="upd-install" class="active" hidden title="Download the newest version, check it, back up and install it (it goes back by itself if the new version does not start)">Update now…</button></div>
          <form id="upd-form" class="row" hidden>
            <span class="dim">The Pi downloads and installs the new version, restarts the service and the page reconnects. Radio Remote is unavailable for about a minute; a transmission in progress is stopped.</span>
            <input name="password" type="password" placeholder="Your password" autocomplete="current-password" required>
            <button class="danger">Update now</button></form>
          <p class="dim" id="upd-prog" hidden></p>
          <pre class="updlog" id="upd-log" hidden></pre>
          <p class="dim" data-updhow hidden></p>
        </div>
        <div class="card2 radecard"><h3>RADE (FreeDV neural mode)</h3>
          <p class="dim" data-radeline></p>
          <div class="row"><button type="button" id="rade-install" class="active" hidden>Install RADE</button></div>
        </div>
        <div class="card2 rigcard"><h3>Logbook link (Hamlib rigctl)</h3>
          <p class="dim">Lets a logbook program on your home network (Log4OM, for example) follow the radio and change its frequency and mode. It can never transmit or switch the radio off. It has no password, so keep the port closed on your router.</p>
          <label class="chk"><input type="checkbox" id="rig-on" ${c.rigctl?.enabled ? "checked" : ""} ${info.writable ? "" : "disabled"}> Switch the logbook link on</label>
          <label class="chk"><input type="checkbox" id="rig-set" ${c.rigctl?.set ? "checked" : ""} ${info.writable ? "" : "disabled"}> Let the logbook change frequency and mode <span class="dim">(off = it can only read)</span></label>
          <div class="row"><label>Port <input id="rig-port" type="number" min="1024" max="65535" value="${esc(c.rigctl?.port ?? 4532)}" style="width:6em"></label>
            <label>Allowed addresses <input id="rig-allow" value="${esc(c.rigctl?.allow ?? "private")}" size="26" title="private = this Pi and home-network ranges, or a list such as 192.168.1.0/24, 100.64.0.0/10"></label>
            <button type="button" id="rig-save" ${info.writable ? "" : "disabled"}>Apply</button></div>
          <p data-rigline></p>
        </div>
        <p class="dim"><b>Not editable here, on purpose:</b> ${info.locked.filter((k) => k !== "safety.allow_ptt").map(esc).join(", ")}.</p>
        <div id="restart"></div>`;
      let lastU = null, polling = false;
      const showInstall = () => {                                    // "Update now" needs a known newer version and the root helper on the Pi
        body.querySelector("#upd-install").hidden = !(lastU?.newer && ustat?.available) || ["running", "requested"].includes(ustat?.state);
      };
      const followUpdate = async () => {                             // follow the helper: the service restarts in the middle, so errors just mean "still busy"
        if (polling) return;
        polling = true;
        const prog = body.querySelector("#upd-prog"), logEl = body.querySelector("#upd-log");
        prog.hidden = false; logEl.hidden = false;
        prog.textContent = "Updating… the page reconnects by itself when the new version starts.";
        for (let i = 0; i < 400; i++) {                              // up to about 20 minutes
          await new Promise((r) => setTimeout(r, 3000));
          let s = null;
          try { s = await api("/api/admin/update/status"); } catch { /* restarting */ }
          if (!s) continue;
          ustat = s;
          if (s.log?.length) { logEl.textContent = s.log.join("\n"); logEl.scrollTop = logEl.scrollHeight; }
          if (s.state === "done") { prog.textContent = `Updated to version ${s.version}. Reloading…`; setTimeout(() => location.reload(), 2500); break; }
          if (s.state === "failed") { prog.textContent = "The update did not complete. Read the log below; the previous version is still in use (an update that does not start goes back by itself)."; break; }
        }
        polling = false;
        showInstall();
      };
      const paintUpd = (u) => {
        const line = body.querySelector("[data-updline]"), how = body.querySelector("[data-updhow]");
        if (!u) { line.textContent = "The update status is not available."; how.hidden = true; return; }
        const when = u.checked_at ? ` Last checked ${when_(u.checked_at)}.` : " Not checked yet.";
        if (!u.enabled) line.textContent = `Update check is off. This is version ${u.current}.`;
        else if (u.newer) { line.replaceChildren(`Version `, Object.assign(document.createElement("b"), { textContent: u.latest }), ` is available (this is ${u.current}). `,
          ...(u.url ? [Object.assign(document.createElement("a"), { href: u.url, target: "_blank", rel: "noopener", textContent: "What's new" })] : [])); }
        else line.textContent = u.latest ? `You have the latest version (${u.current}).${when}` : `This is version ${u.current}.${when}`;
        if (u.enabled && u.error) line.append(` The last check could not reach GitHub (${u.error}).`);
        how.hidden = !u.newer;
        lastU = u;
        showInstall();
        how.textContent = "Press Update now, or sign in to the Pi (SSH) and run:  sudo /opt/radio-remote/current/scripts/self_update.sh   (it makes a backup first and goes back by itself if the new version does not start).";
      };
      // RADE: the same Install / Reinstall button as on the FreeDV tab; the Pi downloads the library from the release page (checksum verified)
      const paintRade = (msgOverride) => {
        const line = body.querySelector("[data-radeline]"), b = body.querySelector("#rade-install");
        if (!fdv) { line.textContent = "The FreeDV status is not available (audio may be off)."; b.hidden = true; return; }
        const have = !fdv.unavailable?.RADE;
        b.hidden = !fdv.rade_installable;
        b.textContent = have ? "Reinstall RADE" : "Install RADE";
        line.textContent = msgOverride || (!fdv.rade_installable
          ? `RADE cannot be installed from here: it needs a 64-bit ARM system (Raspberry Pi OS 64-bit); this one reports "${fdv.arch || "unknown"}".`
          : have ? `RADE is installed (${fdv.rade_path || "system library"}). It is used in the FreeDV tab.`
          : "RADE is not installed. The button downloads the library (about 22 MB) from the project release page, checks it and starts using it at once.");
      };
      paintRade();
      body.querySelector("#rade-install").onclick = guard(async () => {
        if (!confirm("Download the RADE library (about 22 MB) from the project GitHub release page and install it? The Pi needs internet access for this.")) return;
        const b = body.querySelector("#rade-install"), had = !fdv?.unavailable?.RADE;
        b.disabled = true; paintRade("Installing… this takes about half a minute.");
        let result;
        try {
          const r = await api("/api/admin/rade/install", "POST", { confirm: true });
          result = !r.ok ? (r.reason || "RADE was installed but could not be loaded.")
            : had ? "RADE reinstalled. The new copy is used after the next restart of the service." : "RADE installed. Choose RADE in the mode list of the FreeDV tab.";
        } catch (e) { result = `Could not install RADE: ${e.message}`; }
        b.disabled = false;
        fdv = await api("/api/freedv").catch(() => fdv);
        paintRade(result);
        note(result);
      });
      const paintRig = (s) => {
        const l = body.querySelector("[data-rigline]");
        if (!s) { l.textContent = "The status is not available."; return; }
        l.textContent = s.error ? `Problem: ${s.error}` : !s.enabled ? "Switched off." : s.listening
          ? `Listening on port ${s.port}. In the logbook choose Hamlib "NET rigctl" with host ${location.hostname} and port ${s.port}. ` +
            (s.clients.length ? `Connected: ${s.clients.map((x) => x.ip).join(", ")}. ` : "No logbook connected right now. ") + (s.refused ? `${s.refused} refused (address not allowed).` : "")
          : "Enabled, but not listening yet.";
      };
      paintRig(rig);
      body.querySelector("#rig-save").onclick = guard(async () => {
        const v = { enabled: body.querySelector("#rig-on").checked, set: body.querySelector("#rig-set").checked,
                    port: +body.querySelector("#rig-port").value, allow: body.querySelector("#rig-allow").value.trim() };
        await api("/api/config", "PUT", { rigctl: v });
        Object.assign(c.rigctl, v);
        paintRig(await api("/api/admin/rigctl"));
        note("Logbook link settings applied.");
      });
      const when_ = (ts) => new Date(ts * 1000).toLocaleString();
      paintUpd(upd);
      body.querySelector("#upd-install").onclick = () => { body.querySelector("#upd-form").hidden = false; body.querySelector("#upd-form input").focus(); };
      body.querySelector("#upd-form").onsubmit = guard(async (e) => {
        e.preventDefault();
        const f = e.target;
        await api("/api/admin/update/start", "POST", { confirm: true, password: f.password.value });
        f.reset(); f.hidden = true;
        body.querySelector("#upd-install").hidden = true;
        followUpdate();
      });
      if (["running", "requested"].includes(ustat?.state)) followUpdate();             // an update is already on its way (page reopened meanwhile)
      body.querySelector("#upd-now").onclick = guard(async () => {
        const b = body.querySelector("#upd-now"); b.disabled = true;
        try { paintUpd(await api("/api/admin/update/check", "POST", {})); } finally { b.disabled = false; }
      });
      body.querySelector("#upd-on").onchange = guard(async (e) => {
        await api("/api/config", "PUT", { updates: { check: e.target.checked } });
        c.updates.check = e.target.checked;
        paintUpd(await api("/api/admin/update"));
        note(e.target.checked ? "Update check is on." : "Update check is off.");
      });
      const showRestart = () => {
        const b = el(`<button class="danger">Restart service now</button>`);
        b.onclick = guard(async () => { if (confirm("Restart now? The radio connection and audio drop for a few seconds.")) { await api("/api/admin/restart", "POST", { confirm: true }); note("Restarting…"); } });
        body.querySelector("#restart").replaceChildren(b);
      };
      body.querySelector("#bk-dl").onclick = () => { const a = el(`<a href="/api/admin/backup" download></a>`); document.body.append(a); a.click(); a.remove(); };
      const bkFile = body.querySelector("#bk-file");
      body.querySelector("#bk-up").onclick = () => { bkFile.value = ""; bkFile.click(); };
      bkFile.onchange = guard(async () => {
        const file = bkFile.files[0];
        if (!file) return;
        if (file.size > 65536) throw new Error("that file is too big to be a settings backup");
        if (!confirm(`Restore the settings from "${file.name}"?\n\nThe current settings are replaced (a copy is kept as config.toml.bak on the Pi). A restart is needed afterwards.`)) return;
        const r = await api("/api/admin/restore", "POST", { toml: await file.text() });
        await views.config();                                  // show the restored values in the form
        note(`Restored ${r.changed.length} changed setting${r.changed.length === 1 ? "" : "s"}${r.ignored.length ? `; ${r.ignored.length} left alone on purpose (server, storage, transmit permission)` : ""}.${r.restart_required ? " Restart the service to apply." : ""}`);
        if (r.restart_required) showRestart();
      });
      const askBtn = body.querySelector("#ptt-ask"), pttForm = body.querySelector("#ptt-form"), offBtn = body.querySelector("#ptt-off");
      if (askBtn) askBtn.onclick = () => { pttForm.hidden = false; askBtn.hidden = true; pttForm.password.focus(); };
      if (pttForm) pttForm.onsubmit = guard(async (e) => {
        e.preventDefault();
        await api("/api/admin/ptt", "POST", { enabled: true, password: pttForm.password.value, confirm: true });
        await views.config();
        note("Transmitting is enabled. Open pages update by themselves.");
      });
      if (offBtn) offBtn.onclick = guard(async () => {
        if (!confirm("Disable transmitting? A transmission in progress is stopped.")) return;
        await api("/api/admin/ptt", "POST", { enabled: false, confirm: true });
        await views.config();
        note("Transmitting is disabled.");
      });
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
        if (r.restart_required) showRestart();
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
        <p class="dim">Your other signed-in devices are signed out when you change it.</p>
        <form id="disp" class="cfg"><fieldset><legend>Display: status lights in the VFO A header</legend>
          ${(() => { const on = getLights(); return LIGHTS.map(([k, label]) => `<label class="chk"><input type="checkbox" name="${k}" ${on[k] ? "checked" : ""}> ${label}</label>`).join(""); })()}
        </fieldset><p class="dim">Switch off the lights you do not need for a cleaner header. This applies to this device only.</p></form>`;
      body.querySelector("#disp").onchange = (e) => { if (e.target.name) setLight(e.target.name, e.target.checked); };
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
