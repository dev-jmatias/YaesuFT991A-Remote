import { pref } from "../util.js";

// This device's sound while a digital screen is open (FT8/FT4, SSTV, CW): the decoder works on the Pi's own audio, so the tones are not needed here. "off" switches Listen off while the
// screen is open, "low" keeps it at a fraction of the volume; leaving the screen puts everything back as it was (and only switches Listen on again if it was on before).
export function soundGuard(ctx, prefKey, select) {
  let heard = false, here = false, started = false;
  const apply = () => {
    const au = ctx.audio?.();
    if (!au) return;
    const low = select.value === "low";
    if (here && select.value === "on") { au.duck(null); if (!au.hear) { started = true; au.toggleListen(); } }          // "on" (CW: the operator wants to hear the signal)
    else if (here && low) { if (heard && !au.hear) { heard = false; au.toggleListen(); } au.duck(0.12); }
    else if (here) { au.duck(null); if (au.hear) { heard = true; au.toggleListen(); } }
    else { au.duck(null); if (started && au.hear) au.toggleListen(); started = false; if (heard) { heard = false; if (!au.hear) au.toggleListen(); } }
  };
  select.value = pref.get(prefKey, "off");
  select.onchange = () => { pref.set(prefKey, select.value); apply(); };
  return { set(active) { here = !!active; apply(); } };
}
