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

You set these once on the radio. The only menus the program changes by itself are the **microphone source** ones (below), when you press the microphone button or the last operator leaves.
Menu numbers are from the official CAT manual (1711-D).

**Basics (every use)**

| Menu | Setting | Value |
|---|---|---|
| 031 CAT RATE | 4800 / 9600 / 19200 / 38400 | **38400** (the default of the app; if you change it set `radio.baud` to match) |
| 036 TX TOT | OFF, 1-30 min | **set a value (e.g. 3 min)**: the radio's own backstop if the Pi ever fails |
| 047, 071, 076, 108 (PTT select) | DAKY / RTS / DTR | **DAKY**, so nothing can key the radio by toggling the RTS/DTR serial lines. The app opens the port with DTR/RTS low regardless |
| 033 CAT RTS | ENABLE / DISABLE | **DISABLE**. With ENABLE the radio only talks while the RTS line is high, and the app never raises RTS (it could key the radio); the radio then stays silent and the app says "radio offline" (seen after a factory reset) |
| 032 CAT TOT | | leave at the default; raise it only if you see CAT timeouts |

**Microphone source: managed by the app** (0 = MIC, 1 = REAR)

| Menu | Mode | Remote in use (microphone button on) | Nobody remote (at the radio) |
|---|---|---|---|
| 106 SSB MIC SELECT | SSB | REAR | **MIC** |
| 045 AM MIC SELECT | AM | REAR | **MIC** |
| 074 FM MIC SELECT | FM, C4FM | REAR | **MIC** |
| 070 DATA IN SELECT | DATA modes | REAR | **stays REAR** (it only matters in the DATA modes) |

**Where the audio comes in and goes out: USB** (bench-found on a real FT-991A after a reset: the app keyed the radio but no RF came out, because the port menus were on DATA)

| Menu | Mode | Value |
|---|---|---|
| 109 SSB PORT SELECT | SSB | **USB** (1) |
| 048 AM PORT SELECT | AM | **USB** (1) |
| 077 FM PORT SELECT | FM, C4FM | **USB** (1) |
| 072 DATA PORT SELECT | DATA modes | **USB** (1; see the FreeDV table) |

If PTT from the app keys the radio but an external power meter shows nothing (in SSB, no audio means no RF), check these first.

**Receive level sent to the Pi** (0-100, default 50): 107 SSB OUT LEVEL in USB/LSB, **073 DATA OUT LEVEL** in the DATA modes (and 046 AM, 075 FM, 054 CW, 099 RTTY). If the browser audio is too quiet,
raise it (90 was right on one tested radio, 50 on another) before using `audio.rx_gain_db`.

**FreeDV in the DATA modes** (the FreeDV guide's setup; switch on the FreeDV tab's DATA option after setting them)

| Menu | Setting | Value |
|---|---|---|
| 059 CW FREQ DISPLAY | | **DIRECT FREQ** |
| 062 DATA MODE | PSK / OTHER | **OTHER** |
| 064 OTHER DISP (SSB), 065 OTHER SHIFT (SSB) | | **+1500 Hz** both |
| 066 DATA LCUT FREQ, 068 DATA HCUT FREQ | | **OFF** both |
| 070 DATA IN SELECT | MIC / REAR | **REAR** |
| 071 DATA PTT SELECT | | **DAKY** |
| 072 DATA PORT SELECT | DATA / USB | **USB** (the manual numbers it 1 = DATA, 2 = USB, but a real radio took 0 = DATA, 1 = USB: check the display) |

Not set for FreeDV, but worth knowing: 110 SSB TX BPF narrows the SSB transmit audio (it matters only if you use plain USB/LSB for FreeDV, which is why the DATA modes are recommended), and the
speech processor must be off.

Other menus that decide where transmit audio comes from and where receive audio goes (109 SSB PORT SELECT and the AM/FM equivalents) are listed in the manual but without a ready recipe: if your audio does not flow, follow the
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
