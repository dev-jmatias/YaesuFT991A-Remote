## Radio Remote

Control a Yaesu radio from a phone, tablet or computer: frequency, mode, filters, meters, memories, transmit with server-side safety and
remote audio, from a Raspberry Pi next to the radio. The **FT-991A** and the **FTDX101D** are verified on real radios, the **FTDX101MP** has the same commands as the D;
FTDX10 and FT-710 are included but **experimental** (written from the manuals, simulator-tested only).

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
| `radio-remote-rade-linux-aarch64.tar.xz` | the optional RADE library (FreeDV neural modes V1 and V2) for a Raspberry Pi, installed with `install_rade.sh` (the PC build is `…-x86_64.tar.xz`) |
| `SHA256SUMS` | checksums of all of the above |

### What is new in 1.1.19

- **Squelch.** A **Squelch** slider under the mode buttons on the Radio tab, shown in FM, AM and C4FM modes and on the AIR band, and always in the Levels tab. It is the radio's own squelch level (`SQ`, 0 to 100, 0 = open).
  FT-991A: tested on a real radio. FTDX101D and FTDX101MP: MAIN and SUB sliders, written from the CAT manual and simulator-tested (not yet tried on a real FTDX101).
- The manual's radio-menu chapter (docs/radio-connection.md) now has the whole table for the FT-991A: the basics, the microphone-source menus the app manages (106, 045, 074 and 070), the receive-level menus (107 and 073) and the FreeDV DATA-mode menus.

### What is new in 1.1.18

- **FT-991A: choosing a memory after a band key now really retunes the radio.** Found on a real radio: pressing a band key while a memory is recalled puts the radio in *Memory Tune* (IF state 2), and an `MC`
  sent from there changes the channel number (and even reports "memory") without retuning, so the display showed the new channel while the audio stayed on the band key's frequency. Before every recall the app now checks the
  radio's state and, if it is in such a special memory state, presses V/M first (the Win4Yaesu manual names the same way out). The recall is also checked afterwards and retried once.
- **FreeDV on the FT-991A: an option to use the radio's DATA-USB / DATA-LSB mode** (FreeDV tab, administrators; off by default). FreeDV's own guide for the FT-991A uses the DATA modes, which bypass the radio's microphone chain (speech processor,
  equaliser, SSB transmit filter) that distorts RADE; the manual has the table of menus to set once on the radio (062, 064/065, 066/068, 070, 071, 072, 059). With the option on, FreeDV puts the radio in DATA-USB (DATA-LSB below 10 MHz).
- **The microphone button sets the right input in every mode** (FT-991A): REAR on menus 106 (SSB), 045 (AM), 074 (FM) and 070 (DATA IN SELECT) when you arm it; when you release it (or the last operator has left) 106, 045 and 074 go back to
  **MIC**, the front microphone, so local operation works in SSB, AM and FM. Menu 070 is left on REAR (it only matters in the DATA modes). A menu left on REAR by hand is also put back.
- The page asks for the memory's tone before editing (1.1.16) and the band / memory state is refreshed after a band key.

### What is new in 1.1.17

- **FreeDV receive no longer freezes the server on a small PC.** The RADE decoder takes 60 to 100 ms for every modem frame on an Intel Atom (about 64% of a core in the live service), and it ran inside the single loop that also serves
  the web page, the audio to the listeners and PTT, so all of those stalled for that long eight times a second. The decoding now runs in a worker thread (the library releases the Python lock), and switching FreeDV off or changing mode while a frame is being
  decoded is guarded, so the modem is never closed underneath a running decode. On a PC like that this should make the received audio and the page much smoother.
- Tested with a decoder that takes 120 ms per frame: the loop keeps running while it works.

### What is new in 1.1.16

- **Fix in the 1.1.15 memory editor: editing a channel that uses a tone no longer resets its tone frequency.** The radio's `MT` write copies the tone number VFO-A holds at that moment into the channel (found on a real FT-991A), so the
  1.1.15 editor changed the tone of a channel whenever its name or frequency was edited. If you edited tone channels with 1.1.15, check their tone frequency on the radio.
- **Tone frequency and DCS code per memory channel (FT-991A).** The editor now has a **tone frequency** list (the 50 CTCSS tones) and a **DCS code** list. Opening **Edit** on a channel with a tone reads the channel's own tone from the radio first
  (it is recalled for a moment and the radio put back). To store a tone, VFO-A is borrowed: it is set to the channel's mode, frequency, shift and tone, the channel is written, read back by recalling it, and VFO-A with its own settings and the V/M state
  are put back. A save takes about 30 seconds. Tested on a real FT-991A (two channels with different tones, rename keeps the tone, VFO-A and its 2 m settings restored).
- **Delete a memory channel (FT-991A).** **Delete channel** in the editor empties it: the channel is recalled (and checked) and then `AM;` empties it (found on a real FT-991A). Proved like a write: the channel must read as empty and no other channel may change.
- **AIR band (FT-991A).** The band buttons include **AIR** (the radio's AIR key, `BS14`): the radio goes to AM by itself and the page's tuning step becomes 25 kHz, going back to the previous step when you leave the band.
- **The microphone button works in AM and FM too.** It set only menu 106 (SSB MIC SELECT); it now sets 106, 045 (AM MIC SELECT) and 074 (FM MIC SELECT) together (FT-991A). DATA modes (menu 070) are untouched.
- **FreeDV: the end of an over is no longer cut off.** When you release PTT the last modem frame is completed with silence and the queued tones reach the radio before it is unkeyed (about half a second later). The FreeDV Reporter name for RADE V2 (`RADEV2`) is confirmed by another open-source client.

### What is new in 1.1.15

- **Memory editor (FT-991A, administrators).** **Memories > Add** stores a new channel and **Edit** changes one: name (up to 12 characters), frequency, mode, shift direction (simplex, plus, minus) and tone mode (off, CTCSS encode,
  CTCSS encode + decode, DCS). **Export** saves the list as a CSV file first (a backup). Every save asks for confirmation and is **proved**: the whole list is read before and after, the channel must read back as written, and if a write ever
  changed another channel, editing switches itself off and the message names the channels. It takes about 20 seconds. Tested on a real FT-991A. Not offered: the tone **frequency** (e.g. 71.9 Hz) and the repeater offset, which the radio's CAT
  commands cannot store per channel (set them in the radio's menus), and deleting a channel (do it on the radio).
- **Memory recall is checked.** Some radios sometimes fall into a memory check / memory tune state where selecting a memory changes nothing (the Win4Yaesu manual describes it; two friends had to repeat the change until it took). The recall is now checked
  against the radio's answer and, if it did not take, retried once (the V/M key is pressed first only when the radio reports that special state). What happened is written to the log (`memory recall: ...`).
- **A reinstalled RADE library is used.** The copy installed from the page (Admin > Config > RADE) lives in the data folder and now wins over the one in `/opt`, and the service finds it the next time FreeDV is switched on, with no restart. Before, a PC kept loading the
  older V1-only library, so RADE V2 never appeared in the mode list.
- **The FreeDV tab stays visible while RADE can still be installed**, so its **Install RADE** button is reachable (the tab used to vanish on a machine without the library).
- RADE V2 is listed as **RADE V2**; the "experimental" wording is gone from the page.
- **Safari before version 16:** the frequency readout now has a fallback size for browsers that do not understand container query units (a phone showed the frequency falling apart and the page shaking). This is the likely cause and could not be tested on such a phone.
- Admin > Diagnostics shows the new FreeDV timing counters from 1.1.14 (slowest step, holes in the transmitted tones); `freedv_probe.py` prints the slowest step too.

### What is new in 1.1.14

- **FreeDV is now RADE only: RADE V1 and the experimental RADE V2.** 1600, 700D and 700E (and the libcodec2 package) are removed. A configuration that still names one of them is read as RADE V1, so nothing needs editing.
  **RADE V2 is experimental upstream**: its signal may still change, V1 and V2 stations cannot decode each other, and most stations are on V1. It needs the RADE library from this release:
  if you update the program but keep an older library, V1 keeps working and the FreeDV tab tells you to press **Admin > Config > RADE > Reinstall RADE** to get V2. V2 locked across the whole ±300 Hz tried on ideal signals (V1 only within about ±50 Hz).
- **Transmit tones no longer get holes in them** (a likely cause of RADE transmissions that other stations could not decode): the modem delivers its tones in bursts and the sender wrote 20 ms of silence whenever its queue ran dry,
  so a burst that was a few milliseconds late punched a hole into the signal, which RADE cannot follow. The Pi now keeps about 0.2 s of tones queued before it starts (and again after a gap). The price: the last fraction of a second
  of an over may not be sent, so hold PTT a moment after the last word. After every over the log says `FreeDV transmit: … no holes` or `the tones had N hole(s)`, and **Admin > Diagnostics** shows the slowest receive and transmit step
  (it should stay well under 20 ms) and the number of holes.
- Power on from the page (**Power on radio**, PS1) is now verified on a real FT-991A.
- `scripts/freedv_probe.py` also prints the slowest single step of each mode.

### What is new in 1.1.13

- **FreeDV no longer freezes after PTT** (fix for a problem seen over WebSocket audio): the page sends microphone audio only while PTT is held and nothing at all after it is released, so the server never learned
  that the transmission had ended; the receiver stayed muted, the tuning scope frozen, and FreeDV had to be switched off and on. The server now ends the transmit state by itself when no microphone frame has
  arrived for half a second (and the receiver restarts, as in 1.1.12). The FreeDV Reporter's "transmitting" flag ends the same way.
- **System card in Admin > Config**: **Restart the service…** (the page reloads by itself when it is back) and **Reboot the system…** (asks for your password again, is refused while the radio transmits, and is
  written to the audit log). The page only drops a request file; a small root helper started by systemd (`radio-remote-power.path`, installed by `install.sh` and `update.sh`) reboots the machine and does nothing else.

### What is new in 1.1.12

- **FreeDV receives again by itself after every transmission.** After you released PTT the receiver went on with a decoder that had seen a gap, and on one PC it never found the signal again until FreeDV
  was switched off and on. The receiver now restarts its modem about 0.3 s after each transmission (keeping the tuning it had found), for all FreeDV modes.
- **Memories on an FT-991A that answers `IF` with the VFO frequency in memory mode**: the page kept showing the VFO frequency after a recall (only the MEM label changed). While in memory mode, the stored
  frequency and mode of the recalled channel (read from the radio's own memory list) are now shown when the radio only repeats the VFO. A radio that reports the channel's own frequency is trusted as before.
- **Installer for a Debian PC**: it installs `libnss-mdns` and `avahi-utils` (so the PC resolves `.local` names itself) and, at the end, warns when the name the web page answers to does not match the PC's own hostname,
  which is what other devices find on the network, with the commands to fix it. `docs/INSTALL.md` has a new section, "If the page does not open" (name problems, routers holding old names, the hosts file, changing the name later).
- No crash when the RADE library folder is not readable by the user who runs `freedv_probe.py`; it says RADE is not installed.

### What is new in 1.1.11

- The *experimental* banner is gone for the **FTDX101D** (tested on a real radio) and the **FTDX101MP** (the same commands as the D; its ID and its 5 to 200 W power range are the only differences, and no MP has been
  tried here: please report anything that differs). The FTDX10 and FT-710 keep the banner. Docs and the capability matrix say the same.

### What is new in 1.1.10

- **Yaesu FTDX101D tested on a real radio** (Raspberry Pi 4, USB). The FTDX101MP uses the same commands and is enabled the same way (transmit power 5 to 200 W); no MP has been available, so both still show the
  *experimental* banner. What works on the FTDX101D:
  - **MAIN and SUB receivers** shown as two panels, with **RX** (listen, on / off), **TX** (transmit on MAIN or SUB, so split works) and **MAIN | SUB** selector buttons that follow the radio's own keys, a
    **MAIN ↔ SUB** frequency swap and **Set MAIN / Set SUB frequency** side by side. The USB audio carries one receiver at a time and follows the receiver you listen to.
  - **Filter, noise and receiver controls for each receiver** (a MAIN | SUB switch in the Filters & DSP panel), RF and AF gain per receiver, **speech processor** (the radio uses 0 = off / 1 = on; the manual says 1 / 2), **AMC output level**, and a
    **CW** tab (keyer speed, pitch, keyer, break-in), an **Audio** tab (audio out level, audio in level, **transmit audio source DATA / USB**), **Tune** and **Memories** (read and recall only, nothing is ever written).
  - If the radio does not answer at all, the log now says which USB ports and rates were tried; the usual cause is the radio's **CAT RTS** menu being ON (set it to OFF). The notes for this radio are in `docs/08-other-radios.md`.
- **Gain bars show 0 to 100 %** instead of 0 to 255, on all radios.
- **The Memories button sits beside Tune**, and the VFO tools and tuning rows wrap and are centred, so nothing runs off a small screen (all radios).
- The COMP meter shows "off" on the FTDX101 while the speech processor is off (the radio reports a meaningless value then).

### What is new in 1.1.9

- **RADE is now part of the installations**: the ready-made image and the installer pack contain the RADE library, and `install.sh` downloads and installs it by itself
  (`--no-rade` skips it, `--rade-file FILE` uses a local copy). A new installation has the neural FreeDV mode ready; ordinary updates stay small and keep an installed library.
- **Debian 12 / 13 on a PC**: Radio Remote can be installed on a normal 64-bit Debian PC, not only on a Raspberry Pi (installation guide, route D). The RADE library is now also built for the PC
  (`radio-remote-rade-linux-x86_64.tar.xz`), inside a Debian 12 container so it runs on Debian 12 and newer; the **Install RADE** button works on both kinds of machine.
- **FreeDV Reporter** (qso.freedv.org), off by default: the FreeDV tab can announce your station while FreeDV is on (callsign, grid square, frequency, mode, whether you are transmitting; this
  is public on that site) and lists who is on the air, with a **Tune** button for each station. Settings in the FreeDV tab (administrators).

### What is new in 1.1.8

- **FreeDV tuning aid**: FreeDV only locks when the signal is close to where the modem expects it (about +-50 Hz for RADE and 700E, about +-150 Hz for 700D and 1600), which a radio dial
  cannot be set to by hand. The Pi now searches the tuning in software (up to +-450 Hz around the dial), holds it once the modem locks, and keeps it when you move the dial.
  The FreeDV tab shows a spectrum of the received audio with the modem's expected band, a level check (too loud / very low), whether it is locked and how far it is correcting,
  fine dial buttons (+-10 / +-100 Hz), **Centre the dial** and **Search again**. Needs someone listening (speaker icon). Tested against the real codec2 and RADE libraries.

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
