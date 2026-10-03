#!/usr/bin/env bash
# Back up the config file and the user database to /var/backups/radio-remote (keeps the newest 14).
#   sudo scripts/backup.sh
set -euo pipefail
exec python3 /opt/radio-remote/current/scripts/rr_admin.py backup \
  --config /etc/radio-remote/config.toml --data-dir /var/lib/radio-remote \
  --dest /var/backups/radio-remote --keep 14
