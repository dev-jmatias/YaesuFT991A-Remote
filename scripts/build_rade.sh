#!/usr/bin/env bash
# Build the RADE V1 library for Radio Remote: rade_c (the C port of RADE, BSD) plus native/rade/rade_glue.c, as ONE shared library.
#
#   scripts/build_rade.sh [OUT_DIR]        default OUT_DIR = ./dist/rade
#
# Output (in OUT_DIR):  librade-rr.so  RADE-INFO.txt  LICENSE-rade_c  LICENSE-opus   and
#                       radio-remote-rade-linux-<arch>.tar.xz  (the same files, as the release asset)
# Needs: git, cmake, a C compiler, make, autoconf/automake/libtool, curl (the build downloads the Opus sources and the neural model weights).
# The release workflow runs it on an arm64 runner; on a PC it is only needed to run the RADE tests.
set -euo pipefail

RADE_C_URL="${RADE_C_URL:-https://github.com/freedv/rade_c}"
RADE_C_REF="${RADE_C_REF:-cc17222acc597339199bdfd7253c3e6cf147f953}"     # pinned: a known-good commit
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HERE/dist/rade}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

echo "==> Fetching rade_c ($RADE_C_REF)"
git clone --quiet "$RADE_C_URL" "$WORK/rade_c"
git -C "$WORK/rade_c" checkout --quiet "$RADE_C_REF"

echo "==> Adding the Radio Remote glue to the library"
cp "$HERE/native/rade/rade_glue.c" "$WORK/rade_c/src/rade_glue.c"
# put the glue into the same shared library as rade_api.c (it needs the Opus FARGAN/LPCNet code that library already contains)
sed -i 's/^    rade_api\.c$/    rade_api.c\n    rade_glue.c/' "$WORK/rade_c/src/CMakeLists.txt"
grep -q 'rade_glue.c' "$WORK/rade_c/src/CMakeLists.txt" || { echo "could not patch src/CMakeLists.txt" >&2; exit 1; }

echo "==> Building (downloads Opus and the model weights)"
mkdir -p "$WORK/rade_c/build"
(cd "$WORK/rade_c/build" && cmake -DCMAKE_BUILD_TYPE=Release .. >/dev/null && make -j"$(nproc)" rade >/dev/null)

LIB="$(ls "$WORK"/rade_c/build/src/librade.so.* | head -1)"
strip --strip-unneeded -o "$OUT/librade-rr.so" "$LIB"
cp "$WORK/rade_c/LICENSE" "$OUT/LICENSE-rade_c"
OPUS_COPYING="$(find "$WORK/rade_c/build" -maxdepth 4 -name COPYING -path '*build_opus*' | head -1)"
if [ -n "$OPUS_COPYING" ]; then cp "$OPUS_COPYING" "$OUT/LICENSE-opus"; else echo "Opus: BSD-3-Clause, see https://github.com/xiph/opus" > "$OUT/LICENSE-opus"; fi
cat > "$OUT/RADE-INFO.txt" <<EOF
RADE V1 library for Radio Remote
rade_c: $RADE_C_URL @ $RADE_C_REF
built: $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(uname -m)
glue ABI version: 1
EOF

ARCH="$(uname -m)"
TAR="$OUT/radio-remote-rade-linux-$ARCH.tar.xz"
(cd "$OUT" && tar -cJf "$TAR" librade-rr.so RADE-INFO.txt LICENSE-rade_c LICENSE-opus)
echo "==> Done: $OUT/librade-rr.so ($(du -h "$OUT/librade-rr.so" | cut -f1)), $TAR ($(du -h "$TAR" | cut -f1))"
