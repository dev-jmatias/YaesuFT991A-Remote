# Radio Remote PTT helper for macOS

Chrome on a Mac cannot read the PTT button of Bluetooth speaker-mics that send it over the Bluetooth
Serial Port Profile (for example `+PTT=P` when pressed and `+PTT=R` when released, the "B01PTT" type).
This helper reads it natively and hands it to the Radio Remote page.

1. Pair the device with the Mac (System Settings > Bluetooth).
2. Build once: `swiftc -O -o ptt-bridge ptt-bridge.swift` (needs the Xcode command line tools).
3. Run `./ptt-bridge` and leave it running. The first time, allow Bluetooth for Terminal when macOS asks.
4. In Radio Remote, Audio tab, press **Bind PTT** and then the device's button.

Options: `--list` (paired devices), `--name TEXT` (only use devices whose name contains TEXT; also wakes that
device), `--port N`, `--simulate` (no Bluetooth: type p / r / x / c and Enter).

The helper listens on `127.0.0.1` only and sends button state one way; nothing a web page sends to it is acted on.
