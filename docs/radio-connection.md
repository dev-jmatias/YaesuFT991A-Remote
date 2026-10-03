# Connecting the radio

**FT-991A:** complete and tested on a real radio. **FTDX10, FTDX101D, FTDX101MP and FT-710** have experimental profiles (written from their CAT
manuals, tested only against a simulator): [other radios](08-other-radios.md). A built-in simulator ("mock") lets you try everything without a radio.
What each radio supports: [capability matrix](01-capability-matrix.md).

## USB cable

Connect the radio's rear USB jack straight to the Pi (a short, good-quality cable; avoid unpowered hubs). Switch the radio on.

```bash
lsusb                       # expect a "Silicon Labs CP210x" entry (CAT) and a "USB Audio CODEC"-style entry
ls /dev/serial/by-id/       # the FT-991A's USB bridge presents TWO serial ports (CAT and a second one)
arecord -l ; aplay -l       # the radio's sound card, e.g. "card 1: CODEC [USB Audio CODEC]"
```

The app finds the CAT port itself: it probes only Silicon Labs USB serial ports, sending the read-only `ID;` and
accepting the one that answers `ID0670;` (FT-991A). You can also set `radio.serial_port` explicitly (Admin > Config). The service user must be in
group `dialout` (the installer does this). **Only one program may use the port**: close any other CAT software. If the radio is switched off
or unplugged the page shows it as offline and reconnects by itself.

## Radio menu settings (FT-991A)

The software never changes the radio's menus; you set them once on the radio. Menu numbers are from the official CAT manual (1711-D).

| Menu | Setting | Recommendation |
|---|---|---|
| 031 CAT RATE | 4800 / 9600 / 19200 / 38400 | **38400** (the default of the app; if you change it set `radio.baud` to match) |
| 036 TX TOT | OFF, 1-30 min | **set a value (e.g. 3 min)**: the radio's own backstop if the Pi ever fails |
| 047, 071, 076, 108 (PTT select) | DAKY / RTS / DTR | **DAKY**, so nothing can key the radio by toggling the RTS/DTR serial lines. The app opens the port with DTR/RTS low regardless |
| 106 SSB MIC SELECT | MIC / REAR | **REAR** for remote operation. The **REAR / MIC** buttons above the PTT button in the app flip this menu for you (MIC is for operating at the radio) |
| 107 SSB OUT LEVEL (and 073 DATA, 046 AM, 075 FM, 054 CW, 099 RTTY) | 0-100, default 50 | the **receive level sent to the Pi** over USB. If the browser audio is too quiet, raise it (90 was right on the tested radio) before using `audio.rx_gain_db` |
| 032 CAT TOT, 033 CAT RTS | | leave at the defaults; raise CAT TOT only if you see CAT timeouts |

Other menus that decide where transmit audio comes from and where receive audio goes (070 DATA IN SELECT, 072 DATA PORT SELECT,
109 SSB PORT SELECT and the AM/FM equivalents) are listed in the manual but without a ready recipe: if your audio does not flow, follow the
steps in [audio](04-audio.md) and change one menu at a time.

### Powering the radio on and off

*Power off* is an administrator button in the page (`PS0;`). *Power on radio* appears in the offline banner when the radio is in standby
(the server sends dummy data, waits 1.4 s, then `PS1;`, as the manual requires). **Both work over USB on the FT-991A**, because its USB serial port
stays available in standby while the DC supply is on.

## Config keys involved

```toml
[radio]
model = "ft991a"
serial_port = "auto"        # or "/dev/ttyUSB0"; stable names are under /dev/serial/by-id/
baud = 38400

[audio]
backend = "auto"            # real radio -> alsa
input_device = ""           # radio -> Pi, e.g. "plughw:CARD=CODEC,DEV=0"; empty = auto when exactly one USB audio CODEC exists
output_device = ""          # Pi -> radio
```

A device name is an ALSA name such as `plughw:CARD=CODEC,DEV=0`, **not** a bare number (a number like `0` is refused). Leave both empty unless
auto-detection fails.
