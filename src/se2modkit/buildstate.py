"""Where a mod's model build state lives: the block fragments, the build lock and the release stamp.

In the user cache (`<cache>/builds/`), or under `$SE2MODKIT_BUILD_STATE`, never in the mod. This module imports
nothing of the pipeline, so the publisher can find a mod's release stamp without loading the build's read hook.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

__all__ = ["STATE_ENV", "base", "mod_state_dir", "safe_name"]

STATE_ENV = "SE2MODKIT_BUILD_STATE"


def base(environ: Mapping[str, str] | None = None) -> Path:
    """The build state folder of every mod: `$SE2MODKIT_BUILD_STATE`, else `<user cache>/builds`."""
    environ = os.environ if environ is None else environ
    override = environ.get(STATE_ENV)
    if override:
        return Path(override)
    from .env.config import user_cache_dir
    return user_cache_dir(environ=environ) / "builds"


def report_tag(report: Path) -> str:
    """A short hash of the report path: one mod's state, wherever its folder is."""
    return hashlib.sha256(str(Path(report).resolve()).encode("utf-8")).hexdigest()[:16]


def safe_name(name: str) -> str:
    """`name` as a file name (anything else than letters, digits, "-", "_", "." replaced, plus a hash when it was)."""
    s = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name).strip(".") or "block"
    return s if s == name else f"{s}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"


def mod_state_dir(report: Path, mod_name: str, environ: Mapping[str, str] | None = None) -> Path:
    """One mod's build state folder (by its report path)."""
    return base(environ) / f"{safe_name(mod_name or 'mod')}-{report_tag(report)}"
