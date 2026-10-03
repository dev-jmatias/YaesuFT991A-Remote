// Meter scales. The CAT manual gives raw 0..255 values and NO calibration, so everything here is provisional:
//
//  * S-meter: raw -> dB relative to S9 uses a table in the style of the Hamlib calibration for this radio family.
//    It is an APPROXIMATION, not measured on your radio. Replace S_TABLE after a signal-generator test (docs/03-bench-checklist.md, bench 5).
//  * ALC / COMP: percent of a full-scale raw value that you can set in Admin > Config > Meters.
export const S_TABLE = [[0, -54], [12, -48], [27, -42], [40, -36], [55, -30], [65, -24], [80, -18], [95, -12], [112, -6], [130, 0], [150, 10], [172, 20], [190, 30], [220, 40], [240, 50], [255, 60]];

export function rawToDb(raw, table = S_TABLE) {
  if (raw <= table[0][0]) return table[0][1];
  for (let i = 1; i < table.length; i++) {
    const [r1, d1] = table[i];
    if (raw <= r1) {
      const [r0, d0] = table[i - 1];
      return d0 + ((raw - r0) / (r1 - r0)) * (d1 - d0);
    }
  }
  return table[table.length - 1][1];
}

// Position along the bar, like a real S-meter: S0..S9 fill the first 60 %, S9 .. S9+60 dB the rest.
export function dbToPos(db) {
  if (db <= 0) return Math.max(0, ((9 + db / 6) / 9) * 0.6);
  return Math.min(1, 0.6 + (db / 60) * 0.4);
}

export function sText(db) {
  if (db <= 0) return "S" + Math.max(0, Math.round(9 + db / 6));
  const over = Math.round(db / 10) * 10;
  return over ? `S9+${over}` : "S9";
}

export const S_TICKS = [[1, "1"], [3, "3"], [5, "5"], [7, "7"], [9, "9"]].map(([s, l]) => [(s / 9) * 0.6, l])
  .concat([[0.7333, "+20"], [0.8667, "+40"], [1, "+60"]]);

export const pct = (raw, full) => Math.max(0, Math.min(100, (raw / full) * 100));

// Colours. S-meter: blue at the bottom, through cyan/green, yellow past S9, red for strong signals.
export const sColor = (f) => (f < 0.25 ? "#3b82f6" : f < 0.45 ? "#22d3ee" : f < 0.6 ? "#34d399" : f < 0.75 ? "#fbbf24" : f < 0.87 ? "#fb923c" : "#f43f5e");
// ALC / COMP percentage: blue and green are fine, yellow warns, over 50 % is red.
export const levelColor = (f) => (f < 0.25 ? "#3b82f6" : f < 0.45 ? "#34d399" : f < 0.5 ? "#fbbf24" : "#f43f5e");
export const powerColor = (f) => (f < 0.6 ? "#34d399" : f < 0.85 ? "#fbbf24" : "#f43f5e");
export const swrColor = (f) => (f < 0.25 ? "#34d399" : f < 0.5 ? "#fbbf24" : "#f43f5e");   // f = (SWR-1)/2: <1.5 good, <2 caution, else bad
