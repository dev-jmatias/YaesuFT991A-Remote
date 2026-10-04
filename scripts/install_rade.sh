#!/usr/bin/env bash
# Install (or remove) the optional RADE library, the neural FreeDV mode. Without it FreeDV still offers 1600, 700D and 700E.
#
#   sudo /opt/radio-remote/current/scripts/install_rade.sh                  # download it from the matching GitHub release and install it
#   sudo /opt/radio-remote/current/scripts/install_rade.sh --file F.tar.xz  # install from a file you copied to the Pi (no internet needed)
#   sudo /opt/radio-remote/current/scripts/install_rade.sh --remove         # take it out again
#
# The download is verified against the release's SHA256SUMS. The library goes to /opt/radio-remote/lib (outside the program folders, so
# updates keep it) and the service is restarted so the FreeDV tab offers RADE. After a Radio Remote update that changes the RADE library the
# release notes say so; run this script again then.
set -euo pipefail

REPO="${RR_REPO:-dev-jmatias/YaesuFT991A-Remote}"
DEST="${RR_RADE_DIR:-/opt/radio-remote/lib}"
FILE="" SHA="" REMOVE=0 RESTART=1
while [ $# -gt 0 ]; do
  case "$1" in
    --file) FILE="${2:?--file needs a path}"; shift 2 ;;
    --sha256) SHA="${2:?--sha256 needs a checksum}"; shift 2 ;;
    --remove) REMOVE=1; shift ;;
    --repo) REPO="${2:?--repo needs owner/name}"; shift 2 ;;
    --no-restart) RESTART=0; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo:   sudo $0" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

if [ "$REMOVE" = 1 ]; then
  rm -f "$DEST/librade-rr.so" "$DEST/RADE-INFO.txt" "$DEST/LICENSE-rade_c" "$DEST/LICENSE-opus"
  rmdir "$DEST" 2>/dev/null || true
  echo "RADE removed."
else
  ARGS=(fetch-rade --repo "$REPO" --dest "$DEST")
  [ -n "$FILE" ] && ARGS+=(--file "$FILE")
  [ -n "$SHA" ] && ARGS+=(--sha256 "$SHA")
  python3 "$HERE/rr_admin.py" "${ARGS[@]}"
  chmod 0755 "$DEST"
  echo "RADE installed in $DEST."
fi

if [ "$RESTART" = 1 ] && command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet radio-remote 2>/dev/null; then
  echo "Restarting the service so the FreeDV tab picks it up ..."
  systemctl restart radio-remote
fi
echo "Open the FreeDV tab and choose the mode RADE."
