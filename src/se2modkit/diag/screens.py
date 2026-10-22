"""Screenshots via the bridge plugin's take_screenshot, saved with automatic names under resources/previews/diag/
(a workspace folder that is never committed; see README)."""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import locations as paths


def safe_label(label: str | None) -> str:
    if not label:
        return ""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", label.strip()).strip("-")
    return s[:60]


def ext_for(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".bin"


def target_path(data: bytes, label: str | None = None, directory: Path | None = None, now: float | None = None) -> Path:
    d = directory or paths.previews_dir()
    stem = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    lab = safe_label(label)
    base = f"{stem}_{lab}" if lab else stem
    p = d / (base + ext_for(data))
    n = 2
    while p.exists():
        p = d / f"{base}-{n}{ext_for(data)}"
        n += 1
    return p


def save(data: bytes, label: str | None = None, directory: Path | None = None) -> Path:
    p = target_path(data, label, directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p
