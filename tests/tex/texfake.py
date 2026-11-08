"""Writers for the texture-set tests' synthetic Mod SDK files (.meta, .texpreset, .comptex). No game or SDK file is
read."""

from __future__ import annotations

import json
from pathlib import Path

_N = iter(range(1, 100_000))
BUNDLES = {"System.Runtime": "1.0.0.0", "VRage": "9.9.9.9"}


def guid() -> str:
    return f"00000000-0000-4000-8000-{next(_N):012d}"


def write(path: Path, obj, gid: str | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, bytes):
        path.write_bytes(obj)
    else:
        path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    gid = gid or guid()
    meta = {"$Type": "VRage:Keen.VRage.ContentPipeline.Metafiles.MetaData", "$Value": {"AssetID": gid}}
    path.with_name(path.name + ".meta").write_text(json.dumps(meta), encoding="utf-8")
    return gid


def preset(path, roles):
    return write(path, {"$Type": "VRage:Keen.VRage.ContentPipeline.Textures.Preset.TexturePackingPreset",
                        "$Value": {"SourceTextures": [{"FileNameSuffixHint": r, "DefaultValue": d} for r, d in roles]}})


def comptex(path, preset_guid, sources):
    models = []
    for s in sources:
        if isinstance(s, float):
            models.append({"Texture": None, "ConstantValue": s, "SourceSelection": "Constant"})
        else:
            models.append({"Texture": f"{{G}}{s}" if s else None, "ConstantValue": 0, "SourceSelection": "Texture"})
    return write(path, {"$Bundles": BUNDLES,
                        "$Type": "VRage:Keen.VRage.ContentPipeline.Textures.Composite.CompositeTextureModel",
                        "$Value": {"PresetReference": f"{{G}}{preset_guid}",
                                   "PresetModel": {"TextureSourceModels": models}}})
