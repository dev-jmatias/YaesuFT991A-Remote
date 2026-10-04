## Radio Remote

Control a Yaesu radio from a phone, tablet or computer: frequency, mode, filters, meters, memories, transmit with server-side safety and
remote audio, from a Raspberry Pi next to the radio. The **FT-991A** is verified on a real radio; FTDX10, FTDX101D/MP and FT-710 are
included but **experimental** (written from the manuals, simulator-tested only).

### Get started: pick one

**A. Ready-made image (easiest).**
*With Raspberry Pi Imager 2.x:* in its application options choose **Content Repository**, enter a custom repository and paste
`https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest/download/os-list.json`. Then choose your Pi and **Radio Remote for Yaesu**,
fill in Imager's own screens (hostname, user, password, Wi-Fi) and write the card. Put it in the Pi, power on, wait two minutes,
open `https://<hostname>.local`.
*If that does not work for you* (older Imager, or no repository option): download `image_…-radio-remote.img.xz` and
`first-boot-settings.ps1` below, write the image with Imager (*Choose OS > Use custom*; do not unzip it), then run
`pwsh -ExecutionPolicy Bypass -File first-boot-settings.ps1` (Windows, PowerShell 7) while the card's small *bootfs* volume is visible: it
asks for the hostname, user, password and Wi-Fi. Run it on a freshly written card, before its first boot.

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
| `os-list.json` | the repository file for Raspberry Pi Imager 2.x (route A; you paste its link, you do not download it) |
| `first-boot-settings.ps1` | sets hostname, user, password and Wi-Fi on a freshly written card (route A, without the repository) |
| `radio-remote-installer-….zip` | installer pack with the program, the manual and offline libraries (route B) |
| `radio-remote-v….tar.gz` | the program only: used by `self_update.sh`, or by hand to update a running Pi |
| `radio-remote-rade-linux-aarch64.tar.xz` | the optional RADE library (FreeDV neural mode), installed with `install_rade.sh` |
| `SHA256SUMS` | checksums of all of the above |

### What is new in 1.1.7

- **Update now button** (Admin > Config > Updates): when a newer version is known, an administrator presses **Update now…**, types their password, and the Pi updates itself
  (download, SHA-256 check, backup, install, health check, automatic rollback), shows the log and reloads the page when it is done. It works through a small root helper
  (`radio-remote-update.path`) that `update.sh` installs, so **this first update to 1.1.7 still has to be done the usual way** (`self_update.sh`); the button is there for later versions.
- **Install RADE button** (Admin > Config > RADE, and the FreeDV tab): administrators install the RADE library from the web page on a 64-bit ARM Pi, with no command line and no restart
  (it shows **Reinstall RADE** once installed, and says why when it cannot be installed on a system).
- The radio goes back to its own microphone by itself when the last operator leaves was already in 1.1.6; nothing else changed there.

### What is new in 1.1.6

- **The radio goes back to its own microphone by itself**: about 15 seconds after the last operator connection has gone, the Pi sets menu 106 back to MIC, so the radio is
  not left on REAR (USB audio) after you close the page. A page reload or a short network drop does not trigger it; it is left alone while the radio transmits.
- **MIT licence** (Copyright 2026 Jorge Matias) added to the project, with notes on the third-party software it uses.

### What is new in 1.1.5

- **FreeDV RADE** (the newest, neural FreeDV mode) as an optional extra, verified receiving and transmitting on a real radio. It is a separate 24 MB library
  that is not part of normal updates: install it on the Pi with `sudo /opt/radio-remote/current/scripts/install_rade.sh` (it downloads the matching
  `radio-remote-rade-linux-aarch64.tar.xz` from this page, checks its checksum and restarts the service), then choose **RADE** in the FreeDV tab.
  On a Raspberry Pi 4 it needs about 20% of one core to receive and 8% to transmit. Built from the open-source `rade_c` (BSD licence).
- **FreeDV 1600** joins 700D and 700E in the mode list.
- A **FreeDV indicator** under the frequency (before the MEM tag): dark while FreeDV is on, green while it is locked on a signal.
- **Phones**: the channel cards are all the same size, and the channel editor no longer cuts off text.
- New tool: `scripts/freedv_probe.py` measures the CPU each FreeDV mode needs on your Pi.

### What is new in 1.1.4

- **FreeDV digital voice (700D and 700E)**, receive and transmit, done on the Pi with the codec2 library: a **FreeDV tab** beside Audio switches it on and
  off, shows whether the modem is locked on a signal (with its SNR), has editable **preset channels** (the usual FreeDV calling frequencies; LSB
  below 10 MHz, USB above) and a transmit-level setting. Listeners hear the decoded speech; with the microphone armed, your voice is sent as modem
  tones while you hold PTT, through the same server-side transmit safeguards. The installer and the update scripts install `libcodec2` for you;
  without it the tab simply does not appear. The codec is covered by automated tests; try receive first and transmit into a dummy load. Guide:
  [docs/freedv.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/freedv.md).

### What is new in 1.1.3

- **WebSocket audio**: if WebRTC (UDP) cannot connect (hotel, office or mobile networks, some relayed paths), the page now switches to the same
  Opus audio over its normal HTTPS/WebSocket connection after about 9 seconds, listening and microphone included. Audio tab > Connection lets you
  force WebRTC or WebSocket. The server-side PTT gate is unchanged. Needs a browser with WebCodecs (current Chrome, Edge, Firefox, Safari 17+).
  Verified on a real FT-991A with UDP blocked.
- **Hands-free transmit**: hold HOLD TO TRANSMIT, slide up until it says "Locked on", let go; tap the button to stop. The time limit, the heartbeat and
  "page hidden" still end it.

### What is new in 1.1.2

- **Logbook link (Hamlib rigctl)**: switch it on in Admin > Config > Logbook link and a logbook on your home network (tested design for Log4OM:
  Hamlib, model *NET rigctl*, `<pi>.local:4532`) follows the radio and can change frequency and mode. It can never transmit or switch the
  radio off, only answers home-network addresses by default, and is off until you switch it on. Guide:
  [docs/logbook.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/logbook.md).
- **The microphone button now also sets the radio's input**: armed = REAR (USB audio), off = back to MIC. The separate REAR/MIC buttons are gone.
- **Lock PTT** is a round padlock beside HOLD TO TRANSMIT.
- **Phones**: the top menu shows icons, RX/TX and the status lights fit on one line, VFO-A is on the frequency line.
- **Connection dot** replaces the "radio online" text (green online, yellow reconnecting, red offline); the MEM channel tag sits under the frequency.
- Documentation and install guide reorganised; the "what each radio supports" page is generated from the radio profiles.

### What is new in 1.1.1

- **Remote access with Tailscale now works out of the box**: after `sudo tailscale up`, run
  `sudo /opt/radio-remote/current/scripts/tailscale_setup.sh`. Until now the HTTPS front end answered only to `<hostname>.local`, so the
  Tailscale name (`<pi>.<tailnet>.ts.net`), the `100.x` address and any port number gave no answer. The script adds the Tailscale name,
  gets a real certificate for it, checks the configuration before using it, and keeps it across updates and reboots. Switch on *MagicDNS* and
  *HTTPS Certificates* in the Tailscale admin console first. Guide: [docs/tailscale.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/tailscale.md).
- Raspberry Pi Imager's own screens (hostname, user, password, Wi-Fi) are confirmed to work with the image through the content repository
  link above.

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
  until you set one in Imager's screens (or with the settings script). Root login stays disabled; your user has `sudo`.
- HTTPS uses a local certificate (Caddy): your browser asks you to trust it once.
- Remote operation must be allowed by your licence; set the radio's own TX time-out as a last backstop.

### Known limits

- Tested on a Raspberry Pi 4 with an FT-991A. The other radios are experimental. Prefer route B if you hit a problem with the image.
- The settings script's choices apply on a card's **first boot** only; to change them later write the card again (or change them over SSH).
- The image is not signed; check the SHA-256 in `SHA256SUMS`.
