# Installation guide (Raspberry Pi)

Two ways, same result. **Path A (installer pack)** is the easy one: everything is in one folder, including the Python libraries, so
only a few system packages are downloaded. **Path B** installs from a copy of the source code.

## 1. What you need

| | |
|---|---|
| Pi | Raspberry Pi 4 or 5 (3B+ works for CAT and a listener or two) |
| OS | **Raspberry Pi OS Lite, 64-bit** (Bookworm or newer, Python 3.11+). Lite avoids PipeWire/PulseAudio grabbing the radio's sound card. 32-bit is refused |
| SD card | 16 GB or more, good quality |
| Network | wired Ethernet preferred; internet access during the install |
| Radio | USB cable from the radio's USB jack to the Pi (CAT and audio travel over the same cable on the FT-991A) |
| PC | to flash the SD card and copy files (Windows, macOS or Linux) |

## 2. Prepare the Pi (once)

1. Install **Raspberry Pi Imager** on your PC, choose your Pi model, then **Raspberry Pi OS (other) > Raspberry Pi OS Lite (64-bit)**.
2. When it offers *OS customisation* choose **Edit settings** and set: hostname (for example `radio`), a user name and password,
   Wi-Fi (if not using Ethernet), your time zone, and on the *Services* tab **Enable SSH**.
3. Write the card, put it in the Pi, power it on and wait about two minutes.
4. From your PC open a terminal and connect: `ssh <user>@radio.local`. Then update the system:
   ```bash
   sudo apt update && sudo apt full-upgrade -y && sudo reboot
   ```

## 3. Path A: the installer pack (recommended)

The pack is a folder (`radio-remote-installer`) containing `install-everything.sh`, the `radio-remote` program, a `wheels` folder with all
Python libraries for the Pi (so nothing is compiled or downloaded from PyPI), and the documentation.

1. Copy the whole folder to the Pi. From a Windows PC (PowerShell, in the folder that holds `radio-remote-installer`):
   ```powershell
   scp -r radio-remote-installer <user>@radio.local:~
   ```
   (or copy it with a USB stick or WinSCP).
2. On the Pi:
   ```bash
   cd ~/radio-remote-installer
   sudo bash install-everything.sh
   ```
   Useful options (all optional): `--model ft991a` (the default; others are in [other radios](08-other-radios.md)), `--hostname radio.local`,
   `--with-tailscale` (installs Tailscale), `--no-caddy`, `--dry-run` (shows every step and changes nothing).
3. It prints the address to open. **Done**: go to section 5.

What it does, in order: installs `python3-venv alsa-utils libopus0 curl caddy` and friends (needs internet), creates the service user
`radio-remote`, installs the program under `/opt/radio-remote`, installs the Python libraries from the bundled `wheels` folder, writes
`/etc/radio-remote/config.toml` (**transmitting disabled**, listening on the Pi only), installs the udev rule and the service, sets up
HTTPS with Caddy (its own local certificate), and waits until the app answers. It never opens a firewall port, never enables
transmitting and never talks to the radio.

## 4. Path B: from the source code

```bash
git clone <your repository URL> radio-remote      # or unpack a copy of the source folder
cd radio-remote
sudo bash install.sh --dry-run                    # prints every action, changes nothing
sudo bash install.sh --model ft991a               # real install (downloads the Python libraries; several minutes on a Pi)
```
Options: `--model NAME`, `--hostname NAME`, `--no-caddy`, `--with-hamlib`, `--with-tailscale`, `--wheels DIR`, `--force-arch`.
Use `bash install.sh` (not `./install.sh`) when the files came from a Windows PC: the executable flag is lost there.

## 5. First run

1. Browse to the address it printed (`https://radio.local`). Your browser warns about the certificate until you trust the Pi's local
   certificate once per device ([security and remote access](06-security-remote.md)); listening works regardless, the microphone needs HTTPS.
2. The first visit asks you to create the **administrator** account.
3. **Admin > Config**: choose the radio model and the serial port (`auto` finds an FT-991A), *Save*, restart the service when asked.
4. Check the radio shows *online* and the frequency follows the VFO knob.
5. Set the radio's own menus for USB audio and CAT: [radio connection](radio-connection.md) (FT-991A: CAT rate, USB audio levels, mic source).
6. **Transmitting is off.** When you are ready (with a dummy load first), edit `/etc/radio-remote/config.toml`, set `allow_ptt = true`
   under `[safety]`, then `sudo systemctl restart radio-remote`. This can not be done from the web page, on purpose.

## 6. Remote access with Tailscale

Install Tailscale (`--with-tailscale`, or `curl -fsSL https://tailscale.com/install.sh | sh`), log the Pi in once with `sudo tailscale up`, and
follow [tailscale](tailscale.md) to publish the app on your tailnet over HTTPS. No router port is opened.

## 7. Updating, backup, removal

* Update: copy the new pack or source folder to the Pi and run `sudo bash update.sh` (or, from the pack, `sudo bash install-everything.sh` again).
  The old release is kept and the update rolls back by itself if the new one does not start.
* A daily backup runs automatically; see [operations](operations.md) for backup, restore and the configuration file.
* Remove: `sudo bash scripts/uninstall.sh` (keeps configuration and backups) or `--purge` (deletes them too).

## 8. Manual install (without the installer)

```bash
sudo apt install python3 python3-venv alsa-utils libopus0
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/radio-remote.example.toml config/radio-remote.toml      # edit model/port
PYTHONPATH=backend .venv/bin/python -m radio_remote --config config/radio-remote.toml
```
Then see `packaging/` for the systemd unit and udev rule to adapt.

## 9. If the installer stops

The error message says what failed. The most common causes: no internet (apt cannot download), a 32-bit OS, or Python older than 3.11.
Run `sudo /opt/radio-remote/current/scripts/doctor.sh` and see [troubleshooting](troubleshooting.md).
