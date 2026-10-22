"""Workshop field limits (Steam + the HUB) and the thumbnail tools."""
from __future__ import annotations

from pathlib import Path

MAX_NAME_CHARS = 50          # ModHUB ModLimit.MAX_NAME_LENGTH
MAX_TITLE_BYTES = 128        # k_cchPublishedDocumentTitleMax - 1
MAX_SUMMARY_CHARS = 250      # ModHUB ModLimit.MAX_SUMMARY_LENGTH (goes into Keen's metadata)
MAX_DESCRIPTION_BYTES = 8000  # k_cchPublishedDocumentDescriptionMax
PREVIEW_MAX_BYTES = 1_000_000  # Steam: "must be less than 1 Megabyte"; the decimal MB is the safe reading
PREVIEW_MIN_BYTES = 16
THUMB_SIZE = (1280, 720)


def check_title(title: str) -> list[str]:
    errs = []
    if not title.strip():
        errs.append("title is empty")
    if len(title) > MAX_NAME_CHARS:
        errs.append(f"title is {len(title)} characters; the HUB allows {MAX_NAME_CHARS}")
    if len(title.encode("utf-8")) > MAX_TITLE_BYTES:
        errs.append(f"title is over Steam's {MAX_TITLE_BYTES}-byte limit")
    return errs


def check_summary(summary: str) -> list[str]:
    return [f"summary is {len(summary)} characters; the HUB allows {MAX_SUMMARY_CHARS}"] \
        if len(summary) > MAX_SUMMARY_CHARS else []


def check_description(text: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a Steam text field: UTF-8 bytes with newlines counted as CRLF."""
    from .bbcode import DESCRIPTION, DESCRIPTION_WARN_BYTES, steam_bytes, validate
    errs, warns = [], []
    n = steam_bytes(text)
    if n > MAX_DESCRIPTION_BYTES:
        errs.append(f"description is {n} bytes (UTF-8, newlines as CRLF); Steam's limit is {MAX_DESCRIPTION_BYTES}")
    elif n > DESCRIPTION_WARN_BYTES:
        warns.append(f"description is {n} bytes; close to Steam's {MAX_DESCRIPTION_BYTES}")
    warns += [f"description markup: {e}" for e in validate(text, DESCRIPTION)]
    return errs, warns


def jpeg_size(path: Path | bytes) -> tuple[int, int] | None:
    """(width, height) from a baseline/progressive JPEG's SOF marker, without decoding."""
    data = path if isinstance(path, bytes) else path.read_bytes()
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + seg
    return None


def check_thumbnail(path: Path | bytes) -> list[str]:
    if isinstance(path, Path) and not path.exists():
        return [f"thumbnail {path} not found"]
    data = path if isinstance(path, bytes) else path.read_bytes()
    errs = []
    n = len(data)
    if n >= PREVIEW_MAX_BYTES:
        errs.append(f"thumbnail is {n} bytes; Steam needs < {PREVIEW_MAX_BYTES}")
    if n < PREVIEW_MIN_BYTES:
        errs.append("thumbnail is smaller than Steam's 16-byte minimum")
    size = jpeg_size(data)
    if size is None:
        errs.append("thumbnail is not a JPEG (the HUB always uploads thumb.jpg)")
    return errs


def reencode_thumbnail(src: Path, dest: Path, size: tuple[int, int] = THUMB_SIZE) -> tuple[int, int]:
    """Cover-crop to 1280x720 (centre) and save as JPEG under the preview limit. Returns (quality, bytes)."""
    from PIL import Image  # optional dependency, only for this command

    im = Image.open(src).convert("RGB")
    tw, th = size
    scale = max(tw / im.width, th / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left, top = (im.width - tw) // 2, (im.height - th) // 2
    im = im.crop((left, top, left + tw, top + th))
    dest.parent.mkdir(parents=True, exist_ok=True)
    for q in (92, 88, 85, 80, 75, 70, 60):
        im.save(dest, "JPEG", quality=q, optimize=True, progressive=False)
        n = dest.stat().st_size
        if n < PREVIEW_MAX_BYTES:
            return q, n
    raise ValueError("could not get the thumbnail under 1 MB")
