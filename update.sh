#!/usr/bin/env bash
# Update an existing Radio Remote install from the cloned repository.
#
#   cd radio-remote && git pull
#   sudo ./update.sh [--dry-run]
#
# Steps: backup -> deploy new release next to the old one -> switch -> restart -> health check.
# If the new version is not healthy it switches back to the previous release automatically.
# Config and the user database are never touched by an update.
set -euo pipefail

PREFIX=/opt/radio-remote
CONF_DIR=/etc/radio-remote
DATA_DIR=/var/lib/radio-remote
BACKUP_DIR=/var/backups/radio-remote
KEEP_RELEASES=3
DRY=0

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
die() { echo "ERROR: $*" >&2; exit 1; }
# shellcheck source=scripts/deploy_lib.sh
. "$SRC/scripts/deploy_lib.sh"

[ "$DRY" = 1 ] || [ "$(id -u)" -eq 0 ] || die "run as root: sudo ./update.sh"
[ -L "$PREFIX/current" ] || die "no existing install found at $PREFIX (run ./install.sh first)"
[ -d "$SRC/backend/radio_remote" ] || die "run this from the cloned repository"

PREVIOUS="$(readlink -f "$PREFIX/current")"
echo "==> Current release: $PREVIOUS"

echo "==> Backing up configuration and user database"
run python3 "$PREVIOUS/scripts/rr_admin.py" backup --config "$CONF_DIR/config.toml" --data-dir "$DATA_DIR" --dest "$BACKUP_DIR" --keep 14

ensure_codec2                  # FreeDV (optional, never fatal)
NEW_RELEASE=""
deploy_release "$SRC"           # installs dependencies BEFORE anything is switched; a failure here changes nothing live

echo "==> Switching to $NEW_RELEASE"
activate_release "$NEW_RELEASE"
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote.service" /etc/systemd/system/radio-remote.service
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote-backup.service" /etc/systemd/system/radio-remote-backup.service
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote-backup.timer" /etc/systemd/system/radio-remote-backup.timer
run systemctl daemon-reload

if service_restart_and_check; then
  echo "==> Updated and healthy."
  run python3 "$NEW_RELEASE/scripts/rr_admin.py" prune --dir "$PREFIX/releases" --keep "$KEEP_RELEASES"
  exit 0
fi

echo "!! The new version is not healthy. Rolling back to $PREVIOUS" >&2
journalctl -u radio-remote -n 25 --no-pager >&2 || true
activate_release "$PREVIOUS"
if service_restart_and_check; then
  echo "Rolled back successfully. The failed release is kept at $NEW_RELEASE for inspection." >&2
else
  echo "Rollback is ALSO unhealthy. Run: sudo $PREFIX/current/scripts/doctor.sh" >&2
fi
exit 1
