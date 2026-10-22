"""Every "Bundle:Type" name in an asset file resolves through the file's own `$Bundles`.

In archive-format JSON the game's serializer resolves a type name only through the file's bundle set and throws
"There is no bundle named X in the serialization context." otherwise. Every vanilla file that names a Game2: type
declares Game2.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["ASSET_SUFFIXES", "TYPE_FIELDS", "asset_files", "type_bundles", "undeclared", "undeclared_in"]

TYPE_FIELDS = ("$Type", "Type", "UseActionType")
ASSET_SUFFIXES = (".def", ".partialdef", ".model", ".meta")


def type_bundles(x: Any, out: set[str] | None = None) -> set[str]:
    """The bundle prefixes of every "Bundle:Type" name in x ($Type / Type / UseActionType values, nested)."""
    out = set() if out is None else out
    if isinstance(x, dict):
        for k, v in x.items():
            if k in TYPE_FIELDS and isinstance(v, str) and ":" in v:
                out.add(v.split(":", 1)[0])
            type_bundles(v, out)
    elif isinstance(x, list):
        for v in x:
            type_bundles(v, out)
    return out


def undeclared(data: Any) -> set[str]:
    """The bundles `data`'s type names use that its `$Bundles` does not declare."""
    if not isinstance(data, dict):
        return set()
    return type_bundles(data) - set(data.get("$Bundles") or {})


def asset_files(root: Path, suffixes: tuple[str, ...] = ASSET_SUFFIXES):
    """Every asset JSON file under `root` (sorted)."""
    for dp, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            if f.endswith(suffixes):
                yield Path(dp) / f


def undeclared_in(root: Path) -> tuple[int, list[tuple[str, list[str]]]]:
    """(files read, [(relative path, missing bundles)]) for the asset files under `root`; an unreadable file is
    reported with the missing list ["<unreadable: ...>"]."""
    n, bad = 0, []
    for p in asset_files(Path(root)):
        n += 1
        try:
            with open(p, encoding="utf-8-sig") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as e:
            bad.append((p.relative_to(root).as_posix(), [f"<unreadable: {e}>"]))
            continue
        missing = undeclared(d)
        if missing:
            bad.append((p.relative_to(root).as_posix(), sorted(missing)))
    return n, bad
