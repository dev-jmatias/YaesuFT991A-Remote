# Bench results (real FT-991A)

## Summary (as of version 1.1.6, 2026-10-04)

Everything below was tried on a real **FT-991A** with a **Raspberry Pi 4** (Raspberry Pi OS Trixie, Python 3.13).

| Area | Result |
|---|---|
| CAT link, auto-detection of the port, reconnect after unplug / power cycle | works |
| Frequency, mode, bands, VFO B (frequency and mode), split, A=B / B=A / swap | works |
| RIT / XIT, filters and DSP (width, IF shift, contour, notches, DNR, NB), IPO / ATT / AGC | works |
| Memory channels: list and recall | works (the radio answers every `MT` read with channel 001: handled; see below) |
| Meters: S, power, SWR (warning limit), ALC and COMP | work; ALC calibrated from one point, SWR ratio is an estimate from the raw value |
| PTT, TUNE, REAR / MIC switch, transmit switch in Admin, Lock PTT | work |
| Remote audio both ways, Bluetooth headset, background listening on a phone | works |
| Power off and power on over USB from standby | works |
| Installer pack, ready-made image, Imager's own customisation screens, in-app update (`self_update.sh`), Tailscale (`tailscale_setup.sh`) | work |
| Settings backup and restore, update notice | work |
| C4FM RX/TX DG-ID | **not reachable over CAT** (no menu value changes) |
| Quick split (`QS;`) | implemented, not yet tried |
| Automatic rollback of a failed update, `restore.sh`, `uninstall.sh` | scripts exist; a full real-Pi run of each is not recorded yet |

Radio behaviour that differs from the CAT manual (all handled in the code):
- `PR` (speech processor): the radio uses 0 = off, 1 = on, not 1 / 2 as printed.
- `FT` (TX VFO): set 2 / 3 (TX on A / B), read 0 / 1.
- `MT` (memory read with tag): the answer to `MT005;` carries the data of channel 5 but **always says channel `001`**; empty channels answer `?;`.
  The server matches the answer on `MT` alone and takes the channel number from its own request. A channel that stays silent is retried and
  then read with `MR` (no tag).
- `EX153` (WIRES DG-ID): the radio answers with three digits (`EX153000;`), the manual shows two.
- The radio answers a read taken immediately after a VFO command with the **old** value: reads after VFO operations wait 0.35 s and repeat.
- The radio does not announce VFO B changes: VFO B is polled every second.

The sections below are the log of the tests in the order they were done (some notes at the time said "to be tested": the summary above is current).

## Bench 1 - read-only: PASSED

Date: 2026-10-01. Hardware: Raspberry Pi (hostname FT991A), FT-991A over USB, app v0.1.0.
Method: `bench1_readonly.py` (sends read commands only).

| Command | Reply | Meaning / status |
|---|---|---|
| `ID;` | `ID0670;` | FT-991A identified. **Confirmed on hardware** |
| `AI;` | `AI1;` | auto-information was already on (the service enabled it). Confirmed |
| `IF;` | `IF001014220000+000000200000;` | 25-character body matches the manual's layout (mem 001, 14.220000 MHz, clarifier +0000, RIT/XIT off, mode 2 = USB, VFO). **Confirmed** |
| `FA;` / `FB;` | `FA014220000;` / `FB007000000;` | 9-digit frequencies. Confirmed |
| `MD0;` | `MD02;` | USB. Confirmed |
| `TX;` | `TX0;` | receiving. Confirmed |
| `AG0;` `RG0;` `MG;` `PC;` | `AG0040` `RG0255` `MG041` `PC005` | levels read as 3 digits; TX power was 5 W. Confirmed |
| `SM0;` | `SM0032;` | S-meter raw 32 at idle (first calibration point; antenna state unknown) |
| `RM3..RM6;` | `RM3000` `RM4000` `RM5000` `RM6000` | COMP/ALC/PO/SWR are 0 while receiving. Confirmed |

Link statistics: tx 16, rx 28, timeouts 0, rejected (`?;`) 0, unsolicited 12.

Findings:
- The CAT port is `/dev/ttyUSB0` (the FT-991A exposes two ports; detection picked the right one at 38400 baud).
- No read command was answered with `?;`.
- The service user needs read access to `/opt/radio-remote/...`, not `/home/<user>/...` (home directories are private).
- The service must be **stopped** while running the bench script: it holds the port.

## Bench 2 - app against the real radio, receive only, PTT disabled: PASSED (operator report)

- Status badge shows "radio online".
- VFO knob changes follow in the browser.
- Mode changes on the radio follow in the browser.
- Band buttons in the browser move the radio to that band (BS command).
- USB unplug/replug and radio power cycle: shows offline, then recovers by itself.

## Antenna tuner on/off (`AC`) and split (`FT`): replies from the real radio

| Sent | Radio replied | Meaning |
|---|---|---|
| `AC;` | `AC000;` | tuner off |
| `AC001;` / `AC;` | `AC001;` / `AC001;` | tuner on, reads back on |
| `AC000;` / `AC;` | `AC000;` / `AC000;` | tuner off again. The command layer is correct; the app's TUNER chip lights teal (operator report) |
| `FT;` | `FT0;` | transmit on VFO A (normal) |
| `TS;` | `TS0;` | TXW off |
| `FT3;` | `FT1;` + `RI50; RI61;` | transmit now on VFO B = **split**. The radio also announces `RI5x`/`RI6x` (A/B transmit flags) |
| `FT2;` | `FT0;` + `RI51; RI60;` | back to VFO A |

So `FT` behaves as the manual says: **set 2 / 3 (TX on A / B), read 0 / 1**. Split in the app = `FT3;`, normal = `FT2;`; the state
follows the radio's own `FT` announcements. `QS;` (quick split) and the TUNE button (`AC002;`) are implemented but NOT yet verified.
Auto-information also streams `RM0nnn;` (front-panel meter) continuously; currently ignored.

## VFO operations, tuner, DG-ID: operator report after the first VFO B build

- A=B, B=A, SPLIT: work. Tuner on/off and TUNE: work (operator: "tuning works").
- A<->B: the radio changed but the VFO B label did not follow. Cause: the radio answers a read taken immediately after the
  command with the OLD value, and VFO B was only re-polled every ~13 s. Fixed: reads after VFO operations wait 0.35 s and repeat
  about 1 s later; `FB;` and `FT;` are now in the 1 s backstop poll.
- Set B (type a frequency): reported as not working. Not yet understood (possibly the same stale-read problem); raw `FB` test requested.
- Raw `FB` check on the real radio: `FB;` -> `FB007498700;`, `FB007074000;` accepted (the radio sent **no** echo of the new B
  frequency, unlike `AC`, `FT` which announce changes), `FB;` -> `FB007074000;`. So Set B works at the CAT level; the radio does not
  announce VFO B changes, they must be polled (now every 1 s). The "Set B does not work" symptom was the stale label, not the command.
- DG-ID: the control wrote menu 153 and "does nothing on the radio". The CAT manual lists only **menu 153 "WIRES DG-ID"** (the
  WIRES-X setting) and menu 090 "AMS TX MODE" (AUTO / MANUAL / DN / VW / ANALOG). There is NO documented command for the C4FM
  **RX DG-ID / TX DG-ID** shown on the radio, so they cannot be controlled over documented CAT. The control is relabelled
  "WIRES DG-ID" so it does not mislead. If the radio's front-panel menu number for TX/RX DG-ID is known, `EX<number>;` reads
  could be probed; nothing will be guessed.

## Installer and updater on a real Pi: PASSED

- `install.sh` ran on Raspberry Pi OS (aarch64, Python 3.13.5): service, Caddy, udev rule, config created; app healthy.
  (First install was done before these notes; it worked, app reachable, radio detected.)
- `update.sh` from a tarball copy (no git): backup written, new release deployed beside the old one, dependencies already
  satisfied, switched, restarted, health check passed ("Updated and healthy"). Config and users untouched.
- Not yet exercised: the automatic rollback path, `restore.sh`, `uninstall.sh`.

## Bench finding: manual vs radio, speech processor (`PR`)

The service logged `bad frame from radio: bad PR` every slow-poll cycle. Raw capture from the real radio:

| Sent | Radio replied |
|---|---|
| `PR0;` | `PR00;` (processor off) |
| `PR1;` | `PR10;` (parametric mic EQ off) |

The manual says the on/off digit is 1 = OFF, 2 = ON; **the radio uses 0 = OFF, 1 = ON.** Set test on the real radio:

| Sent | Radio |
|---|---|
| `PR01;` | accepted; `PR0;` then reads `PR01;` (processor ON) |
| `PR02;` | `?;` (rejected) |
| `PR00;` | accepted; reads `PR00;` (OFF) |

Fixed in both directions (parser and encoder): the processor toggle now sends `PR01;` / `PR00;`. Before the fix the UI would
have turned the processor on when asked to turn it off. **Lesson: the CAT manual can be wrong on value tables; every set
command in the controls panel should be checked on the real radio** (Bench 2b), not trusted from the manual.
Side note: with auto-information on, the radio streams `RM0nnn;` (front-panel meter) frames continuously; they are
currently ignored.

All other polled controls (NA, SH, IS, CO, BP, BC, NR, RL, NB, NL, PA, RA, RT, XT, PL, ML, AC) produced no warnings on the
real radio, i.e. their reply formats parsed. Rejections (`?;`) are not logged at warning level, so a command refused in the
current mode would not show up in that log.

## Receive audio: PASSED (operator report)

Listen in the browser: receive audio is heard and the RX meter moves. Observation: with IPO selected nothing useful is
heard, with AMP1/AMP2 it receives. Expected radio behaviour (IPO = no preamp gain) but needs confirming against the
radio's own display (open question: do app labels IPO/AMP1/AMP2 match the radio's front panel?).

## Audio: device detection and CPU

`arecord -l` / `aplay -l` (Pi 4, radio on): the radio appears as `card 1: CODEC [USB Audio CODEC], device 0` for both capture
and playback; the other playback cards are the Pi's headphones and two HDMI outputs. Exactly one USB audio CODEC, so
auto-detection selects `plughw:CARD=CODEC,DEV=0` for both directions.

`audio_spike.py` on a Raspberry Pi 4 Model B (aarch64, 4 cores, Python 3.13.5):

| Test | Result |
|---|---|
| Opus encode 96 / 32 / 24 kbps | 599 / 851 / 849 us per 20 ms frame (3.0 / 4.3 / 4.2 % of one core) |
| Opus decode | 124-156 us per frame (about 0.7 %) |
| measured bitrate for the 32 kbps target | 31.7 kbps (the bitrate patch works) |
| WebRTC loopback, 1 listener | 20.8 % of one core (server encode + client decode + SRTP, both ends in one process) |
| WebRTC loopback, 3 listeners | 55.6 % of one core |
| frame delivery | 50 frames/s per listener in both cases |

Reading: real server-side cost is below these figures because the loopback also runs the client. A Pi 4 has ample headroom
for the default `audio.max_peers = 3`; a separate audio process is not needed there. A Pi 3B+ is expected (estimate, not
measured) to be 2-3x slower per core: use `max_peers` 1-2.

At that point still unverified (all of it was verified later, see the summary at the top): every *set* command, the controls panel, PTT,
audio, `NA`/`GT` quirks, `PS0;`/`PS1;`. Meter calibration against real signals is still open.

- DG-ID follow-up (bench): `scripts/ex_dump.py` dumped EX001..EX160 (153 readable) before and after changing the DG-ID on the radio
  (GM long press, C4FM). **No EX value changed**, so the C4FM RX/TX DG-ID is not reachable over CAT. The web selector was replaced by
  a hint "DG-ID: set on the radio (hold GM)"; the backend `dgid` control (menu 153) remains but is not offered in the UI.

- Mic input (menu 106 SSB MIC SELECT, manual p.9: 0 = MIC, 1 = REAR): added as a control `mic_select` (`EX106`), shown as a "Mic: REAR/MIC"
  button in the Receiver & tools block, refused while transmitting. Verified later: the button flips menu 106 on the radio (now the REAR / MIC buttons above PTT).

- Power on (PS1): the CAT manual (p.15) says the command needs dummy data first and PS1; one to two seconds later. Implemented in the
  driver supervisor (`power_on`, `wake_gap_s` = 1.4 s): while the radio is not connected an admin gets a "Power on radio" button in the
  offline banner; the server opens the serial port, sends `;`, waits, sends `PS1;`, closes it and keeps retrying the normal connection.
  *Bench to do at the time (done, see below):* radio in standby with the DC supply on, USB cable connected: does the USB serial port stay present and answer? If the
  port disappears in standby this cannot work over USB (the Pi would need a relay on the radio's power input instead).

- **Power on / off VERIFIED on the real FT-991A** (operator report): "Power off" (PS0) and "Power on radio" (dummy data, 1.4 s, PS1) both
  work over USB with the radio in standby; the USB serial port stays available in standby.

- VFO B mode: added `OI;` (manual p.14) to the sync and the 1 s backstop poll; the answer is decoded like `IF` and only `mode_b` is used.
  *Bench to do at the time (done, see below):* check that VFO B shows the right mode, also after A<->B / A=B / B=A.

- **RX audio level (operator report):** the browser audio was too quiet with menu 107 SSB OUT LEVEL at its default (50). Setting menu 107 to
  **90** fixed it, with `audio.rx_gain_db` left at 0. So the radio's USB output level menus (107 SSB, 073 DATA, 046 AM, 075 FM, 054 CW,
  099 RTTY) are the right place to set the receive level for the Pi.

- **ALC calibration (operator report, dummy load, FT-991A):** normal speech: radio ALC meter about 50%, app raw ALC peak 79 (with the old
  scale 50 the app bar was full). So `ui.meter_alc_full` default raised to 160. One point only: the radio's ALC meter may not be linear;
  a second point (louder speech) is still wanted.

- **VFO B mode via `OI;` VERIFIED on the real FT-991A** (operator report): VFO A and VFO B show the correct band and mode.

- **RIT/XIT clarifier VERIFIED** (operator report): the RIT + XIT buttons with the shared offset bar work on the FT-991A. **COMP scale**
  (`ui.meter_comp_full` = 255 default) looks right to the operator, no change needed.

- **Background audio VERIFIED** (operator report, phone): Listen keeps playing with the app in the background.

- **Memory channels (to be tested at the time; verified below):** `Memories` button, list read with `MT001;`..`MT099;`, recall with `MC005;`, back with `VM;`.
  Check: do the tags and frequencies match the radio's own memory list, are empty channels skipped (`?;`), does the MEM badge follow the
  radio, and does Back to VFO return to the previous VFO frequency? If an empty channel is answered with frequency 000000000 instead
  of `?;` it is skipped as well.

- **Memory channels VERIFIED on the real FT-991A** (operator report: "it works"): Memories list, recall and back to VFO.

- **AGC VERIFIED on the real FT-991A** (operator report): the AGC list (OFF / FAST / MID / SLOW / AUTO, `GT0`) works.

- **Bluetooth audio device choice VERIFIED** (operator report): choosing a Bluetooth output/input on the Audio tab works.
- **ALC scale accepted** by the operator for now (`ui.meter_alc_full` = 160); a second point (louder speech) is optional.

- **Installer pack, static checks (development PC, Debian under WSL1):** every shell script passes `bash -n`; `install-everything.sh --dry-run`
  prints the expected sequence (packages, user, release copy, offline pip from the bundled wheels, config, udev, systemd, Caddy, docs URL);
  `--model ftdx10` prints the experimental note, a bad model and a non-root run are refused. (Run for real on a Pi 4 afterwards: it found one
  bug, the program folders were copied with private permissions so the service user could not read them; fixed and covered by a test.)

- **Ready-made image, Imager and updates (2026-10-03):** the image built by GitHub Actions booted on a Pi 4 and ran. Raspberry Pi Imager 2.x
  shows its own hostname / user / Wi-Fi screens for it through the content repository (`os-list.json`); the settings script was needed before.
  The in-app update (`self_update.sh`: checksum, backup, install, health check) installed a newer release. `tailscale_setup.sh` made the Pi
  reachable over its `.ts.net` name with a real certificate. The memory list read failed until the "answer says channel 001" behaviour was handled.
