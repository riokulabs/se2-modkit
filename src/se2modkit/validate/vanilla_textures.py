"""Refuse a mod that carries a byte-for-byte copy of a game texture.

Reading the game's and the Mod SDK's textures locally (for previews, for reference) is fine; shipping or committing
them is not. This finds every image file in a mod (or a staged upload) whose bytes equal a texture under the game's or
the SDK's `GameData/Vanilla` folder.

The vanilla folders hold tens of gigabytes of images, so nothing is hashed up front: the mod's image sizes are
collected first, the vanilla folders are walked by size only (a stat per file), and only vanilla files with a size
some mod file also has are hashed. Those hashes are cached by path, size and modification time.

  image_files(root)                       every image under root, skipping .git, preview folders and caches; a
                                          symlinked image counts (the build copies what it points at)
  vanilla_roots(env)                      the game's and the SDK's GameData/Vanilla folders that exist
  find_copies(files, roots, cache=None)   [(mod file, vanilla file)] for every exact copy
  refusal(copies, base)                   the message to print, or "" when there are none
  NO_ROOTS                                the warning to print when no vanilla folder was found
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

__all__ = ["IMAGE_EXT", "find_copies", "image_files", "refusal", "vanilla_roots"]

IMAGE_EXT = frozenset({".png", ".dds", ".tga", ".tif", ".tiff", ".jpg", ".jpeg", ".exr", ".bmp", ".hdr"})
SKIP_DIRS = frozenset({".git", ".venv", "__pycache__", "node_modules", "preview", "previews"})
NO_ROOTS = "warning: no game or Mod SDK GameData/Vanilla folder found; game textures were not checked"
MIN_BYTES = 256  # smaller files are too generic to call a copy (a 1x1 swatch)
CACHE_NAME = "vanilla-texture-hashes.json"


def image_files(root: Path) -> list[Path]:
    """Every image file under `root` (by extension), skipping SKIP_DIRS. Symlinked folders are not entered; a symlinked
    file is kept, since its bytes are what a build copies."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath, name)
            if p.suffix.lower() in IMAGE_EXT and p.is_file():
                out.append(p)
    return sorted(out)


def vanilla_roots(env) -> list[Path]:
    """The game's and the Mod SDK's GameData/Vanilla folders, whichever exist."""
    roots = []
    for base in (getattr(env, "game_dir", None), getattr(env, "sdk_dir", None)):
        if base is not None and (Path(base) / "GameData" / "Vanilla").is_dir():
            roots.append(Path(base) / "GameData" / "Vanilla")
    return roots


def _walk_sizes(root: Path, wanted: set[int]) -> Iterator[tuple[Path, os.stat_result]]:
    stack = [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False) and os.path.splitext(e.name)[1].lower() in IMAGE_EXT:
                        st = e.stat(follow_symlinks=False)
                        if st.st_size in wanted:
                            yield Path(e.path), st
        except OSError:
            continue  # an unreadable folder is not a reason to block a build


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(cache: Path | None) -> dict:
    if cache is None:
        return {}
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cache: Path | None, data: dict) -> None:
    if cache is None:
        return
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache.parent, prefix=cache.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, cache)
    except OSError:
        pass  # the cache only saves time


def find_copies(files: Iterable[Path], roots: Iterable[Path], cache: Path | None = None) -> list[tuple[Path, Path]]:
    """(mod file, vanilla file) for every mod file whose bytes equal a vanilla image under `roots`."""
    by_size: dict[int, list[Path]] = {}
    for f in files:
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size >= MIN_BYTES:
            by_size.setdefault(size, []).append(f)
    if not by_size:
        return []
    known = _load(cache)
    fresh: dict = {}
    mod_hash: dict[Path, str] = {}
    copies: list[tuple[Path, Path]] = []
    for root in roots:
        for vpath, st in _walk_sizes(Path(root), set(by_size)):
            key = str(vpath)
            stamp = [st.st_size, st.st_mtime_ns]
            entry = known.get(key)
            digest = entry[2] if isinstance(entry, list) and len(entry) == 3 and entry[:2] == stamp else None
            if digest is None:
                try:
                    digest = _sha256(vpath)
                except OSError:
                    continue
            fresh[key] = stamp + [digest]
            for f in by_size[st.st_size]:
                if f not in mod_hash:
                    try:
                        mod_hash[f] = _sha256(f)
                    except OSError:
                        mod_hash[f] = ""  # gone or unreadable since it was listed
                if mod_hash[f] == digest:
                    copies.append((f, vpath))
    if fresh:
        _save(cache, {**known, **fresh})
    return sorted(set(copies))


def refusal(copies: list[tuple[Path, Path]], base: Path) -> str:
    """The refusal message for `copies` (paths shown relative to `base` where possible), or ""."""
    if not copies:
        return ""

    def rel(p: Path) -> str:
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            return str(p)

    lines = [f"{len(copies)} file(s) are exact copies of game textures, which must never ship or be committed:"]
    lines += [f"  {rel(f)}  =  {v.parent.name}/{v.name}" for f, v in copies]
    lines.append("Reference a vanilla material by its GUID instead, or make your own texture.")
    return "\n".join(lines)
