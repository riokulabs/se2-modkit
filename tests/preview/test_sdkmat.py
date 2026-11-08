"""The SDK material chain (.def -> .comptex -> .texpreset -> source PNG) on a tiny synthetic SDK tree, and the preview
keys it gives Keen slots. No Blender, no game files: every file here is written by the test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from se2modkit.preview import mats, sdkmat

_N = iter(range(1, 10_000))


def _guid() -> str:
    return f"00000000-0000-4000-8000-{next(_N):012d}"


def _write(path: Path, obj, guid: str | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    guid = guid or _guid()
    meta = {"$Type": "VRage:Keen.VRage.ContentPipeline.Metafiles.MetaData", "$Value": {"AssetID": guid}}
    path.with_name(path.name + ".meta").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return guid


def _preset(path, roles):
    inputs = [{"FileNameSuffixHint": r, "DefaultValue": d} for r, d in roles]
    return _write(path, {"$Type": "VRage:Keen.VRage.ContentPipeline.Textures.Preset.TexturePackingPreset",
                         "$Value": {"SourceTextures": inputs}})


def _comptex(path, preset, sources):
    models = []
    for s in sources:
        if isinstance(s, float):
            models.append({"Texture": None, "ConstantValue": s, "SourceSelection": "Constant"})
        else:
            models.append({"Texture": f"{{G}}{s}" if s else None, "ConstantValue": 0, "SourceSelection": "Texture"})
    value = {"PresetReference": f"{{G}}{preset}", "PresetModel": {"TextureSourceModels": models}}
    return _write(path, {"$Type": "VRage:Keen.VRage.ContentPipeline.Textures.Composite.CompositeTextureModel",
                         "$Value": value})


def _def(path, kind, textures, emissivity=1.0):
    v = {"Guid": _guid(), **{k: f"{{G}}{g}" for k, g in textures.items()}, "EmissivityMultiplier": emissivity}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"$Type": f"VRage:Keen.VRage.Core.Render.Materials.Templates.{kind}",
                                "$Value": v}), encoding="utf-8-sig")


@pytest.fixture
def sdk(tmp_path):
    """<tmp>/GameData/{Engine/Assets/Materials/Presets, Vanilla/Assets/BlockMaterials} with a handful of materials."""
    gd = tmp_path / "GameData"
    pre = gd / "Engine" / "Assets" / "Materials" / "Presets"
    cm = _preset(pre / "CM.texpreset", [("albedo", 0), ("albedo", 0), ("albedo", 0), ("metal", 0), ("alphamask", 1)])
    nr = _preset(pre / "NR.texpreset", [("normal", .5), ("normal", .5), ("normal", 1), ("roughness", 1)])
    ext = _preset(pre / "EXT.texpreset", [("ao", 1), ("emissive", 0), ("colormask", 0), ("alphamask", 1)])
    bm = gd / "Vanilla" / "Assets" / "BlockMaterials"

    def png(rel):
        return _write(bm / rel, "png bytes")

    def material(name, kind="PBRMaterialDefinitionObjectBuilder", cm_src=(), nr_src=(), ext_src=(), emissivity=1.0):
        d = bm / name
        tex = {"ColorMetalTexture": _comptex(d / f"{name}_cm.comptex", cm, cm_src),
               "NormalRoughnessTexture": _comptex(d / f"{name}_nr.comptex", nr, nr_src),
               "ExtensionsTexture": _comptex(d / f"{name}_add.comptex", ext, ext_src)}
        _def(bm / f"{name}.def", kind, tex, emissivity)

    alb = png("PaintedMetalColorable/PaintedMetalColorable_albedo.png")
    metal = png("PaintedMetalColorable/PMC_metal.png")
    normal, rough = png("PaintedMetalColorable/PMC_normal.png"), png("PaintedMetalColorable/PMC_roughness.png")
    ao, mask = png("PaintedMetalColorable/PMC_ao.png"), png("PaintedMetalColorable/PMC_colormask.png")
    material("PaintedMetalColorable", cm_src=(alb, alb, alb, metal, None), nr_src=(normal, normal, normal, rough),
             ext_src=(ao, None, mask, None))
    dark = png("PaintedMetalDarker/PaintedMetalDarker_albedo.png")
    material("PaintedMetalDarker", cm_src=(dark, dark, dark, metal), nr_src=(normal, normal, normal, rough),
             ext_src=(ao, None, 0.0, None))   # the shared Colorable metal and normal; a constant color mask
    green, glow = png("EmissiveGreen/EmissiveGreen_albedo.png"), png("EmissiveGreen/EmissiveGreen_emissive.png")
    material("EmissiveGreen", cm_src=(green, green, green), ext_src=(None, glow), emissivity=80)
    term = png("Terminal/Terminal_albedo.png")
    material("Terminal", cm_src=(term, term, term), emissivity=80)   # a screen: no emissive mask
    glass = png("FrostedGlass/FrostedGlass_albedo.png")
    material("FrostedGlass", kind="PBRTransparentMaterialDefinitionObjectBuilder", cm_src=(glass, glass, glass))
    material("Rubber", cm_src=(_guid(),) * 3)   # its albedo GUID resolves to no file
    return bm


@pytest.fixture
def sdk_on(sdk, monkeypatch):
    monkeypatch.setitem(mats._PATHS, "sdk_mats", str(sdk))
    monkeypatch.setitem(mats._PATHS, "discovered", True)
    monkeypatch.setitem(mats._PATHS, "sdk_materials", True)
    return sdk


def _index(bm):
    return sdkmat.index(bm, mats._presets_dir(bm))


def test_parse_guid():
    g = "B8C12AD6-a10b-4fdb-bbc0-8f2d655380f0"
    assert sdkmat.parse_guid("{G}" + g) == g.lower() and sdkmat.parse_guid(g) == g.lower()
    assert sdkmat.parse_guid(None) is None and sdkmat.parse_guid("{G}nope") is None and sdkmat.parse_guid(3) is None


def test_comptex_constant_and_null_sources(tmp_path):
    p = _comptex(tmp_path / "x.comptex", "11111111-1111-4111-8111-111111111111", ["", 0.25])
    preset, sources = sdkmat.parse_comptex(tmp_path / "x.comptex")
    assert p and preset == "11111111-1111-4111-8111-111111111111" and sources == [(None, None), (None, 0.25)]


def test_the_chain_resolves_every_role(sdk):
    m = _index(sdk).material("PaintedMetalColorable")
    assert m.kind == "PBRMaterialDefinitionObjectBuilder" and m.emissivity == 1.0
    names = {r: Path(s.path).name for r, s in m.maps.items() if s.path}
    assert names == {"albedo": "PaintedMetalColorable_albedo.png", "metal": "PMC_metal.png",
                     "normal": "PMC_normal.png", "roughness": "PMC_roughness.png", "ao": "PMC_ao.png",
                     "colormask": "PMC_colormask.png"}
    assert m.value("alphamask", 0) == 1 and m.value("emissive", 9) == 0   # null sources take the preset default


def test_shared_maps_constants_and_missing_files(sdk):
    ix = _index(sdk)
    dark = ix.material("PaintedMetalDarker")
    assert Path(dark.path("metal")).parent.name == "PaintedMetalColorable"
    assert dark.path("colormask") is None and dark.value("colormask", 1) == 0.0
    assert dark.path("roughness") and dark.path("normal")
    rubber = ix.material("Rubber")
    assert rubber.path("albedo") is None and rubber.value("albedo", 9) == 0   # an unknown GUID: the default
    assert ix.material("NoSuchMaterial") is None


def test_slot_keys_pick_source_maps_when_they_exist(sdk_on):
    assert mats.slot_key("PaintedMetalColorable.001", "blue") == "SDK:PaintedMetalColorable:blue"
    assert mats.slot_key("PaintedMetalColorable_Subpart") == "SDK:PaintedMetalColorable:keen"
    assert mats.slot_key("Reactor_LastLOD") == "SDK:PaintedMetalColorable:keen"
    assert mats.slot_key("PaintedMetalDarker") == "SDK:PaintedMetalDarker"   # no color mask: no paint in its key
    assert mats.slot_key("EmissiveGreen") == "SDK:EmissiveGreen"             # a lit stand-in with an emissive mask


def test_slot_keys_keep_the_stand_in_otherwise(sdk_on, monkeypatch):
    assert mats.slot_key("Terminal") == "Terminal"            # glowing stand-in, no emissive mask to light
    assert mats.slot_key("FrostedGlass") == "Frosted"         # glass keeps its own material
    assert mats.slot_key("Rubber") == "Rubber"                # no albedo map
    assert mats.slot_key("BrushedMetal") == "Brushed"         # no definition
    assert mats.slot_key("pv_Darker") == "Darker"             # an explicit stand-in
    assert mats.slot_key("AtlasDecal_Parts01") == "AtlasDecal_Parts01:keen"
    monkeypatch.setitem(mats._PATHS, "sdk_materials", False)
    assert mats.slot_key("PaintedMetalColorable", "blue") == "PMC:blue"
    assert mats.slot_key("PaintedMetalDarker") == "Darker"


def test_no_sdk_means_stand_ins(monkeypatch, tmp_path):
    monkeypatch.setitem(mats._PATHS, "sdk_mats", str(tmp_path / "missing"))
    monkeypatch.setitem(mats._PATHS, "discovered", True)
    assert mats.sdk_index() is None
    assert mats.slot_key("PaintedMetalColorable.002", "red") == "PMC:red"


def test_configure_switches_source_maps(monkeypatch):
    monkeypatch.setitem(mats._PATHS, "sdk_materials", True)
    mats.configure(sdk_materials=False)
    assert mats._PATHS["sdk_materials"] is False
    mats.configure(game_mats="/x")   # other overrides leave it alone
    assert mats._PATHS["sdk_materials"] is False


def test_presets_dir_is_beside_the_vanilla_assets():
    p = Path(mats._presets_dir("/sdk/GameData/Vanilla/Assets/BlockMaterials"))
    assert p.parts[-5:] == ("GameData", "Engine", "Assets", "Materials", "Presets")


def test_parse_comptex_reads_the_current_source_types(tmp_path):
    p = tmp_path / "x_add.comptex"
    pre = "VRage:Keen.VRage.ContentPipeline.Textures.Composite.CompositeTexturePresetModel+"
    p.write_text(json.dumps({"$Value": {"PresetReference": "{G}aa524acf-9017-43ff-a5d8-48fcbc48cade",
                                        "PresetModel": {"TextureSourceModels": [
                                            {"$Type": pre + "SourceTexture", "Texture": "{G}dcbb1667-3e2f-460f-8ad1-d27509c50908"},
                                            {"$Type": pre + "SourceTexture", "Texture": None},
                                            {"$Type": pre + "SourceConstantValue", "ConstantValue": 1}]}}}),
                 encoding="utf-8")
    preset, sources = sdkmat.parse_comptex(p)
    assert preset == "aa524acf-9017-43ff-a5d8-48fcbc48cade"
    assert sources == [("dcbb1667-3e2f-460f-8ad1-d27509c50908", None), (None, None), (None, 1.0)]
