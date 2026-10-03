# Raspberry Pi image

A ready-to-write 64-bit Raspberry Pi OS Lite image (Debian Trixie) with Radio Remote already installed, built with
[pi-gen](https://github.com/RPi-Distro/pi-gen) by GitHub Actions. Verified on a Raspberry Pi 4 with an FT-991A.

## What the image contains

- Raspberry Pi OS Lite (64-bit), SSH enabled, **no password set**: the account is locked until you set one while writing the card.
- Radio Remote installed exactly like `install.sh` does it (`--image` mode): service user, `/opt/radio-remote`, config for the
  FT-991A, udev rule, systemd service and backup timer, Caddy (HTTPS), Tailscale (installed, not logged in), the manual at `/docs/`.
- Transmitting disabled (`allow_ptt = false`) until an administrator enables it in the web page.
- `radio-remote-caddy-host.service`: on every boot, before Caddy starts, sets the HTTPS site name to `<hostname>.local`, so the
  address matches whatever hostname you chose when writing the card.
- Root login stays disabled. The user you create has `sudo`, like on every Raspberry Pi OS.

## Writing the card: two ways

### 1. With Raspberry Pi Imager 2.x and the content repository (recommended)

Each release carries `os-list.json`, a small file that makes Imager list this image next to the official ones **and** show its own
customisation screens (hostname, user name and password, Wi-Fi, SSH, time zone) for it. Nothing needs to be downloaded by hand.

1. Open Raspberry Pi Imager 2.x. In the application options choose **Content Repository** and enter a custom repository (URL):
   `https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest/download/os-list.json`
   (the same works from a terminal: `rpi-imager --repo <URL>`).
2. Choose your Pi (4 or 5; the 3 should work but is untested), then **Radio Remote for Yaesu** in the list.
3. Choose the card, fill in the customisation screens, and write.
4. Put the card in the Pi, power on, wait about two minutes, open `https://<hostname>.local` and create the administrator account
   (use your callsign as the user name). Then Admin > Config: radio model and serial port. Remote access: `sudo tailscale up`.

### 2. With the settings script (older Imager, or if the repository option is missing)

1. Download `image_…-radio-remote.img.xz` and `first-boot-settings.ps1` from the release.
2. Imager > *Choose OS > Use custom* > the `.img.xz` file (do not unzip it) > write the card. (Imager 2.x does not offer its settings
   step for plain custom images, which is why the repository above exists.)
3. Take the card out and put it back in the PC so Windows shows the small **bootfs** volume (it may have no drive letter: that is fine).
   Do this right after writing, **before the card has ever booted**: the settings are read on the first boot only.
4. In PowerShell 7 run `pwsh -ExecutionPolicy Bypass -File first-boot-settings.ps1`. It asks for a hostname, user name, password
   (8+ characters), Wi-Fi and time zone and writes the cloud-init files (`user-data`, `network-config`, `meta-data`) onto the card.
   The password is stored only as a hash, computed by the script itself (nothing else has to be installed).
5. Eject the card, boot the Pi and continue as in step 4 above.

To change the name, password or Wi-Fi later, write the card again (or change them over SSH).

## Building it

**On GitHub:** push a tag that matches `__version__` (for example `v1.2.0`) or run Actions > "Build Raspberry Pi image". It runs the
tests, builds the manual, builds the image on a native arm64 runner (about 10 minutes), and then
`scripts/build_release_assets.py` and `scripts/build_imager_repo.py` produce the update package, the installer zip, `os-list.json` and
`SHA256SUMS`. For a tag, everything is attached to the release (text from `RELEASE-NOTES.md`).

**Testing `os-list.json` before publishing:** `python scripts/build_imager_repo.py --image X.img.xz --tag auto --repo owner/name
--out os-list.json --image-url file:///C:/path/X.img.xz` writes a repository file that points at a local copy of the image; load it in
Imager as a custom repository from a file or from a local web server.

**Locally (Linux or WSL2 with Docker):** run `python scripts/build_docs.py && bash image/stage_program.sh`, then use
pi-gen's own `build-docker.sh` with a `config` file containing:

```
IMG_NAME=radio-remote
RELEASE=trixie
TARGET_HOSTNAME=radio
FIRST_USER_NAME=pi
ENABLE_SSH=1
STAGE_LIST="stage0 stage1 stage2 /path/to/radio-remote/image/stage-radio-remote"
```

## Layout

```
image/
  stage_program.sh                     copies the program into the stage (CI and local builds run it first)
  stage-radio-remote/                  the custom pi-gen stage
    EXPORT_IMAGE, prerun.sh            start from stage2 (Lite) and export an image
    00-radio-remote/
      00-run.sh                        host side: copy the program and the first-boot helper into the image
      01-run-chroot.sh                 inside the image: run install.sh --image
      files/radio-remote-caddy-host.*  keeps the HTTPS name equal to <hostname>.local
  first-boot-settings.ps1              writes hostname/user/password/Wi-Fi onto the flashed card (cloud-init files)
  RELEASE-NOTES.md                     the text of each GitHub release
```

The stage scripts must be committed **executable** (pi-gen silently skips the ones that are not); a test checks it.
