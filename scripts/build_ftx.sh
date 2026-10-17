#!/usr/bin/env bash
# Build the FT8 / FT4 library for Radio Remote: ft8_lib (MIT) in native/ftx/lib plus native/ftx/ftx_glue.c as ONE small shared library.
#
#   scripts/build_ftx.sh [OUT_DIR]      default OUT_DIR = ./dist/ftx      output: libftx-rr.so
# Needs only a C compiler. The release workflow builds it for both architectures; on a PC it is built by the tests (or by hand).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HERE/dist/ftx}"
mkdir -p "$OUT"
CC="${CC:-cc}"
case "$(uname -s)" in Darwin) EXT=dylib; SO="-dynamiclib" ;; *) EXT=so; SO="-shared" ;; esac
SRC="$HERE/native/ftx"
"$CC" -O2 -fPIC $SO -std=gnu11 -I"$SRC/lib" -o "$OUT/libftx-rr.$EXT" \
    "$SRC/ftx_glue.c" "$SRC"/lib/ft8/{constants,crc,decode,encode,ldpc,message,text}.c \
    "$SRC/lib/fft/kiss_fft.c" "$SRC/lib/fft/kiss_fftr.c" "$SRC/lib/common/monitor.c" -lm
echo "built $OUT/libftx-rr.$EXT"
