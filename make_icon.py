"""Generate assets/icon.ico without needing Pillow or an art tool.

Writes a real multi-size .ico (16/32/48/64/128/256) made of uncompressed
32-bit BMP frames: a rounded blue tile with a white "A" cut into it.
"""

from __future__ import annotations

import struct
from pathlib import Path

SIZES = [16, 32, 48, 64, 128, 256]

BG = (0x4C, 0x8D, 0xFF)   # accent blue, matches the app theme
FG = (0xFF, 0xFF, 0xFF)


def rounded(x: int, y: int, n: int) -> bool:
    """True if (x, y) is inside a rounded square of side n."""
    r = max(2, n // 6)
    cx = min(max(x, r), n - 1 - r)
    cy = min(max(y, r), n - 1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def in_letter_a(x: int, y: int, n: int) -> bool:
    """A blocky capital A, scaled to the tile."""
    fx, fy = x / n, y / n
    if not (0.18 <= fy <= 0.82):
        return False
    # the two diagonal legs
    span = 0.30 * (fy - 0.18) / 0.64          # widens toward the base
    left_leg = abs(fx - (0.5 - span)) < 0.075
    right_leg = abs(fx - (0.5 + span)) < 0.075
    # the crossbar
    crossbar = 0.54 <= fy <= 0.64 and abs(fx - 0.5) < span
    return left_leg or right_leg or crossbar


def frame(n: int) -> bytes:
    """One BMP frame: DIB header + BGRA rows (bottom-up) + AND mask."""
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # header size
        n,           # width
        n * 2,       # height (image + mask)
        1,           # planes
        32,          # bits per pixel
        0,           # BI_RGB
        n * n * 4,   # image size
        0, 0, 0, 0,
    )

    rows = []
    for y in range(n - 1, -1, -1):      # bottom-up
        row = bytearray()
        for x in range(n):
            if not rounded(x, y, n):
                row += bytes((0, 0, 0, 0))          # transparent corner
            elif in_letter_a(x, y, n):
                row += bytes((FG[2], FG[1], FG[0], 255))
            else:
                row += bytes((BG[2], BG[1], BG[0], 255))
        rows.append(bytes(row))

    # AND mask: one bit per pixel, padded to 4-byte rows. Zero = opaque;
    # the alpha channel does the real work for 32-bit icons.
    mask_row = b"\x00" * (((n + 31) // 32) * 4)
    mask = mask_row * n

    return header + b"".join(rows) + mask


def build(path: Path) -> Path:
    frames = [frame(n) for n in SIZES]

    out = bytearray()
    out += struct.pack("<HHH", 0, 1, len(frames))     # ICONDIR

    offset = 6 + 16 * len(frames)
    for n, data in zip(SIZES, frames):
        out += struct.pack(
            "<BBBBHHII",
            0 if n >= 256 else n,    # width  (0 means 256)
            0 if n >= 256 else n,    # height
            0,                        # palette
            0,                        # reserved
            1,                        # planes
            32,                       # bpp
            len(data),
            offset,
        )
        offset += len(data)

    for data in frames:
        out += data

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "assets" / "icon.ico"
    build(target)
    print("wrote {} ({} bytes)".format(target, target.stat().st_size))
