"""The JSON envelope every SDK asset file uses, written byte-for-byte as the legacy `se2mod.py` wrote it."""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["envelope", "write_json"]


def envelope(type_: str, value: dict, bundles: dict[str, str]) -> dict:
    """`{"$Bundles": bundles, "$Type": type_, "$Value": value}`; callers pass the stamps (see `bundle_stamps`)."""
    return {"$Bundles": bundles, "$Type": type_, "$Value": value}


def write_json(path: Path, data) -> None:
    """Write `data` as 2-space indented JSON (ASCII-escaped) plus a final newline, LF, creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
