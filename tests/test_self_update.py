"""self_update: rr_admin.py fetch-release against a small local web server that plays GitHub."""
import hashlib
import http.server
import io
import json
import sys
import tarfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rr_admin  # noqa: E402


def make_package(version_line='__version__ = "1.1.0"', extra=None):
    """A minimal 'release': radio-remote/{update.sh, backend/radio_remote/__init__.py}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in {"radio-remote/update.sh": b"#!/usr/bin/env bash\n", "radio-remote/backend/radio_remote/__init__.py": version_line.encode(),
                           **(extra or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def github():
    """A fake GitHub: serves /repos/o/r/releases/latest and the asset files. Configure with state['release'] and state['files']."""
    state = {"release": None, "files": {}, "hits": []}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            state["hits"].append(self.path)
            if self.path == "/repos/o/r/releases/latest" and state["release"] is not None:
                body = json.dumps(state["release"]).encode()
            elif self.path in state["files"]:
                body = state["files"][self.path]
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    state["base"] = base

    def publish(tag, package, sums=True, name=None, bad_sum=False):
        name = name or f"radio-remote-{tag}.tar.gz"
        digest = hashlib.sha256(package).hexdigest()
        if bad_sum:
            digest = "0" * 64
        state["files"] = {f"/dl/{name}": package}
        assets = [{"name": name, "browser_download_url": f"{base}/dl/{name}"}]
        if sums:
            state["files"]["/dl/SHA256SUMS"] = f"{digest}  {name}\nffff  other.zip\n".encode()
            assets.append({"name": "SHA256SUMS", "browser_download_url": f"{base}/dl/SHA256SUMS"})
        state["release"] = {"tag_name": tag, "assets": assets}

    state["publish"] = publish
    yield state
    srv.shutdown()


def fetch(github, tmp_path, *extra):
    return rr_admin.main(["fetch-release", "--repo", "o/r", "--out", str(tmp_path / "out"), "--api-base", github["base"], *extra])


def test_version_helpers():
    assert rr_admin._version_tuple("v1.0.0.2") == (1, 0, 0, 2) and rr_admin._version_tuple("nightly") is None
    assert rr_admin._newer((1, 0, 0, 2), (1, 0, 0)) and not rr_admin._newer((1, 0, 0, 0), (1, 0, 0)) and not rr_admin._newer((0, 9), (1,))
    assert rr_admin.UPDATE_ASSET.fullmatch("radio-remote-v1.2.0.tar.gz") and not rr_admin.UPDATE_ASSET.fullmatch("radio-remote-installer-1.0.0.zip")
    assert not rr_admin.UPDATE_ASSET.fullmatch("radio-remote-v1.2.0.tar.gz.exe") and not rr_admin.UPDATE_ASSET.fullmatch("../radio-remote-1.tar.gz")


def test_newer_release_is_downloaded_verified_and_unpacked(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    github["publish"]("v1.1.0", make_package())
    assert fetch(github, tmp_path) == 0
    out = capsys.readouterr().out
    assert "newest release v1.1.0" in out and "checksum ok" in out and "READY " in out
    ready = Path(out.split("READY ", 1)[1].splitlines()[0])
    assert (ready / "update.sh").is_file() and (ready / "backend" / "radio_remote" / "__init__.py").is_file()


def test_up_to_date_stops_quietly_and_check_only_asks(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.1.0")
    github["publish"]("v1.1.0", make_package())
    assert fetch(github, tmp_path) == rr_admin.EXIT_UP_TO_DATE
    assert "Already up to date" in capsys.readouterr().out
    assert not [h for h in github["hits"] if h.startswith("/dl/")]                        # nothing was downloaded
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    assert fetch(github, tmp_path, "--check") == 0 and "newer release is available" in capsys.readouterr().out
    assert not [h for h in github["hits"] if h.startswith("/dl/")]
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.1.0")
    assert fetch(github, tmp_path, "--force") == 0                                          # reinstall on request


def test_a_wrong_checksum_installs_nothing(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    github["publish"]("v1.1.0", make_package(), bad_sum=True)
    assert fetch(github, tmp_path) == 1
    err = capsys.readouterr().err
    assert "CHECKSUM MISMATCH" in err and "Nothing was installed" in err
    assert not (tmp_path / "out" / "package").exists()


def test_incomplete_releases_are_refused_politely(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    github["publish"]("v1.1.0", make_package(), sums=False)                                 # image still building: no SHA256SUMS yet
    assert fetch(github, tmp_path) == 1 and "no update package" in capsys.readouterr().err
    github["publish"]("v1.1.0", make_package(), name="radio-remote-installer-1.1.0.zip")    # only other assets
    assert fetch(github, tmp_path) == 1
    github["release"] = None                                                                # GitHub 404
    assert fetch(github, tmp_path) == 1 and "could not ask GitHub" in capsys.readouterr().err
    github["release"] = {"tag_name": "nightly", "assets": []}
    assert fetch(github, tmp_path) == 1 and "unusable version" in capsys.readouterr().err


def test_assets_from_elsewhere_are_ignored(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    github["publish"]("v1.1.0", make_package())
    for a in github["release"]["assets"]:
        a["browser_download_url"] = "http://evil.example/" + a["name"]                       # not the repository's own download URL
    assert fetch(github, tmp_path) == 1 and "no update package" in capsys.readouterr().err


@pytest.mark.parametrize("evil", [
    {"radio-remote/../../escape.txt": b"x"},
    {"/abs/path.txt": b"x"},
])
def test_unsafe_packages_are_refused(github, tmp_path, capsys, monkeypatch, evil):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    github["publish"]("v1.1.0", make_package(extra=evil))
    assert fetch(github, tmp_path) == 1 and "unsafe entry" in capsys.readouterr().err
    assert not (tmp_path / "escape.txt").exists()


def test_a_package_that_is_not_radio_remote_is_refused(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("something/readme.txt")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"hi"))
    github["publish"]("v1.1.0", buf.getvalue())
    assert fetch(github, tmp_path) == 1 and "does not look like a Radio Remote release" in capsys.readouterr().err


def test_a_link_in_the_archive_is_refused(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.0.0")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("radio-remote/evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    github["publish"]("v1.1.0", buf.getvalue())
    assert fetch(github, tmp_path) == 1 and "unsafe entry" in capsys.readouterr().err
