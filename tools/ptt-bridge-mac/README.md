# Radio Remote PTT helper for macOS

Chrome on a Mac cannot read the PTT button of Bluetooth speaker-mics that send it over the Bluetooth
Serial Port Profile (for example `+PTT=P` when pressed and `+PTT=R` when released, the "B01PTT" type).
This helper reads it natively and hands it to the Radio Remote page.

**Use it:** download `radio-remote-ptt-mac.zip` from the latest release, unzip, open **Radio Remote PTT** once
(right-click > Open: it is not notarised), allow Bluetooth, and tick *Start at login* in its menu-bar icon.
Pair the device in System Settings > Bluetooth first. In Radio Remote press **Bind PTT** and then the device's button.

**Build it yourself:** `./build_app.sh` (Xcode command line tools; makes `build/radio-remote-ptt-mac.zip`).
For the text mode run the executable inside the app with `--cli`, or build only the program with `swiftc -O -o ptt-bridge ptt-bridge.swift`.
Options: `--list` (paired devices), `--name TEXT` (only use devices whose name contains TEXT; also wakes that
device), `--port N`, `--simulate` (no Bluetooth: type p / r / x / c and Enter).

The helper listens on `127.0.0.1` only and sends button state one way; nothing a web page sends to it is acted on.
