"""Keen's Blender material and decal library from the Mod SDK (`<SDK>/BlenderMaterialLibrary/*.zip`).

The SDK ships the library as a zip (a .blend with the materials and decals, a Blender asset catalog file and some
preview images). `unpack` extracts it once into the user cache (`<cache>/blender-library/<zip stem>-<stamp>/`, never a
repository) and returns that folder: the path to register as a Blender asset library. The probe, relink and register
steps run inside Blender through the job queue (`library_blend.py`); this module never starts Blender.

The library's images point at Keen's own machines (`//../../../KSHProjects/...`). A relink writes a copy of the
library beside the unpacked one (`<folder>-relinked/`), its .blend pointing those images at the same files in this
game or Mod SDK install; the unpacked copy and the installs are never changed. Registering prefers that copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from . import KitError

__all__ = ["DONE", "RELINKED", "finish_relink", "find_blend", "find_zip", "library_dir", "local_equivalent",
           "planned_blend", "prepare_relink", "register_dir", "relinked_dir", "summarize", "unpack"]

DONE = ".unpacked"            # written last: a folder without it is a half-finished unpack and is redone
RELINKED = ".relinked"        # the same for a relinked copy
WORK_FILES = {DONE, RELINKED, "probe.json", "relink.json", "relink-result.json"}   # never copied into a relinked copy
MAX_UNPACKED = 2 * 1024 ** 3  # the 2026 library is about 360 MB unpacked


def find_zip(sdk_dir: Path | None) -> Path:
    """The library zip in the SDK's BlenderMaterialLibrary folder; KitError when there is none."""
    if sdk_dir is None:
        raise KitError("The Mod SDK was not found; install it or point --sdk at it.")
    folder = Path(sdk_dir) / "BlenderMaterialLibrary"
    zips = sorted(folder.glob("*.zip")) if folder.is_dir() else []
    if not zips:
        raise KitError(f"There is no material library zip in {folder}.")
    return zips[0]


def _stamp(zpath: Path) -> str:
    st = zpath.stat()
    return hashlib.sha256(f"{zpath.name}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:10]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def library_dir(zpath: Path, cache_root: Path) -> Path:
    """Where `unpack` puts this zip: one folder per zip name and version (size and time), so an SDK update unpacks
    anew."""
    return Path(cache_root) / "blender-library" / f"{_slug(Path(zpath).stem)}-{_stamp(Path(zpath))}"


def _safe_members(z: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    out = []
    for info in z.infolist():
        name = PurePosixPath(info.filename)
        if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename:
            raise KitError(f"The library zip has an unsafe member {info.filename!r}; refusing to unpack it.")
        if not info.is_dir():
            out.append(info)
    return out


def unpack(zpath: Path, cache_root: Path, *, max_bytes: int = MAX_UNPACKED) -> tuple[Path, bool]:
    """(library folder, unpacked now): extracts the zip once into the user cache. Nested zips are left as they are
    (the library carries a Blender add-on zip; nothing here installs or runs it)."""
    dest = library_dir(zpath, cache_root)
    if (dest / DONE).is_file():
        return dest, False
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".unpack-", dir=dest.parent))
    try:
        with zipfile.ZipFile(zpath) as z:
            members = _safe_members(z)
            if sum(i.file_size for i in members) > max_bytes:
                raise KitError(f"The library zip unpacks to over {max_bytes // 1024 ** 2} MB; refusing it.")
            written = 0
            for info in members:
                target = stage / PurePosixPath(info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("wb") as dst:
                    while chunk := src.read(1 << 20):
                        written += len(chunk)
                        if written > max_bytes:   # the sizes in the zip's directory can lie
                            raise KitError("The library zip unpacks to more than its directory says; refusing it.")
                        dst.write(chunk)
        (stage / DONE).write_text(f"{Path(zpath).name}\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(stage, dest)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as e:
        raise KitError(f"{zpath} is not a readable zip ({e}).") from None
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return dest, True


def find_blend(lib: Path) -> Path:
    """The library's .blend (the largest, when there are several); KitError when there is none."""
    blends = sorted(Path(lib).rglob("*.blend"), key=lambda p: p.stat().st_size, reverse=True)
    if not blends:
        raise KitError(f"There is no .blend in {lib}.")
    return blends[0]


def planned_blend(zpath: Path, lib: Path) -> Path:
    """Where the library's .blend is (unpacked) or will be (read from the zip's directory, nothing extracted)."""
    if (Path(lib) / DONE).is_file():
        return find_blend(lib)
    try:
        with zipfile.ZipFile(zpath) as z:
            blends = sorted((i for i in _safe_members(z) if i.filename.lower().endswith(".blend")),
                            key=lambda i: i.file_size, reverse=True)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as e:
        raise KitError(f"{zpath} is not a readable zip ({e}).") from None
    if not blends:
        raise KitError(f"There is no .blend in {zpath}.")
    return Path(lib).joinpath(*PurePosixPath(blends[0].filename).parts)


def local_equivalent(path: str, game_dir: Path | None, sdk_assets: Path | None) -> Path | None:
    """Where a library image path written on Keen's machines would be here: `.../Vanilla/Content/<rest>` in the
    game's `GameData/Vanilla/Content`, `.../Vanilla/Assets/<rest>` in the SDK's Vanilla assets; None otherwise."""
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    for i in range(len(parts) - 2, 0, -1):
        if parts[i - 1] == "Vanilla" and parts[i] in ("Content", "Assets"):
            root = (Path(game_dir) / "GameData" / "Vanilla" / "Content" if parts[i] == "Content" and game_dir
                    else Path(sdk_assets) if parts[i] == "Assets" and sdk_assets else None)
            return root.joinpath(*parts[i + 1:]) if root is not None else None
    return None


def summarize(probe: dict, game_dir: Path | None = None, sdk_assets: Path | None = None) -> dict:
    """What a probe found: image counts, the missing paths and where each would be locally (if it is there)."""
    images = probe.get("images", [])
    missing = [i for i in images if not i.get("packed") and i.get("filepath") and not i.get("exists")]
    found = {}
    for i in missing:
        loc = local_equivalent(i["filepath"], game_dir, sdk_assets)
        if loc is not None and loc.is_file():
            found[i["filepath"]] = str(loc)
    return {"images": len(images), "packed": sum(1 for i in images if i.get("packed")), "missing": missing,
            "found_locally": found, "materials": len(probe.get("materials", {})),
            "libraries": probe.get("libraries", [])}


def relinked_dir(lib: Path) -> Path:
    """Where the relinked copy of the unpacked library `lib` goes: a sibling folder, in the same cache."""
    return Path(lib).with_name(Path(lib).name + "-relinked")


def register_dir(lib: Path) -> Path:
    """The folder to register as the asset library: the relinked copy when one is finished, else `lib`."""
    rel = relinked_dir(lib)
    return rel if (rel / RELINKED).is_file() else Path(lib)


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def prepare_relink(lib: Path, blend: Path, found: dict) -> tuple[Path, Path, Path]:
    """Stage a relinked copy of `lib`: a fresh folder beside it holding every library file but the .blend (hard links
    when it can) and the map {path in the .blend: local file} the Blender job reads. Returns (stage folder, map file,
    where the job saves the .blend). Nothing outside the cache is touched."""
    lib, blend = Path(lib), Path(blend)
    stage = Path(tempfile.mkdtemp(prefix=f".{relinked_dir(lib).name}-", dir=lib.parent))
    for f in lib.rglob("*"):
        if f.is_file() and f != blend and f.name not in WORK_FILES:
            _link_or_copy(f, stage / f.relative_to(lib))
    mapping = stage / "relink.json"
    mapping.write_text(json.dumps(dict(sorted(found.items())), indent=1) + "\n", encoding="utf-8")
    return stage, mapping, stage / blend.relative_to(lib)


def finish_relink(lib: Path, stage: Path) -> Path:
    """Put a staged copy whose Blender job succeeded in place (replacing an older one) and mark it finished."""
    final = relinked_dir(lib)
    (Path(stage) / RELINKED).write_text(f"{Path(lib).name}\n", encoding="utf-8")
    if final.exists():
        shutil.rmtree(final)
    os.replace(stage, final)
    return final
