# Other radios: FTDX10, FTDX101D, FTDX101MP, FT-710 (EXPERIMENTAL)

These four profiles exist **only on paper**: they were written from the Yaesu CAT manuals (FTDX10 2308-F, FTDX101MP/D 2101-I,
FT-710 2306-C) and no such radio was available for testing. The app shows an amber *EXPERIMENTAL PROFILE* banner whenever one of them is
selected (`bench_tested = false` in its capability file). Nothing here has been seen working on a real radio.

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
| Split | `FT3;`/`FT2;` | `ST1;`/`ST0;` (`FT` is the main/sub transmitter selector, never sent) |
| IF shift | `IS0+0000` | `IS00+0000`, 0-1200 Hz in 20 Hz steps |
| Attenuator | on/off | `RA0` 0-3 = OFF / 6 / 12 / 18 dB (control `att_level`) |
| Width | `SH0nn` with narrow/wide tables | `SH00nn`, Table 3 per mode class (differs per radio) |
| AGC | (documented, now offered) | `GT0` + 0..4 (OFF/FAST/MID/SLOW/AUTO); the answer reports AUTO as 4/5/6 |
| Mic source menu | `EX106` (SSB MIC SELECT, 0 MIC / 1 REAR) | FTDX10 `EX010113`, FTDX101 `EX010111` (SSB MOD SOURCE), FT-710 `EX010114` (MOD SOURCE, 1 = USB) |
| Tune | `AC002` start/stop toggle | FTDX10/101 same; FT-710: `AC003` start, `AC000` stop |

## What is deliberately NOT offered yet (`"unverified"` in the capability file)

* **Speech processor** (`PR`/`PL`): the manual says `PR` 1 = OFF / 2 = ON, which was wrong on the FT-991A (it is 0/1). Verify on the radio first.
* **TUNE** (`tuner_tune`): it keys the transmitter. Enable it only after a dummy-load test, by setting `tuner_tune = true` in the model's toml.
* **FTDX101D/MP VFO B and split**: these radios have two receivers; `FB` is the SUB band frequency, not a VFO B. Only the MAIN receiver is
  controlled (every command is sent with receiver 0).
* **FT-710 clarifier** (only the `CF` command is in its manual) and quick split (no `QS`).
* **APF** (CW only, did nothing on the FT-991A) and **C4FM / DG-ID** (FT-991A only).
* Roofing filter (`RF`), the spectrum scope, memories, CW keyer: not implemented.

## Practical notes

* **Two serial ports.** The radios have a *Dual* CP210x bridge: the **Enhanced** COM port is CAT, the **Standard** one is PTT/keying. Auto-detect
  probes every Silicon Labs port with `ID;` and keeps the one that answers; if it picks wrongly, set `radio.serial_port` explicitly. The
  "Power on radio" button also needs the CAT port: set `radio.serial_port` explicitly for these radios.
* **Baud.** Set the radio's CAT RATE menu and `radio.baud` to the same value (38400 is the app's default).
* **Receive audio level to the Pi** (USB OUT LEVEL menus, 0-100): FTDX10 `EX010111` (SSB OUT LEVEL); FTDX101 `EX010109`; FT-710 `EX010111`
  (USB OUT LEVEL). Raise it on the radio instead of boosting `audio.rx_gain_db`. Transmit audio over USB also needs the radio's
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
