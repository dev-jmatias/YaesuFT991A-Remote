## Radio Remote 1.0.0 - Raspberry Pi image

A ready-to-flash 64-bit Raspberry Pi OS Lite image (Debian trixie) with **Radio Remote** installed: web remote control for a Yaesu
radio from a phone, tablet or computer (frequency, mode, filters, meters, memories, PTT with server-side safety, remote audio).

**Radio support:** the **FT-991A** is built and bench-verified on a real radio. FTDX10, FTDX101D/MP and FT-710 profiles are
included but **EXPERIMENTAL** (written from the manuals, simulator-tested only). See the manual on the Pi at `/docs/`.

### Use it

1. Download `radio-remote-*.img.xz` below (do not unzip it) and the `first-boot-settings.ps1` script from the repository's
   `image/` folder.
2. Raspberry Pi Imager > Choose OS > **Use custom** > the `.img.xz` file > write the card. (Imager 2.x offers no "Edit settings"
   for custom images, so the next step sets the name, user and password.)
3. Remove the card and put it back in the PC so Windows shows the small **bootfs** volume, then run
   `pwsh -File first-boot-settings.ps1` (needs PowerShell 7 and Git for Windows). It asks for hostname, user, password and Wi-Fi.
4. Put the card in the Pi and power on. After about two minutes open `https://<hostname>.local`, create the administrator account,
   then Admin > Config: choose the radio model and serial port. Remote access from anywhere: `sudo tailscale up` once.

Full steps and background: [image/README.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/image/README.md).

### Safe by default

- **Transmitting is disabled** (`allow_ptt = false`). Enable it in `/etc/radio-remote/config.toml` only when you are ready,
  and test with a dummy load first.
- The image contains **no password, key or login**: the account is locked until you set one in step 3.
- HTTPS uses a local certificate (Caddy internal CA): your browser asks you to trust it once.

### Known limits of this first release

- The image was built and booted on a real Raspberry Pi and the web app runs, but it has had little testing across Pi models.
  Prefer the tested installer route (Raspberry Pi OS Lite + `install-everything.sh`, see the manual) if you hit a problem.
- Run `first-boot-settings.ps1` on a **freshly written** card, before its first boot. The settings are applied only on the first
  boot: to change them later (password, hostname, Wi-Fi) write the card again. Use the script from release `v1.0.0.1` or newer
  (the `v1.0.0` copy had a bug that stored a wrong password hash, so SSH login was refused).
- Not signed or reproducible bit-for-bit; check the SHA-256 in `SHA256SUMS`.
