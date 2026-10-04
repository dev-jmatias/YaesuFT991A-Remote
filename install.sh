#!/usr/bin/env bash
# Radio Remote installer for Raspberry Pi OS / Debian (64-bit).
#
#   git clone <repo> radio-remote && cd radio-remote
#   sudo ./install.sh --dry-run        # show what it would do, change nothing
#   sudo ./install.sh                  # install (safe to re-run)
#
# What it does:  apt packages -> service user -> /opt/radio-remote (venv + release) -> config -> udev rule
#                -> systemd services -> Caddy (HTTPS) -> health check.
# What it NEVER does:  enable transmitting (safety.allow_ptt stays false), touch the firewall, or talk to the radio.
set -euo pipefail

APP_USER=radio-remote
PREFIX=/opt/radio-remote
CONF_DIR=/etc/radio-remote
DATA_DIR=/var/lib/radio-remote
BACKUP_DIR=/var/backups/radio-remote
MODEL=mock
HOST_NAME=""
WITH_CADDY=1
WITH_HAMLIB=0
WITH_TAILSCALE=0
WHEELS=""
DRY=0
FORCE_ARCH=0
IMAGE=0

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]
  --model NAME      initial radio model: mock (default), ft991a   (you can change it later in the web UI)
  --hostname NAME   name for the HTTPS site (default: <this host>.local)
  --no-caddy        do not install/configure Caddy (you then provide HTTPS yourself)
  --with-hamlib     also install hamlib utilities (rigctl) for diagnostics
  --with-tailscale  also install Tailscale (you still log in once with: sudo tailscale up)
  --wheels DIR      install the Python packages from DIR (offline, no PyPI): used by the installer pack
  --dry-run         print the actions only
  --force-arch      allow a 32-bit ARM OS (Python wheels may be missing; not recommended)
  --image           install into an OS image being built (chroot): enable the services but do not start them,
                    and skip the health check. Used by image/ (pi-gen); not for a running Pi.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="${2:?--model needs a value}"; shift 2 ;;
    --hostname) HOST_NAME="${2:?--hostname needs a value}"; shift 2 ;;
    --no-caddy) WITH_CADDY=0; shift ;;
    --with-hamlib) WITH_HAMLIB=1; shift ;;
    --with-tailscale) WITH_TAILSCALE=1; shift ;;
    --wheels) WHEELS="${2:?--wheels needs a directory}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --force-arch) FORCE_ARCH=1; shift ;;
    --image) IMAGE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
# in an image build (chroot) nothing can be started: enable only; on a running Pi enable and start now
if [ "$IMAGE" = 1 ]; then NOW=""; else NOW="--now"; fi
die() { echo "ERROR: $*" >&2; exit 1; }
# shellcheck source=scripts/deploy_lib.sh
. "$SRC/scripts/deploy_lib.sh"

# ---- preflight --------------------------------------------------------------------------------------------------
[ -d "$SRC/backend/radio_remote" ] || die "run this from the cloned repository (backend/ not found next to install.sh)"
if [ "$DRY" != 1 ] && [ "$(id -u)" -ne 0 ]; then die "run as root: sudo ./install.sh"; fi
[ -f /etc/debian_version ] || die "this installer supports Debian-based systems (Raspberry Pi OS). See docs/INSTALL.md for manual steps."
command -v systemctl >/dev/null || die "systemd is required"

case "$(uname -m)" in
  aarch64|x86_64) ;;
  *) if [ "$FORCE_ARCH" != 1 ]; then
       die "$(uname -m) detected. Use a 64-bit Raspberry Pi OS: the audio libraries ship ready-made 64-bit wheels only (override with --force-arch)."
     fi ;;
esac

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  die "Python 3.11 or newer is required (Raspberry Pi OS 'Bookworm' has it; older releases do not)."
fi

case "$MODEL" in
  mock|ft991a) ;;
  ftdx10|ftdx101d|ftdx101mp|ft710) echo "NOTE: the $MODEL profile is EXPERIMENTAL (written from the manual, never tested on a real radio): see docs/08-other-radios.md" ;;
  *) die "--model must be one of: mock ft991a ftdx10 ftdx101d ftdx101mp ft710" ;;
esac
if [ -n "$WHEELS" ]; then
  [ -d "$WHEELS" ] || die "--wheels: directory not found: $WHEELS"
  WHEELS="$(cd "$WHEELS" && pwd)"            # absolute: pip runs from another directory
fi
export RR_WHEELS="$WHEELS"
[ -n "$HOST_NAME" ] || HOST_NAME="$(hostname).local"

# ---- packages ---------------------------------------------------------------------------------------------------
echo "==> Installing system packages"
PKGS="python3 python3-venv python3-pip alsa-utils libopus0 curl ca-certificates"
[ "$WITH_CADDY" = 1 ] && PKGS="$PKGS caddy"
[ "$WITH_HAMLIB" = 1 ] && PKGS="$PKGS libhamlib-utils"
run apt-get update
# shellcheck disable=SC2086
run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $PKGS

ensure_codec2

# ---- optional: Tailscale (package only; the login stays a manual step) ---------------------------------------------
if [ "$WITH_TAILSCALE" = 1 ]; then
  echo "==> Installing Tailscale"
  if command -v tailscale >/dev/null 2>&1; then
    echo "    already installed"
  else
    echo "    downloading Tailscale's official install script (you can read it first: /tmp/tailscale-install.sh)"
    run curl -fsSL https://tailscale.com/install.sh -o /tmp/tailscale-install.sh
    run sh /tmp/tailscale-install.sh
  fi
fi

# ---- service user and directories -------------------------------------------------------------------------------
echo "==> Service user and directories"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  run useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi
run usermod -aG dialout,audio "$APP_USER"
run install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$DATA_DIR"
run install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$CONF_DIR"
run install -d -m 0755 "$PREFIX" "$PREFIX/releases"
run install -d -m 0700 "$BACKUP_DIR"

# ---- code, venv, config -----------------------------------------------------------------------------------------
NEW_RELEASE=""
deploy_release "$SRC"
activate_release "$NEW_RELEASE"

echo "==> Configuration"
run "$PREFIX/venv/bin/python" "$NEW_RELEASE/scripts/rr_admin.py" render-config \
    --out "$CONF_DIR/config.toml" --data-dir "$DATA_DIR" --model "$MODEL" --owner "$APP_USER" --app-dir "$NEW_RELEASE"

# ---- udev, systemd ----------------------------------------------------------------------------------------------
echo "==> udev rule and systemd units"
run install -m 0644 "$NEW_RELEASE/packaging/99-radio-remote.rules" /etc/udev/rules.d/99-radio-remote.rules
if [ "$DRY" != 1 ] && [ "$IMAGE" != 1 ]; then udevadm control --reload-rules || true; udevadm trigger --subsystem-match=tty || true; fi
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote.service" /etc/systemd/system/radio-remote.service
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote-backup.service" /etc/systemd/system/radio-remote-backup.service
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote-backup.timer" /etc/systemd/system/radio-remote-backup.timer
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote-update.path" /etc/systemd/system/radio-remote-update.path
run install -m 0644 "$NEW_RELEASE/packaging/radio-remote-update.service" /etc/systemd/system/radio-remote-update.service
run systemctl daemon-reload
run systemctl enable $NOW radio-remote.service
run systemctl enable $NOW radio-remote-backup.timer
run systemctl enable $NOW radio-remote-update.path          # the "Update now" button in the web page

# ---- HTTPS ------------------------------------------------------------------------------------------------------
if [ "$WITH_CADDY" = 1 ]; then
  echo "==> HTTPS (Caddy) for https://$HOST_NAME"
  run python3 "$NEW_RELEASE/scripts/rr_admin.py" render-caddy --host "$HOST_NAME" --hosts-file "$CONF_DIR/caddy-extra-hosts" --out /etc/caddy/Caddyfile
  run systemctl enable $NOW caddy
  if [ "$IMAGE" != 1 ]; then run systemctl reload caddy || run systemctl restart caddy; fi
fi

if [ "$IMAGE" = 1 ]; then
  echo "Image build: the services are enabled and start on the first boot of the finished image."
  exit 0
fi

# ---- verify -----------------------------------------------------------------------------------------------------
echo "==> Checking that the app is up"
if [ "$DRY" != 1 ]; then
  if ! python3 "$NEW_RELEASE/scripts/rr_admin.py" health --timeout 60; then
    echo "The service did not become healthy. Recent log:" >&2
    journalctl -u radio-remote -n 30 --no-pager >&2 || true
    die "installation finished but the app is not healthy; see above, or run: sudo $PREFIX/current/scripts/doctor.sh"
  fi
fi

cat <<EOF

Done.

  Open:   https://$HOST_NAME        (or http://127.0.0.1:8080 on the Pi itself)
  First visit creates the administrator account.
  Then:   Admin > Config  -> choose your radio model and serial port, Save, Restart.
  PTT:    transmitting is DISABLED. When you are ready (dummy load first), sign in as the administrator and use
          Admin > Config > "Transmitting (PTT)", or set  allow_ptt = true  in $CONF_DIR/config.toml  and run:
          sudo systemctl restart radio-remote
  HTTPS:  your devices must trust Caddy's local certificate once (docs/06-security-remote.md), or use Tailscale.
  Docs:   https://$HOST_NAME/docs/   (the manual is installed on the Pi, no internet needed)
  Help:   sudo $PREFIX/current/scripts/doctor.sh      (paste its output when asking for help)
EOF
