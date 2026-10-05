#!/usr/bin/env bash
# Copies the program into the pi-gen stage so the image build can install it. Run from anywhere; CI runs it before pi-gen.
# Build the manual first (python scripts/build_docs.py) so the image ships it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/image/stage-radio-remote/00-radio-remote/files/radio-remote-src"

[ -d "$ROOT/docs-html" ] || echo "WARNING: docs-html/ not built (python scripts/build_docs.py): the image will have no manual" >&2
rm -rf "$DEST"
mkdir -p "$DEST"
for item in backend frontend scripts packaging config docs docs-html requirements.txt install.sh update.sh pyproject.toml README.md LICENSE; do
  if [ -e "$ROOT/$item" ]; then cp -a "$ROOT/$item" "$DEST/"; fi
done
# the RADE library for the image (the release workflow builds it first): RADE_FILE=path/to/radio-remote-rade-linux-aarch64.tar.xz
if [ -n "${RADE_FILE:-}" ]; then
  [ -f "$RADE_FILE" ] || { echo "RADE_FILE not found: $RADE_FILE" >&2; exit 1; }
  mkdir -p "$DEST/rade" && cp "$RADE_FILE" "$DEST/rade/"
fi
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -f "$DEST/config/radio-remote.toml" "$DEST/config/none.toml"
echo "staged the program in $DEST"
