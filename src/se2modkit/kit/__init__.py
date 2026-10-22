"""Kit assets: the Rioku Labs maker mark and the Rioku decal atlas.

Text sources live here in git (SVG artwork, the atlas element table `atlas/rioku.toml`). The binaries made from them
(atlas PNG, DDS when a native `texconv` exists) are never committed: `se2mod kit build` renders them into the user
cache, `se2mod kit fetch` downloads them from the release zip recorded in `assets.toml`, and a mod's build copies what
it uses into its own `Assets/`.
"""

from __future__ import annotations

from pathlib import Path

from .. import __version__

__all__ = ["KIT_DIR", "KitError", "asset_path", "cache_dir"]

KIT_DIR = Path(__file__).resolve().parent


class KitError(Exception):
    """A one-sentence, user-facing failure."""


def cache_dir(cache_root: Path | None = None) -> Path:
    """`<user cache>/kit/<modkit version>/`, where built or fetched kit assets live."""
    if cache_root is None:
        from ..env.config import user_cache_dir
        cache_root = user_cache_dir()
    return Path(cache_root) / "kit" / __version__


def asset_path(name: str, cache_root: Path | None = None) -> Path:
    """The cached kit asset `name` (e.g. `RiokuAtlas01.png`); `KitError` says how to make it when it is missing."""
    p = cache_dir(cache_root) / name
    if not p.is_file():
        raise KitError(f"Kit asset {name} is not in {p.parent}; run `se2mod kit build` (or `se2mod kit fetch`).")
    return p
