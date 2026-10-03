## Radio Remote

Control a Yaesu radio from a phone, tablet or computer: frequency, mode, filters, meters, memories, transmit with server-side safety and
remote audio, from a Raspberry Pi next to the radio. The **FT-991A** is verified on a real radio; FTDX10, FTDX101D/MP and FT-710 are
included but **experimental** (written from the manuals, simulator-tested only).

### Get started: pick one

**A. Ready-made image (easiest).** Download `image_…-radio-remote.img.xz` and `first-boot-settings.ps1` below.
Write the image with Raspberry Pi Imager (*Choose OS > Use custom*; do not unzip it), then run
`pwsh -ExecutionPolicy Bypass -File first-boot-settings.ps1` (Windows, PowerShell 7) while the card's small *bootfs* volume is visible: it asks
for the hostname, user, password and Wi-Fi. Put the card in the Pi, power on, wait two minutes, open `https://<hostname>.local`.
Run the script on a freshly written card, before its first boot.

**B. On top of Raspberry Pi OS Lite (64-bit).** Write the official OS with Imager (set user, password, Wi-Fi and SSH in its settings),
download `radio-remote-installer-….zip` below, unzip it, copy the folder to the Pi and run `sudo bash install-everything.sh`.

Then create the administrator (use your callsign), choose the radio and the serial port in **Admin > Config**, and (with a dummy load
first) switch transmitting on in **Admin > Config > Transmitting (PTT)**. Guide: [docs/INSTALL.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/INSTALL.md).

### Updating

Administrators see a notice in the page when a newer release exists. On the Pi run
`sudo /opt/radio-remote/current/scripts/self_update.sh`: it downloads the new release, verifies the checksum, makes a backup and installs it,
and goes back by itself if the new version does not start. Settings and accounts are kept.
*Coming from 1.0.x?* Those versions have no update command: download `radio-remote-v….tar.gz` below, copy it to the Pi, then
`tar xzf radio-remote-v*.tar.gz && cd radio-remote && sudo ./update.sh` (or write the new image).

### The files on this page

| File | What it is |
|---|---|
| `image_…-radio-remote.img.xz` | the Raspberry Pi image (route A) |
| `first-boot-settings.ps1` | sets hostname, user, password and Wi-Fi on a freshly written card (route A) |
| `radio-remote-installer-….zip` | installer pack with the program, the manual and offline libraries (route B) |
| `radio-remote-v….tar.gz` | the program only: used by `self_update.sh`, or by hand to update a running Pi |
| `SHA256SUMS` | checksums of all of the above |

### What is new in 1.1.0

- **Status lights** next to RX/TX: the preamp setting (IPO / AMP1 / AMP2), ATT, AGC (FAST / MID / SLOW / AUTO) and the antenna tuner; each can
  be hidden per device (Account > Display).
- **Lock PTT**: one switch that blocks the PTT button and TUNE on that device, so a stray touch cannot transmit.
- **SWR warning**: the SWR bar turns red and a message appears while transmitting above a limit you set (default 3:1).
- **Transmit switch in the page**: an administrator enables or disables transmitting in Admin > Config (password asked again, audited).
- **Callsign as user name** (including portable callsigns such as G4XYZ/P), and a *Show password* option on the sign-in page.
- **Settings backup and restore** in Admin > Config; a restore never changes the listener address, the storage folder or the transmit permission.
- **Memory channels**: fixed for radios that answer every channel read as "001" (found on a real FT-991A); the list is read in the background
  after connecting so it opens at once.
- **Update notice** and **`self_update.sh`** (see above), with checksums verified.
- Fixes: the settings script no longer needs Git for Windows; the DG-ID answer no longer fills the log with warnings.

### Safe by default

- **Transmitting is off** until an administrator switches it on. The image contains **no password, key or login**: the account stays locked
  until you set one with the settings script (or Imager's settings for route B).
- HTTPS uses a local certificate (Caddy): your browser asks you to trust it once.
- Remote operation must be allowed by your licence; set the radio's own TX time-out as a last backstop.

### Known limits

- Tested on a Raspberry Pi 4 with an FT-991A. The other radios are experimental. Prefer route B if you hit a problem with the image.
- The settings script's choices apply on a card's **first boot** only; to change them later write the card again (or change them over SSH).
- The image is not signed; check the SHA-256 in `SHA256SUMS`.
