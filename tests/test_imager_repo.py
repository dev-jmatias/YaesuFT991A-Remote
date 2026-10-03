"""The Raspberry Pi Imager content repository file (os-list.json)."""
import hashlib
import json
import lzma
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_imager_repo as bir  # noqa: E402

RAW = (b"pretend this is a 3 GB disk image " * 5000) + b"end"


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "image_2026-10-04-radio-remote.img.xz"
    p.write_bytes(lzma.compress(RAW))
    return p


def test_sizes_and_hashes_are_those_of_the_uncompressed_image(image):
    size, digest = bir.uncompressed_stats(image)
    assert size == len(RAW) and digest == hashlib.sha256(RAW).hexdigest()


def test_entry_has_every_field_imager_requires(image, tmp_path):
    doc = bir.build(image, "v1.1.0", "owner/name", date="2026-10-04")
    e = doc["os_list"][0]
    for field in ("name", "description", "icon", "url", "extract_size", "extract_sha256", "image_download_size", "release_date", "devices"):
        assert field in e, field                                                       # the schema's required fields
    assert e["url"] == "https://github.com/owner/name/releases/download/v1.1.0/image_2026-10-04-radio-remote.img.xz"
    assert e["extract_size"] == len(RAW) and e["image_download_size"] == image.stat().st_size
    assert re.fullmatch(r"[0-9a-f]{64}", e["extract_sha256"]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["release_date"])
    assert e["init_format"] == "cloudinit-rpi"                                          # the same customisation path as the official Lite image
    assert set(e["devices"]) <= {t for d in doc["imager"]["devices"] for t in d["tags"]}   # every device tag is defined
    assert e["icon"].startswith("https://raw.githubusercontent.com/owner/name/v1.1.0/")
    assert json.loads(json.dumps(doc)) == doc


def test_a_local_url_can_be_used_for_a_test_before_publishing(image):
    doc = bir.build(image, "v1.1.0", "o/r", image_url="file:///C:/x/y.img.xz")
    assert doc["os_list"][0]["url"] == "file:///C:/x/y.img.xz"


def test_command_line_writes_the_file_and_auto_uses_the_program_version(image, tmp_path, capsys):
    out = tmp_path / "os-list.json"
    assert bir.main(["--image", str(image), "--tag", "auto", "--repo", "o/r", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    version = bir.program_version()
    assert f"/v{version}/" in doc["os_list"][0]["url"]
    assert "wrote" in capsys.readouterr().out and out.read_bytes().endswith(b"\n") and b"\r" not in out.read_bytes()


def test_the_docs_give_the_stable_link_users_paste_into_imager():
    root = Path(__file__).resolve().parents[1]
    link = "https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest/download/os-list.json"
    for doc in ("README.md", "image/README.md", "image/RELEASE-NOTES.md"):
        assert link in (root / doc).read_text(encoding="utf-8"), doc
    from radio_remote import config
    assert config.DEFAULTS["updates"]["repo"] == "dev-jmatias/YaesuFT991A-Remote"             # the link and the update check use one repository


def test_workflow_publishes_it_with_the_release():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-image.yml").read_text()
    assert "scripts/build_imager_repo.py" in wf and "os-list.json" in wf
    assert wf.index("scripts/build_imager_repo.py") < wf.index("scripts/build_release_assets.py")      # listed in SHA256SUMS too
