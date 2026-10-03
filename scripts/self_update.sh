#!/usr/bin/env bash
# Update Radio Remote to the newest release published on GitHub.
#
#   sudo /opt/radio-remote/current/scripts/self_update.sh            # update if a newer release exists
#   sudo /opt/radio-remote/current/scripts/self_update.sh --check    # only say whether one exists (no root needed)
#   sudo /opt/radio-remote/current/scripts/self_update.sh --force    # install the newest release even if it is not newer
#
# It downloads the release's update package, verifies its checksum, then runs the package's own update.sh: backup of the settings and
# user database first, the new version installed next to the old one, health check, and an automatic switch back if the new version
# does not start. Settings and accounts are never touched. No internet on the Pi? Download the update package on another computer and
# use update.sh from the unpacked folder instead (see docs/operations.md).
set -euo pipefail

REPO="${RR_REPO:-dev-jmatias/YaesuFT991A-Remote}"
CHECK=0
EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; EXTRA+=(--check); shift ;;
    --force) EXTRA+=(--force); shift ;;
    --repo) REPO="${2:?--repo needs owner/name}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$CHECK" != 1 ] && [ "$(id -u)" -ne 0 ]; then
  echo "Run it with sudo:   sudo $0" >&2
  exit 1
fi
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

OUT="$(mktemp -d /var/tmp/rr-update.XXXXXX)"
trap 'rm -rf "$OUT"' EXIT

set +e
LOG="$(python3 "$HERE/rr_admin.py" fetch-release --repo "$REPO" --out "$OUT" "${EXTRA[@]}")"
RC=$?
set -e
printf '%s\n' "$LOG"
[ "$RC" -eq 10 ] && exit 0                 # already up to date
[ "$RC" -eq 0 ] || exit 1
[ "$CHECK" = 1 ] && exit 0

NEW="$(printf '%s\n' "$LOG" | sed -n 's/^READY //p' | tail -n 1)"
[ -n "$NEW" ] && [ -f "$NEW/update.sh" ] || { echo "ERROR: the downloaded package has no update.sh" >&2; exit 1; }
cd "$NEW"
bash ./update.sh
