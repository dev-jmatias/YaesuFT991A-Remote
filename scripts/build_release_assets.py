#!/usr/bin/env python3
"""Build the files that go on a GitHub release. Run by CI (.github/workflows/build-image.yml) and usable on any computer:

  python scripts/build_release_assets.py --tag v1.1.0 --out release-assets [--installer] [--extra FILE ...]

Writes into --out:
  radio-remote-<version>.tar.gz           the program (what self_update.sh and update.sh install on a running Pi)
  radio-remote-installer-<version>.zip    only with --installer: for a fresh Raspberry Pi OS (install-everything.sh + the program +
                                          the manual + offline Python libraries for 64-bit Pi OS, Python 3.11 and 3.13)
  SHA256SUMS                              checksums of everything above and of every --extra file (copied next to them)

The tag must match backend/radio_remote/__init__.py (__version__): the update check compares them, so a release whose program still says
the old version would announce itself as "newer" forever. The manual (docs-html/) is built first; it needs the 'markdown' package.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ["backend", "frontend", "scripts", "packaging", "config", "docs", "docs-html", "requirements.txt", "install.sh", "update.sh",
         "README.md", "LICENSE", "pyproject.toml"]
JUNK_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".git"}
JUNK_FILES = {"config/radio-remote.toml", "config/none.toml", "config/none.toml.bak"}
LF_SUFFIXES = {".sh", ".service", ".timer", ".rules", ".txt", ".md", ".py", ".js", ".css", ".html", ".toml", ".json"}
EXECUTABLE = re.compile(r"(^|/)(install\.sh|update\.sh|scripts/[\w.-]+\.(sh|py))$")
MTIME = 1_700_000_000                      # fixed: the same sources give the same archive
PYTHONS = (("3.11", "cp311"), ("3.13", "cp313"))


def program_version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "backend" / "radio_remote" / "__init__.py").read_text(encoding="utf-8"))
    if not m:
        sys.exit("cannot read __version__")
    return m.group(1)


def files_of(item: str):
    """Yield (relative posix path, absolute Path) for every file of one top-level item."""
    p = ROOT / item
    if p.is_file():
        yield item, p
        return
    for f in sorted(p.rglob("*")):
        rel = f.relative_to(ROOT)
        if f.is_file() and not (set(rel.parts) & JUNK_DIRS) and rel.as_posix() not in JUNK_FILES:
            yield rel.as_posix(), f


def content(path: Path) -> bytes:
    data = path.read_bytes()
    return data.replace(b"\r\n", b"\n") if path.suffix in LF_SUFFIXES else data      # scripts must have LF endings on the Pi


def program_files() -> list[tuple[str, bytes]]:
    out = []
    for item in ITEMS:
        if not (ROOT / item).exists():
            if item == "docs-html":
                sys.exit("docs-html/ is missing: build the manual first (python scripts/build_docs.py)")
            continue
        out += [(rel, content(f)) for rel, f in files_of(item)]
    return out


def write_tar(dest: Path, files: list[tuple[str, bytes]]) -> None:
    with tarfile.open(dest, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=9) as tf:
        dirs = sorted({str(Path("radio-remote", *Path(rel).parts[:i]).as_posix()) for rel, _ in files for i in range(len(Path(rel).parts))})
        for d in dirs:
            ti = tarfile.TarInfo(d)
            ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, MTIME
            tf.addfile(ti)
        for rel, data in files:
            ti = tarfile.TarInfo(f"radio-remote/{rel}")
            ti.size, ti.mtime = len(data), MTIME
            ti.mode = 0o755 if EXECUTABLE.search(rel) else 0o644
            tf.addfile(ti, io.BytesIO(data))


def write_zip(dest: Path, version: str, files: list[tuple[str, bytes]], docs: list[tuple[str, bytes]], wheels: Path | None) -> None:
    top = "radio-remote-installer"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def add(name: str, data: bytes, mode: int = 0o644):
            zi = zipfile.ZipInfo(f"{top}/{name}", date_time=(2023, 11, 14, 22, 13, 20))
            zi.external_attr, zi.compress_type = (mode & 0xFFFF) << 16, zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
        for rel, data in files:
            add(f"radio-remote/{rel}", data, 0o755 if EXECUTABLE.search(rel) else 0o644)
        for name in ("install-everything.sh", "START-HERE.txt"):
            data = content(ROOT / "installer" / name)
            add(name, data, 0o755 if name.endswith(".sh") else 0o644)
        for rel, data in docs:
            add(f"docs/{rel}", data)
        if wheels:
            for w in sorted(wheels.glob("*.whl")):
                add(f"wheels/{w.name}", w.read_bytes())


def download_wheels(into: Path) -> None:
    for py, abi in PYTHONS:
        cmd = [sys.executable, "-m", "pip", "download", "-r", str(ROOT / "requirements.txt"), "-d", str(into), "--only-binary=:all:",
               "--python-version", py, "--implementation", "cp", "--abi", abi, "--quiet"]
        for plat in ("manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64", "linux_aarch64"):
            cmd += ["--platform", plat]
        subprocess.run(cmd, check=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="the release tag, for example v1.1.0 ('auto' = v<program version>)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--installer", action="store_true", help="also build the installer zip (downloads the offline Python libraries)")
    ap.add_argument("--extra", nargs="*", default=[], help="other release files to copy into --out and list in SHA256SUMS")
    ap.add_argument("--skip-docs", action="store_true", help="do not (re)build the manual: docs-html/ must already exist")
    ap.add_argument("--allow-version-mismatch", action="store_true")
    a = ap.parse_args(argv)

    version = program_version()
    if a.tag == "auto":
        a.tag = f"v{version}"
    if a.tag.lstrip("v") != version and not a.allow_version_mismatch:
        sys.exit(f"the tag {a.tag} does not match the program version {version} (backend/radio_remote/__init__.py): "
                 "bump __version__ before tagging, or the update check would announce this release as newer forever")
    if not a.skip_docs:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_docs.py"), "--out", str(ROOT / "docs-html")], check=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = program_files()
    produced = []

    tar = out / f"radio-remote-v{a.tag.lstrip('v')}.tar.gz"                        # the name self_update.sh looks for
    write_tar(tar, files)
    produced.append(tar)

    if a.installer:
        wheels = out / "_wheels"
        wheels.mkdir(exist_ok=True)
        download_wheels(wheels)
        docs = [(rel.removeprefix("docs-html/"), data) for rel, data in files if rel.startswith("docs-html/")]
        zip_path = out / f"radio-remote-installer-{version}.zip"
        write_zip(zip_path, version, files, docs, wheels)
        shutil.rmtree(wheels)
        produced.append(zip_path)

    for extra in a.extra:
        src = Path(extra)
        dst = out / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        produced.append(dst)

    sums = out / "SHA256SUMS"
    sums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in produced), encoding="utf-8", newline="\n")
    for p in (*produced, sums):
        print(f"{p.stat().st_size / 1048576:8.1f} MB  {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
