#!/usr/bin/env bash
# Runs as root, started by systemd (radio-remote-power.path) when an administrator presses "Reboot the system" in the web page.
# The web service itself cannot reboot the machine (it runs as an unprivileged user in a sandbox): it only drops the file
# /var/lib/radio-remote/power-request. This script removes that file, checks it asks for a reboot (the only thing it will do),
# waits a moment so the page gets its answer, and reboots.
set -uo pipefail

DATA="${RR_DATA_DIR:-/var/lib/radio-remote}"
REQ="$DATA/power-request"
REBOOT_CMD="${RR_REBOOT_CMD:-systemctl reboot}"

[ -f "$REQ" ] || exit 0                         # nothing was asked (the path unit can fire twice)
ACTION="$(sed -n 's/^action=//p' "$REQ" | head -n 1)"
WHO="$(sed -n 's/^user=//p' "$REQ" | head -n 1)"
rm -f "$REQ"

case "$ACTION" in
  reboot) ;;
  *) echo "radio-remote-power: ignored an unknown request (${ACTION:-empty})" >&2; exit 0 ;;
esac

logger -t radio-remote-power "reboot requested by ${WHO:-?} in the web page" 2>/dev/null || true
sleep "${RR_REBOOT_DELAY:-3}"                   # the page gets its answer first
# shellcheck disable=SC2086
exec $REBOOT_CMD
