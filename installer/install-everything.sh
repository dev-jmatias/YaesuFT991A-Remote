#!/usr/bin/env bash
# Radio Remote - one-step installer for a Raspberry Pi (64-bit Raspberry Pi OS / Debian).
#
#   cd radio-remote-installer
#   sudo bash install-everything.sh                 # install (the radio model defaults to ft991a; change it later in the web UI)
#   sudo bash install-everything.sh --dry-run       # show every step, change nothing
#   sudo bash install-everything.sh --with-tailscale --hostname radio.local
#
# It runs the program's own install.sh with the Python libraries taken from the bundled "wheels" folder (no PyPI download),
# then prints what to do next. Every option is passed through to install.sh (see: bash radio-remote/install.sh --help).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HERE/radio-remote"

[ -f "$APP/install.sh" ] || { echo "ERROR: the 'radio-remote' folder was not found next to this script ($HERE)." >&2; exit 1; }
if [ "$(id -u)" -ne 0 ] && ! printf '%s\n' "$@" | grep -qx -- '--dry-run'; then
  echo "Run it with sudo:   sudo bash install-everything.sh" >&2
  exit 1
fi

ARGS=()
WHEELS="$HERE/wheels"
if [ -d "$WHEELS" ] && ls "$WHEELS"/*.whl >/dev/null 2>&1; then
  ARGS+=(--wheels "$WHEELS")
  echo "Using the bundled Python libraries in $WHEELS"
else
  echo "No bundled 'wheels' folder: the Python libraries will be downloaded from PyPI (this needs internet and takes longer)."
fi

# default radio model for this pack, unless the caller chose one
case " $* " in
  *" --model "*) ;;
  *) ARGS+=(--model ft991a) ;;
esac

echo "Starting the installation ..."
bash "$APP/install.sh" "${ARGS[@]}" "$@"

cat <<'EOF'

---------------------------------------------------------------------------------------------------
Next steps
  1. Open the address printed above in a browser on your phone or computer and create the administrator account.
  2. Admin > Config: choose the radio model and serial port, Save, restart when asked.
  3. Set the radio's own menus (CAT rate, USB audio): the manual is installed on the Pi at  https://<this pi>/docs/
     (also in the "docs" folder of this pack).
  4. Transmitting is DISABLED. Enable it only when you are ready (dummy load first): in the web page, Admin > Config >
     "Transmitting (PTT)" (asks for your password), or in the file:
        sudo nano /etc/radio-remote/config.toml     # set  allow_ptt = true  under [safety]
        sudo systemctl restart radio-remote
  5. Remote access from outside your home: log Tailscale in once with  sudo tailscale up  (install it with --with-tailscale).
---------------------------------------------------------------------------------------------------
EOF
