# Raspberry Pi image

A ready-to-flash 64-bit Raspberry Pi OS Lite image with Radio Remote already installed, built with
[pi-gen](https://github.com/RPi-Distro/pi-gen). **Status: written from the pi-gen and pi-gen-action documentation, not yet
built or booted** - treat the first build as a test (see "First build" below).

## What the image contains

- Raspberry Pi OS Lite (64-bit), SSH enabled, no password set (the account is locked until you set one).
- Radio Remote installed exactly like `install.sh` does it (`--image` mode): service user, `/opt/radio-remote`, config for the
  FT-991A, udev rule, systemd service and backup timer, Caddy (HTTPS), Tailscale (installed, not logged in), the manual at `/docs/`.
- Transmitting disabled (`allow_ptt = false`), as always.
- `radio-remote-caddy-host.service`: on every boot, before Caddy starts, sets the HTTPS site name to `<hostname>.local`, so the
  address matches whatever hostname you chose when writing the card.

## Using the image

1. Download `radio-remote-*.img.xz` (GitHub Actions artifact or release).
2. Open Raspberry Pi Imager > Choose OS > Use custom > select the file and write the card. (Imager 2.x shows its
   "Edit settings" step only for official Raspberry Pi OS images, so it is not offered here.)
3. Take the card out and put it back in the PC so Windows shows the small **bootfs** drive, then run
   `pwsh image\first-boot-settings.ps1`. It asks for a hostname, user name, password, Wi-Fi and time zone and writes the
   cloud-init files (`user-data`, `network-config`) onto the card. The password is stored only as a hash. (Older Imager
   versions: use "Edit settings" instead.)
4. Eject the card, boot the Pi, wait about two minutes, then open `https://<hostname>.local` and create the administrator account.
5. Admin > Config: choose the radio model and the serial port. For remote access: `sudo tailscale up`.

## Building it

**On GitHub (recommended):** push the project to a GitHub repository, then Actions > "Build Raspberry Pi image" > Run workflow.
It runs the tests, builds the manual, and builds the image on a native arm64 runner (about 30-60 minutes). The image is an artifact
of the run; pushing a tag such as `v1.0.0` also attaches it to a release.

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
```

## First build - what to check

The workflow and stage could not be run on the development PC. After the first build, flash it and check: the page opens at
`https://<hostname>.local`, `systemctl is-active radio-remote caddy` are both active, the radio connects, and
`sudo /opt/radio-remote/current/scripts/doctor.sh` is clean. Likely first-build problems: the pi-gen release/branch pairing
(`RELEASE` and `pi-gen-version` must match the base image generation), or a package that cannot start inside the chroot.
