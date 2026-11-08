"""Which source maps make a Keen block material, read from the Mod SDK's text files (no Blender, read only).

How the SDK describes a material (all JSON with a `$Type` / `$Value` envelope):

  <Name>.def          a material definition (`PBRMaterialDefinitionObjectBuilder`, ...). Its texture slots
                      (`ColorMetalTexture`, `NormalRoughnessTexture`, `ExtensionsTexture`, ...) name packed textures
                      by asset GUID, written `{G}<guid>`; `EmissivityMultiplier` scales the emissive channel.
  <Name>_<x>.comptex  a packed texture (`CompositeTextureModel`): `PresetReference` names a packing preset and
                      `PresetModel.TextureSourceModels` lists one source per preset input, in the preset's order: a
                      source PNG by GUID, or none (the preset's default), or a constant. Current files type each
                      source as `CompositeTexturePresetModel+SourceTexture` (`Texture`) or `+SourceConstantValue`
                      (`ConstantValue`); files with old stamps use `CompositeTextureSourceModel` with
                      `SourceSelection: "Texture"` or `"Constant"` and `ConstantValue`.
  <Preset>.texpreset  a packing preset (`TexturePackingPreset`, under GameData/Engine/Assets/Materials/Presets):
                      `SourceTextures` gives each input's role as `FileNameSuffixHint` (albedo, metal, alphamask,
                      normal, roughness, ao, emissive, colormask, ...) with its `DefaultValue`; `OutputChannels` packs
                      them (ColorMetal = albedo RGB x alphamask + metal; NormalRoughness = normal RGB + roughness;
                      Extensions = ao, emissive, colormask, alphamask). No preset flips or inverts a channel.
  <file>.meta         beside every asset: `$Value.AssetID` is the GUID other files use for it.

`maps(name)` follows that chain from a definition to `{role: Source}`, where a Source is a PNG path or a constant.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Source", "Material", "Index", "parse_guid", "parse_def", "parse_comptex", "parse_preset", "index"]

ROLES = ("albedo", "metal", "alphamask", "normal", "roughness", "ao", "emissive", "colormask")
_ASSET_ID = re.compile(rb'"AssetID"\s*:\s*"([0-9a-fA-F-]{36})"')


@dataclass(frozen=True)
class Source:
    """One input of a packed texture: a source image file, or a constant value (0..1)."""
    path: str | None = None
    value: float | None = None


@dataclass
class Material:
    name: str
    kind: str                         # the definition's type, e.g. PBRMaterialDefinitionObjectBuilder
    maps: dict[str, Source]           # role -> source
    emissivity: float = 0.0           # EmissivityMultiplier

    def path(self, role: str) -> str | None:
        s = self.maps.get(role)
        return s.path if s else None

    def value(self, role: str, default: float) -> float:
        s = self.maps.get(role)
        return s.value if s is not None and s.value is not None else default


def _load(path) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def parse_guid(ref) -> str | None:
    """`{G}<guid>` (or a bare GUID) -> the lower-case GUID; None for null or anything else."""
    if not isinstance(ref, str):
        return None
    g = ref[3:] if ref.startswith("{G}") else ref
    return g.lower() if len(g) == 36 and g.count("-") == 4 else None


def parse_def(path) -> tuple[str, dict[str, str], float]:
    """(type name, {texture slot: GUID}, EmissivityMultiplier) of a material .def."""
    d = _load(path)
    kind = str(d.get("$Type", "")).rsplit(".", 1)[-1]
    v = d.get("$Value") or {}
    tex = {k: g for k, ref in v.items() if k.endswith("Texture") and (g := parse_guid(ref))}
    return kind, tex, float(v.get("EmissivityMultiplier") or 0.0)


def parse_comptex(path) -> tuple[str | None, list[tuple[str | None, float | None]]]:
    """(preset GUID, [(source GUID or None, constant or None)] in the preset's input order) of a .comptex."""
    v = _load(path).get("$Value") or {}
    out = []
    for s in (v.get("PresetModel") or {}).get("TextureSourceModels") or []:
        if s.get("SourceSelection") == "Constant" or str(s.get("$Type", "")).endswith("+SourceConstantValue"):
            out.append((None, float(s.get("ConstantValue") or 0.0)))
        else:
            out.append((parse_guid(s.get("Texture")), None))
    return parse_guid(v.get("PresetReference")), out


def parse_preset(path) -> list[tuple[str, float]]:
    """[(role, default value)] of a .texpreset's inputs, in order (role = its FileNameSuffixHint, lower case)."""
    v = _load(path).get("$Value") or {}
    return [(str(s.get("FileNameSuffixHint", "")).lower(), float(s.get("DefaultValue") or 0.0))
            for s in v.get("SourceTextures") or []]


def _asset_id(meta: str) -> str | None:
    try:
        with open(meta, "rb") as f:
            m = _ASSET_ID.search(f.read(4096))
    except OSError:
        return None
    return m[1].decode().lower() if m else None


class Index:
    """GUID -> file over the SDK folders that hold block materials and their presets (scanned once)."""

    def __init__(self, roots):
        self.roots = [Path(r) for r in roots if r and Path(r).is_dir()]
        self.files: dict[str, str] = {}
        self.defs: dict[str, str] = {}
        for root in self.roots:
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    if n.endswith(".meta"):
                        g = _asset_id(os.path.join(dirpath, n))
                        if g:
                            self.files[g] = os.path.join(dirpath, n[:-5])
                    elif n.endswith(".def"):
                        self.defs.setdefault(n[:-4], os.path.join(dirpath, n))
        self._presets: dict[str, list] = {}

    def preset(self, guid) -> list[tuple[str, float]]:
        if guid not in self._presets:
            p = self.files.get(guid)
            self._presets[guid] = parse_preset(p) if p and os.path.isfile(p) else []
        return self._presets[guid]

    def material(self, name: str) -> Material | None:
        """The definition `<name>.def` resolved to its source maps; None when there is no such definition."""
        p = self.defs.get(name)
        if not p:
            return None
        try:
            kind, tex, emissivity = parse_def(p)
        except (OSError, ValueError):
            return None
        maps: dict[str, Source] = {}
        for g in tex.values():
            ct = self.files.get(g)
            if not ct or not ct.endswith(".comptex") or not os.path.isfile(ct):
                continue
            try:
                preset_guid, sources = parse_comptex(ct)
            except (OSError, ValueError):
                continue
            for (role, default), (src, const) in zip(self.preset(preset_guid), sources):
                if role not in ROLES or role in maps:   # albedo fills three inputs (R, G, B): keep the first
                    continue
                f = self.files.get(src) if src else None
                if f and os.path.isfile(f):
                    maps[role] = Source(path=f)
                else:
                    maps[role] = Source(value=const if const is not None else default)
        return Material(name, kind, maps, emissivity)


_INDEX: dict[tuple, Index] = {}


def index(*roots) -> Index:
    """The (cached) index over these folders."""
    key = tuple(str(r) for r in roots)
    if key not in _INDEX:
        _INDEX[key] = Index(roots)
    return _INDEX[key]
