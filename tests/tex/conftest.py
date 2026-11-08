"""Fixtures for the texture-set tests: a tiny synthetic Mod SDK material tree and a mod folder. Every file is written
here; no game or SDK file is read."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # texfake

from texfake import comptex, guid, preset, write  # noqa: E402

from se2modkit.preview import sdkmat  # noqa: E402


@pytest.fixture
def sdk(tmp_path):
    """{"bm": BlockMaterials folder, "presets": {name: GUID}, "vanilla": GameData/Vanilla, "albedo": a vanilla PNG}."""
    gd = tmp_path / "sdk" / "GameData"
    pre = gd / "Engine" / "Assets" / "Materials" / "Presets"
    cm = preset(pre / "CM.texpreset", [("albedo", 0), ("albedo", 0), ("albedo", 0), ("metal", 0), ("alphamask", 1)])
    nr = preset(pre / "NR.texpreset", [("normal", .5), ("normal", .5), ("normal", 1), ("roughness", 1)])
    ext = preset(pre / "EXT.texpreset", [("ao", 1), ("emissive", 0), ("colormask", 0), ("alphamask", 1)])
    bm = gd / "Vanilla" / "Assets" / "BlockMaterials"
    d = bm / "Painted"
    vanilla_png = d / "Painted_albedo.png"
    alb = write(vanilla_png, b"\x89PNG vanilla albedo pixels " * 40)
    metal, normal, rough = (write(d / f"Painted_{r}.png", f"vanilla {r}" * 40) for r in ("metal", "normal", "rough"))
    ao, mask = write(d / "Painted_ao.png", "ao" * 300), write(d / "Painted_colormask.png", "cm" * 300)
    tex = {"ColorMetalTexture": comptex(d / "Painted_cm.comptex", cm, (alb, alb, alb, metal, None)),
           "NormalRoughnessTexture": comptex(d / "Painted_nr.comptex", nr, (normal, normal, normal, rough)),
           "ExtensionsTexture": comptex(d / "Painted_add.comptex", ext, (ao, None, mask, 1.0))}
    (bm / "Painted.def").write_text(json.dumps({
        "$Type": "VRage:Keen.VRage.Core.Render.Materials.Templates.PBRMaterialDefinitionObjectBuilder",
        "$Value": {"Guid": guid(), **{k: f"{{G}}{g}" for k, g in tex.items()}, "EmissivityMultiplier": 1}}),
        encoding="utf-8-sig")
    return {"bm": bm, "presets": {"cm": cm, "nr": nr, "ext": ext}, "vanilla": gd / "Vanilla",
            "albedo": vanilla_png, "pre": pre}


@pytest.fixture
def ix(sdk):
    return sdkmat.Index([sdk["bm"], sdk["pre"]])


@pytest.fixture
def mod(tmp_path):
    d = tmp_path / "mods" / "MyMod"
    (d / "Assets").mkdir(parents=True)
    (d / "MyMod.vrgproj").write_text(json.dumps({"$Value": {}}), encoding="utf-8")
    return d
