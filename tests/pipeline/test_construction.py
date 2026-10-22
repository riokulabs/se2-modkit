"""`pipeline.construction`: the BlockModelDefinition.BuildProgressModels generator (the stage-1.0 ApplyOnlyWhenDamaged
hide names exactly the block's Scaffold-type mesh parts; no scaffold -> an empty last stage, as vanilla writes it) and
the construction roles per face (`pipeline.fracture.role_of`, read from `hooks.roles`)."""

import json
import os
from unittest import mock

import pytest

from se2modkit.pipeline import construction as C
from se2modkit.pipeline import fracture as FR

PAINT = "Fracture_01-PaintedMetalColorable"
DEFAULT = (PAINT, ("Default", "AlwaysVisible", "Default"))
WINDOWGLASS = "83c075f5-f177-460d-94a7-8aca19fd4d6f"
SUBPART = "851d4d6e-9ec8-467f-87a5-06ef06e97336"
RATIOS = [0, 0.1, 0.2, 0.3, 0.5333, 0.7667, 1]


def _gen(parts, **kw):
    return C.generate_stages(dict(parts), "{G}m", "{G}f", "s", **kw)


def _changes(stages):
    """{(ratio, part): change value} over every stage."""
    return {(s["Value"]["BuildRatioLowerBound"], c["Value"]["MeshPart"]): c["Value"]
            for s in stages for c in s["Value"]["PartChanges"]["_data"]}


def test_no_scaffold_no_damaged_hide():
    parts = dict([DEFAULT])
    assert C.scaffold_parts(parts) == []
    assert _gen(parts)[-1]["Value"]["PartChanges"]["_data"] == []
    assert _gen(parts, damaged_hide=C.scaffold_parts(parts))[-1]["Value"]["PartChanges"]["_data"] == []


def test_damaged_hide_names_scaffold():
    parts = dict([DEFAULT, ("BrushedMetal", ("Scaffold", "AlwaysVisible", "Scaffolding"))])
    assert C.scaffold_parts(parts) == ["BrushedMetal"]
    last = _gen(parts, damaged_hide=C.scaffold_parts(parts))[-1]["Value"]
    assert last["BuildRatioLowerBound"] == 1 and last["Model"] == "{G}m"
    got = [{k: v for k, v in c["Value"].items() if k in ("MeshPart", "ShowPart", "ApplyOnlyWhenDamaged")}
           for c in last["PartChanges"]["_data"]]
    assert got == [{"MeshPart": "BrushedMetal", "ShowPart": False, "ApplyOnlyWhenDamaged": True}]
    c = last["PartChanges"]["_data"][0]["Value"]
    assert c["SetMaterial"] is None and c["ResetToSkinMaterial"] is False


def test_scaffold_parts_sorted_and_typed():
    parts = {"Z": ("Scaffold", "AlwaysVisible", "Scaffolding"), "A": ("Scaffold", "AlwaysVisible", "Scaffolding"),
             "M": ("Default", "AlwaysVisible", "Default"), "S": ("Split", "HiddenUntilSecondSequenceStart", "Fracture")}
    assert C.scaffold_parts(parts) == ["A", "Z"]


@pytest.mark.parametrize("preset,mat", [("WindowGlass", WINDOWGLASS), ("SubpartConstruct", SUBPART)])
def test_new_presets_timing(preset, mat):
    parts = dict([DEFAULT, ("Fracture_01-X", ("Default", "AlwaysVisible", preset))])
    st = _gen(parts)
    ch = _changes(st)
    assert ch[(0, "Fracture_01-X")]["SetMaterial"] == mat
    assert ch[(0.3, "Fracture_01-X")]["SetMaterial"] == mat
    assert [s["Value"]["BuildRatioLowerBound"] for s in st] == RATIOS
    assert [s["Value"]["BuildRatioLowerBound"] for s in _gen(dict([DEFAULT]))] == RATIOS


def test_stage_template_unchanged():
    """The stage ratios of a plain Default part: (0, .1, .2, .3, .533, .767, 1), floats except the last (vanilla's)."""
    st = _gen(dict([DEFAULT]))
    assert [s["Value"]["BuildRatioLowerBound"] for s in st] == RATIOS
    assert st[0]["Value"]["Model"] == "{G}f" and all(s["Value"]["Model"] is None for s in st[1:-1])
    assert all(isinstance(s["Value"]["BuildRatioLowerBound"], float) for s in st[:-1])


def test_stages_are_deterministic_per_seed():
    a, b = _gen(dict([DEFAULT])), _gen(dict([DEFAULT]))
    assert a == b
    assert a != C.generate_stages(dict([DEFAULT]), "{G}m", "{G}f", "other")


def test_presets_match_sdk_names():
    """The preset table: the five classic presets plus the two 4.0 presets (WindowGlass, SubpartConstruct)."""
    assert set(C.PRESETS) == {"default", "fracture", "support", "scaffolding", "conveyor_port",
                              "windowglass", "subpartconstruct"}
    assert C.PRESETS["windowglass"] == {"T": [WINDOWGLASS], "S": [WINDOWGLASS]}
    assert C.PRESETS["subpartconstruct"] == {"T": [SUBPART], "S": [SUBPART]}


def test_unknown_preset_raises():
    """A mistyped preset ("WindowGlas") raises instead of silently building with no materials; no preset (None / "")
    stays the plain no-material form."""
    with pytest.raises(KeyError, match="WindowGlas"):
        _gen(dict([DEFAULT, ("Fracture_01-X", ("Default", "AlwaysVisible", "WindowGlas"))]))
    for none in (None, ""):
        st = _gen(dict([DEFAULT, ("Fracture_01-X", ("Default", "AlwaysVisible", none))]))
        assert (0, "Fracture_01-X") not in _changes(st)


def _vanilla_last_stage_lists(root):
    out = []

    def walk(o, f):
        if isinstance(o, dict):
            bpm = o.get("BuildProgressModels")
            if isinstance(bpm, list) and bpm:
                out.append((f, bpm[-1]["Value"]["PartChanges"]["_data"]))
            for v in o.values():
                walk(v, f)
        elif isinstance(o, list):
            for v in o:
                walk(v, f)

    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith((".def", ".partialdef")):
                with open(os.path.join(dp, f), encoding="utf-8-sig") as fh:
                    try:
                        walk(json.load(fh), f)
                    except ValueError:
                        continue
    return out


@pytest.mark.sdk
def test_empty_last_stage_form_is_vanilla():
    """An empty last-stage PartChanges is a form vanilla 4.0 itself ships (so a block without a scaffold writes [],
    not a hide naming a part it lacks)."""
    from se2modkit.model import config
    ref = config.VANILLA_REF
    root = os.path.join(ref, "Blocks") if ref else None
    if not root or not os.path.isdir(root):
        pytest.skip("no vanilla reference (set `vanilla_ref` in the user config)")
    lists = _vanilla_last_stage_lists(root)
    assert lists, "no vanilla BuildProgressModels found under the vanilla reference's Blocks/"
    assert [f for f, d in lists if d == []], "no vanilla 4.0 BuildProgressModels ends with an empty PartChanges"


# ------------------------------------------------------------------------------------------------ construction roles
def test_role_of_rules(hooks):
    assert FR.role_of(hooks, "box", "cable", "AtlasDecal_Stickers02") == "hide"
    assert FR.role_of(hooks, "box", "glass", "FrostedGlass") == "glass"
    assert FR.role_of(hooks, "box", "perf", "MetalPerforationSteel") == "skin"
    hooks.roles["box"] = {"support": ("rail",)}
    assert FR.role_of(hooks, "box", "rail", "PaintedMetalDarker") == "support"
    assert FR.role_of(hooks, "box", "rails", "PaintedMetalDarker") == "skin"     # exact name, as keep_whole
    assert FR.role_of(hooks, "box", "rail", "EmissiveGreen") == "hide"           # the material rules come first
    assert FR.role_of(hooks, "lid", "rail", "PaintedMetalDarker") == "skin"      # another family's table


def test_hide_is_one_predicate(hooks):
    """One hide test: role_of's hide is fracture.is_hide, and the exporter imports the same table."""
    with mock.patch.object(FR, "is_hide", return_value=True) as h:
        assert FR.role_of(hooks, "box", "perf", "PaintedMetalDarker") == "hide"
        h.assert_called_once_with("PaintedMetalDarker")
    for m in FR.HIDE_PREFIXES:
        assert FR.is_hide(m + "_X") and FR.role_of(hooks, "box", "x", m + "_X") == "hide"


def test_role_props_match_guide():
    """(ConstructionMeshType, ConstructionMeshVisibility, ConstructionMeshPreset) per role, each preset a preset of the
    generator."""
    assert FR.ROLE_IDS == ("skin", "hide", "support", "frame", "glass")
    assert FR.ROLE_SUFFIX == {"skin": "", "hide": "_Hide", "support": "_Support", "frame": "_Frame", "glass": ""}
    assert FR.ROLE_PROPS == {"skin": ("Default", "AlwaysVisible", "Default"),
                             "hide": ("Default", "AlwaysHidden", "Default"),
                             "support": ("Default", "AlwaysVisible", "Support"),
                             "frame": ("Split", "HiddenUntilSecondSequenceStart", "Default"),
                             "glass": ("Default", "AlwaysVisible", "WindowGlass")}
    assert all(p.lower() in C.PRESETS for _t, _v, p in FR.ROLE_PROPS.values())
    assert set(FR.GLASS_MATERIALS) == {"FrostedGlass", "WindowGlass", "WindowGlassThin", "ClearGlass"}
    assert FR.role_props("support") == {"ConstructionMeshType": "Default", "ConstructionMeshVisibility": "AlwaysVisible",
                                        "ConstructionMeshPreset": "Support"}


def test_skin_carrier():
    """A zone keeps a skin. LOD0 without a skin fails with a message naming the zone and the roles it has; a lower LOD
    promotes the first present role in ROLE_IDS order."""
    assert FR.skin_carrier({"skin", "glass"}, 0, "B", "Fracture_01") == "skin"
    assert FR.skin_carrier({"glass", "hide", "frame"}, 1, "B", "Fracture_01") == "hide"
    assert FR.skin_carrier({"glass", "frame"}, 2, "B", "Fracture_01") == "frame"
    assert FR.skin_carrier(set(), 1, "B", "Fracture_01") is None
    with pytest.raises(RuntimeError, match=r"B: fracture zone Fracture_03 has no LOD0 skin face .*glass, support"):
        FR.skin_carrier({"support", "glass"}, 0, "B", "Fracture_03")
    with pytest.raises(RuntimeError, match="no LOD0 geometry"):
        FR.skin_carrier(set(), 0, "B", "Fracture_03")


def test_role_tables_cannot_name_skin_or_glass(hooks):
    """A part table names only the suffixed roles (a glass or skin entry would give two meshes one mesh-part name);
    plan() checks the hooks' tables."""
    FR.check_role_tables(hooks.roles)
    for bad in ("glass", "skin"):
        with pytest.raises(ValueError, match="may name only"):
            FR.check_role_tables({"box": {bad: ("rail",)}})
    assert FR.TABLE_ROLES == ("hide", "support", "frame")
