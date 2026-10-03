#!/usr/bin/env bash
# Shared by install.sh and update.sh. Source it; do not run it.
# Expects: PREFIX, DRY, and the run() function from the caller.

deploy_release() {   # $1 = source tree (repo root)
  local src="$1" ts dest item
  ts="$(date +%Y%m%d-%H%M%S)"
  dest="$PREFIX/releases/$ts"
  echo "==> Deploying release $ts"
  run install -d -m 0755 "$dest"
  for item in backend frontend scripts packaging config docs docs-html requirements.txt; do
    [ -e "$src/$item" ] && run cp -a "$src/$item" "$dest/"
  done
  run find "$dest" -name __pycache__ -type d -prune -exec rm -rf {} +
  run find "$dest" -name "*.sh" -exec chmod 0755 {} +          # the executable flag is lost when the files travel through Windows
  run chown -R root:root "$dest"
  # files that travelled through Windows/scp can arrive private (drwx------); the service user must be able to read the code
  run chmod -R u+rwX,go+rX,go-w "$dest"
  if [ "${DRY:-0}" != 1 ] && id -u radio-remote >/dev/null 2>&1; then
    runuser -u radio-remote -- test -r "$dest/backend/radio_remote/__init__.py" \
      || die "the service user cannot read $dest: check the permissions of /opt/radio-remote and the folder you copied from"
  fi
  if [ ! -x "$PREFIX/venv/bin/python" ]; then
    run python3 -m venv "$PREFIX/venv"
  fi
  echo "==> Installing Python dependencies (the first time this can take several minutes on a Pi)"
  if [ -n "${RR_WHEELS:-}" ]; then
    echo "    (offline: from $RR_WHEELS)"
    run "$PREFIX/venv/bin/pip" install --disable-pip-version-check --no-input --no-index --find-links "$RR_WHEELS" -r "$dest/requirements.txt"
  else
    run "$PREFIX/venv/bin/pip" install --disable-pip-version-check --no-input -r "$dest/requirements.txt"
  fi
  NEW_RELEASE="$dest"
}

activate_release() {   # $1 = release dir
  run ln -sfn "$1" "$PREFIX/current"
}

service_restart_and_check() {   # returns non-zero if the app does not become healthy
  run systemctl restart radio-remote
  if [ "${DRY:-0}" = 1 ]; then return 0; fi
  python3 "$PREFIX/current/scripts/rr_admin.py" health --timeout 45
}
