# Other radios: FTDX10, FTDX101D, FTDX101MP, FT-710 (EXPERIMENTAL)

These four profiles were written from the Yaesu CAT manuals (FTDX10 2308-F, FTDX101MP/D 2101-I, FT-710 2306-C). The app shows an amber
*EXPERIMENTAL PROFILE* banner whenever one of them is selected (`bench_tested = false` in its capability file).

* **FTDX101D: tested on a real radio** (2026-10-05, Raspberry Pi 4, USB), see the next section for what works and what the manual gets wrong.
  Not everything is tried yet (transmitting on the SUB receiver, the MP variant).
* **FTDX101MP**: same CAT commands as the D (one shared manual), so its profile is **enabled exactly like the D's**, with transmit power 5 to 200 W (the D: 5 to 100 W). No MP has been
  available, so it still shows the experimental banner; the first owner to try it should follow the checklist below and report.
* **FTDX10 and FT-710**: still only on paper. Nothing there has been seen working on a real radio.

## FTDX101D: what was verified on a real radio

**Connection.** The radio has a dual CP210x USB bridge: the **Enhanced** port (usually `/dev/ttyUSB0`) is CAT. Set, on the radio, **CAT RATE 38400**, **CAT TIME OUT TIMER** to a
long value and **CAT RTS = OFF**. With *CAT RTS* on, the radio only answers while the computer holds RTS high, and this program keeps RTS low on purpose (RTS can key a transmitter,
and the Standard port may be set to use it for the CW keyer or PTT), so it would stay silent: the app then says "radio offline". The ID is `0681`; the radio's CAT port answers at the rate set in the menu only.

**MAIN and SUB receivers.** `FA` is the MAIN receiver and `FB` / `OI` the SUB receiver (the radio's VFO-A and VFO-B). The app shows them as **MAIN** and **SUB**. Instead of the
VFO copy / swap buttons it has:

* a **MAIN | SUB** selector (`VS0;` / `VS1;`: the receiver the radio's own dial and keys operate; follows the radio's MAIN / SUB keys),
* **RX** buttons (`FR P1 P2`, 0 = listening, 1 = muted; at least one stays on) and **TX** buttons (`FT2;` MAIN / `FT3;` SUB; the answer to `FT;` is 0 / 1). Listening to one receiver and transmitting on the other is split,
* **MAIN ↔ SUB** swaps the two frequencies, **Set MAIN frequency** and **Set SUB frequency** sit side by side in Band select.

The rear / USB audio carries **one receiver at a time**, chosen per mode family by the menus *SSB / AM / FM / DATA / RTTY / CW OUT SELECT* (`EX010108`, `EX010208`, `EX010308`, `EX010410`,
`EX010510`, `EX020108`; 0 = MAIN, 1 = SUB). When only one receiver is listening the app sets all six to that receiver; when both listen it leaves them alone. Changing receivers on the radio's
own panel does not change them.

**Controls** (Filters & DSP panel; a MAIN | SUB switch at the top makes the per-receiver ones act on the chosen receiver, `set_control` carries `receiver`):

| Tab | Controls |
|---|---|
| Filter | Width, Narrow, IF Shift, Contour (+ frequency) |
| Noise | Manual notch (+ frequency), Auto notch, DNR (+ level), Noise blanker (the NB level, `NL`, is one setting for both receivers) |
| Receiver | IPO / Preamp, ATT, AGC, RIT, XIT, clarifier (RIT / XIT / clarifier belong to the whole radio) |
| Transmit | Monitor (+ level), Antenna tuner on / off, Mic input, **Processor** (+ level), **AMC output level** |
| Audio | **Audio out level** (SSB / AM / FM / DATA / RTTY / CW OUT LEVEL, written together) and **Audio in level** (RPORT GAIN of SSB / AM / FM) |
| CW | Keyer speed (`KS`, 4-60 WPM), CW pitch (`KP`, 300-1050 Hz in 10 Hz steps), Keyer, Break-in (`KR`, `BI`) |

Levels: RF gain and AF gain exist per receiver (`RG0` / `RG1`, `AG0` / `AG1`). **AF gain** is only the radio's own speaker volume (the app does not use it for remote audio); all 0-255 gains are shown as **0-100 %**.
**TUNE** works (`AC002;`, keys the transmitter: dummy load or antenna, the app asks first). **Memories** are listed, recalled (`MC`) and left with the radio's V/M key (`VM;`); nothing is ever written.

**Where the manual is wrong or unclear on this radio**

* **Speech processor (`PR`)**: the manual says 1 = OFF / 2 = ON. The radio uses **0 = OFF, 1 = ON** (`PR0` the processor, `PR1` the parametric mic equalizer) and rejects `PR02;` with `?;`. The processor level is `PL`.
* **The radio's PROC / PITCH knob** sets the **AMC output level** (`AO`, 1-100), shown on the radio as "AMC out", unless the menu *OPERATION SETTING → TX AUDIO → PROC LEVEL* is set to COMP; then it sets the processor level (`PL`).
  The processor on / off is the MIC / SPEED knob (press).
* **`SD` (break-in delay)** is answered with two digits (`SD04`), not the four digits the manual shows, so it is not offered.
* `GT` answers AUTO as 4, 5 or 6 (AUTO-FAST, -MID, -SLOW); the app shows AUTO.

**Not tried yet:** transmitting on the SUB receiver on the air, the DATA / RTTY / CW audio-source menus with a signal, and the MP.

## How a model is selected

`radio.model` (Admin > Config > Radio > Model, restart needed) picks, for that model:

1. its **capability file** (`radio/capabilities/<model>.toml`): which functions the UI offers (only `true` entries; `"unverified"` is hidden);
2. its **protocol object** (`radio/cat/proto.py`): the exact commands, answer layouts, mode codes, width tables, level ranges and the
   menu (EX) items for that radio. The FT-991A keeps its own, bench-verified protocol object; the four newer radios share one
   (`HfProto`) whose per-model differences are plain data (`HfModel`).

The driver (`YaesuCatDriver`) never builds a command itself, so an FT-991A command can never reach another radio, and the ID answer
(`ID0761;` FTDX10, `ID0681;` FTDX101D, `ID0682;` FTDX101MP, `ID0800;` FT-710) is checked on every connect: a different radio is refused.

## What is the same as the FT-991A and what is not (from the manuals)

Same: `FA FB` (9 digits, 30 kHz-75 MHz), `IF`/`OI` layout (25 characters), `TX0/1`, `AG0 RG0` (0-255), `MG` (0-100), `PC` (5-100 W, **5-200 W on
the FTDX101MP**), `SM0`, `RM` selectors (3 COMP, 4 ALC, 5 PO, 6 SWR), `NA NB NL NR RL BC BP CO PA ML AC` formats, `BS` band codes 00-10,
`AB BA SV`, `PS` (dummy data, then PS1 after 1-2 s), `RT XT RU RD RC` (not on the FT-710).

Different (and handled by the protocol object):

| Topic | FT-991A | Newer radios |
|---|---|---|
| ID | 0670 | 0761 / 0681 / 0682 / 0800 |
| Mode codes | up to `E` = C4FM | up to `F`; `E` = PSK, `F` = DATA-FM-N, no C4FM |
| `RM` answer | `RM4xxx;` | `RM4xxx000;` (7 characters) |
| Split | `FT3;`/`FT2;` | `ST1;`/`ST0;` on the FTDX10 / FT-710. On the FTDX101 `FT2;` / `FT3;` choose the MAIN or SUB receiver as the transmitter (the TX buttons) |
| IF shift | `IS0+0000` | `IS00+0000`, 0-1200 Hz in 20 Hz steps |
| Attenuator | on/off | `RA0` 0-3 = OFF / 6 / 12 / 18 dB (control `att_level`) |
| Width | `SH0nn` with narrow/wide tables | `SH00nn`, Table 3 per mode class (differs per radio) |
| AGC | (documented, now offered) | `GT0` + 0..4 (OFF/FAST/MID/SLOW/AUTO); the answer reports AUTO as 4/5/6 |
| Mic source menu | `EX106` (SSB MIC SELECT, 0 MIC / 1 REAR) | FTDX10 `EX010113`, FTDX101 `EX010111` (SSB MOD SOURCE), FT-710 `EX010114` (MOD SOURCE, 1 = USB) |
| Tune | `AC002` start/stop toggle | FTDX10/101 same; FT-710: `AC003` start, `AC000` stop |

## What is deliberately NOT offered yet (`"unverified"` in the capability file)

This list is for the radios that have **not** been tried (FTDX10, FT-710); the FTDX101D (and the MP, which uses the same commands) have these enabled as described above.

* **Speech processor** (`PR`/`PL`): the manual says `PR` 1 = OFF / 2 = ON, which is wrong on the FT-991A and on the FTDX101D (it is 0/1). Verify on the radio first.
* **TUNE** (`tuner_tune`): it keys the transmitter. Enable it only after a dummy-load test, by setting `tuner_tune = true` in the model's toml.
* **Split** (`ST`) on the FTDX101: the TX buttons (`FT`) cover the MAIN / SUB case; the radio's own VFO-A / VFO-B split inside one receiver is not offered.
* **FT-710 clarifier** (only the `CF` command is in its manual) and quick split (no `QS`).
* **APF** (CW only, did nothing on the FT-991A) and **C4FM / DG-ID** (FT-991A only).
* Roofing filter (`RF`), the spectrum scope, CW message keying (`KY`, it transmits), VOX, the break-in delay: not implemented.

## Practical notes

* **Two serial ports.** The radios have a *Dual* CP210x bridge: the **Enhanced** COM port is CAT, the **Standard** one is PTT/keying. Auto-detect
  probes every Silicon Labs port with `ID;` and keeps the one that answers; if it picks wrongly, set `radio.serial_port` explicitly. The
  "Power on radio" button also needs the CAT port: set `radio.serial_port` explicitly for these radios.
* **Baud.** Set the radio's CAT RATE menu and `radio.baud` to the same value (38400 is the app's default). If the radio does not answer at all, check its **CAT RTS** menu (it must be OFF, see the FTDX101D section);
  the log then says which ports and rates were tried.
* **Receive audio level to the Pi** (USB OUT LEVEL menus, 0-100): FTDX10 `EX010111` (SSB OUT LEVEL); FTDX101 `EX010109`; FT-710 `EX010111`
  (USB OUT LEVEL); on the FTDX101D the app has an **Audio out level** slider for it. Raise it on the radio instead of boosting `audio.rx_gain_db`. Transmit audio over USB also needs the radio's
  *REAR SELECT = USB* menu (FTDX10 `EX010114`, FTDX101 `EX010112`) and, on the FT-710, MOD SOURCE = USB.

## First contact checklist (do it in this order, report each step)

1. Stop the service, run the read-only script and keep the output:
   ```
   python scripts/bench_hf_readonly.py ftdx10        # or ftdx101d / ftdx101mp / ft710
   ```
   Every line shows the command, the radio's answer and what the decoder made of it. A `?;` or a "(decoder: ...)" note is exactly what to fix.
2. Start the service with `radio.model` set, check the banner, the frequency, mode, band and the S-meter.
3. Change frequency, mode, band; switch NB / DNR / notch / contour; try the AGC, ATT and IF-shift controls.
4. Into a **dummy load**: PTT (a short key-up), meters (power, SWR, ALC), mic source REAR / MIC.
5. Only then consider TUNE, the clarifier, split and the speech processor.

Every function confirmed this way should be written into `docs/bench-results.md` and the capability toml flipped to `bench_tested = true`.
