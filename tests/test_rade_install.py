"""install_rade.sh / rr_admin.py fetch-rade: the optional RADE library is downloaded, checked and installed safely."""
import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest
from test_self_update import github  # noqa: F401  (the fake GitHub server fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rr_admin  # noqa: E402

PKG = "radio-remote-rade-linux-x86_64.tar.xz"
FILES = {"librade-rr.so": b"\x7fELF fake library", "RADE-INFO.txt": b"info\n", "LICENSE-rade_c": b"BSD\n", "LICENSE-opus": b"BSD\n"}


def make_rade(files=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for name, data in (files or FILES).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def fetch(github, tmp_path, *extra):  # noqa: F811
    return rr_admin.main(["fetch-rade", "--repo", "o/r", "--dest", str(tmp_path / "lib"), "--arch", "x86_64", "--api-base", github["base"], *extra])


def test_release_download_is_verified_and_installed(github, tmp_path, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.1.5")
    github["publish"]("v1.1.5", make_rade(), name=PKG)
    assert fetch(github, tmp_path) == 0
    out = capsys.readouterr().out
    assert "checksum ok" in out and "INSTALLED" in out
    lib = tmp_path / "lib"
    assert sorted(p.name for p in lib.iterdir()) == sorted(FILES) and (lib / "librade-rr.so").read_bytes() == FILES["librade-rr.so"]


def test_a_wrong_checksum_installs_nothing(github, tmp_path, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.1.5")
    github["publish"]("v1.1.5", make_rade(), name=PKG, bad_sum=True)
    assert fetch(github, tmp_path) == 1
    assert "CHECKSUM MISMATCH" in capsys.readouterr().err and not (tmp_path / "lib").exists()


def test_a_release_without_the_package_is_refused_politely(github, tmp_path, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.1.5")
    github["publish"]("v1.1.5", b"x", name="radio-remote-v1.1.5.tar.gz")
    assert fetch(github, tmp_path) == 1
    assert "no release has" in capsys.readouterr().err


@pytest.mark.parametrize("files", [{**FILES, "../evil.so": b"x"}, {**FILES, "extra.sh": b"x"}, {"RADE-INFO.txt": b"x"}])
def test_unexpected_or_incomplete_packages_are_refused(github, tmp_path, capsys, monkeypatch, files):  # noqa: F811
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.1.5")
    github["publish"]("v1.1.5", make_rade(files), name=PKG)
    assert fetch(github, tmp_path) == 1
    assert "RADE install failed" in capsys.readouterr().err and not (tmp_path / "lib").exists()


def test_a_link_in_the_package_is_refused(github, tmp_path, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rr_admin, "installed_version", lambda: "1.1.5")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        info = tarfile.TarInfo("librade-rr.so")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(info)
    github["publish"]("v1.1.5", buf.getvalue(), name=PKG)
    assert fetch(github, tmp_path) == 1 and not (tmp_path / "lib").exists()


def test_install_from_a_local_file_with_and_without_a_checksum(tmp_path, capsys):
    data = make_rade()
    f = tmp_path / PKG
    f.write_bytes(data)
    base = ["fetch-rade", "--dest", str(tmp_path / "lib"), "--file", str(f)]
    assert rr_admin.main([*base, "--sha256", "0" * 64]) == 1 and not (tmp_path / "lib").exists()
    assert rr_admin.main([*base, "--sha256", hashlib.sha256(data).hexdigest()]) == 0
    assert (tmp_path / "lib" / "librade-rr.so").is_file()
    assert rr_admin.main(["fetch-rade", "--dest", str(tmp_path / "lib2"), "--file", str(f)]) == 0       # checksum optional for a file you copied yourself


def test_the_script_is_wired_up():
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "install_rade.sh").read_text(encoding="utf-8")
    assert "fetch-rade" in sh and "--remove" in sh and "systemctl restart radio-remote" in sh and "\r" not in sh
