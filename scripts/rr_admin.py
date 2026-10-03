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
  rr_admin.py fetch-release --repo owner/name --out DIR [--check] [--force]     (used by self_update.sh)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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


_HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")


def _extra_hosts(a) -> list[str] | None:
    """Additional site names from --extra-host and from --hosts-file (one per line, '#' comments). None = something unusable."""
    names = list(a.extra_host or [])
    if a.hosts_file and Path(a.hosts_file).exists():
        for line in Path(a.hosts_file).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
    out = []
    for n in names:
        n = n.strip().rstrip(".").lower()
        if not _HOSTNAME.fullmatch(n):
            print(f"refusing unusual hostname {n!r}", file=sys.stderr)
            return None
        if n not in out:
            out.append(n)
    return out


def _site(host: str, port: int) -> str:
    # a Tailscale name (*.ts.net) gets its publicly trusted certificate from the local tailscaled (Caddy does this by itself for such
    # names, see scripts/tailscale_setup.sh); every other name uses Caddy's own certificate authority
    tls = "" if host.endswith(".ts.net") else "\ttls internal\n"
    return f"{host} {{\n{tls}\tencode zstd gzip\n\treverse_proxy 127.0.0.1:{port}\n}}\n"


def cmd_render_caddy(a) -> int:
    out = Path(a.out)
    host = a.host.strip()
    if not host or any(c in host for c in " \t\n{}\"'\\;"):
        print(f"refusing unusual hostname {host!r}", file=sys.stderr)
        return 2
    extras = _extra_hosts(a)
    if extras is None:
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
    sites = [host] + [h for h in extras if h != host.lower()]
    out.write_text(
        "# radio-remote managed (safe to regenerate with install.sh). Edit config/Caddyfile.example for other layouts.\n"
        "# Extra names (Tailscale) are listed in /etc/radio-remote/caddy-extra-hosts; scripts/tailscale_setup.sh maintains that file.\n"
        + "\n".join(_site(h, a.port) for h in sites),
        encoding="utf-8")
    print(f"wrote {out} for " + ", ".join(f"https://{h}" for h in sites))
    return 0


# ------------------------------------------------------------------------ tailscale
def cmd_tailscale_name(a) -> int:
    """Print this machine's Tailscale DNS name (for example pi.tail1234.ts.net) when Tailscale is running and HTTPS certificates are
    enabled for the tailnet. Exit codes: 2 not installed / not signed in, 3 MagicDNS or HTTPS certificates not enabled, 4 unusable answer."""
    try:
        if a.status_file:
            raw = Path(a.status_file).read_text(encoding="utf-8")
        else:
            exe = shutil.which("tailscale")
            if not exe:
                print("Tailscale is not installed (see docs/tailscale.md)", file=sys.stderr)
                return 2
            raw = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15, check=True).stdout
        st = json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"could not read the Tailscale status: {e}", file=sys.stderr)
        return 2
    if st.get("BackendState") != "Running":
        print(f"Tailscale is not signed in and running (state: {st.get('BackendState')}). Run: sudo tailscale up", file=sys.stderr)
        return 2
    name = str((st.get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
    if not name:
        print("This machine has no Tailscale DNS name: enable MagicDNS in the Tailscale admin console (login.tailscale.com/admin/dns)",
              file=sys.stderr)
        return 3
    domains = [str(d).rstrip(".").lower() for d in (st.get("CertDomains") or [])]
    if name not in domains:
        print("HTTPS certificates are not enabled for your tailnet: in login.tailscale.com/admin/dns turn on MagicDNS and "
              "'HTTPS Certificates', then run this again", file=sys.stderr)
        return 3
    if not name.endswith(".ts.net") or not _HOSTNAME.fullmatch(name):
        print(f"unexpected Tailscale name {name!r}", file=sys.stderr)
        return 4
    print(name)
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


# ------------------------------------------------------------ self update: fetch the newest release
GITHUB_API = "https://api.github.com"
UPDATE_ASSET = re.compile(r"radio-remote-v?\d+(?:\.\d+){0,3}\.tar\.gz")
MAX_UPDATE_BYTES = 150 * 1024 * 1024
EXIT_UP_TO_DATE = 10


def _version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", text.strip()) if isinstance(text, str) and len(text) <= 40 else None
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _newer(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def installed_version() -> str:
    text = (ROOT / "backend" / "radio_remote" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "0"


def _get(url: str, limit: int | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"radio-remote-self-update/{installed_version()}",
                                               "Accept": "application/vnd.github+json, application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1 if limit else -1)
    if limit and len(data) > limit:
        raise ValueError(f"{url} is bigger than {limit} bytes: refusing it")
    return data


def _safe_extract(archive: Path, dest: Path) -> None:
    """Unpack a .tar.gz only if it holds plain files and folders inside its own top folder (no '..', no absolute paths, no links)."""
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        if not members:
            raise ValueError("the update package is empty")
        for m in members:
            parts = Path(m.name).parts
            if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir()):
                raise ValueError(f"the update package holds an unsafe entry: {m.name!r}")
        tf.extractall(dest, members=members, filter="data") if sys.version_info >= (3, 12) else tf.extractall(dest, members=members)


def cmd_fetch_release(a) -> int:
    """Download the newest GitHub release's update package, verify its checksum and unpack it. Prints 'READY <folder>' on success.
    Exit 10 = already up to date. This only fetches; self_update.sh then runs the package's own update.sh (backup, switch, rollback)."""
    api = a.api_base.rstrip("/")
    try:
        rel = json.loads(_get(f"{api}/repos/{a.repo}/releases/latest", limit=2_000_000, timeout=20))
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"could not ask GitHub for the newest release: {e}", file=sys.stderr)
        return 1
    tag = rel.get("tag_name")
    latest, current = _version_tuple(tag) if isinstance(tag, str) else None, _version_tuple(installed_version())
    if latest is None or current is None:
        print(f"unusable version (release {tag!r}, installed {installed_version()!r})", file=sys.stderr)
        return 1
    print(f"installed version {installed_version()}, newest release {tag}")
    is_new = _newer(latest, current)
    if not is_new and not a.force:
        print("Already up to date.")
        return EXIT_UP_TO_DATE
    if a.check:
        print("A newer release is available." if is_new else "Nothing newer (use --force to reinstall).")
        return 0 if is_new else EXIT_UP_TO_DATE
    prefix = f"https://github.com/{a.repo}/releases/download/" if api == GITHUB_API else api
    assets = {x["name"]: x["browser_download_url"] for x in rel.get("assets", [])
              if isinstance(x, dict) and isinstance(x.get("name"), str) and isinstance(x.get("browser_download_url"), str)
              and x["browser_download_url"].startswith(prefix)}
    pkg = next((n for n in sorted(assets) if UPDATE_ASSET.fullmatch(n)), None)
    if not pkg or "SHA256SUMS" not in assets:
        print(f"release {tag} has no update package (radio-remote-<version>.tar.gz and SHA256SUMS): it may still be building; "
              "try again in a few minutes", file=sys.stderr)
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        sums = _get(assets["SHA256SUMS"], limit=1_000_000).decode("utf-8", "replace")
        want = next((ln.split()[0].lower() for ln in sums.splitlines()
                     if len(ln.split()) >= 2 and ln.split()[1].lstrip("*") == pkg), None)
        if not want or not re.fullmatch(r"[0-9a-f]{64}", want):
            print(f"SHA256SUMS has no checksum for {pkg}", file=sys.stderr)
            return 1
        print(f"downloading {pkg} ...")
        data = _get(assets[pkg], limit=MAX_UPDATE_BYTES, timeout=120)
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            print(f"CHECKSUM MISMATCH for {pkg}: expected {want}, got {got}. Nothing was installed.", file=sys.stderr)
            return 1
        archive = out / pkg
        archive.write_bytes(data)
        folder = out / "package"
        _safe_extract(archive, folder)
    except (urllib.error.URLError, OSError, ValueError, tarfile.TarError) as e:
        print(f"download failed: {e}", file=sys.stderr)
        return 1
    root = next((p for p in sorted(folder.iterdir()) if (p / "update.sh").is_file() and (p / "backend" / "radio_remote").is_dir()), None)
    if root is None:
        print("the update package does not look like a Radio Remote release", file=sys.stderr)
        return 1
    print(f"checksum ok: {want}")
    print(f"READY {root}")
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

    p = sub.add_parser("tailscale-name")
    p.add_argument("--status-file", help=argparse.SUPPRESS)                              # tests: a saved 'tailscale status --json'
    p.set_defaults(fn=cmd_tailscale_name)

    p = sub.add_parser("render-caddy")
    p.add_argument("--host", required=True)
    p.add_argument("--extra-host", action="append", help="another site name (repeatable), e.g. a Tailscale name")
    p.add_argument("--hosts-file", help="file with more site names, one per line (/etc/radio-remote/caddy-extra-hosts)")
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

    p = sub.add_parser("fetch-release")
    p.add_argument("--repo", required=True, help="owner/name of the GitHub repository")
    p.add_argument("--out", required=True)
    p.add_argument("--api-base", default=GITHUB_API, help=argparse.SUPPRESS)          # tests point it at a local server
    p.add_argument("--check", action="store_true", help="only say whether a newer release exists")
    p.add_argument("--force", action="store_true", help="fetch even when the newest release is not newer")
    p.set_defaults(fn=cmd_fetch_release)

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
