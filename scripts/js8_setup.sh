#!/usr/bin/env bash
# Runs as root, started by systemd (radio-remote-js8setup.path) when an administrator presses "Prepare the system" on the JS8 screen. The web service cannot install packages
# (it runs as an unprivileged user in a sandbox): it only drops the file /var/lib/radio-remote/js8-setup-request. This script removes that file and then does the one-off
# system preparation JS8Call needs, and nothing else:
#   * installs the libraries Qt (JS8Call) loads even without a screen, and PulseAudio (a PRIVATE copy is started only for JS8Call; the default user units are masked below);
#   * loads the ALSA loopback module (snd-aloop) now and at every boot.
# The result is written to /var/lib/radio-remote/js8-setup.log (shown on the web page).
set -uo pipefail

DATA="${RR_DATA_DIR:-/var/lib/radio-remote}"
REQ="$DATA/js8-setup-request"
LOG="$DATA/js8-setup.log"
[ -f "$REQ" ] || exit 0
WHO="$(sed -n 's/^user=//p' "$REQ" | head -n 1)"
rm -f "$REQ"

exec > "$LOG" 2>&1
echo "JS8 system preparation requested by ${WHO:-?} at $(date -u +%FT%TZ)"
logger -t radio-remote-js8setup "preparation requested by ${WHO:-?} in the web page" 2>/dev/null || true

PKGS="${RR_JS8_PACKAGES:-libgl1 libegl1 libfontconfig1 libharfbuzz0b libfreetype6 libx11-xcb1 pulseaudio pulseaudio-utils}"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y --no-install-recommends $PKGS || { echo "RESULT: failed (apt-get)"; exit 1; }
else
  echo "This is not a Debian system: install these packages by hand: $PKGS"
fi

# the default per-user PulseAudio units must never start a sound server that takes the radio's USB sound card
for u in $(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1}'); do
  systemctl --global mask pulseaudio.socket pulseaudio.service >/dev/null 2>&1 || true
done
systemctl --global mask pulseaudio.socket pulseaudio.service >/dev/null 2>&1 || true

if ! grep -q "^snd_aloop" /proc/modules 2>/dev/null; then
  modprobe snd-aloop || { echo "RESULT: failed (modprobe snd-aloop: this kernel has no loopback sound module)"; exit 2; }
fi
printf 'snd-aloop\n' > /etc/modules-load.d/radio-remote-js8.conf
grep -q "Loopback" /proc/asound/cards 2>/dev/null || { echo "RESULT: failed (the Loopback sound card did not appear)"; exit 3; }
chgrp -R audio /dev/snd 2>/dev/null || true
echo "RESULT: ok"
