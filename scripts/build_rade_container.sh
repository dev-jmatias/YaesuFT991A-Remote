#!/usr/bin/env bash
# Build the RADE library inside a Debian 12 container, so the result also loads on Debian 12 ("bookworm", glibc 2.36) and on everything newer
# (a library built on a newer system would refuse to load on an older one). Then load it once in that container as a self-test.
#
#   scripts/build_rade_container.sh [OUT_DIR]        default OUT_DIR = ./dist/rade
#
# Needs Docker. Produces the same files as scripts/build_rade.sh, for the CPU this machine has (aarch64 or x86_64).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HERE/dist/rade}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

docker run --rm -v "$HERE":/src:ro -v "$OUT":/out debian:12 bash -ec '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq cmake build-essential autoconf automake libtool curl git ca-certificates xz-utils python3 python3-numpy libcodec2-1.0 >/dev/null
  mkdir /build && cd /src && tar --exclude=.git --exclude=.venv --exclude=dist -cf - . | tar -xf - -C /build
  cd /build
  bash scripts/build_rade.sh /out
  echo "==> self-test on Debian 12:"
  RADIO_REMOTE_RADE_LIB=/out/librade-rr.so python3 scripts/freedv_probe.py
'
