#!/usr/bin/env python3
"""Deployment helper used by install.sh / update.sh / backup / doctor. Logic lives here (not in bash) so it is testable.

Only `render-config` needs the app's venv (it imports radio_remote.config); everything else is plain stdlib.

  rr_admin.py render-config --out /etc/radio-remote/config.toml --data-dir /var/lib/radio-remote [--model ft991a]
  rr_admin.py render-caddy  --host raspberrypi.local --out /etc/caddy/Caddyfile
  rr_admin.py backup  --config CFG --data-dir DIR --dest DIR [--keep 10]
  rr_admin.py restore --archive FILE --config CFG --data-dir DIR
  rr_admin.py prune   --dir DIR --keep N [--glob PATTERN]
  rr_admin.py health  --url http://127.0.0.1:8080/api/status [--timeout 30]
  rr_admin.py doctor  [--config CFG] [--url URL]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_NAME = "radio-remote.db"


# ----------------------------------------------------------------------- config
def cmd_render_config(a) -> int:
    out = Path(a.out)
    if out.exists() and not a.force:
        print(f"kept existing config: {out}")
        return 0
    sys.path.insert(0, str(Path(a.app_dir or ROOT) / "backend"))
    from radio_remote import config

    cfg = config.load(None)
    cfg["storage"]["data_dir"] = a.data_dir
    cfg["radio"]["model"] = a.model
    cfg["server"]["host"] = "127.0.0.1"            # always loopback: the proxy is the only thing facing the network
    cfg["server"]["port"] = a.port
    cfg["safety"]["allow_ptt"] = False             # the installer never enables transmitting
    if a.allowed_origin:
        cfg["server"]["allowed_origins"] = list(a.allowed_origin)
    try:
        config.save(cfg, out)                      # validates, atomic, mode 0600
    except config.ConfigError as e:
        print(f"invalid configuration: {e}", file=sys.stderr)
        return 2
    if a.owner:
        shutil.chown(out, user=a.owner)
    print(f"wrote {out} (model={a.model}, allow_ptt=false)")
    return 0


def cmd_render_caddy(a) -> int:
    out = Path(a.out)
    host = a.host.strip()
    if not host or any(c in host for c in " \t\n{}\"'\\;"):
        print(f"refusing unusual hostname {host!r}", file=sys.stderr)
        return 2
    if out.exists():
        text = out.read_text(encoding="utf-8", errors="replace")
        stock = "/usr/share/caddy" in text             # the Caddyfile Debian ships; safe to replace (backed up)
        if "radio-remote managed" in text:
            pass                                    # ours: safe to rewrite
        elif text.strip() and not (stock or a.force):
            print(f"{out} exists and was not written by this installer; leaving it. See config/Caddyfile.example.")
            return 0
        else:
            out.rename(out.with_name(out.name + ".bak-" + datetime.now().strftime("%Y%m%d%H%M%S")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# radio-remote managed (safe to regenerate with install.sh). Edit config/Caddyfile.example for other layouts.\n"
        f"{host} {{\n\ttls internal\n\tencode zstd gzip\n\treverse_proxy 127.0.0.1:{a.port}\n}}\n",
        encoding="utf-8")
    print(f"wrote {out} for https://{host}")
    return 0


# ------------------------------------------------------------- backup / restore
def cmd_backup(a) -> int:
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = dest / f"radio-remote-{stamp}.tar.gz"
    db = Path(a.data_dir) / DB_NAME
    with tempfile.TemporaryDirectory() as tmp:
        members = []
        if Path(a.config).exists():
            shutil.copy2(a.config, Path(tmp) / "config.toml")
            members.append("config.toml")
        if db.exists():
            snap = Path(tmp) / DB_NAME                # consistent snapshot even while the service is writing
            src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            dst = sqlite3.connect(snap)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            members.append(DB_NAME)
        if not members:
            print("nothing to back up (no config, no database)", file=sys.stderr)
            return 1
        with tarfile.open(archive, "w:gz") as tf:
            for m in members:
                tf.add(Path(tmp) / m, arcname=m)
    os.chmod(archive, 0o600)                           # contains password hashes
    print(f"backup written: {archive}")
    if a.keep:
        _prune(dest, a.keep, "radio-remote-*.tar.gz")
    return 0


def cmd_restore(a) -> int:
    arc = Path(a.archive)
    if not arc.exists():
        print(f"no such archive: {arc}", file=sys.stderr)
        return 1
    with tarfile.open(arc, "r:gz") as tf:
        names = tf.getnames()
        allowed = {"config.toml", DB_NAME}
        if not names or not set(names) <= allowed:      # refuse anything unexpected (path traversal etc.)
            print(f"archive contains unexpected entries: {sorted(set(names) - allowed)}", file=sys.stderr)
            return 1
        for m in tf.getmembers():
            if not m.isfile():
                print(f"archive member {m.name!r} is not a regular file", file=sys.stderr)
                return 1
        with tempfile.TemporaryDirectory() as tmp:
            tf.extractall(tmp, filter="data") if sys.version_info >= (3, 12) else tf.extractall(tmp)
            if "config.toml" in names:
                _install(Path(tmp) / "config.toml", Path(a.config), a.owner)
            if DB_NAME in names:
                dbp = Path(a.data_dir) / DB_NAME
                for suffix in ("-wal", "-shm"):
                    Path(str(dbp) + suffix).unlink(missing_ok=True)
                _install(Path(tmp) / DB_NAME, dbp, a.owner)
    print(f"restored {', '.join(names)}")
    return 0


def _install(src: Path, dst: Path, owner: str | None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.copy2(dst, dst.with_name(dst.name + ".before-restore"))
    shutil.copy2(src, dst)
    os.chmod(dst, 0o600)
    if owner:
        shutil.chown(dst, user=owner)


def _prune(directory: Path, keep: int, pattern: str) -> list[Path]:
    items = sorted((p for p in directory.glob(pattern)), key=lambda p: p.name)
    doomed = items[:-keep] if keep > 0 else []
    for p in doomed:
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    return doomed


def cmd_prune(a) -> int:
    gone = _prune(Path(a.dir), a.keep, a.glob)
    for p in gone:
        print(f"removed {p}")
    return 0


# ------------------------------------------------------------------ health / doctor
def cmd_health(a) -> int:
    end = time.monotonic() + a.timeout
    last = "no answer"
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(a.url, timeout=3) as r:
                body = json.loads(r.read().decode())
                if "version" in body:
                    print(f"healthy: version {body['version']}")
                    return 0
                last = f"unexpected reply {body!r}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = str(e)
        time.sleep(1)
    print(f"NOT healthy after {a.timeout}s: {last}", file=sys.stderr)
    return 1


def _run(argv: list[str], timeout: int = 8) -> str:
    exe = shutil.which(argv[0])
    if not exe:
        return f"(not installed: {argv[0]})"
    try:
        p = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip() or "(no output)"
    except (OSError, subprocess.SubprocessError) as e:
        return f"(failed: {e})"


def cmd_doctor(a) -> int:
    """Everything useful for a bug report. Contains no passwords; config is printed without secrets (it has none)."""
    sec = lambda t: print(f"\n===== {t} =====")
    sec("host")
    print(_run(["uname", "-a"]))
    try:
        print(Path("/proc/device-tree/model").read_text().strip("\x00\n "))
    except OSError:
        pass
    print("python", sys.version.split()[0])
    sec("service")
    print(_run(["systemctl", "is-active", "radio-remote"]))
    print(_run(["systemctl", "status", "radio-remote", "--no-pager", "-n", "0"]))
    print(_run(["systemctl", "is-active", "caddy"]))
    sec("recent log (last 40 lines)")
    print(_run(["journalctl", "-u", "radio-remote", "-n", "40", "--no-pager"], timeout=15))
    sec("usb / serial")
    print(_run(["lsusb"]))
    ser = Path("/dev/serial/by-id")
    print("\n".join(sorted(p.name for p in ser.iterdir())) if ser.exists() else "(no /dev/serial/by-id)")
    sec("audio")
    print(_run(["arecord", "-l"]))
    print(_run(["aplay", "-l"]))
    sec("service user groups")
    print(_run(["id", "radio-remote"]))
    sec("config (secrets are not stored in it)")
    cfg = Path(a.config) if a.config else None
    print(cfg.read_text() if cfg and cfg.exists() else "(no config file)")
    sec("app status")
    try:
        with urllib.request.urlopen(a.url, timeout=3) as r:
            print(r.read().decode())
    except (urllib.error.URLError, OSError) as e:
        print(f"(not reachable: {e})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rr_admin")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("render-config")
    p.add_argument("--out", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--model", default="mock")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--owner")
    p.add_argument("--app-dir")
    p.add_argument("--allowed-origin", action="append")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_render_config)

    p = sub.add_parser("render-caddy")
    p.add_argument("--host", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_render_caddy)

    p = sub.add_parser("backup")
    p.add_argument("--config", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--keep", type=int, default=10)
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("restore")
    p.add_argument("--archive", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--owner")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("prune")
    p.add_argument("--dir", required=True)
    p.add_argument("--keep", type=int, required=True)
    p.add_argument("--glob", default="*")
    p.set_defaults(fn=cmd_prune)

    p = sub.add_parser("health")
    p.add_argument("--url", default="http://127.0.0.1:8080/api/status")
    p.add_argument("--timeout", type=int, default=30)
    p.set_defaults(fn=cmd_health)

    p = sub.add_parser("doctor")
    p.add_argument("--config", default="/etc/radio-remote/config.toml")
    p.add_argument("--url", default="http://127.0.0.1:8080/api/status")
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
