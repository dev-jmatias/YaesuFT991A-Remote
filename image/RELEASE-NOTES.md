## Radio Remote - Raspberry Pi image

A ready-to-flash 64-bit Raspberry Pi OS Lite image (Debian trixie) with **Radio Remote** installed: web remote control for a Yaesu
radio from a phone, tablet or computer (frequency, mode, filters, meters, memories, PTT with server-side safety, remote audio).

**Radio support:** the **FT-991A** is built and bench-verified on a real radio. FTDX10, FTDX101D/MP and FT-710 profiles are
included but **EXPERIMENTAL** (written from the manuals, simulator-tested only). See the manual on the Pi at `/docs/`.

### Use it

You need a Windows PC with Raspberry Pi Imager and PowerShell 7 (`winget install Microsoft.PowerShell`). Nothing else.

1. Download the two files below: `image_*-radio-remote.img.xz` (do not unzip it) and `first-boot-settings.ps1`.
2. Raspberry Pi Imager > Choose OS > **Use custom** > the `.img.xz` file > write the card. (Imager 2.x offers no "Edit settings"
   for custom images, so the next step sets the name, user and password.)
3. Remove the card and put it back in the PC so Windows shows the small **bootfs** volume (it may have no drive letter: that is
   fine), then in PowerShell 7 run
   `pwsh -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\first-boot-settings.ps1"`
   (adjust the path to where you saved it). It asks for hostname, user, password (8+ characters) and Wi-Fi.
4. Eject the card, put it in the Pi and power on. After about two minutes open `https://<hostname>.local`, create the
   administrator account, then Admin > Config: choose the radio model and serial port. Remote access from anywhere:
   `sudo tailscale up` once.

Full steps and background: [image/README.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/image/README.md).

### Safe by default

- **Transmitting is disabled** (`allow_ptt = false`). Enable it in `/etc/radio-remote/config.toml` only when you are ready,
  and test with a dummy load first.
- The image contains **no password, key or login**: the account is locked until you set one in step 3.
- HTTPS uses a local certificate (Caddy internal CA): your browser asks you to trust it once.

### Known limits

- Tested on a Raspberry Pi 4 with an FT-991A. Prefer the installer route (Raspberry Pi OS Lite + `install-everything.sh`, see the
  manual) if you hit a problem.
- Run `first-boot-settings.ps1` on a **freshly written** card, before its first boot. The settings are applied only on the first
  boot: to change them later (password, hostname, Wi-Fi) write the card again.
- Not signed or reproducible bit-for-bit; check the SHA-256 in `SHA256SUMS`.

### Changes

- **1.0.0.2** - `first-boot-settings.ps1` no longer needs Git for Windows / OpenSSL (it computes the password hash itself), and
  it is attached to this release.
- **1.0.0.1** - fixed the password hash that `first-boot-settings.ps1` stored (SSH login was refused); the radio's three-digit
  DG-ID answer is decoded, so the log no longer warns `bad EX153` every 14 seconds.
- **1.0.0** - first image.
