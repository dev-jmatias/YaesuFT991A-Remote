#!/bin/bash -e
# Runs inside the image (chroot, arm64). Installs the program exactly like a normal install, but only enables the services.
# The radio model starts as ft991a; the owner changes it in the web UI (Admin > Config). PTT stays disabled.
bash /usr/src/radio-remote-src/install.sh --image --with-tailscale --model ft991a --hostname raspberrypi.local

# the Caddy site name follows the hostname chosen at first boot (Raspberry Pi Imager's settings), see radio-remote-caddy-host
systemctl enable radio-remote-caddy-host.service

rm -rf /usr/src/radio-remote-src /tmp/tailscale-install.sh
apt-get clean
