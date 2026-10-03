# Phase 1 — Research and Capability Matrix

Status: **draft for review**. Nothing in this document is implemented yet.

## 1. Sources actually inspected

| Source | Location | Used for | Authority |
|---|---|---|---|
| FT-991A CAT Operation Reference Manual (1711-D), 20 pp. | `...\991A\FT-991A_CAT_OM_ENG_1711-D.pdf` | Every FT-991A CAT claim below | **Official, authoritative** |
| SCU-LAN10 Operation Manual (2311-D) | `...\991A\SCU-LAN10_...pdf` | Which radios Yaesu's own network-remote unit supports; remote power-on behaviour | Official |
| `AH067_V0112.dat` | `...\991A\` | Not inspected: opaque binary, looks like a firmware image. Not needed. Never flash anything from this project. | n/a |
| `Yaesu_Web_Control-main` (YWC, C#/.NET, **GPL-3.0**) | `...\991A\Yaesu_Web_Control-main` | Cross-check only (model IDs, field reports). **No code is copied.** Our project is a clean-room design; GPL-3.0 would bind any derivative. Third-party claims are *never* marked Confirmed. | Secondary / hearsay |

**Not available to me — required before I can verify the other four radios:**

1. `FTDX101D/MP CAT Operation Reference Manual`
2. `FTDX10 CAT Operation Reference Manual`
3. `FT-710 CAT Operation Reference Manual`
4. (Nice to have) FT-991A *Operation Manual* — for the USB/audio/menu routing chapters, and the exact power-switch behaviour in standby.

Until those exist, every FTDX101/FTDX10/FT-710 command below is **REQUIRES VERIFICATION**. I will not write a driver for them from memory.

## 2. Legend

| Tag | Meaning |
|---|---|
| **Confirmed** | Present in the official manual I read (page cited). Safe to implement; still bench-tested on hardware. |
| **Model-specific** | Exists but differs per model; each driver must implement it separately. |
| **Direct CAT** | Documented Yaesu CAT command that Hamlib does not (or cannot be assumed to) expose. |
| **External HW** | Cannot be done over CAT; needs additional hardware. |
| **Not supported** | Absent from the official command list, or the hardware does not exist. |
| **REQUIRES VERIFICATION** | I have not seen documentation sufficient to assert it. Not implemented until verified. |

## 3. How each path was assessed

- **Hamlib**: I know Hamlib has a `newcat` Yaesu backend covering the FT-991(A), FTDX101D/MP, FTDX10 and FT-710. I will **not** claim a specific function works until checked against the Hamlib 4.7 installed on the Pi (`rigctl -m <id> --dump-caps`, and `rigctl -l` for the model IDs). All "Hamlib" cells are therefore **REQUIRES VERIFICATION** by design; this is a one-minute check at install time that the driver layer will automate (`hamlib_caps_probe`).
- **Direct CAT**: ASCII frames ending in `;`, 2-letter opcode. Full detail for the FT-991A below.
- **USB audio**: all five radios present a USB audio codec alongside USB serial (the 991A manual p.1 confirms "USB to Dual UART Bridge"; the audio codec is from the operating manual → **REQUIRES VERIFICATION** until I have it).

## 4. FT-991A — verified against the official CAT manual

Page numbers are the manual's printed page numbers. `P1`… are parameters.

### 4.1 Connection

| Item | Finding | Tag |
|---|---|---|
| Interface | USB: "built-in USB to Dual UART Bridge" (two COM ports: one is CAT, the other is the "standard" port, used for e.g. PTT-by-RTS/DTR and data). Which one is which must be detected, not assumed. p.1 | Confirmed |
| Baud | Menu **031 CAT RATE**: 4800 / 9600 / 19200 / 38400 bps. Menu **032 CAT TOT**: 10 ms / 100 ms / 1 s / 3 s. Menu **033 CAT RTS**: disable/enable. Readable/writable with `EX`. p.7–8 | Confirmed |
| Framing | `CC` + params + `;`. Set / Read / Answer forms. Wrong digit count ⇒ command rejected. p.2 | Confirmed |
| Identification | `ID;` → `ID0670;` for FT-991A. p.10 | Confirmed |
| Push updates | `AI1;` enables Auto Information; **radio resets it to 0 at power-off**, so it must be re-sent on every (re)connect. p.4 | Confirmed |
| One-shot status | `IF;` returns memory ch, VFO-A freq, clarifier, mode, VFO/mem state, CTCSS, shift in one frame. `OI;` same for the opposite band. p.10, 13 | Confirmed |
| Error reply | The manual shows no error frame; the radio answers `?;` to bad commands (**REQUIRES VERIFICATION** on hardware). | REQUIRES VERIFICATION |

Documentation defects noticed (to be bench-checked, not silently "fixed"): the NARROW command is printed as `MA P1 P2` on p.13 but is listed as `NA` in the index on p.3; I will treat `NA` as correct and verify. The `GT` (AGC) set/answer parameter mapping differs between the set table and answer (set P2 vs answer P3, and the 0–4 vs 0–6 value lists disagree), so AGC mode mapping must be verified on hardware.

### 4.2 Capability matrix: FT-991A

| Feature | CAT command(s) (manual p.) | Status | Hamlib | Notes |
|---|---|---|---|---|
| Frequency VFO-A / VFO-B read/set | `FA`/`FB` 9-digit Hz, 30 kHz–470 MHz (p.9) | **Confirmed** | REQUIRES VERIFICATION | Radio's coverage has gaps (RX 30 kHz–56 MHz, 118–164, 420–470 per YWC; **not** from the CAT manual → REQUIRES VERIFICATION against operating manual). |
| Band select / up / down | `BS` 00–16 (1.8,3.5,5,7,10,14,18,21,24.5,28,50 MHz, GEN, MW, AIR, 144, 430), `BU`/`BD` (p.5) | **Confirmed** | REQUIRES VERIFICATION | AIR/MW/GEN are not amateur bands; UI will hide them. |
| VHF / UHF | `BS` 15/16; frequencies via `FA` (p.5, 9) | **Confirmed** | REQUIRES VERIFICATION | |
| Mode | `MD0` P2: 1 LSB, 2 USB, 3 CW-U, 4 FM, 5 AM, 6 RTTY-L, 7 CW-L, 8 DATA-L, 9 RTTY-U, A DATA-FM, B FM-N, C DATA-USB, D AM-N, E **C4FM** (p.11) | **Confirmed** | REQUIRES VERIFICATION | Note `IF` reply uses same codes but labels 3/7 as CW/CW-R. |
| C4FM | `MD0E` selects mode. | **Confirmed (mode select only)** | REQUIRES VERIFICATION | No CAT command for DG-ID, GM, WIRES-X, digital voice; only menu items 088–090, 153 via `EX`. |
| A/B, swap, split | `AB`, `BA`, `SV`; `FT`/`TS` (TX VFO, TXW), `QS` quick split (p.3,9,17) | **Confirmed** | REQUIRES VERIFICATION | |
| Meter: S | `SM0;` 000–255 (p.17); `RM1` | **Confirmed** | REQUIRES VERIFICATION | Raw value → S-units needs a calibration table (**not in the manual**). |
| Meter: PO, SWR, ALC, COMP, ID, VDD | `RM` P1: 3 COMP, 4 ALC, 5 PO, 6 SWR, 7 ID, 8 VDD; 0–255 (p.15). `MS` selects the front-panel meter (p.12) | **Confirmed** | REQUIRES VERIFICATION | TX meters only meaningful while transmitting. Raw→watts/SWR calibration is **not** documented → needs bench measurement. |
| RX audio / TX audio level meters | none | **Not supported over CAT** | n/a | Computed on the Pi from the audio stream (software). |
| PTT | `TX1;` = CAT TX on, `TX0;` off, `TX;` read returns 0/1/2 (2 = radio keyed itself) (p.17). Also `MX0/1` (MOX). | **Confirmed** | REQUIRES VERIFICATION | Reading `2` lets us detect local keying. |
| AF gain | `AG0` 000–255 (p.4) | **Confirmed** | REQUIRES VERIFICATION | |
| RF gain | `RG0` 000–255 (p.15) | **Confirmed** | REQUIRES VERIFICATION | |
| Squelch | `SQ0` 000–100 (p.17) | **Confirmed** | REQUIRES VERIFICATION | Note: front-panel "RF/SQL" knob behaviour is a menu setting; not exposed. |
| Mic gain | `MG` 000–100 (p.11) | **Confirmed** | REQUIRES VERIFICATION | |
| Monitor level | `ML0` on/off, `ML1` level 000–100 (p.12) | **Confirmed** | REQUIRES VERIFICATION | |
| Speech processor | `PR0` on/off, `PL` level 000–100 (p.14); `PR1` = parametric mic EQ on/off | **Confirmed** | REQUIRES VERIFICATION | Manual lists the PR title as "SPEECH PROCESSOR LEVEL" for PR; field list shows on/off. |
| Compression (level) | same as processor level; COMP meter `RM3` | **Confirmed** | REQUIRES VERIFICATION | |
| AGC | `GT0` (p.10) | **Confirmed, mapping REQUIRES VERIFICATION** | REQUIRES VERIFICATION | See defect note. Delay times via `EX` 001–003. |
| RX bandwidth / WIDTH | `SH0` code 00–21, table maps code → Hz per mode class (SSB/CW/RTTY-PSK, narrow/wide) (p.16) | **Confirmed** | REQUIRES VERIFICATION | Table is mode-class-dependent; driver must carry the table. `NA0` = narrow filter on/off (p.13). |
| IF SHIFT | `IS0` ±1200 Hz, 20 Hz steps, **sign+4 digits** (p.10) | **Confirmed** | REQUIRES VERIFICATION | |
| CONTOUR | `CO0` P2=0 on/off, P2=1 freq 10–3200 Hz (p.5) | **Confirmed** | REQUIRES VERIFICATION | Level/width via `EX` 112 / 113. |
| APF | `CO` P2=2 on/off, P2=3 offset −250…+250 Hz | **Confirmed** | REQUIRES VERIFICATION | Applies to CW only (**REQUIRES VERIFICATION** which modes). |
| Manual NOTCH | `BP00` on/off, `BP01` freq 001–320 (×10 Hz) (p.5) | **Confirmed** | REQUIRES VERIFICATION | `EX114` notch width. |
| Auto NOTCH ("DNF") | `BC0` on/off (p.4) | **Confirmed**; front-panel name equivalence to "DNF" REQUIRES VERIFICATION | REQUIRES VERIFICATION | |
| DNR / Noise Reduction | `NR0` on/off, `RL0` level 01–15 (p.13,15) | **Confirmed** | REQUIRES VERIFICATION | |
| Noise Blanker | `NB0` on/off, `NL0` level 000–010 (p.13); `EX` 023–025 width/rejection/level | **Confirmed** | REQUIRES VERIFICATION | |
| IPO / preamp | `PA0` 0 IPO, 1 AMP1, 2 AMP2 (p.14) | **Confirmed** | REQUIRES VERIFICATION | |
| ATT | `RA0` on/off (p.15) | **Confirmed** | REQUIRES VERIFICATION | |
| RIT / clarifier | `RT` on/off, `RU`/`RD` offset ±0–9999 Hz, `RC` clear (p.16) | **Confirmed** | REQUIRES VERIFICATION | `IF` frame carries offset+state (p.10). `EX040` selects RX/TX/TRX clarifier mode. |
| XIT | `XT` TX clar on/off (p.18) | **Confirmed** | REQUIRES VERIFICATION | |
| TX bandwidth | Only via menu `EX110 SSB TX BPF` (0: 50–3000 … 4: 400–2600 Hz) (p.8) | **Confirmed (menu)** | **Direct CAT** | No dedicated TX-width command. Changing menus remotely alters radio state; require explicit admin opt-in. |
| IF bandwidth (distinct from WIDTH) | none distinct from `SH` | **Not supported** (same as WIDTH) | n/a | |
| Antenna 1/2 selection | **no antenna-select opcode in the 20-page command list** (p.3) | **Not supported via CAT** | n/a | The radio has separate HF/50 and VHF/UHF jacks, selected by band automatically. Future external relay possible. |
| ATAS / tuner | `AC` P3 0 off, 1 on, 2 start/stop tune (p.4); `EX141 TUNER SELECT` | **Confirmed** | REQUIRES VERIFICATION | Tuning keys the transmitter → must go through the TX safety gate. |
| TX power set | `PC` 005–100 W (p.14); band caps `EX137–140` (HF/50M 5–100, 144/430 5–50) | **Confirmed** | REQUIRES VERIFICATION | Server enforces per-band ceiling; UI slider 5–100 HF/50, 5–50 VHF/UHF. |
| TX timeout (radio-side) | `EX036 TX TOT` 0 (OFF)–30 min (p.8) | **Confirmed** | **Direct CAT** | Used as a radio-side backstop; read-only unless admin allows. |
| VOX | `VX`, `VG`, `VD` (+ `EX142–148`) (p.17–18) | **Confirmed** | REQUIRES VERIFICATION | **Disabled in UI by default**: VOX + remote audio = unintended TX risk. |
| CTCSS / DCS / repeater shift | `CT`, `CN`, `OS` (p.6, 13) | **Confirmed** | REQUIRES VERIFICATION | `OS` works in FM only. |
| Power OFF (software) | `PS0;` (p.14) | **Confirmed** | REQUIRES VERIFICATION | |
| Power ON (software) | `PS1;` documented: "requires dummy data be initially sent. Then after one second and before two seconds the command is sent." (p.14) | **Documented, but REQUIRES VERIFICATION on hardware** | n/a | The manual does not say whether the USB UART is alive while the radio is in standby. This is the single most important bench test for this project's power feature. |
| Power ON (external) | n/a | **External HW** fallback (see §6) | n/a | |
| Memory read/write/recall | `MC`, `MR`, `MT`, `MW`, `QI/QR`, `VM` | **Confirmed** | REQUIRES VERIFICATION | Out of initial scope. |
| Menus | `EX001–153` (p.7–10) | **Confirmed** | **Direct CAT** | Needed for audio routing (menus 070–077, 106–109), baud, TOT. |
| Lock | `LK` VFO-A dial lock | **Confirmed** | REQUIRES VERIFICATION | |
| Scope/waterfall | `SS`-style commands not in this manual | **Not supported / out of scope** | n/a | Explicitly excluded by the project. |
| USB audio device | Present (codec). Routing menus: `EX070 DATA IN SELECT`, `072 DATA PORT SELECT`, `106 SSB MIC SELECT`, `109 SSB PORT SELECT`, `073/107 OUT LEVEL`, `108/071 PTT SELECT` (p.9) | **Confirmed (menus exist)**; exact working combination REQUIRES VERIFICATION | n/a | The audio *port/route* menu semantics are cryptic ("DATA/USB", "MIC/REAR"); confirm in the operating manual and bench. |
| Auto-information push | `AI1;` | **Confirmed** | **Direct CAT** | Hamlib newcat polls instead; AI push reduces CAT traffic and lets us detect front-panel changes quickly. |

### 4.3 What this means for the FT-991A driver

- **Direct CAT is the primary path.** Everything required is in the official manual, including several things Hamlib does not abstract (IF-frame sync, `TX` read value 2, menu access, `AI1`, `PS`).
- The only items I will refuse to ship before bench validation: power-ON, AGC mapping, meter calibration, exact audio-route menu values, antenna-related UI (none shown).

## 5. The other four radios

All cells **REQUIRES VERIFICATION** because their CAT manuals were not provided. Facts I can state with their source:

| Fact | FTDX101D | FTDX101MP | FTDX10 | FT-710 | Source |
|---|---|---|---|---|---|
| Supported by Yaesu SCU-LAN10 network remote unit | Yes | Yes | Yes | Yes | SCU-LAN10 manual (provided). **FT-991A is not listed.** |
| SCU-LAN10 can power the transceiver ON remotely | Yes ("automatically powers ON") | Yes | Yes | Yes | SCU-LAN10 manual. Mechanism is Yaesu's hardware, proprietary → not replicable by us |
| Dual receiver (MAIN/SUB) | Yes | Yes | **REQUIRES VERIFICATION** (SCU manual shows MAIN only) | **REQUIRES VERIFICATION** | SCU-LAN10 manual |
| Max output power | 100 W | 200 W | 100 W | 100 W | **REQUIRES VERIFICATION** (YWC data; not an official source) |
| `ID;` response | **REQUIRES VERIFICATION** | `0682` appears in YWC notes (hearsay) | **REQUIRES VERIFICATION** | **REQUIRES VERIFICATION** | Needs the manuals |
| Antenna switching via CAT | **REQUIRES VERIFICATION** | **REQUIRES VERIFICATION** | **REQUIRES VERIFICATION** (YWC claims single ANT jack, unconfirmed) | **REQUIRES VERIFICATION** | — |
| VHF/UHF | No (HF/50 MHz) | No | No | No | **REQUIRES VERIFICATION** (general knowledge, no doc) |
| Hamlib `newcat` support | likely | likely | likely | likely (added in recent Hamlib) | **REQUIRES VERIFICATION** per above |
| Radio-side scope over CAT | Out of scope | Out of scope | Out of scope | Out of scope | project exclusion |

Feature matrix for these radios (all rows share one status, so listed once):

| Feature group | Status for all four | What I need |
|---|---|---|
| Frequency, mode, band, S-meter, PTT, AF/RF/SQL/MIC | **REQUIRES VERIFICATION** (command syntax differs from 991A in the newer family: per-receiver `P1` selectors, 8/9-digit frequency, etc.) | The CAT manual for each radio |
| WIDTH/SHIFT/CONTOUR/NOTCH/DNR/DNF/NB/IPO/ATT/AGC/APF/RIT/XIT | **REQUIRES VERIFICATION** | same |
| Meters (PO/SWR/ALC/COMP/…) and calibration | **REQUIRES VERIFICATION** | same + bench data |
| Antenna selection | **REQUIRES VERIFICATION** | same |
| Power OFF/ON over CAT | **REQUIRES VERIFICATION** | same |
| Power ON without Yaesu SCU-LAN10 | **External HW** or REQUIRES VERIFICATION | see §6 |
| Radio USB audio | **REQUIRES VERIFICATION** | operating manuals |

The abstraction will treat these radios as **declared-but-disabled drivers**: the capability file exists with every feature `false`/`unverified`, the UI shows only what is `verified: true`, and a driver cannot be enabled until a maintainer fills it from the manual. This satisfies "do not present fake controls".

## 6. Power control — separated as requested

| Layer | FT-991A | Others |
|---|---|---|
| 1. CAT software power **OFF** | `PS0;` — Confirmed (p.14). Safe: needs explicit confirm in UI, admin-only. | REQUIRES VERIFICATION |
| 2. CAT/wake power **ON** | `PS1;` with dummy byte + 1–2 s gap — documented (p.14), **hardware-unproven** whether the UART is powered in standby via USB. | REQUIRES VERIFICATION |
| 3. External hardware | Needed if (2) fails. Candidate designs **only documented, not built or wired by this project** until you approve: (a) Pi-controlled DC feed relay — only valid if the radio actually powers up when DC is applied (**REQUIRES VERIFICATION**); (b) a transistor/optocoupler across the front-panel POWER switch — needs opening the radio, **not recommended**; (c) a USB-serial adapter wired to the rear CAT/RS-232 jack (separate UART, may remain alive) — plausible for `PS1;` (**REQUIRES VERIFICATION**). | FTDX101/10/FT-710: Yaesu's SCU-LAN10 does this natively (confirmed by manual) — integration with it is out of scope. |

I will not activate any GPIO pin or recommend wiring until the bench tests for layer 2 are done and you approve a concrete schematic.

## 7. Features that need Direct CAT rather than Hamlib

(Independent of exact Hamlib coverage, which is unverified.)

1. `AI1;` auto-information push and `IF;`/`OI;` composite reads — reduces polling.
2. `TX;` read distinguishing CAT-keyed vs radio-keyed (value 2).
3. `EX` menu read/write: TX TOT, CAT RATE, audio routing, SSB TX BPF, notch/contour widths.
4. `PS` power on/off with its timing rule.
5. Mode-class-aware `SH` width table and `IS` shift format.
6. `RM` raw meters with a calibration layer.
7. Anything for FTDX101-family dual-receiver addressing (REQUIRES VERIFICATION).

Hamlib remains useful for: (a) a fallback `HamlibDriver` for radios where we don't yet have a verified native driver, (b) reporting the library version in diagnostics, (c) an optional rigctld-compatible listener for WSJT-X/etc. (see architecture doc).

## 8. Features that may require external hardware

| Feature | Why | Notes |
|---|---|---|
| Power ON (FT-991A, if `PS1;` fails in standby) | CAT port may be dead in standby | §6 |
| Antenna selection (FT-991A) | No CAT opcode | optional GPIO/network relay driver behind a `AntennaSwitch` interface; not enabled by default |
| Safe PTT dead-man (optional) | Final backstop if the Pi hangs | A hardware watchdog (e.g. relay opening the mic/PTT line) — optional and documented only; relies on radio menu `EX036 TX TOT` otherwise |
| USB audio isolation | Ground-loop hum | Passive USB isolator, mentioned in docs |
