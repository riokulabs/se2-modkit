"""Just enough PNG for texture sets, with no image library: write a flat (one-value) map, read a file's header.

  flat(path, size, value)   write a size x size PNG of one value: an int (8-bit grayscale) or an (r, g, b) tuple
  header(path)              Header(width, height, bit_depth, color_type) of a PNG, or None when it is not one
  channels(color_type)      how many channels a PNG color type stores (1 gray, 2 gray+alpha, 3 RGB, 4 RGBA, 1 palette)
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Header", "channels", "flat", "header"]

SIGNATURE = b"\x89PNG\r\n\x1a\n"
GRAY, RGB, PALETTE, GRAY_ALPHA, RGBA = 0, 2, 3, 4, 6
_CHANNELS = {GRAY: 1, RGB: 3, PALETTE: 1, GRAY_ALPHA: 2, RGBA: 4}


@dataclass(frozen=True)
class Header:
    width: int
    height: int
    bit_depth: int
    color_type: int

    @property
    def channels(self) -> int:
        return channels(self.color_type)


def channels(color_type: int) -> int:
    return _CHANNELS.get(color_type, 0)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def flat(path: Path, size: int, value: int | tuple[int, int, int]) -> Path:
    """Write a `size` x `size` 8-bit PNG filled with `value` (gray int, or an RGB tuple); parent folders are made."""
    rgb = isinstance(value, tuple)
    px = bytes(value) if rgb else bytes([value])
    row = b"\x00" + px * size                        # filter type 0, then the pixels
    data = zlib.compress(row * size, 9)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, RGB if rgb else GRAY, 0, 0, 0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", data) + _chunk(b"IEND", b""))
    return path


def header(path: Path) -> Header | None:
    """The IHDR of a PNG file, or None when the file is not a readable PNG."""
    try:
        with open(path, "rb") as f:
            head = f.read(33)
    except OSError:
        return None
    if len(head) < 33 or head[:8] != SIGNATURE or head[12:16] != b"IHDR":
        return None
    w, h, depth, ctype = struct.unpack(">IIBB", head[16:26])
    return Header(w, h, depth, ctype)
