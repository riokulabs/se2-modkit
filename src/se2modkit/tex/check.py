"""`check_mod`: validate a mod's own texture sets before the Mod SDK packs them.

A texture set is a folder under the mod's `Assets/` holding `.comptex` files (see `tex.scaffold`). For every set:

  - each `.comptex` reads, names a packing preset, and (when the Mod SDK is found) that preset exists and the
    `.comptex` lists as many sources as the preset has inputs;
  - each source GUID resolves, to a file in the mod (by its `.meta` AssetID) or to an SDK asset;
  - the mod's own maps are PNG files whose sides are powers of two, and all maps of one set share one size;
  - albedo and normal maps carry RGB; a single-value map (roughness, metal, AO, ...) that carries color is flagged;
    with Pillow installed, a normal map whose blue channel is mostly dark (not a tangent-space normal map) fails;
  - a PNG in the set folder that no `.comptex` uses is flagged.

Over the whole mod, any image that is a byte-for-byte copy of a game texture fails (`validate.vanilla_textures`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..preview import sdkmat
from ..validate import vanilla_textures
from . import png

__all__ = ["Problem", "Report", "check_mod"]

RGB_ROLES = ("albedo", "normal")
SCALAR_ROLES = ("metal", "roughness", "ao", "emissive", "colormask", "colormask2", "alphamask", "height", "f0",
                "translucency", "reflectance")
SKIP_DIRS = frozenset({".git", "Content", "preview", "previews", "__pycache__"})


@dataclass(frozen=True)
class Problem:
    level: str        # "error" | "warning"
    path: str         # relative to the mod
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.path}: {self.message}"


@dataclass
class Report:
    sets: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    presets_checked: bool = False
    pixels_checked: bool = False

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for n in sorted(filenames):
            yield Path(dirpath, n)


def _asset_ids(mod: Path) -> dict[str, Path]:
    out = {}
    for f in _walk(mod):
        if f.suffix == ".meta":
            g = sdkmat._asset_id(str(f))
            if g:
                out[g] = f.with_name(f.name[:-5])
    return out


def _pow2(n: int) -> bool:
    return n > 0 and not n & (n - 1)


def _normal_means(path: Path) -> tuple[float, float, float] | None:
    """Mean R, G, B (0..1) of a normal map on a small copy, or None without Pillow or on a read error."""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            small = im.convert("RGB")
            small.thumbnail((64, 64))
            mean = ImageStat.Stat(small).mean
    except (OSError, ValueError):
        return None
    return mean[0] / 255, mean[1] / 255, mean[2] / 255


def _pillow() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def check_mod(mod: Path, ix: sdkmat.Index | None = None, vanilla_roots=(), cache: Path | None = None) -> Report:
    """Check every texture set in `mod`. `ix` is the SDK index (presets, SDK asset GUIDs); None skips those checks."""
    mod = Path(mod)
    rep = Report(presets_checked=ix is not None, pixels_checked=_pillow())

    def rel(p) -> str:
        try:
            return Path(p).relative_to(mod).as_posix()
        except ValueError:
            return str(p)

    def add(level, path, msg):
        rep.problems.append(Problem(level, rel(path), msg))

    assets = mod / "Assets"
    comptex = [f for f in _walk(assets) if f.suffix == ".comptex"] if assets.is_dir() else []
    ids = _asset_ids(mod) if comptex else {}
    sets: dict[Path, list[Path]] = {}
    for f in comptex:
        sets.setdefault(f.parent, []).append(f)

    for folder, files in sorted(sets.items()):
        rep.sets.append(rel(folder))
        used: dict[Path, set[str]] = {}       # mod image -> roles it fills
        for ct in files:
            try:
                preset, sources = sdkmat.parse_comptex(ct)
            except (OSError, ValueError, AttributeError) as e:
                add("error", ct, f"cannot be read as a .comptex ({e})")
                continue
            if not preset:
                add("error", ct, "names no packing preset (PresetReference)")
                continue
            roles = ix.preset(preset) if ix is not None else []
            if ix is not None:
                if not roles:
                    add("error", ct, f"its packing preset {preset} is not in the Mod SDK")
                elif len(sources) != len(roles):
                    add("error", ct, f"lists {len(sources)} sources but its preset has {len(roles)} inputs")
            for i, (src, _const) in enumerate(sources):
                if not src:
                    continue
                role = roles[i][0] if i < len(roles) else None
                f = ids.get(src)
                if f is None:
                    if ix is not None and src in ix.files:
                        continue            # an SDK asset, referenced by GUID only
                    where = "in the mod" + (" or the Mod SDK" if ix is not None else " (Mod SDK not checked)")
                    add("error", ct, f"source {i + 1} ({role or 'unknown role'}) GUID {src} resolves to no file "
                                     f"{where}")
                    continue
                if not f.is_file():
                    add("error", ct, f"source {i + 1} points at {rel(f)}, which is missing")
                    continue
                if role is None:
                    stem = f.stem.rsplit("_", 1)
                    role = stem[1].lower() if len(stem) == 2 else ""
                used.setdefault(f, set()).add(role)

        sizes: dict[tuple[int, int], list[Path]] = {}
        for f, roles_ in sorted(used.items()):
            h = png.header(f)
            if h is None:
                add("error", f, "is not a PNG (the set's source maps are PNG; the SDK makes the DDS)")
                continue
            if not (_pow2(h.width) and _pow2(h.height)):
                add("error", f, f"is {h.width}x{h.height}; both sides must be powers of two")
            sizes.setdefault((h.width, h.height), []).append(f)
            if roles_ & set(RGB_ROLES) and h.channels < 3:
                add("error", f, f"is a {'/'.join(sorted(roles_ & set(RGB_ROLES)))} map but has no RGB channels")
            elif roles_ and roles_ <= set(SCALAR_ROLES) and h.channels >= 3:
                add("warning", f, f"is a single-value map ({'/'.join(sorted(roles_))}) saved in color; save it as "
                                  "grayscale")
            if "normal" in roles_ and h.channels >= 3 and rep.pixels_checked:
                m = _normal_means(f)
                if m is not None and m[2] < 0.5:
                    add("error", f, f"does not look like a tangent-space normal map (mean blue {m[2]:.2f}, expected "
                                    "mostly high, about 1.0 on flat areas)")
        if len(sizes) > 1:
            listing = ", ".join(f"{w}x{h} ({len(fs)})" for (w, h), fs in sorted(sizes.items()))
            add("error", folder, f"maps of one set must share one size; found {listing}")
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() == ".png" and f not in used:
                add("warning", f, "no .comptex in the set uses it")

    copies = vanilla_textures.find_copies(vanilla_textures.image_files(mod), list(vanilla_roots), cache)
    for f, v in copies:
        add("error", f, f"is an exact copy of the game texture {v.parent.name}/{v.name}; make your own texture")
    return rep


def to_json(rep: Report) -> str:
    return json.dumps({"sets": rep.sets, "presets_checked": rep.presets_checked,
                       "pixels_checked": rep.pixels_checked,
                       "problems": [p.__dict__ for p in rep.problems]}, indent=2)
