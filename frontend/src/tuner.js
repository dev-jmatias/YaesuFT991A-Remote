// Rate-limited frequency setter. The CAT link is slow compared with a spinning dial, so only one
// set_frequency is in flight at a time; the newest target wins. The display shows `target` while tuning
// and falls back to the radio's reported frequency shortly after (the radio is authoritative).
import { clamp } from "./util.js";

// Keep a tuning move inside the coverage range containing the current frequency (no jumping across gaps).
export function rangeFor(ranges, hz) {
  let best = ranges[0];
  for (const r of ranges) {
    if (hz >= r[0] && hz <= r[1]) return r;
    if (Math.abs(hz - r[0]) < Math.abs(hz - best[0])) best = r;
  }
  return best;
}

export function createTuner({ send, getFreq, getRanges, onChange }) {
  let target = null, inflight = false, settle = 0;

  async function pump() {
    if (inflight || target === null) return;
    const t = target;
    inflight = true;
    try { await send("set_frequency", { hz: t }); } catch { /* shown by caller's toast; radio value wins below */ }
    inflight = false;
    if (target === t) {
      clearTimeout(settle);
      settle = setTimeout(() => { target = null; onChange(); }, 350);
    } else pump();
  }

  return {
    get target() { return target; },
    nudge(deltaHz) {
      const base = target ?? getFreq();
      if (!base) return;
      const r = rangeFor(getRanges(), base);
      target = clamp(base + deltaHz, r[0], r[1]);
      clearTimeout(settle);
      onChange();
      pump();
    },
    set(hz) { target = hz; onChange(); pump(); },
  };
}
