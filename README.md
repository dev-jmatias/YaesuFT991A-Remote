# Radio Remote

Control a Yaesu radio from a phone, tablet or computer, at home or from anywhere: frequency, mode, filters, meters, memories,
transmit with server-side safety, and remote audio. It runs on a Raspberry Pi next to the radio. No cloud account, no app store.

**Radios:** the **FT-991A** is complete and verified on a real radio. **FTDX10, FTDX101D/MP and FT-710** are included but
**experimental** (written from their manuals, tested only against a simulator). A built-in simulator lets you try everything without a radio.

**You need:** a Raspberry Pi 4 or 5 (64-bit) with a power supply and an SD card (8 GB or more), the radio's USB cable, and a network
(cable or Wi-Fi). Everything below is free software.

## Get it: two ways

Download the files from the **[latest release](https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest)**.

### A. Ready-made image (easiest)

**With Raspberry Pi Imager 2.x** (nothing to download first): open Imager, go to *App Options > Content Repository*, choose a custom
repository and paste

```
https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest/download/os-list.json
```

Then pick your Pi, choose **Radio Remote for Yaesu** in the OS list, and fill in Imager's own screens (hostname, user, password, Wi-Fi,
SSH). Write the card, put it in the Pi, power on, wait two minutes, open `https://<hostname>.local`.

**If that does not work for you** (older Imager, or no repository option): download `image_…-radio-remote.img.xz` and
`first-boot-settings.ps1` from the release, write the image with Imager (*Choose OS > Use custom*), then run the script (Windows
PowerShell 7) while the card is still in the PC: it sets the hostname, user, password and Wi-Fi.

Details of each step: [image/README.md](image/README.md).

### B. On top of Raspberry Pi OS

1. Write **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager and set the user, password, Wi-Fi and SSH in its settings.
2. Download `radio-remote-installer-….zip` from the release and unzip it.
3. Copy it to the Pi and run the installer:

```bash
scp -r radio-remote-installer <user>@<hostname>.local:~
ssh <user>@<hostname>.local
cd radio-remote-installer && sudo bash install-everything.sh
```

Full guide: [docs/INSTALL.md](docs/INSTALL.md).

### First run (both ways)

Open `https://<hostname>.local` (your browser asks once to trust the local certificate). The first visit creates the administrator:
use your **callsign** and a password. Then **Admin > Config**: choose the radio model and the serial port, Save, Restart.
**Transmitting is off** until an administrator switches it on in Admin > Config > *Transmitting (PTT)* (password required): test with
a dummy load first. Remote access from outside your home: [Tailscale](docs/tailscale.md) (`sudo tailscale up`, then
`sudo /opt/radio-remote/current/scripts/tailscale_setup.sh`), no router ports to open.

## Updating

Administrators see a notice in the page when a newer release exists. Then, on the Pi:

```bash
sudo /opt/radio-remote/current/scripts/self_update.sh
```

It downloads the new release, verifies its checksum, makes a backup and installs it; if the new version does not start it goes back by
itself. Your settings and accounts are kept. (Switch the notice off in Admin > Config > Updates.) More: [docs/operations.md](docs/operations.md).

## Safety

- Transmitting is **off by default** and needs an administrator and a password to enable.
- Hold-to-talk with a heartbeat: the server un-keys on lost connection, lost control, time limit or shutdown.
- One client controls at a time; microphone audio reaches the radio only while that client is keyed. *Lock PTT* prevents stray touches.
- Set the radio's own TX time-out as a last backstop. Remote operation must be allowed by your licence.

## Documentation

The manual is installed on the Pi and served at `/docs/` (Help button in the app), and it is in the release as well.

| | |
|---|---|
| [INSTALL.md](docs/INSTALL.md) | preparing the Pi, installing, first run, removal |
| [USER-GUIDE.md](docs/USER-GUIDE.md) | operating the radio: screens, control, audio, memories, transmitting |
| [operations.md](docs/operations.md) | configuration, services, logs, updating, backup and restore |
| [radio-connection.md](docs/radio-connection.md) | USB CAT and audio, the radio's own menu settings |
| [troubleshooting.md](docs/troubleshooting.md) | symptom-by-symptom fixes |
| [logbook.md](docs/logbook.md) | follow and tune the radio from Log4OM or another logbook (Hamlib rigctl) |
| [tailscale.md](docs/tailscale.md), [06-security-remote.md](docs/06-security-remote.md) | remote access and the security model |
| [08-other-radios.md](docs/08-other-radios.md) | FTDX10, FTDX101D/MP, FT-710 (experimental) |

<details>
<summary>For developers and testers</summary>

Design and test notes: [02-architecture.md](docs/02-architecture.md), [04-audio.md](docs/04-audio.md), [05-ui.md](docs/05-ui.md),
[01-capability-matrix.md](docs/01-capability-matrix.md), [03-bench-checklist.md](docs/03-bench-checklist.md),
[bench-results.md](docs/bench-results.md), [07-next-revisions.md](docs/07-next-revisions.md).

**Run it without a radio** (Windows or Linux):

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest pytest-asyncio markdown      # Windows: .venv\Scripts\pip
PYTHONPATH=backend .venv/bin/python -m radio_remote --config config/radio-remote.toml   # mock radio on http://localhost:8080
PYTHONPATH=backend .venv/bin/python -m pytest -q                                          # the tests never touch hardware
```

**Make a release:** bump `__version__` in `backend/radio_remote/__init__.py` (and `pyproject.toml`), commit, then push a tag that matches it
(`v1.2.0`). GitHub Actions (`.github/workflows/build-image.yml`) runs the tests and builds the Raspberry Pi image, the update package
(`radio-remote-vX.Y.Z.tar.gz`, used by `self_update.sh` and the update notice), the installer zip and `SHA256SUMS`, and attaches them to the
release. The release text comes from `image/RELEASE-NOTES.md`. The same files can be built locally with
`python scripts/build_release_assets.py --tag auto --out release-assets --installer`.

Folders: `backend/` server, `frontend/` web page (no build step), `packaging/` systemd and udev files, `image/` Raspberry Pi image kit,
`installer/` installer pack scripts, `scripts/` admin and bench scripts, `tools/` developer tools, `tests/`, `docs/`.

</details>
