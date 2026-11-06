"""Keen's Blender material and decal library from the Mod SDK (`<SDK>/BlenderMaterialLibrary/*.zip`).

The SDK ships the library as a zip (a .blend with the materials and decals, a Blender asset catalog file and some
preview images). `unpack` extracts it once into the user cache (`<cache>/blender-library/<zip stem>-<stamp>/`, never a
repository) and returns that folder: the path to register as a Blender asset library. The probe and register steps
run inside Blender through the job queue (`library_blend.py`); this module never starts Blender.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from . import KitError

__all__ = ["DONE", "find_blend", "find_zip", "library_dir", "local_equivalent", "summarize", "unpack"]

DONE = ".unpacked"            # written last: a folder without it is a half-finished unpack and is redone
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
