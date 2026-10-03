#!/usr/bin/env bash
# Restore a backup made by backup.sh. The service is stopped while files are replaced.
#   sudo scripts/restore.sh /var/backups/radio-remote/radio-remote-YYYYMMDD-HHMMSS.tar.gz
# The previous files are kept next to the originals with a .before-restore suffix.
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: sudo $0 BACKUP.tar.gz" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
systemctl stop radio-remote
python3 /opt/radio-remote/current/scripts/rr_admin.py restore --archive "$1" \
  --config /etc/radio-remote/config.toml --data-dir /var/lib/radio-remote --owner radio-remote
systemctl start radio-remote
python3 /opt/radio-remote/current/scripts/rr_admin.py health --timeout 45
