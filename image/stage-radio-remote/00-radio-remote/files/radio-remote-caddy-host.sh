#!/bin/sh
# Keeps the HTTPS site name (Caddyfile) equal to "<hostname>.local" on every boot, so the address works whatever hostname
# was set when the image was written (Raspberry Pi Imager) or changed later. Runs before Caddy starts. Never fails the boot.
set -u
CADDYFILE=/etc/caddy/Caddyfile
HOST="$(hostname).local"
if grep -q "radio-remote managed" "$CADDYFILE" 2>/dev/null && grep -q "^$HOST {" "$CADDYFILE"; then
  exit 0                                   # already right
fi
/opt/radio-remote/venv/bin/python /opt/radio-remote/current/scripts/rr_admin.py render-caddy --host "$HOST" --out "$CADDYFILE" || true
exit 0
