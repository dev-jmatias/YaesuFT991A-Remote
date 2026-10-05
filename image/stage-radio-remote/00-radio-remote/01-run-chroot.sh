#!/bin/bash -e
# Runs inside the image (chroot, arm64). Installs the program exactly like a normal install, but only enables the services.
# The radio model starts as ft991a; the owner changes it in the web UI (Admin > Config). PTT stays disabled.
# the RADE library (neural FreeDV mode) goes into the image when the release build supplied it
RADE_ARG=()
[ -f /usr/src/radio-remote-src/rade/radio-remote-rade-linux-aarch64.tar.xz ] && RADE_ARG=(--rade-file /usr/src/radio-remote-src/rade/radio-remote-rade-linux-aarch64.tar.xz)
bash /usr/src/radio-remote-src/install.sh --image --with-tailscale --model ft991a --hostname raspberrypi.local "${RADE_ARG[@]}"

# the Caddy site name follows the hostname chosen at first boot (Raspberry Pi Imager's settings), see radio-remote-caddy-host
systemctl enable radio-remote-caddy-host.service

rm -rf /usr/src/radio-remote-src /tmp/tailscale-install.sh
apt-get clean
