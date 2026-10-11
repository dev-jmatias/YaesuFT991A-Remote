import { api } from "../api.js";
import { rawToDb } from "../meter-cal.js";
import { el, fmtStep } from "../util.js";

// SCAN, next to TUNE. Idle: a tap opens a sheet with two kinds of scan. Running (either kind): a tap stops it.
//  * the memories: the radio's own scan (CAT SC), which steps through the stored channels;
//  * a range: the server steps the VFO from a start to a stop frequency, waits on a signal, and starts again at the start.
// Neither transmits.
const KEY = "rr-scan-range";
const LEVELS = [["S1", -48], ["S3", -36], ["S5", -24], ["S7", -12], ["S9", 0], ["S9+20", 20]];
const STEPS = [100, 500, 1000, 2500, 5000, 6250, 8330, 10000, 12500, 25000];
const MHZ = (hz) => (hz / 1e6).toFixed(6).replace(/0+$/, "").replace(/\.$/, ".0");
const rawFor = (db) => { for (let r = 0; r <= 255; r++) if (rawToDb(r) >= db) return r; return 255; };      // the smallest raw S-meter reading that reaches this level

export function openScan(ctx) {
  const s = ctx.S.state;
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; } })();
  const f0 = s.frequency || 0;
  const v = { start: saved?.start ?? f0, stop: saved?.stop ?? f0 + 100000, step: saved?.step ?? ctx.ui.step ?? 5000, level: saved?.level ?? -36, hold: saved?.hold ?? 2, stopOn: saved?.stopOn ?? false };
  const canMem = !!ctx.S.caps.features.scan && !!ctx.S.caps.features.memories;
  const sheet = el(`<div class="sheet scansheet" role="dialog" aria-label="Scan"><div class="sheetbox">
    <div class="sheethead"><h2>Scan</h2><button class="icon wide" data-x>Close</button></div>
    ${canMem ? `<section><h3>Memories</h3>
      <p class="dim">The radio steps through its stored channels and stops on a signal.</p>
      <div class="row"><button class="active" data-mem>Scan the memories</button></div></section>` : ""}
    <section><h3>Between two frequencies</h3>
      <p class="dim">Steps from the start to the stop frequency, waits on a signal, then starts again at the start. Receive only; set the mode (FM, AM, SSB) first.</p>
      <div class="memfields">
        <label>Start (MHz)<input data-f="start" inputmode="decimal" value="${MHZ(v.start)}"></label>
        <label>Stop (MHz)<input data-f="stop" inputmode="decimal" value="${MHZ(v.stop)}"></label>
        <label>Step<select data-f="step">${STEPS.map((x) => `<option value="${x}"${x === v.step ? " selected" : ""}>${fmtStep(x)}</option>`).join("")}</select></label>
        <label>Stop on a signal above<select data-f="level">${LEVELS.map(([n, db]) => `<option value="${db}"${db === v.level ? " selected" : ""}>${n}</option>`).join("")}</select></label>
        <label>Wait after it ends<select data-f="hold">${[0, 1, 2, 3, 5, 10].map((x) => `<option value="${x}"${x === v.hold ? " selected" : ""}>${x} s</option>`).join("")}</select></label>
        <label class="check"><span>When a signal is found</span><select data-f="stopOn"><option value="0"${v.stopOn ? "" : " selected"}>wait, then carry on</option><option value="1"${v.stopOn ? " selected" : ""}>stop the scan there</option></select></label>
      </div>
      <div class="memnote" data-note></div>
      <div class="row"><button class="active" data-go>Start the range scan</button></div></section>
  </div></div>`);
  document.body.append(sheet);
  const q = (x) => sheet.querySelector(x);
  const f = (n) => sheet.querySelector(`[data-f="${n}"]`);
  const close = () => { sheet.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  sheet.addEventListener("click", (e) => { if (e.target === sheet) close(); });
  q("[data-x]").onclick = close;
  const note = (t) => { q("[data-note]").textContent = t; };
  const hz = (name) => { const x = parseFloat(String(f(name).value).replace(",", ".")); return Number.isFinite(x) ? Math.round(x * 1e6) : NaN; };

  if (canMem) {
    q("[data-mem]").onclick = async () => {
      const b = q("[data-mem]");
      b.disabled = true;
      try {
        if (ctx.S.state.vfo_memory !== "memory") {                          // the radio scans the memories only from memory mode: recall the first stored channel
          note("Going to the memories…");
          const ch = ((await api("/api/memories")).channels || [])[0];
          if (!ch) { note("There are no stored memory channels to scan."); b.disabled = false; return; }
          await ctx.sock.send("memory_select", { channel: ch.channel });
        }
        await ctx.sock.send("set_control", { name: "scan", value: true });
        close();
      } catch (e) { note(e.message || "Could not start the scan."); b.disabled = false; }
    };
  }

  q("[data-go]").onclick = async () => {
    const start = hz("start"), stop = hz("stop");
    if (!(start > 0) || !(stop > start)) { note("Enter a start frequency and a higher stop frequency, in MHz."); return; }
    const saveV = { start, stop, step: +f("step").value, level: +f("level").value, hold: +f("hold").value, stopOn: f("stopOn").value === "1" };
    try { localStorage.setItem(KEY, JSON.stringify(saveV)); } catch { /* private window */ }
    note("");
    try {
      await ctx.sock.send("scan_range", { start, stop, step: saveV.step, level: rawFor(saveV.level), hold_s: saveV.hold, stop_on_signal: saveV.stopOn });
      close();
    } catch (e) { note(e.message || "The scan could not be started."); }
  };
}

export function createScanButton(host, ctx) {
  if (!ctx.S.caps.features.scan && !ctx.S.caps.features.range_scan) return { update() {} };
  const b = el(`<button class="led scan" title="Scan the memories, or between two frequencies. Tap again to stop.">Scan</button>`);
  b.onclick = () => {
    const s = ctx.S.state;
    if (s.range_scan) { ctx.send("scan_range_stop"); return; }
    if (s.scan) { ctx.send("set_control", { name: "scan", value: false }); return; }
    openScan(ctx);
  };
  host.append(b);
  return {
    update() {
      const s = ctx.S.state;
      const on = !!(s.scan || s.range_scan);
      b.classList.toggle("on", on);
      b.textContent = on ? "Stop scan" : "Scan";
      b.disabled = !!(s.tx || s.tuning);
    },
  };
}
