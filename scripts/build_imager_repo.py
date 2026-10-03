#!/usr/bin/env python3
"""Write the Raspberry Pi Imager "content repository" file (os-list.json) for a Radio Remote image.

With it, Raspberry Pi Imager 2.x lists the image next to the official ones and offers its OWN customisation screens for it
(hostname, user name, password, Wi-Fi, SSH), so first-boot-settings.ps1 is not needed. The user pastes one link in Imager:
App Options > Content Repository > custom URL:

    https://github.com/<owner>/<repo>/releases/latest/download/os-list.json

  python scripts/build_imager_repo.py --image image_2026-10-03-radio-remote.img.xz --tag v1.1.0 --repo owner/name --out os-list.json
  python scripts/build_imager_repo.py --image X.img.xz --tag auto --repo owner/name --out os-list.json \
         --image-url file:///C:/Users/me/Downloads/X.img.xz              # test locally before publishing

The format is Raspberry Pi's "OS list" JSON (docs: rpi-imager/doc/json-schema/os-list-schema.json). The image is cloud-init based like the
official Lite image, hence init_format "cloudinit-rpi". extract_size / extract_sha256 are those of the UNcompressed image, which is read
once (about a minute for 3 GB).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_release_assets import program_version  # noqa: E402

DEVICES = [   # same tags and wording as Raspberry Pi's own list, so Imager's device filter works
    {"name": "Raspberry Pi 5", "tags": ["pi5-64bit", "pi5-32bit"], "icon": "https://downloads.raspberrypi.com/imager/icons/RPi_5.png",
     "description": "Raspberry Pi 5, 500 / 500+, and Compute Module 5", "matching_type": "exclusive", "capabilities": []},
    {"name": "Raspberry Pi 4", "tags": ["pi4-64bit", "pi4-32bit"], "default": False, "icon": "https://downloads.raspberrypi.com/imager/icons/RPi_4.png",
     "description": "Raspberry Pi 4 Model B, 400, and Compute Module 4 / 4S", "matching_type": "inclusive", "capabilities": []},
    {"name": "Raspberry Pi 3", "tags": ["pi3-64bit", "pi3-32bit"], "default": False, "icon": "https://downloads.raspberrypi.com/imager/icons/RPi_3.png",
     "description": "Raspberry Pi 3 Model B, B+ and A+", "matching_type": "inclusive", "capabilities": []},
    {"name": "No filtering", "tags": [], "default": False, "description": "Show every possible image", "matching_type": "inclusive", "capabilities": []},
]
IMAGE_DEVICES = ["pi5-64bit", "pi4-64bit", "pi3-64bit"]       # 64-bit image; tested on a Pi 4


def uncompressed_stats(path: Path) -> tuple[int, str]:
    """(size in bytes, sha256 hex) of the decompressed image, read in 1 MiB pieces."""
    h, n = hashlib.sha256(), 0
    with lzma.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return n, h.hexdigest()


def build(image: Path, tag: str, repo: str, image_url: str | None = None, date: str | None = None) -> dict:
    size, digest = uncompressed_stats(image)
    url = image_url or f"https://github.com/{repo}/releases/download/{tag}/{image.name}"
    return {
        "imager": {"latest_version": "2.0.0", "url": "https://www.raspberrypi.com/software/", "devices": DEVICES},
        "os_list": [{
            "name": "Radio Remote for Yaesu (Raspberry Pi OS Lite, 64-bit)",
            "description": f"Web remote control for Yaesu radios on Raspberry Pi OS Lite (Debian Trixie, 64-bit), release {tag}. "
                           "Transmitting is off until you enable it. Set the hostname, user, password and Wi-Fi in the next step.",
            "icon": f"https://raw.githubusercontent.com/{repo}/{tag}/frontend/icons/icon-192.png",
            "website": f"https://github.com/{repo}",
            "url": url,
            "extract_size": size,
            "extract_sha256": digest,
            "image_download_size": image.stat().st_size,
            "release_date": date or time.strftime("%Y-%m-%d", time.gmtime()),
            "init_format": "cloudinit-rpi",
            "devices": IMAGE_DEVICES,
            "capabilities": [],
        }],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="the .img.xz file of the release")
    ap.add_argument("--tag", required=True, help="release tag, e.g. v1.1.0 ('auto' = v<program version>)")
    ap.add_argument("--repo", required=True, help="owner/name of the GitHub repository")
    ap.add_argument("--out", required=True)
    ap.add_argument("--image-url", help="download URL to write instead of the release URL (for a local test: file:///...)")
    ap.add_argument("--date", help="release date YYYY-MM-DD (default: today, UTC)")
    a = ap.parse_args(argv)
    tag = f"v{program_version()}" if a.tag == "auto" else a.tag
    doc = build(Path(a.image), tag, a.repo, a.image_url, a.date)
    Path(a.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    e = doc["os_list"][0]
    print(f"wrote {a.out}: {e['name']} ({e['image_download_size'] / 1048576:.0f} MB download, {e['extract_size'] / 1048576:.0f} MB written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
