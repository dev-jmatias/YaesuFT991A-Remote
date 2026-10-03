#!/usr/bin/env bash
# Remove Radio Remote. Your config and user database are KEPT unless you pass --purge.
#   sudo scripts/uninstall.sh [--purge]
# Caddy and the apt packages are left installed (other things may use them).
set -euo pipefail
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

systemctl disable --now radio-remote.service radio-remote-backup.timer 2>/dev/null || true
rm -f /etc/systemd/system/radio-remote.service /etc/systemd/system/radio-remote-backup.service \
      /etc/systemd/system/radio-remote-backup.timer /etc/udev/rules.d/99-radio-remote.rules
systemctl daemon-reload
udevadm control --reload-rules 2>/dev/null || true
rm -rf /opt/radio-remote

if [ "$PURGE" = 1 ]; then
  rm -rf /etc/radio-remote /var/lib/radio-remote /var/backups/radio-remote
  userdel radio-remote 2>/dev/null || true
  echo "Removed everything, including config, users and backups."
else
  echo "Removed the program. Kept: /etc/radio-remote, /var/lib/radio-remote, /var/backups/radio-remote (use --purge to delete)."
fi
echo "If you used the generated Caddyfile, edit or remove /etc/caddy/Caddyfile yourself."
