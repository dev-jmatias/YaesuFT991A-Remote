# Installation guide (Raspberry Pi)

Three ways to the same result:

* **Route A: the ready-made image** (easiest). You write one image to the SD card and answer a few questions in Raspberry Pi Imager.
* **Route B: the installer pack** on top of the official Raspberry Pi OS. Everything is in one folder, including the Python libraries.
* **Route C: from the source code** (advanced).

Download the files from the **[latest release](https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest)**.

## 1. What you need

| | |
|---|---|
| Pi | Raspberry Pi 4 or 5 (3B+ works for CAT and a listener or two), 64-bit OS only |
| SD card | 16 GB or more, good quality |
| Network | wired Ethernet preferred, or Wi-Fi; internet access during the install |
| Radio | USB cable from the radio's USB jack to the Pi (CAT and audio travel over the same cable on the FT-991A) |
| PC | to write the SD card (Windows, macOS or Linux) with **Raspberry Pi Imager** (version 2.x for route A) |

## 2. Route A: the ready-made image

The image is Raspberry Pi OS Lite (64-bit, Debian Trixie) with Radio Remote already installed. It contains **no password**: you set the
user, password, hostname and Wi-Fi while writing the card.

1. Open Raspberry Pi Imager 2.x. In its application options choose **Content Repository**, enter a custom repository and paste:
   ```
   https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest/download/os-list.json
   ```
2. Choose your Pi, then **Radio Remote for Yaesu (Raspberry Pi OS Lite, 64-bit)** in the OS list. (Do not use *Use custom*: that route shows
   no customisation screens.)
3. Choose the SD card and fill in Imager's screens: **hostname** (for example `radio`), your **user name and password**, **Wi-Fi** if you
   do not use a cable, the **time zone**, and keep **SSH** enabled. Write the card.
4. Put the card in the Pi, connect the radio, power on and wait about two minutes.
5. Open `https://<hostname>.local` and continue with section 5 (first run).

**Root login stays disabled.** The user you create has `sudo`, like on every Raspberry Pi OS.

*If Imager has no Content Repository option* (older versions), download `image_…-radio-remote.img.xz` and `first-boot-settings.ps1` from the
release, write the image with *Choose OS > Use custom* (do not unzip it), take the card out and put it back in the PC so the small
`bootfs` volume shows up (before the card has ever booted), then run in PowerShell 7:
`pwsh -ExecutionPolicy Bypass -File first-boot-settings.ps1`. It asks for hostname, user, password and Wi-Fi. More: [image/README.md](../image/README.md).

## 3. Route B: the installer pack on Raspberry Pi OS

1. In Raspberry Pi Imager choose **Raspberry Pi OS (other) > Raspberry Pi OS Lite (64-bit)** (Bookworm or newer: Python 3.11 or newer
   is needed; Lite avoids PipeWire/PulseAudio grabbing the radio's sound card). In the customisation screens set a hostname, a user and
   password, Wi-Fi if needed, and enable SSH. Write the card, boot the Pi and wait two minutes.
2. From your PC connect and update the system:
   ```bash
   ssh <user>@<hostname>.local
   sudo apt update && sudo apt full-upgrade -y && sudo reboot
   ```
3. Download `radio-remote-installer-….zip` from the release and unzip it on your PC. It is a folder (`radio-remote-installer`) with
   `install-everything.sh`, the `radio-remote` program, a `wheels` folder with the Python libraries for the Pi (nothing is compiled or
   fetched from PyPI) and the manual.
4. Copy the folder to the Pi. From a Windows PC (PowerShell, in the folder that holds `radio-remote-installer`):
   ```powershell
   scp -r radio-remote-installer <user>@<hostname>.local:~
   ```
5. On the Pi:
   ```bash
   cd ~/radio-remote-installer
   sudo bash install-everything.sh
   ```
   Useful options (all optional): `--model ft991a` (the default; others are in [other radios](08-other-radios.md)),
   `--hostname radio.local`, `--with-tailscale` (installs Tailscale), `--no-caddy`, `--dry-run` (shows every step and changes nothing).
6. It prints the address to open. Continue with section 5.

What it does, in order: installs `python3-venv alsa-utils libopus0 curl caddy` and friends (needs internet), creates the service user
`radio-remote`, installs the program under `/opt/radio-remote`, installs the Python libraries from the bundled `wheels` folder, writes
`/etc/radio-remote/config.toml` (**transmitting disabled**, listening on the Pi only), installs the udev rule and the service, sets up
HTTPS with Caddy (its own local certificate), and waits until the app answers. It never opens a firewall port, never enables
transmitting and never talks to the radio.

## 4. Route C: from the source code

```bash
# on the Pi: unpack radio-remote-vX.Y.Z.tar.gz from the release (or clone the repository)
cd radio-remote
sudo bash install.sh --dry-run                    # prints every action, changes nothing
sudo bash install.sh --model ft991a               # real install (downloads the Python libraries; several minutes on a Pi)
```
Options: `--model NAME`, `--hostname NAME`, `--no-caddy`, `--with-hamlib`, `--with-tailscale`, `--wheels DIR`, `--force-arch`.
Use `bash install.sh` (not `./install.sh`) when the files came from a Windows PC: the executable flag is lost there. A git checkout does not
contain the built manual; run `python scripts/build_docs.py` first if you want it on the Pi.

## 5. First run (all routes)

1. Browse to `https://<hostname>.local`. Your browser warns about the certificate until you trust the Pi's local certificate once per
   device ([security and remote access](06-security-remote.md)); listening works regardless, the microphone needs HTTPS.
2. The first visit asks you to create the **administrator** account: use your **callsign** as the user name and a password of at least
   10 characters. Do this on your home network, not over a forwarded port.
3. **Admin > Config**: choose the radio model and the serial port (`auto` finds an FT-991A), *Save*, restart the service when asked.
4. Check the radio shows *online* and the frequency follows the VFO knob.
5. Set the radio's own menus for USB audio and CAT: [radio connection](radio-connection.md) (FT-991A: CAT rate, USB audio levels, mic source).
6. **Transmitting is off.** When you are ready (with a dummy load first), sign in as the administrator, open **Admin > Config >
   Transmitting (PTT)**, choose *Enable transmitting…* and type your password again. It takes effect at once and is kept after a
   restart. (Alternatively edit `/etc/radio-remote/config.toml`, set `allow_ptt = true` under `[safety]`, then
   `sudo systemctl restart radio-remote`.)

## 6. Remote access with Tailscale

Tailscale gives you a private way to the Pi from anywhere, with no router port opened.

1. Install Tailscale on the Pi (the installer option `--with-tailscale`, or follow tailscale.com/download) and sign in once:
   `sudo tailscale up`.
2. In the Tailscale admin console switch on **MagicDNS** and **HTTPS Certificates**.
3. Run `sudo /opt/radio-remote/current/scripts/tailscale_setup.sh`, then open the `https://<pi>.<tailnet>.ts.net` address it prints from any
   device that has Tailscale on. Details and troubleshooting: [tailscale](tailscale.md).

## 7. Updating, backup, removal

* **Update:** administrators are told in the web page when a newer release exists. Then run on the Pi
  `sudo /opt/radio-remote/current/scripts/self_update.sh` (downloads and verifies the new release, makes a backup, installs it).
  Without internet: copy the release's `radio-remote-vX.Y.Z.tar.gz` to the Pi, unpack it and run `sudo ./update.sh` in the folder.
  The old release is kept and the update rolls back by itself if the new one does not start. Details: [operations](operations.md).
* **Backup:** a daily backup of the settings and accounts runs automatically; *Admin > Config > Settings backup* downloads the settings as a
  file. See [operations](operations.md) for backup, restore and the configuration file.
* **Remove:** `sudo bash /opt/radio-remote/current/scripts/uninstall.sh` (keeps configuration and backups) or `--purge` (deletes them too).

## 8. Manual install (without the installer)

```bash
sudo apt install python3 python3-venv alsa-utils libopus0
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/radio-remote.example.toml config/radio-remote.toml      # edit model/port
PYTHONPATH=backend .venv/bin/python -m radio_remote --config config/radio-remote.toml
```
Then see `packaging/` for the systemd unit and udev rule to adapt.

## 9. If something stops

The error message says what failed. The most common causes: no internet (apt cannot download), a 32-bit OS, or Python older than 3.11.
Run `sudo /opt/radio-remote/current/scripts/doctor.sh` and see [troubleshooting](troubleshooting.md).
