#!/usr/bin/env bash
# Runs as root, started by systemd (radio-remote-update.path) when an administrator presses "Update now" in the web page.
# The web service itself cannot install anything (it runs as an unprivileged user in a sandbox): it only drops the file
# /var/lib/radio-remote/update-request. This script removes that file, runs the normal self_update.sh (download, SHA-256 check, backup,
# install, health check, automatic rollback) and writes the outcome to /var/lib/radio-remote/update-status and update.log for the page to show.
set -uo pipefail

DATA="${RR_DATA_DIR:-/var/lib/radio-remote}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_UPDATE="${RR_SELF_UPDATE:-$HERE/self_update.sh}"
OWNER="${RR_OWNER:-radio-remote}"
REQ="$DATA/update-request"
STATUS="$DATA/update-status"
LOG="$DATA/update.log"

[ -f "$REQ" ] || exit 0                         # nothing was asked (the path unit can fire twice)
WHO="$(sed -n 's/^user=//p' "$REQ" | head -n 1)"
rm -f "$REQ"

status() {                                      # state, written atomically so the page never reads half a line
  printf '%s %s %s\n' "$1" "$(date +%s)" "${WHO:-?}" > "$STATUS.tmp" && mv -f "$STATUS.tmp" "$STATUS"
  chown "$OWNER:$OWNER" "$STATUS" "$LOG" 2>/dev/null || true
}

: > "$LOG"
status running
{ echo "Update requested by ${WHO:-?} at $(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo; } >> "$LOG"
bash "$SELF_UPDATE" >> "$LOG" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then status done; else status failed; fi
exit 0
