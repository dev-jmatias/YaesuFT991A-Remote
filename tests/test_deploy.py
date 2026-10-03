import asyncio
import copy
import http.server
import json
import os
import re
import sqlite3
import sys
import tarfile
import threading
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import rr_admin  # noqa: E402

from radio_remote import config, sdnotify  # noqa: E402
from radio_remote.app import create_app  # noqa: E402
from radio_remote.auth import AuthStore  # noqa: E402
from radio_remote.radio.mock import MockDriver  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
POSIX = os.name == "posix"


# ------------------------------------------------------------ render-config
def render(tmp_path, *extra, name="config.toml"):
    out = tmp_path / name
    rc = rr_admin.main(["render-config", "--out", str(out), "--data-dir", "/var/lib/radio-remote", *extra])
    return rc, out


def test_render_config_is_valid_and_safe(tmp_path):
    rc, out = render(tmp_path, "--model", "ft991a", "--port", "8123")
    assert rc == 0
    cfg = config.load(out)                                   # passes the app's own validation
    assert cfg["radio"]["model"] == "ft991a" and cfg["server"]["port"] == 8123
    assert cfg["safety"]["allow_ptt"] is False               # the installer can never enable transmitting
    assert cfg["server"]["host"] == "127.0.0.1"
    assert cfg["storage"]["data_dir"] == "/var/lib/radio-remote"
    if POSIX:
        assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_render_config_never_overwrites_without_force(tmp_path, capsys):
    rc, out = render(tmp_path, "--model", "mock")
    cfg = config.load(out)
    cfg["safety"]["allow_ptt"] = True                        # operator enabled PTT by hand
    config.save(cfg, out)
    rc, _ = render(tmp_path, "--model", "ft991a")
    assert rc == 0 and "kept existing" in capsys.readouterr().out
    assert config.load(out)["safety"]["allow_ptt"] is True and config.load(out)["radio"]["model"] == "mock"
    rc, _ = render(tmp_path, "--model", "ft991a", "--force")
    assert config.load(out)["safety"]["allow_ptt"] is False  # a forced re-render goes back to the safe default


def test_render_config_rejects_bad_values(tmp_path, capsys):
    rc, out = render(tmp_path, "--model", "ftdx9000")
    assert rc == 2 and not out.exists() and "invalid configuration" in capsys.readouterr().err
    rc, out = render(tmp_path, "--port", "70000", name="b.toml")
    assert rc == 2 and not out.exists()


def test_render_config_allowed_origin(tmp_path):
    rc, out = render(tmp_path, "--allowed-origin", "https://pi.example.ts.net")
    assert config.load(out)["server"]["allowed_origins"] == ["https://pi.example.ts.net"]


# -------------------------------------------------------------- render-caddy
def caddy(tmp_path, host="raspberrypi.local", *extra):
    out = tmp_path / "Caddyfile"
    return rr_admin.main(["render-caddy", "--host", host, "--out", str(out), *extra]), out


def test_render_caddy_basic(tmp_path):
    rc, out = caddy(tmp_path)
    text = out.read_text()
    assert rc == 0 and "raspberrypi.local {" in text and "reverse_proxy 127.0.0.1:8080" in text
    assert "tls internal" in text and "radio-remote managed" in text
    rc, out = caddy(tmp_path, "pi.lan")                      # our own file is safe to regenerate
    assert "pi.lan {" in out.read_text()


@pytest.mark.parametrize("bad", ["", "a b", "evil.com {\nfile_server", 'x"y', "a;b", "a}b"])
def test_render_caddy_refuses_unusual_hostnames(tmp_path, bad):
    rc, out = caddy(tmp_path, bad)
    assert rc == 2 and not out.exists()


def test_render_caddy_respects_foreign_files_but_replaces_debian_default(tmp_path):
    out = tmp_path / "Caddyfile"
    out.write_text("my-own-site.example {\n\trespond hello\n}\n")
    rc, _ = caddy(tmp_path)
    assert rc == 0 and "my-own-site" in out.read_text()      # left alone
    out.write_text(":80 {\n\troot * /usr/share/caddy\n\tfile_server\n}\n")
    rc, _ = caddy(tmp_path)
    assert "radio-remote managed" in out.read_text()         # stock default replaced...
    assert list(tmp_path.glob("Caddyfile.bak-*"))            # ...after being backed up


def test_render_caddy_with_a_tailscale_name(tmp_path):
    # a *.ts.net site gets its certificate from Tailscale (no 'tls internal'), the LAN name keeps Caddy's own authority
    rc, out = caddy(tmp_path, "pi.local", "--extra-host", "Pi.Tail1234.ts.net.")
    text = out.read_text()
    assert rc == 0 and "pi.local {\n\ttls internal" in text and "pi.tail1234.ts.net {\n\tencode zstd gzip" in text
    ts_block = text.split("pi.tail1234.ts.net {")[1].split("}")[0]
    assert "tls internal" not in ts_block and "reverse_proxy 127.0.0.1:8080" in ts_block


def test_render_caddy_reads_extra_names_from_the_hosts_file(tmp_path):
    hosts = tmp_path / "caddy-extra-hosts"
    hosts.write_text("# written by tailscale_setup.sh\npi.tail1234.ts.net   # my Pi\n\nsecond.example.ts.net\npi.tail1234.ts.net\n")
    rc, out = caddy(tmp_path, "pi.local", "--hosts-file", str(hosts))
    text = out.read_text()
    assert rc == 0 and text.count("pi.tail1234.ts.net {") == 1 and "second.example.ts.net {" in text    # duplicates dropped
    rc, out = caddy(tmp_path, "pi.local", "--hosts-file", str(tmp_path / "does-not-exist"))              # a missing file is fine
    assert rc == 0 and "ts.net" not in out.read_text()
    assert out.read_text().startswith("# radio-remote managed")                                          # still recognised as ours


@pytest.mark.parametrize("bad", ["evil.com {\nfile_server", "a b.ts.net", "x;y", "-bad.ts.net", "a/b", ""])
def test_render_caddy_refuses_unusual_extra_names(tmp_path, bad):
    out = tmp_path / "Caddyfile"
    rc = rr_admin.main(["render-caddy", "--host", "pi.local", f"--extra-host={bad}", "--out", str(out)])
    assert rc == 2 and not out.exists()


# ------------------------------------------------------------------ tailscale
def ts_status(tmp_path, **over):
    status = {"BackendState": "Running", "Self": {"DNSName": "Pi.tail1234.ts.net."}, "CertDomains": ["pi.tail1234.ts.net"]}
    status.update(over)
    f = tmp_path / "status.json"
    f.write_text(json.dumps(status))
    return f


def test_tailscale_name_prints_the_name_when_everything_is_ready(tmp_path, capsys):
    assert rr_admin.main(["tailscale-name", "--status-file", str(ts_status(tmp_path))]) == 0
    assert capsys.readouterr().out.strip() == "pi.tail1234.ts.net"


@pytest.mark.parametrize("over, code, hint", [
    ({"BackendState": "NeedsLogin"}, 2, "tailscale up"),
    ({"Self": {"DNSName": ""}}, 3, "MagicDNS"),
    ({"CertDomains": []}, 3, "HTTPS Certificates"),
    ({"CertDomains": ["other.tail1234.ts.net"]}, 3, "HTTPS Certificates"),
    ({"Self": {"DNSName": "pi.example.com."}, "CertDomains": ["pi.example.com"]}, 4, "unexpected"),
])
def test_tailscale_name_explains_what_is_missing(tmp_path, capsys, over, code, hint):
    assert rr_admin.main(["tailscale-name", "--status-file", str(ts_status(tmp_path, **over))]) == code
    assert hint in capsys.readouterr().err


def test_tailscale_name_without_tailscale_or_with_garbage(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert rr_admin.main(["tailscale-name", "--status-file", str(bad)]) == 2
    assert rr_admin.main(["tailscale-name", "--status-file", str(tmp_path / "missing.json")]) == 2


def test_installer_and_boot_helper_keep_the_tailscale_name():
    root = Path(__file__).resolve().parents[1]
    assert '--hosts-file "$CONF_DIR/caddy-extra-hosts"' in (root / "install.sh").read_text()
    helper = (root / "image" / "stage-radio-remote" / "00-radio-remote" / "files" / "radio-remote-caddy-host.sh").read_text()
    assert "--hosts-file /etc/radio-remote/caddy-extra-hosts" in helper
    script = (root / "scripts" / "tailscale_setup.sh").read_text()
    for needle in ("TS_PERMIT_CERT_UID=caddy", "caddy validate", "tailscale-name", "caddy-extra-hosts", "--remove", "--dry-run"):
        assert needle in script, needle
    assert script.index("caddy validate") < script.index('install -m 0644 "$new" "$CADDYFILE"')    # checked before it replaces the file


# ----------------------------------------------------------- backup / restore
def make_state(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    cfgp = tmp_path / "config.toml"
    config.save(config.load(None), cfgp)
    auth = AuthStore(data / "radio-remote.db")
    auth.create_user("alice", "alice long password", "admin")
    return data, cfgp, auth


def test_backup_restore_roundtrip_while_db_is_open(tmp_path):
    data, cfgp, auth = make_state(tmp_path)                  # `auth` still holds the db open, as the service would
    dest = tmp_path / "bk"
    assert rr_admin.main(["backup", "--config", str(cfgp), "--data-dir", str(data), "--dest", str(dest)]) == 0
    (archive,) = dest.glob("radio-remote-*.tar.gz")
    if POSIX:
        assert oct(archive.stat().st_mode & 0o777) == "0o600"   # holds password hashes
    with tarfile.open(archive) as tf:
        assert sorted(tf.getnames()) == ["config.toml", "radio-remote.db"]

    # disaster: lose everything, restore into a fresh place
    new_data, new_cfg = tmp_path / "data2", tmp_path / "etc" / "config.toml"
    new_data.mkdir()
    assert rr_admin.main(["restore", "--archive", str(archive), "--config", str(new_cfg), "--data-dir", str(new_data)]) == 0
    restored = AuthStore(new_data / "radio-remote.db")
    assert restored.login("alice", "alice long password", "1.1.1.1")
    assert config.load(new_cfg) == config.load(cfgp)


def test_restore_keeps_previous_files(tmp_path):
    data, cfgp, _ = make_state(tmp_path)
    dest = tmp_path / "bk"
    rr_admin.main(["backup", "--config", str(cfgp), "--data-dir", str(data), "--dest", str(dest)])
    (archive,) = dest.glob("*.tar.gz")
    cfgp.write_text(cfgp.read_text() + "\n# local edit\n")
    rr_admin.main(["restore", "--archive", str(archive), "--config", str(cfgp), "--data-dir", str(data)])
    assert "local edit" in (tmp_path / "config.toml.before-restore").read_text()
    assert "local edit" not in cfgp.read_text()


def test_restore_refuses_hostile_archives(tmp_path, capsys):
    evil = tmp_path / "evil.tar.gz"
    victim = tmp_path / "victim.txt"
    victim.write_text("x")
    with tarfile.open(evil, "w:gz") as tf:
        tf.add(victim, arcname="../escaped.txt")
    rc = rr_admin.main(["restore", "--archive", str(evil), "--config", str(tmp_path / "c.toml"), "--data-dir", str(tmp_path)])
    assert rc == 1 and "unexpected entries" in capsys.readouterr().err
    assert not (tmp_path.parent / "escaped.txt").exists()

    link = tmp_path / "link.tar.gz"
    with tarfile.open(link, "w:gz") as tf:
        ti = tarfile.TarInfo("config.toml")
        ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(ti)
    assert rr_admin.main(["restore", "--archive", str(link), "--config", str(tmp_path / "c.toml"), "--data-dir", str(tmp_path)]) == 1
    assert rr_admin.main(["restore", "--archive", str(tmp_path / "missing.tar.gz"), "--config", "x", "--data-dir", "y"]) == 1


def test_backup_with_nothing_to_back_up_fails(tmp_path):
    assert rr_admin.main(["backup", "--config", str(tmp_path / "no.toml"), "--data-dir", str(tmp_path), "--dest", str(tmp_path / "bk")]) == 1


def test_backup_prunes_old_archives(tmp_path):
    data, cfgp, _ = make_state(tmp_path)
    dest = tmp_path / "bk"
    dest.mkdir()
    for i in range(5):
        (dest / f"radio-remote-2020010{i}-000000.tar.gz").write_bytes(b"old")
    rr_admin.main(["backup", "--config", str(cfgp), "--data-dir", str(data), "--dest", str(dest), "--keep", "3"])
    names = sorted(p.name for p in dest.glob("radio-remote-*.tar.gz"))
    assert len(names) == 3 and "radio-remote-20200100-000000.tar.gz" not in names
    assert names[-1] > "radio-remote-20200104"               # the fresh backup survived


def test_prune_releases_keeps_newest_dirs(tmp_path):
    rel = tmp_path / "releases"
    for ts in ("20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"):
        (rel / ts).mkdir(parents=True)
        (rel / ts / "f").write_text("x")
    assert rr_admin.main(["prune", "--dir", str(rel), "--keep", "2"]) == 0
    assert sorted(p.name for p in rel.iterdir()) == ["20260103-000000", "20260104-000000"]


# --------------------------------------------------------------------- health
class _Handler(http.server.BaseHTTPRequestHandler):
    body = b'{"version": "9.9.9", "setup_required": true}'

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def test_health_ok_and_failure(http_server, capsys):
    url = f"http://127.0.0.1:{http_server.server_port}/api/status"
    assert rr_admin.main(["health", "--url", url, "--timeout", "5"]) == 0
    assert "healthy: version 9.9.9" in capsys.readouterr().out
    assert rr_admin.main(["health", "--url", "http://127.0.0.1:9/api/status", "--timeout", "1"]) == 1
    _Handler.body = b'{"not": "us"}'
    try:
        assert rr_admin.main(["health", "--url", url, "--timeout", "1"]) == 1     # a different service on the port
    finally:
        _Handler.body = b'{"version": "9.9.9"}'


def test_doctor_runs_anywhere_and_prints_sections(tmp_path, capsys):
    cfgp = tmp_path / "c.toml"
    config.save(config.load(None), cfgp)
    assert rr_admin.main(["doctor", "--config", str(cfgp), "--url", "http://127.0.0.1:9/api/status"]) == 0
    out = capsys.readouterr().out
    for section in ("host", "service", "usb / serial", "audio", "config", "app status"):
        assert f"===== {section}" in out, section
    assert "allow_ptt = false" in out


# --------------------------------------------------------- packaging assets
def test_service_unit_is_hardened_and_consistent():
    unit = (ROOT / "packaging" / "radio-remote.service").read_text()
    for needle in ("Type=notify", "WatchdogSec=", "Restart=always", "NoNewPrivileges=true", "ProtectSystem=strict",
                   "ReadWritePaths=/etc/radio-remote /var/lib/radio-remote", "SupplementaryGroups=dialout audio",
                   "User=radio-remote", "TimeoutStopSec=", "PrivateTmp=true"):
        assert needle in unit, needle
    assert "-m radio_remote --config /etc/radio-remote/config.toml" in unit
    assert "/opt/radio-remote/venv/bin/python" in unit and "PYTHONPATH=/opt/radio-remote/current/backend" in unit
    assert "PrivateDevices=true" not in unit                 # would hide the serial and sound devices
    wd = int(re.search(r"WatchdogSec=(\d+)", unit).group(1))
    assert wd >= 10                                          # app pings at wd/2; leave headroom on a slow Pi


def test_backup_timer_and_udev_rule():
    assert "OnCalendar=daily" in (ROOT / "packaging" / "radio-remote-backup.timer").read_text()
    assert "rr_admin.py backup" in (ROOT / "packaging" / "radio-remote-backup.service").read_text()
    rules = (ROOT / "packaging" / "99-radio-remote.rules").read_text()
    assert 'ATTRS{idVendor}=="10c4"' in rules and "ID_MM_DEVICE_IGNORE" in rules
    assert "MODE=" not in rules and "RUN+=" not in rules    # no permission changes, no commands run from udev


SCRIPTS = [ROOT / "install.sh", ROOT / "update.sh", *sorted((ROOT / "scripts").glob("*.sh"))]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_are_lf_strict_and_free_of_dangerous_habits(path):
    raw = path.read_bytes()
    assert b"\r" not in raw, "CRLF line endings break scripts on the Pi"
    text = raw.decode()
    assert text.startswith("#!/usr/bin/env bash")
    if path.name != "deploy_lib.sh":
        assert "set -" in text and "pipefail" in text
    for bad in ("ufw ", "iptables", "nftables", "| sh", "| bash", "curl -s", "wget "):
        assert bad not in text, f"{path.name} contains {bad!r}"
    assert "allow_ptt = true" not in text.split("cat <<EOF")[0]      # never written by the scripts, only mentioned in help


def test_installer_has_dry_run_and_refuses_32bit_without_force():
    text = (ROOT / "install.sh").read_text()
    assert "--dry-run" in text and 'run() { if [ "$DRY" = 1 ]' in text
    assert "aarch64|x86_64" in text and "--force-arch" in text
    assert "python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11)" in text
    assert "--model must be one of: mock ft991a ftdx10 ftdx101d ftdx101mp ft710" in text and "EXPERIMENTAL" in text
    assert "render-config" in text and "--model \"$MODEL\"" in text


def test_update_script_backs_up_first_and_can_roll_back():
    text = (ROOT / "update.sh").read_text()
    assert text.index("rr_admin.py\" backup") < text.index("deploy_release")
    assert text.index("deploy_release") < text.index("activate_release \"$NEW_RELEASE\"")
    assert "Rolling back" in text and 'activate_release "$PREVIOUS"' in text


def test_release_is_readable_by_the_service_user_whatever_the_source_modes():
    # regression: scp from Windows delivered drwx------ folders, the unprivileged service could not import the code
    text = (ROOT / "scripts" / "deploy_lib.sh").read_text()
    chown, chmod = text.index("chown -R root:root"), text.index("chmod -R u+rwX,go+rX,go-w")
    assert chown < chmod < text.index('runuser -u radio-remote -- test -r')
    assert "NEW_RELEASE=" in text[chmod:]                     # the readability check runs before the release is activated


def test_gitattributes_forces_lf_for_scripts():
    ga = (ROOT / ".gitattributes").read_text()
    assert "*.sh text eol=lf" in ga and "*.service text eol=lf" in ga


# ------------------------------------------------------------------- sd_notify
def test_notify_is_a_noop_outside_systemd(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sdnotify.notify("READY=1") is False


def test_notify_sends_to_socket_including_abstract(monkeypatch):
    sent = []

    class FakeSock:
        def __init__(self, *a):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def connect(self, addr):
            sent.append(("connect", addr))
        def sendall(self, data):
            sent.append(("data", data))

    monkeypatch.setattr(sdnotify.socket, "socket", FakeSock)
    monkeypatch.setattr(sdnotify.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    assert sdnotify.notify("READY=1") is True
    assert ("connect", "/run/systemd/notify") in sent and ("data", b"READY=1") in sent
    sent.clear()
    monkeypatch.setenv("NOTIFY_SOCKET", "@abstract")
    sdnotify.notify("WATCHDOG=1")
    assert ("connect", "\0abstract") in sent


def test_watchdog_interval_parsing(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert sdnotify.watchdog_interval_s() is None
    monkeypatch.setenv("WATCHDOG_USEC", "20000000")
    assert sdnotify.watchdog_interval_s() == 10.0
    monkeypatch.setenv("WATCHDOG_PID", "1")                  # meant for another process
    assert sdnotify.watchdog_interval_s() is None
    monkeypatch.setenv("WATCHDOG_USEC", "garbage")
    monkeypatch.delenv("WATCHDOG_PID")
    assert sdnotify.watchdog_interval_s() is None


async def test_watchdog_pings_only_while_healthy(monkeypatch):
    msgs = []
    monkeypatch.setattr(sdnotify, "notify", lambda m: msgs.append(m) or True)
    state = {"ok": True}
    wd = sdnotify.Watchdog(lambda: state["ok"], interval_s=0.02)
    wd.start()
    await asyncio.sleep(0.12)
    assert msgs[0] == "READY=1" and msgs.count("WATCHDOG=1") >= 2
    state["ok"] = False
    await asyncio.sleep(0.05)
    n = msgs.count("WATCHDOG=1")
    await asyncio.sleep(0.1)
    assert msgs.count("WATCHDOG=1") == n                     # unhealthy: pings withheld
    await wd.stop()
    assert msgs[-1] == "STOPPING=1"


async def test_app_reports_ready_and_stopping_to_systemd(monkeypatch):
    msgs = []
    monkeypatch.setattr(sdnotify, "notify", lambda m: msgs.append(m) or True)
    app = create_app(copy.deepcopy(config.DEFAULTS), driver=MockDriver(seed=1), auth=AuthStore(":memory:"))
    c = TestClient(TestServer(app))
    await c.start_server()
    assert "READY=1" in msgs
    await c.close()
    assert "STOPPING=1" in msgs
