# Status and roadmap

What is finished, what is verified on a real radio, and what is still open. For the details of each test see [bench results](bench-results.md);
for what each radio supports see the [capability matrix](01-capability-matrix.md).

## Done and verified on a real FT-991A (Raspberry Pi 4)

- Frequency, mode, band, VFO B (A=B, B=A, swap, set frequency, mode), split, RIT/XIT, memory channels (list and recall).
- Receive filters and DSP: width, IF shift, contour, notches, noise reduction, noise blanker, IPO / preamp, ATT, AGC.
- Meters: S-meter, power, SWR (with a warning limit), ALC and COMP (ALC scale calibrated from one point).
- PTT with the server-side safeguards, the transmit switch in Admin > Config, Lock PTT, TUNE, the REAR / MIC input switch.
- Remote audio both ways, Bluetooth headsets, background listening on phones; power off and power on over USB.
- Accounts, roles, the control lease, trusted users, the audit log, settings backup and restore.
- Installation: the ready-made image (also through Raspberry Pi Imager's own screens), the installer pack, in-app update notice and `self_update.sh`
  with automatic rollback, Tailscale access (`tailscale_setup.sh`).

## Done, but only against the simulator

- **FTDX10, FTDX101D/MP and FT-710**: profiles written from their CAT manuals and marked experimental. They need someone with the radio to run the
  first-contact checklist in [other radios](08-other-radios.md).
- Quick split (`QS;`) on the FT-991A.

## Built, not yet released (simulator and real Hamlib `rigctl` only)

- **Logbook link** ([logbook](logbook.md)): a Hamlib "NET rigctl" server on the Pi (port 4532, home network only, off by default) so a logbook
  such as Log4OM can follow the radio and change frequency and mode. PTT and power are blocked for it. Tested against Hamlib 4.6.2 `rigctl`;
  a test with Log4OM itself on the real radio is still to do. WSJT-X transmitting through the Pi is out of scope.

## Open

| Item | Why it is open |
|---|---|
| WebSocket audio fallback | for networks where WebRTC (UDP) is blocked; not built |
| Meter calibration | S-meter, power and SWR are shown from raw values (SWR ratio is an estimate on the FT-991A); ALC has one calibration point |
| Audio latency | not measured |
| Antenna switching | the FT-991A has no CAT command for it |
| C4FM RX/TX DG-ID | not reachable over CAT (checked on the radio); only the WIRES DG-ID menu value is read |
| Writing memories from the page | deliberately not built: the program never changes a memory |
| Automatic rollback test, `restore.sh`, `uninstall.sh` | scripts exist; a full real-Pi run of each is still to be recorded |
| Real-device UI testing | tablets, iPhone/iPad and PWA install have had little testing |
| Colour themes / colour-blind mode | the dark theme is the only one |
