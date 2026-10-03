#!/bin/bash -e
# Runs on the build host. Copies the program (staged by the workflow / build script into files/radio-remote-src) and the
# first-boot helper into the image's root filesystem. The chroot step (01-run-chroot.sh) then installs it.
SRC="${STAGE_DIR}/00-radio-remote/files/radio-remote-src"
[ -f "${SRC}/install.sh" ] || { echo "radio-remote-src not staged: run image/stage_program.sh first" >&2; exit 1; }

rm -rf "${ROOTFS_DIR}/tmp/radio-remote-src"
install -d "${ROOTFS_DIR}/tmp/radio-remote-src"
cp -a "${SRC}/." "${ROOTFS_DIR}/tmp/radio-remote-src/"

install -m 0755 files/radio-remote-caddy-host.sh "${ROOTFS_DIR}/usr/local/sbin/radio-remote-caddy-host"
install -m 0644 files/radio-remote-caddy-host.service "${ROOTFS_DIR}/etc/systemd/system/radio-remote-caddy-host.service"
