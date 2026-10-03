#!/usr/bin/env bash
# Make Radio Remote reachable over Tailscale: https://<this Pi>.<your tailnet>.ts.net with a real, trusted certificate.
#
#   sudo tailscale up                                                # once, sign in (if you have not)
#   sudo /opt/radio-remote/current/scripts/tailscale_setup.sh        # add the Tailscale name to the HTTPS front end (Caddy)
#   sudo /opt/radio-remote/current/scripts/tailscale_setup.sh --remove    # back to the LAN name only
#   ... --dry-run                                                    # show what it would do, change nothing
#
# Why it is needed: Caddy (the HTTPS front end) answers only to the names it is configured for, by default <hostname>.local. A request
# that arrives under the Tailscale name or the 100.x.y.z address matches no site and gets no answer. This script adds the Tailscale name
# (kept in /etc/radio-remote/caddy-extra-hosts, so updates and reboots do not lose it) and lets Caddy fetch the certificate from
# tailscaled. In the Tailscale admin console (login.tailscale.com/admin/dns) MagicDNS and "HTTPS Certificates" must be switched on.
set -euo pipefail

CONF_DIR=/etc/radio-remote
HOSTS_FILE="$CONF_DIR/caddy-extra-hosts"
CADDYFILE=/etc/caddy/Caddyfile
TS_DEFAULTS=/etc/default/tailscaled
DRY=0
REMOVE=0

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --remove) REMOVE=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
die() { echo "ERROR: $*" >&2; exit 1; }

[ "$DRY" = 1 ] || [ "$(id -u)" -eq 0 ] || die "run as root: sudo $0"
command -v caddy >/dev/null || die "Caddy (the HTTPS front end) is not installed: run the installer again"
[ -f "$CADDYFILE" ] && grep -q "radio-remote managed" "$CADDYFILE" \
  || die "$CADDYFILE was not written by Radio Remote: add the Tailscale name by hand (docs/tailscale.md)"

# the main (LAN) name: the first site in the current Caddyfile
PRIMARY="$(grep -m1 -E '^[^#[:space:]][^ ]* \{$' "$CADDYFILE" | cut -d' ' -f1 || true)"
[ -n "$PRIMARY" ] || PRIMARY="$(hostname).local"

apply_caddyfile() {   # render with the names in HOSTS_FILE, check it, switch to it, reload
  local dir new
  dir="$(mktemp -d /var/tmp/rr-caddy.XXXXXX)"
  new="$dir/Caddyfile"
  python3 "$HERE/rr_admin.py" render-caddy --host "$PRIMARY" --hosts-file "$HOSTS_FILE" --out "$new" >/dev/null
  if ! caddy validate --config "$new" --adapter caddyfile >/dev/null 2>&1; then
    caddy validate --config "$new" --adapter caddyfile || true
    rm -rf "$dir"
    die "Caddy does not accept the new configuration: nothing was changed"
  fi
  run cp -a "$CADDYFILE" "$CADDYFILE.before-tailscale"
  run install -m 0644 "$new" "$CADDYFILE"
  rm -rf "$dir"
  run systemctl reload caddy || run systemctl restart caddy
}

if [ "$REMOVE" = 1 ]; then
  echo "==> Removing the Tailscale name from the HTTPS front end"
  run rm -f "$HOSTS_FILE"
  apply_caddyfile
  echo "Done: Radio Remote now answers only to https://$PRIMARY"
  exit 0
fi

echo "==> Looking for this machine's Tailscale name"
NAME="$(python3 "$HERE/rr_admin.py" tailscale-name)" || exit 1
echo "    $NAME"

# Caddy runs as the 'caddy' user; tailscaled only hands certificates to root unless it is told otherwise.
if ! grep -qs '^TS_PERMIT_CERT_UID=caddy' "$TS_DEFAULTS"; then
  echo "==> Allowing Caddy to get certificates from Tailscale ($TS_DEFAULTS)"
  if [ "$DRY" = 1 ]; then
    echo "+ set TS_PERMIT_CERT_UID=caddy in $TS_DEFAULTS and restart tailscaled"
  else
    if grep -qs '^TS_PERMIT_CERT_UID=' "$TS_DEFAULTS"; then
      sed -i 's/^TS_PERMIT_CERT_UID=.*/TS_PERMIT_CERT_UID=caddy/' "$TS_DEFAULTS"
    else
      printf '\nTS_PERMIT_CERT_UID=caddy\n' >> "$TS_DEFAULTS"
    fi
    systemctl restart tailscaled
    for _ in $(seq 1 20); do                       # wait until it is connected again
      tailscale status >/dev/null 2>&1 && break
      sleep 1
    done
  fi
fi

echo "==> Adding $NAME to the HTTPS front end"
run install -d -m 0755 "$CONF_DIR"
if [ "$DRY" = 1 ]; then
  echo "+ add $NAME to $HOSTS_FILE"
else
  grep -qx "$NAME" "$HOSTS_FILE" 2>/dev/null || echo "$NAME" >> "$HOSTS_FILE"
  chmod 0644 "$HOSTS_FILE"
fi
apply_caddyfile

if [ "$DRY" = 1 ]; then
  echo "Dry run finished: nothing was changed."
  exit 0
fi

echo "==> Checking https://$NAME (the first request makes Caddy fetch the certificate: up to a minute)"
ok=0
for _ in $(seq 1 12); do
  if curl -fsS --max-time 15 --resolve "$NAME:443:127.0.0.1" "https://$NAME/api/status" >/dev/null 2>&1; then ok=1; break; fi
  sleep 5
done
if [ "$ok" = 1 ]; then
  cat <<EOF

Done. From any device signed in to your Tailscale network (the Tailscale app switched on), open:

    https://$NAME

The microphone works there because the certificate is a real one. Check the audio path with:  tailscale ping $NAME
("direct" is good; "via DERP" means relayed and the audio may lag).
EOF
else
  echo "The page did not answer yet. Recent Caddy messages:" >&2
  journalctl -u caddy -n 15 --no-pager >&2 || true
  die "https://$NAME did not come up: check that HTTPS Certificates are enabled in the Tailscale admin console, then run this again"
fi
