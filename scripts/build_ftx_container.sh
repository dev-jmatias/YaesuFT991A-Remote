#!/usr/bin/env bash
# Build the FT8 / FT4 library inside a Debian 12 container, so the result also loads on Debian 12 ("bookworm", glibc 2.36) and on everything newer, then load it once
# there and decode a generated FT8 slot as a self-test.
#
#   scripts/build_ftx_container.sh [OUT_DIR]        default OUT_DIR = ./dist/ftx        output: libftx-rr.so  (for the CPU this machine has: aarch64 or x86_64)
# Needs Docker. The release workflow runs it on both architectures and packs the two libraries into the program (backend/radio_remote/audio/native/linux-<arch>/).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HERE/dist/ftx}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
docker run --rm -v "$HERE":/src:ro -v "$OUT":/out debian:12 bash -ec '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq build-essential python3 python3-numpy >/dev/null
  mkdir /build && cd /src && tar --exclude=.git --exclude=.venv --exclude=dist -cf - . | tar -xf - -C /build
  cd /build
  bash scripts/build_ftx.sh /out
  echo "==> self-test on Debian 12:"
  PYTHONPATH=backend RADIO_REMOTE_FTX_LIB=/out/libftx-rr.so python3 - <<PY
import numpy as np
from radio_remote.audio import ftx
ok, why = ftx.available()
assert ok, why
sig, canon = ftx.encode("CQ K1FM FN30", "FT8", 1000.0, 12000)
x = np.zeros(15 * 12000, dtype=np.float32)
x[6000:6000 + len(sig)] = sig * 0.5
got = [d.text for d in ftx.decode(x, "FT8")]
assert got == ["CQ K1FM FN30"], got
print("decoded", got)
PY
'
