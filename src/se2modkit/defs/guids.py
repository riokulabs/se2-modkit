"""Stable asset GUIDs: minted once, kept in the mod's `asset_guids.json` (`ModPaths.asset_guids`) and reused by every
rebuild, so a regenerated package never changes a reference. Keys are readable paths ("dir/<Mod>/<Block>/Models",
"def/<Block>/BreakableBlockDefinition", "dummy/<Block>/<name>", or a staged file's mod-relative path)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

__all__ = ["Guids"]


class Guids:
    def __init__(self, path: str | os.PathLike):
        if path is None:
            raise ValueError("No asset GUID store path (ModPaths.asset_guids); build the mod's paths from its project.")
        self.path = Path(path)
        self.d: dict[str, str] = {}
        if self.path.exists():
            self.d = json.loads(self.path.read_text(encoding="utf-8"))

    def __getitem__(self, key: str) -> str:
        if key not in self.d:
            self.d[key] = str(uuid.uuid4())
        return self.d[key]

    def __contains__(self, key: str) -> bool:
        return key in self.d

    def save(self) -> None:
        """Atomic (a temp file renamed over the old one): a concurrent reader (another test run, a build) never sees a
        half-written file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(dict(sorted(self.d.items())), indent=1) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, self.path)
