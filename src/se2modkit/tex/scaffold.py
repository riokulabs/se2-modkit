"""`new_set`: start a mod's own texture set shaped like a vanilla material, without copying any of its pixels.

A vanilla block material (`<Name>.def` in the Mod SDK) names one packed texture per slot (`ColorMetalTexture`,
`NormalRoughnessTexture`, `ExtensionsTexture`, ...). Each packed texture is a `.comptex` that points at a packing preset
(`.texpreset`, which says what each input is: albedo, metal, normal, ...) and lists one source per input. The Mod SDK
packs and compresses the sources into DDS at build time.

`new_set` writes, under `<mod>/Assets/<folder>/<name>/`:
  - one flat placeholder PNG per map the set needs (albedo, normal, roughness, metal, AO, emissive, color mask, and any
    other input the vanilla material fills from a texture), made here from fixed neutral values;
  - one `.comptex` per packed texture, reusing the vanilla packing preset by its GUID and pointing each input at our
    PNG (or at the same constant or default the vanilla one uses);
  - a minimal `.meta` beside every new file and folder, so the `.comptex` files can name the PNGs by GUID.

What is taken from the vanilla material is its layout only: the preset GUIDs, the order of inputs and the version
stamps of the file format. No vanilla image is read, copied or referenced.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..defs.envelope import envelope, write_json
from ..preview import sdkmat
from . import png

__all__ = ["AUTHORED", "COMPTEX_TYPE", "META_TYPE", "Packed", "SetError", "flat_value", "layout", "new_set"]

COMPTEX_TYPE = "VRage:Keen.VRage.ContentPipeline.Textures.Composite.CompositeTextureModel"
SOURCE_TYPE = "VRage:Keen.VRage.ContentPipeline.Textures.Composite.CompositeTextureSourceModel"
META_TYPE = "VRage:Keen.VRage.ContentPipeline.Metafiles.MetaData"
AUTHORED = ("albedo", "metal", "normal", "roughness", "ao", "emissive", "colormask")   # always get a map
RGB_ROLES = ("albedo", "normal")
SLOT_SUFFIX = {"ColorMetalTexture": "cm", "NormalRoughnessTexture": "nr", "ExtensionsTexture": "add"}
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class SetError(ValueError):
    """A texture set cannot be made as asked; the message is one sentence for the user."""


@dataclass
class Packed:
    """One packed texture of a vanilla material: its slot, file suffix, preset and inputs."""
    slot: str
    suffix: str
    preset: str                                    # preset GUID
    inputs: list[tuple[str, float, str, float]]    # (role, preset default, "texture" | "constant" | "none", constant)
    bundles: dict = field(default_factory=dict)    # the vanilla .comptex's $Bundles (format version stamps)


def _bundles(path: str | Path) -> dict:
    try:
        b = sdkmat._load(path).get("$Bundles")
    except (OSError, ValueError):
        return {}
    return b if isinstance(b, dict) else {}


def layout(ix: sdkmat.Index, like: str) -> list[Packed]:
    """The packed textures of the vanilla material `like` (an SDK `.def` name), in slot order."""
    p = ix.defs.get(like)
    if not p:
        raise SetError(f"No vanilla material named {like!r} in the Mod SDK (expected {like}.def under BlockMaterials).")
    try:
        _kind, tex, _em = sdkmat.parse_def(p)
    except (OSError, ValueError) as e:
        raise SetError(f"Cannot read the vanilla material {like!r} ({e}).") from e
    out = []
    for slot, guid in tex.items():
        ct = ix.files.get(guid)
        if not ct or not ct.endswith(".comptex"):
            continue
        try:
            preset, sources = sdkmat.parse_comptex(ct)
        except (OSError, ValueError):
            continue
        roles = ix.preset(preset) if preset else []
        if not roles:
            continue
        inputs = []
        for i, (role, default) in enumerate(roles):
            src, const = sources[i] if i < len(sources) else (None, None)
            kind = "constant" if const is not None else ("texture" if src else "none")
            inputs.append((role, default, kind, const or 0.0))
        stem = Path(ct).stem
        suffix = stem[len(like) + 1:] if stem.startswith(like + "_") else SLOT_SUFFIX.get(slot, slot.lower())
        out.append(Packed(slot, suffix or SLOT_SUFFIX.get(slot, slot.lower()), preset, inputs, _bundles(ct)))
    if not out:
        raise SetError(f"The vanilla material {like!r} has no packed textures whose preset the Mod SDK provides.")
    return out


def flat_value(role: str, default: float, *, vanilla_textured: bool = False) -> int | tuple[int, int, int]:
    """The neutral placeholder value of a map: mid-gray albedo, a flat OpenGL normal (pointing out of the surface),
    half roughness, no metal, no occlusion, no glow; the color mask full when the vanilla material is paintable."""
    if role == "albedo":
        return (128, 128, 128)
    if role == "normal":
        return (128, 128, 255)
    fixed = {"roughness": 128, "metal": 0, "ao": 255, "emissive": 0, "alphamask": 255}
    if role in fixed:
        return fixed[role]
    if role == "colormask":
        return 255 if vanilla_textured else 0
    return max(0, min(255, round(default * 255)))


def _default_bundles() -> dict:
    from ..compat import load_compat
    from ..env.versions import SYSTEM_RUNTIME_BUNDLE
    return {"System.Runtime": SYSTEM_RUNTIME_BUNDLE, "VRage": load_compat().game_versions_tested[-1]}


def _meta(path: Path, bundles: dict, asset_id: str | None = None) -> str:
    asset_id = asset_id or str(uuid.uuid4())
    write_json(path.with_name(path.name + ".meta"), envelope(META_TYPE, {"AssetID": asset_id, "InternalItems": []},
                                                             bundles))
    return asset_id


def new_set(mod: Path, name: str, like: str, ix: sdkmat.Index, *, size: int = 1024, folder: str = "Textures",
            bundles: dict | None = None) -> tuple[Path, dict[str, str], list[Path]]:
    """Write the set; returns (set folder, {material slot: our .comptex GUID}, files written).

    `bundles` stamps the new `.meta` files (default: the vanilla `.comptex` files' own stamps)."""
    if not _NAME.match(name):
        raise SetError(f"Texture set name {name!r} must start with a letter and use only letters, digits and _.")
    if size < 4 or size & (size - 1):
        raise SetError(f"--size {size} is not a power of two (4 or more).")
    packs = layout(ix, like)
    mod = Path(mod)
    assets = mod / "Assets"
    if not assets.is_dir():
        raise SetError(f"{mod} has no Assets folder; is it a mod?")
    dest = assets / folder / name
    if dest.exists():
        raise SetError(f"{dest.relative_to(mod).as_posix()} already exists; pick another name or remove it first.")
    meta_bundles = bundles or next((p.bundles for p in packs if p.bundles), None) or _default_bundles()
    written: list[Path] = []
    d = assets
    for part in (*Path(folder).parts, name):     # folders need a .meta too ("File with unreadable parent")
        d = d / part
        if not d.exists():
            d.mkdir()
            if not d.with_name(d.name + ".meta").exists():
                _meta(d, meta_bundles)
                written.append(d.with_name(d.name + ".meta"))

    textured = {role for p in packs for role, _d, kind, _c in p.inputs if kind == "texture"}
    roles: dict[str, float] = {}
    for p in packs:
        for role, default, kind, _c in p.inputs:
            if role in AUTHORED or kind == "texture":
                roles.setdefault(role, default)
    maps: dict[str, str] = {}
    for role, default in roles.items():
        f = png.flat(dest / f"{name}_{role}.png", size,
                     flat_value(role, default, vanilla_textured=role in textured))
        maps[role] = _meta(f, meta_bundles)
        written += [f, f.with_name(f.name + ".meta")]

    slots: dict[str, str] = {}
    for p in packs:
        models = []
        for role, _default, kind, const in p.inputs:
            if role in maps:
                models.append({"$Type": SOURCE_TYPE, "Texture": "{G}" + maps[role], "ConstantValue": 0,
                               "SourceSelection": "Texture"})
            elif kind == "constant":
                models.append({"$Type": SOURCE_TYPE, "Texture": None, "ConstantValue": const,
                               "SourceSelection": "Constant"})
            else:
                models.append({"$Type": SOURCE_TYPE, "Texture": None, "ConstantValue": 0,
                               "SourceSelection": "Texture"})
        f = dest / f"{name}_{p.suffix}.comptex"
        write_json(f, envelope(COMPTEX_TYPE, {"PresetReference": "{G}" + p.preset, "Rotation": "None", "Flip": "None",
                                              "PresetModel": {"TextureSourceModels": models}},
                               p.bundles or meta_bundles))
        slots[p.slot] = _meta(f, meta_bundles)
        written += [f, f.with_name(f.name + ".meta")]
    return dest, slots, written
