#!/usr/bin/env python3
"""Generate the PWA icons (dark tile, teal tuning-dial ring with tick marks). Pure numpy + zlib; no image libraries."""
import os
import struct
import zlib

import numpy as np

OUT = os.path.join(os.path.dirname(__file__), "..", "frontend", "icons")
BG, ACCENT, DIM = (8, 9, 11), (61, 214, 198), (139, 146, 156)


def png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    open(path, "wb").write(data)


def icon(size, safe=1.0, full_bleed=False):
    ss = 3                                         # supersample for smooth edges
    n = size * ss
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / n * 2 - 1  # -1..1
    r = np.hypot(x, y) / safe
    ang = np.degrees(np.arctan2(y, x)) % 360
    img = np.zeros((n, n, 3), dtype=np.float32)
    img[:] = BG
    if not full_bleed:                             # rounded-square tile
        k = 0.86
        inside = (np.abs(x) < k) & (np.abs(y) < k)
        corner = np.hypot(np.maximum(np.abs(x) - (k - 0.28), 0), np.maximum(np.abs(y) - (k - 0.28), 0)) < 0.28
        mask = inside & corner
        img[~mask] = 0
        alpha = mask
    else:
        alpha = np.ones((n, n), bool)
    ring = (r > 0.50) & (r < 0.62)
    img[ring] = ACCENT
    ticks = (r > 0.66) & (r < 0.74) & ((ang % 15) < 2.2)
    img[ticks & ~ring] = DIM
    major = (r > 0.66) & (r < 0.78) & ((ang % 90) < 3)
    img[major] = (232, 234, 237)
    dot = np.hypot(x - 0.0, y + 0.56 / 1.0 * safe) / safe < 0.07
    img[dot] = ACCENT
    hub = r < 0.34
    img[hub] = (27, 30, 36)
    pointer = (np.abs(x / safe) < (0.30 - (y / safe + 0.9)) * 0.35) & (y / safe > -0.9) & (y / safe < -0.78)
    img[pointer] = ACCENT
    img[~alpha] = BG if full_bleed else (0, 0, 0)
    out = img.reshape(size, ss, size, ss, 3).mean(axis=(1, 3))
    if not full_bleed:                             # flatten transparent corners onto the background colour
        a = alpha.reshape(size, ss, size, ss).mean(axis=(1, 3))[..., None]
        out = out * a + np.array(BG) * (1 - a)
    return out.clip(0, 255).astype(np.uint8)


os.makedirs(OUT, exist_ok=True)
png(os.path.join(OUT, "icon-192.png"), icon(192))
png(os.path.join(OUT, "icon-512.png"), icon(512))
png(os.path.join(OUT, "apple-touch-icon.png"), icon(180, full_bleed=True))
png(os.path.join(OUT, "icon-maskable-512.png"), icon(512, safe=0.8, full_bleed=True))   # artwork inside the 80% safe zone
print("icons written to", os.path.abspath(OUT))
