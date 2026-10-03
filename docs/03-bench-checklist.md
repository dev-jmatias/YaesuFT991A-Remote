# Bench checklist (FT-991A + Raspberry Pi 4)

Rule for every step: **PTT stays disabled** (`safety.allow_ptt = false`) and the antenna jack should have a dummy load or be disconnected until a step says otherwise.

## Before connecting

1. Radio menu **031 CAT RATE**: note the value (default 4800). Recommended: 38400 on both ends.
2. Radio menus **071 DATA PTT SELECT, 076 FM PKT PTT SELECT, 108 SSB PTT SELECT, 047 AM PTT SELECT**: note them. Our software holds DTR/RTS low when opening the port, but if any of these is set to RTS/DTR, a *different* program opening the port could key the radio. Prefer `DAKY`.
3. Menu **036 TX TOT**: set to a non-zero value (for example 3 min) as a radio-side backstop for later phases.
4. USB-connect the radio, power it on. On the Pi: `ls /dev/serial/by-id/` and `lsusb` (expect Silicon Labs CP210x, two serial ports).

## Bench 1 - read-only (this phase)

```bash
python scripts/bench1_readonly.py
```

Expected: `ID;` -> `ID0670;`. Paste the whole output back to me. Things I specifically want to learn:
- whether `?;` appears for any read (manual silent on error replies),
- the real `IF;` / `SM0;` / `RM*` values at idle (meter calibration starts from these),
- which of the two serial ports answers, and at what baud.

## Bench 2 - app against the real radio, RX only

Config: `radio.model = "ft991a"`, `serial_port = "auto"`, `allow_ptt = false`.
- Turn the VFO knob: browser frequency must follow within ~0.5 s.
- Change mode on the radio and in the browser.
- Pull the USB cable and re-plug: UI shows "radio offline" then recovers on its own.
- Power-cycle the radio: UI recovers; confirm frequency/mode are correct afterwards.
- Check the log shows `AI1` re-sent after reconnect.

## Bench 2b - Phase 5 controls (RX only, PTT disabled, dummy load)

Change each control in the browser and compare with the radio's own display. Things the manual leaves open:

| Check | What could differ from the code |
|---|---|
| **Width** per mode | Table is manual p.16. DATA-* modes are deliberately not offered. Confirm USB narrow/wide steps match the front panel, and that code 00 ("default") reads back sensibly. |
| **Narrow filter** (`NA0`) | Printed as `MA` in the manual body, `NA` in its index. Code uses `NA`. If the radio answers `?;`, report it. |
| **Contour / APF / notch** step sizes | Manual gives ranges, not steps. UI uses 10 Hz. Confirm the radio accepts e.g. 1210 Hz. |
| **APF** | Probably CW-only; the radio may answer `?;` in SSB. |
| **IF shift** | Radio steps are 20 Hz; the UI enforces multiples of 20. |
| **Clarifier** | Offset set via `RC;` + `RU`/`RD`. Check sign, and that the RIT/XIT flags follow. |
| **IPO / AMP1 / AMP2** (`PA0`) | Confirm the labels against the front panel. |
| **Processor** (`PR0`: 1 = off, 2 = on) | Confirm on/off is not inverted. |
| **Band buttons** (`BS`) | The radio goes to its last frequency on that band. Check the 60m and 12m codes especially. |
| **Antenna tuner** | Only the on/off toggle (`AC001`/`AC000`) is exposed. Tune *start* (`AC002`) keys the transmitter and is **not** implemented (`tuner_tune = "unverified"`). |
| **Power off** (admin only) | `PS0;`. Confirm the radio goes to standby and the app shows "radio offline". Then see whether USB serial still answers `ID;` in standby (this decides whether CAT power-on is possible). |

Never exposed until verified: AGC (the manual's mapping is inconsistent), power-on, VOX, menu (`EX`) writes, tune start.

## Bench 2c - antenna tuner and C4FM DG-ID (added after the first operator report)

**Tuner on/off chip (`TUNER`)** reported as "does nothing" on the real radio. Diagnose with raw reads first (no transmit):

```bash
sudo systemctl stop radio-remote
sudo -u radio-remote /opt/radio-remote/venv/bin/python - <<'EOF'
import serial, time
s = serial.Serial(); s.port, s.baudrate, s.timeout = '/dev/ttyUSB0', 38400, 0.5
s.dtr = False; s.rts = False; s.open()
def ask(c):
    s.reset_input_buffer(); s.write(c); time.sleep(0.4); print(c, '->', s.read(64))
ask(b'AC;'); ask(b'AC001;'); ask(b'AC;'); time.sleep(2); ask(b'AC000;'); ask(b'AC;')
s.close()
EOF
sudo systemctl start radio-remote
```
(`AC001;`/`AC000;` switch the tuner on/off; they do not transmit.) Watch the radio's display for the tuner indicator.

**TUNE button (new, NOT bench-verified):** sends `AC002;` (manual: "tuning start / tuning stop"), which **makes the radio
transmit a carrier**. Server-side it is guarded like PTT: needs `allow_ptt = true`, control, an operator account; hard limit
15 s; stops if the browser disconnects; a *stop* (`AC002;` again) is only ever sent while the radio itself reports RF going
out, because the command is a toggle and would otherwise start a new tune. Test ONLY with a dummy load / suitable antenna, at
5 W, tuner switched on, and be ready to switch the radio off.
Questions the bench must answer: does `AC002;` start a tune; does the radio report TX (`TX;`) or a power reading while
tuning; does a second `AC002;` stop it; how long does a tune take.

**DG-ID (new):** radio menu 153 "WIRES DG-ID" via `EX153` (00 = AUTO, 01-99), shown next to the mode buttons in C4FM mode only.
The CAT manual lists ONE DG-ID item; no separate TX/RX one. If the radio has separate TX and RX DG-ID settings, give me their
menu names/numbers (or the operating-manual page); they will not be guessed. Check: set a value in the app, compare with the
radio's menu 153; set it on the radio, see the app follow within about 10 s (read every slow-poll cycle).

## Later benches (not yet implemented)

- Bench 3: `PS0;` power off / `PS1;` power on from standby (does the USB UART answer in standby?).
- Bench 4: PTT with dummy load, timeouts, heartbeat loss (unplug network while keyed).
- Bench 5: meter calibration (S-meter vs signal generator, PO/SWR vs wattmeter).
- Bench 6: USB audio routing menus.
