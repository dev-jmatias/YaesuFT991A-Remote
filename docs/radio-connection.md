# Connecting the radio

**Implemented drivers today: FT-991A (and the built-in simulator "mock").** The FTDX101D/MP, FTDX10 and FT-710 CAT manuals
are in hand but their drivers are not written yet; selecting them is refused. See [07-next-revisions.md](07-next-revisions.md).

## USB cable

Connect the radio's rear USB jack straight to the Pi (a short, good-quality cable; avoid unpowered hubs). Switch the radio on.

```bash
lsusb                       # expect a "Silicon Labs CP210x" entry (CAT) and a "USB Audio CODEC"-style entry
ls /dev/serial/by-id/       # the FT-991A's USB bridge presents TWO serial ports (CAT and a second one)
arecord -l ; aplay -l       # the radio's sound card, e.g. "card 2: CODEC [USB Audio CODEC]"
```

The app finds the CAT port itself: it probes only Silicon Labs USB serial ports, sending the read-only `ID;` and
accepting the one that answers `ID0670;`. You can also set `radio.serial_port` explicitly. The service user must be in
group `dialout` (the installer does this). **Only one program may use the port**: close any other CAT software.

## Radio menu settings (FT-991A)

Menu numbers and ranges below are from the official CAT manual (1711-D). Items marked **verify** are not specified well
enough in the manual and need checking on your radio. The software never changes menus.

| Menu | Setting | Recommendation |
|---|---|---|
| 031 CAT RATE | 4800 / 9600 / 19200 / 38400 | 38400; set `radio.baud` to match (the app also tries the other rates while detecting) |
| 032 CAT TOT | 10 ms / 100 ms / 1 s / 3 s | leave default; raise it if you see CAT timeouts (**verify**) |
| 033 CAT RTS | disable / enable | leave default (**verify** behaviour over USB) |
| 036 TX TOT | OFF, 1-30 min | **set a value (e.g. 3 min)**: the radio's own backstop if the Pi ever fails |
| 047, 071, 076, 108 (PTT select) | DAKY / RTS / DTR | **DAKY**, so nothing can key the radio by toggling the RTS/DTR serial lines. The app opens the port with DTR/RTS low regardless |

### USB audio routing (**verify on the bench**)

The menus that decide where transmit audio comes from and where receive audio goes are: 070 DATA IN SELECT, 072 DATA PORT
SELECT, 073 DATA OUT LEVEL, 106 SSB MIC SELECT, 107 SSB OUT LEVEL, 109 SSB PORT SELECT (and the AM/FM equivalents 045-049, 074-078).
The manual lists their values but not a working "USB audio into the transmitter" recipe. Follow the audio bench in
[04-audio.md](04-audio.md): confirm receive audio first, then transmit audio with a dummy load and PTT disabled.

### Powering the radio on

`PS0;` (off) is implemented, as an admin-only action. **Powering the radio on over USB is not implemented**: the manual
documents `PS1;` but it is unknown whether the USB serial port is alive while the radio is in standby. This is bench test 3
in [03-bench-checklist.md](03-bench-checklist.md); an external relay is a fallback that has not been designed.

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
