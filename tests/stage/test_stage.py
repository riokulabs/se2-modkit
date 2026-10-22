"""`stage`: the staging helpers that write a mod's integration package (definitions, .meta files, .model assets)."""

import json
import math

import pytest

from hookfix import GAME, STAMPS, VRAGE, Spec, make_hooks, read, write
from se2modkit import stage
from se2modkit.defs import assetio
from se2modkit.defs import material_guids as mg
from se2modkit.defs.guids import Guids
from se2modkit.env.errors import EnvError

BDEF = "50000000-0000-4000-8000-000000000001"
SCOMP = "50000000-0000-4000-8000-000000000002"
DIR = "50000000-0000-4000-8000-000000000003"
OLD = {"System.Runtime": "1.0.0.0", "VRage": "1.2.3.4"}
DIFF = assetio.PARTIAL
SPEC = Spec(name="XX_Box")


def _install(h, rel, value, bundles=OLD, meta=True):
    """A definition as the clone step left it in the mod's Assets/ (with its .meta)."""
    p = write(h.paths.assets / rel, {"$Bundles": bundles, "$Type": DIFF, "$Value": value})
    if meta:
        assetio.write_meta(p, assetio.own_guid(read(p)), (OLD, OLD))
    return p


def _copy(guid, **manip):
    return {"BaseDefinition": "60000000-0000-4000-8000-000000000001", "PartialDefinitionKind": "Copy",
            "Manipulator": {"Guid": guid, **manip}, "DefinitionType": "Game2:T", "PriorityOverride": False}


@pytest.fixture
def h(tmp_path):
    hooks = make_hooks(tmp_path / "mod")
    stage.reset(hooks)
    return hooks


@pytest.fixture
def G(h):
    return Guids(h.paths.asset_guids)


def test_stage_def_uses_given_stamps(h):
    rel = "XX_Mod/XX_Box/XX_Box_Block.partialdef"
    _install(h, rel, _copy(BDEF))
    d = stage.read_mod(h, rel)
    d["$Value"]["Manipulator"]["Use"] = {"$Type": "Game2:Keen.Game2.X", "Inner": [{"Type": "VRage:Keen.VRage.Y"}]}
    stage.stage_def(h, rel, d)
    out = read(h.paths.staged / rel)
    # the bundles the file already declares keep their version; new ones come from the hooks' game stamps
    assert out["$Bundles"] == {"Game2": GAME["Game2"], "System.Runtime": "1.0.0.0", "VRage": "1.2.3.4"}
    assert list(out["$Bundles"]) == sorted(out["$Bundles"])
    assert not (h.paths.staged / (rel + ".meta")).exists()   # the installed .meta already has this AssetID
    new = "XX_Mod/XX_Box/XX_Box_New.partialdef"
    stage.stage_def(h, new, assetio.envelope(DIFF, _copy(DIR), {}))
    meta = read(h.paths.staged / (new + ".meta"))
    assert meta["$Bundles"] == VRAGE and meta["$Value"]["AssetID"] == DIR
    assert stage.asset_id(h, new) == DIR and stage.asset_id(h, rel) == BDEF


def test_stage_stamps_from_env_once(tmp_path, monkeypatch):
    from se2modkit.pipeline import hooks as hooks_mod
    h = make_hooks(tmp_path / "mod", bundle_stamps=None)
    with pytest.raises(EnvError, match="bundle stamps"):
        stage.reset(h)
    monkeypatch.setattr(hooks_mod, "bundle_stamps", lambda env: STAMPS)
    stage.reset(h, env=object())
    assert stage.bundle_versions(h) == {"Game2": GAME["Game2"], "VRage": GAME["VRage"]}


def test_read_mod_after_stage_def_raises(h):
    rel = "XX_Mod/XX_Box/XX_Box_Block.partialdef"
    _install(h, rel, _copy(BDEF))
    stage.stage_def(h, rel, stage.read_mod(h, rel))
    with pytest.raises(RuntimeError, match="read_current"):
        stage.read_mod(h, rel)
    assert stage.read_current(h, rel)["$Bundles"] == OLD
    other = make_hooks(h.paths.root)          # another registry: its own run
    assert stage.read_mod(other, rel)["$Value"]["Manipulator"]["Guid"] == BDEF
    stage.reset(h)
    assert not h.paths.staged.joinpath(rel).exists() and stage.read_mod(h, rel)
    assert (h.paths.staged / "XX_Mod").is_dir()


def test_stage_def_guid_mismatch(h):
    rel = "XX_Mod/XX_Box/XX_Box_Block.partialdef"
    p = _install(h, rel, _copy(BDEF))
    assetio.write_meta(p, DIR, STAMPS)
    with pytest.raises(RuntimeError, match="AssetID"):
        stage.stage_def(h, rel, stage.read_mod(h, rel))
    with pytest.raises(RuntimeError, match="installed .meta AssetID"):
        stage.stage_meta(h, rel, BDEF)


def test_stage_folder_metas(h, G):
    _install(h, "XX_Mod/XX_Box/XX_Box_Block.partialdef", _copy(BDEF))
    assetio.write_meta(h.paths.assets / "XX_Mod" / "XX_Box", DIR, STAMPS)   # an installed folder keeps its AssetID
    stage.stage_def(h, "XX_Mod/XX_Box/XX_Box_Block.partialdef",
                    stage.read_mod(h, "XX_Mod/XX_Box/XX_Box_Block.partialdef"))
    stage.stage_def(h, "XX_Mod/XX_Box/Models/NonFractured/x.partialdef", assetio.envelope(DIFF, _copy(SCOMP), {}))
    stage.write_folder_metas(h, G)
    st = h.paths.staged
    assert not (st / "XX_Mod" / "XX_Box.meta").exists()          # the installed one matches: not restaged
    for rel in ("XX_Mod", "XX_Mod/XX_Box/Models", "XX_Mod/XX_Box/Models/NonFractured"):
        meta = read(st / (rel + ".meta"))
        assert meta["$Value"]["AssetID"] == G["dir/" + rel] and meta["$Bundles"] == VRAGE, rel


def test_stage_file_and_models(h, G, tmp_path):
    src = h.paths.models / "XX_Box" / "NonFractured"
    for f in ("XX_Box_LOD0.glb", "XX_Box_LOD1.glb", "XX_Box_Collision.glb", "XX_Box_Collision.bin"):
        write(src / f, {"f": f})
    man = {"shift": [0.5, 0, -0.25], "nonfractured": {
        "lods": [{"file": "XX_Box_LOD0.glb", "materials": ["PaintedMetalColorable", "XX_Lamp"]},
                 {"file": "XX_Box_LOD1.glb", "materials": ["PaintedMetalColorable"]}],
        "collision": {"file": "XX_Box_Collision.glb", "bin": "XX_Box_Collision.bin"}}}
    h.material_slots["XX_Lamp"] = mg.MATERIAL_GUIDS["EmissiveOff"]
    out = stage.stage_models(h, SPEC, man, G)
    mrel = "XX_Mod/XX_Box/Models/NonFractured/XX_Box.model"
    assert out == {"NonFractured": G[mrel]}
    model = read(h.paths.staged / mrel)
    assert model["$Bundles"] == VRAGE and model["$Type"] == stage.MODEL_TYPE
    v = model["$Value"]
    lod0 = G["XX_Mod/XX_Box/Models/NonFractured/XX_Box_LOD0.glb"]
    assert v["GeometryAssets"][0]["AssetReference"] == "{G}" + lod0
    assert v["GeometryAssets"][0]["Transformations"]["Translation"] == {"X": 0.5, "Y": 0, "Z": -0.25}
    assert v["GeometryAssets"][1]["Transformations"]["Translation"] == {"X": 0, "Y": 0, "Z": 0}
    assert v["MaterialAssignments"] == [
        {"MaterialName": "PaintedMetalColorable", "MaterialReference": mg.MATERIAL_GUIDS["PaintedMetalColorable"][0]},
        {"MaterialName": "XX_Lamp", "MaterialReference": mg.MATERIAL_GUIDS["EmissiveOff"][0]}]
    col = G["XX_Mod/XX_Box/Models/NonFractured/XX_Box_Collision.glb"]
    assert v["CollisionShapesAsset"] == {"AssetReference": "{G}" + col, "GroupsByFracture": [],
                                         "GeneratePreciseCollider": True}
    assert read(h.paths.staged / "XX_Mod/XX_Box/Models/NonFractured/XX_Box_LOD0.glb") == {"f": "XX_Box_LOD0.glb"}
    assert assetio.meta_id(h.paths.staged / "XX_Mod/XX_Box/Models/NonFractured/XX_Box_LOD0.glb") == lod0


def test_model_asset_damaged_remap(h):
    m = stage.model_asset(h, [("g0", ["Chrome", "Rubber"])], "c", [0, 0, 0],
                          lambda s: mg.damaged_slot(h, s))
    refs = {a["MaterialName"]: a["MaterialReference"] for a in m["$Value"]["MaterialAssignments"]}
    assert refs == {"Chrome": mg.MATERIAL_GUIDS["ChromeDeformed"][0], "Rubber": mg.MATERIAL_GUIDS["Rubber"][0]}


def test_subpart_model_asset(h):
    v = stage.subpart_model_asset(h, [("g0", ["Chrome"])])["$Value"]
    assert v["CollisionShapesAsset"] == {"AssetReference": None, "GroupsByFracture": []}
    assert v["GeometryAssets"][0]["Transformations"]["Translation"] == {"X": 0, "Y": 0, "Z": 0}
    v = stage.subpart_model_asset(h, [("g0", ["Chrome"])], "col")["$Value"]
    assert v["CollisionShapesAsset"]["AssetReference"] == "{G}col"


def test_breakable_definition_new_and_patched(h, G):
    man = {"fractured": {"zones": [1, 2], "critical": ["Fracture_01"]}}
    assert stage.keeps_breakable(man) and not stage.keeps_breakable({"fractured": {"zones": [1]}})
    rel, guid = stage.breakable_definition(h, SPEC, man, G)
    assert rel == "XX_Mod/XX_Box/XX_Box_BreakableBlockDefinition.partialdef"
    d = read(h.paths.staged / rel)
    assert guid == G["def/XX_Box/BreakableBlockDefinition"] == d["$Value"]["Manipulator"]["Guid"]
    assert d["$Value"]["BaseDefinition"] == stage.BBD_BASE and d["$Value"]["DefinitionType"] == stage.T_BBD
    assert d["$Value"]["Manipulator"]["CriticalFractures"]["Added"] == [{"Id": "Fracture_01"}]
    assert d["$Bundles"] == GAME
    # a block whose clone has one already: only its CriticalFractures change
    h2 = make_hooks(h.paths.root.parent / "two")
    stage.reset(h2)
    _install(h2, "XX_Mod/XX_Box/XX_Box_BreakableBlockDefinition.partialdef", _copy(BDEF, CriticalFractures=None))
    rel2, g2 = stage.breakable_definition(h2, SPEC, man, Guids(h2.paths.asset_guids))
    assert g2 == BDEF and read(h2.paths.staged / rel2)["$Value"]["Manipulator"]["CriticalFractures"]["Added"]


def _composition(h, side, keys):
    _install(h, f"XX_Mod/XX_Box/XX_Box_{side}Composition.partialdef", _copy(
        SCOMP if side == "Server" else DIR, Components={"$DeltaEncoded": True, "Keys": list(keys), "Changed": [],
                                                        "Removed": []}))
    _install(h, f"XX_Mod/XX_Box/XX_Box_{side}.partialdef", _copy(
        BDEF if side == "Server" else "50000000-0000-4000-8000-0000000000aa",
        _entity={"ObjectBuilders": {"$DeltaEncoded": True, "Keys": [], "Changed": [], "Removed": []}}))


def test_add_and_drop_components(h):
    _composition(h, "Client", ["own"])
    new = [("k1", "d1", "Game2:A", "SlotA", None), ("k2", "d2", "VRage:B", ("SlotB", "SlotC"), {"x": 1})]
    cc, cl = stage.add_components(h, SPEC, "Client", new)
    stage.add_components(h, SPEC, "Client", new)                    # idempotent
    c = assetio.body(read(h.paths.staged / cc))
    assert c["Components"]["Keys"] == ["own", "k1", "k2"]
    assert [x["Index"] for x in c["Components"]["Changed"]] == [1, 2]
    assert stage.tag_slots(h, SPEC, "Client") == {"SlotA": "k1", "SlotB": "k2", "SlotC": "k2"}
    ob = assetio.body(read(h.paths.staged / cl))["_entity"]["ObjectBuilders"]
    assert ob["Keys"] == ["k1", "k2"] and ob["Changed"][1]["Value"] == {"x": 1}
    assert read(h.paths.staged / cc)["$Bundles"]["Game2"] == GAME["Game2"]
    stage.drop_components(h, SPEC, "Client", ["k1"])
    c = assetio.body(read(h.paths.staged / cc))
    assert c["Components"]["Keys"] == ["own", "k2"] and c["Components"]["Changed"][0]["Index"] == 1
    assert stage.tag_slots(h, SPEC, "Client") == {"SlotB": "k2", "SlotC": "k2"}
    with pytest.raises(RuntimeError, match="not a component this package inserted"):
        stage.drop_components(h, SPEC, "Client", ["own"])


def test_ensure_breakable_checks_both_compositions(h, G):
    for side in ("Server", "Client"):
        _composition(h, side, [stage.BREAKABLE_KEY])
    man = {"fractured": {"zones": [1, 2], "critical": []}}
    files, guid = stage.ensure_breakable(h, SPEC, man, G)
    assert files == ["XX_Mod/XX_Box/XX_Box_BreakableBlockDefinition.partialdef"] and guid
    h2 = make_hooks(h.paths.root.parent / "two")
    stage.reset(h2)
    _composition(h2, "Server", [])
    with pytest.raises(RuntimeError, match="lost its BreakableBlockComponent"):
        stage.ensure_breakable(h2, SPEC, man, Guids(h2.paths.asset_guids))


def test_block_files_and_patch_body(h):
    _install(h, "XX_Mod/XX_Box/XX_Box_Block.partialdef", _copy(BDEF))
    _install(h, "XX_Mod/XX_Box/Sub/XX_Box_Block2.partialdef", _copy(DIR))
    assert stage.block_files(h, SPEC, "_Block") == ["XX_Mod/XX_Box/Sub/XX_Box_Block2.partialdef",
                                                    "XX_Mod/XX_Box/XX_Box_Block.partialdef"]
    with pytest.raises(RuntimeError, match="expected one"):
        stage.one_file(h, SPEC, "_Block")
    rel = stage.patch_body(h, SPEC, "_Block2", lambda b: b.update(PCU=7))
    assert assetio.body(stage.read_current(h, rel))["PCU"] == 7


def test_partial_default_bundles(h):
    p = stage.partial(h, "base", {"Guid": "g"}, "Game2:T")
    assert p["$Bundles"] == VRAGE and p["$Value"]["PartialDefinitionKind"] == "Copy"
    assert stage.partial(h, "base", {}, "T", GAME)["$Bundles"] == GAME


def test_declare_bundles_unknown(h):
    with pytest.raises(RuntimeError, match="no known version"):
        stage.declare_bundles(h, {"$Type": "Other:Keen.X"})
    assert stage.type_bundles({"a": [{"Type": "VRage:X"}, {"UseActionType": "Game2:Y"}], "Type": "nocolon"}) == {
        "VRage", "Game2"}


def test_put_dummy_idempotent(G):
    dummies = {}
    v = stage.dummy("Lamp", "t", (1, 2, 3))
    stage.put_dummy(SPEC, dummies, G, v)
    stage.put_dummy(SPEC, dummies, G, stage.dummy("Lamp", "t", (4, 5, 6)))
    assert dummies["Keys"] == [G["dummy/XX_Box/Lamp"]] and len(dummies["Changed"]) == 1
    assert dummies["Changed"][0]["Value"]["Transform"]["Transform"]["Position"] == {"X": 4.0, "Y": 5.0, "Z": 6.0}
    assert dummies["Removed"] == []


def test_dummy_and_rotations():
    d = stage.dummy("A", "t", (0.1234567, 0, 0), support="Fracture_01")
    assert d["Transform"]["Transform"]["Position"]["X"] == 0.123457
    assert d["SupportingFracture"] == {"Id": "Fracture_01"} and d["HighlightSection"] is None
    q = stage.quat_axis_angle((0, 0, 1), 90)
    assert q == pytest.approx((0, 0, math.sqrt(0.5), math.sqrt(0.5)))
    rz = ((0, -1, 0), (1, 0, 0), (0, 0, 1))
    assert stage.quat_from_matrix(rz) == pytest.approx(q)
    assert stage.euler_hint(rz) == pytest.approx((0.0, 0.0, 90.0))
    assert stage.rot_apply(rz, (1, 0, 0)) == (0, 1, 0)
    flip = ((-1, 0, 0), (0, -1, 0), (0, 0, 1))
    assert stage.quat_from_matrix(flip) == pytest.approx((0, 0, 1, 0))


def test_built_names_and_manifest(h):
    write(h.paths.models / "XX_Box" / "export.json", {"shift": [0, 0, 0]})
    write(h.paths.report, {"blocks": {"XX_Box": {"placement": {"total": 3}}, "XX_Lid": {"placement": {"total": 1}},
                                      "XX_Nil": {"placement": {"total": 0}}}})
    specs = [SPEC, Spec("XX_Lid"), Spec("XX_Nil")]
    assert stage.built_names(h, specs) == {"XX_Box"}
    assert stage.manifest(h, SPEC) == {"shift": [0, 0, 0]} and stage.manifest(h, Spec("XX_Lid")) is None
    assert stage.built_names(h, specs, {"blocks": {}}) == set()


def test_stage_meta_reuses_installed(h):
    rel = "XX_Mod/XX_Box/x.glb"
    write(h.paths.assets / rel, {})
    assetio.write_meta(h.paths.assets / rel, DIR, STAMPS)
    stage.stage_meta(h, rel, DIR)
    assert not (h.paths.staged / (rel + ".meta")).exists()
    json.dumps(stage.TYPE_FIELDS)


# What the legacy staging wrote for this run (captured once from the original implementation, its 4.0.0.3155
# stamps): the partial's Game2 type extends the shared VRage stamps, so its own .meta and every later one carry Game2,
# appended after VRage (unsorted).
LEGACY_VRAGE = {"System.Runtime": "1.0.0.0", "VRage": "4.0.0.3155"}
LEGACY_GAME = {"Game2": "4.0.0.3155", "System.Runtime": "1.0.0.0", "VRage": "4.0.0.3155"}
LEGACY_META = ('{\n  "$Bundles": {\n    "System.Runtime": "1.0.0.0",\n    "VRage": "4.0.0.3155",\n'
               '    "Game2": "4.0.0.3155"\n  },\n  "$Type": "VRage:Keen.VRage.ContentPipeline.Metafiles.MetaData",\n'
               '  "$Value": {\n    "AssetID": "%s",\n    "InternalItems": []\n  }\n}\n')
LEGACY_PARTIAL = ('{\n  "$Bundles": {\n    "Game2": "4.0.0.3155",\n    "System.Runtime": "1.0.0.0",\n'
                  '    "VRage": "4.0.0.3155"\n  },\n'
                  '  "$Type": "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff",\n  "$Value": {\n'
                  '    "BaseDefinition": "60000000-0000-4000-8000-000000000001",\n'
                  '    "PartialDefinitionKind": "Copy",\n    "Manipulator": {\n'
                  '      "Guid": "50000000-0000-4000-8000-000000000011",\n      "Use": {\n'
                  '        "$Type": "Game2:Keen.Game2.Foo"\n      }\n    },\n    "DefinitionType": "Game2:T",\n'
                  '    "PriorityOverride": false\n  }\n}\n')
A1 = "50000000-0000-4000-8000-000000000011"
A2 = "50000000-0000-4000-8000-000000000012"


def _legacy_run(tmp_path, accumulate):
    h = make_hooks(tmp_path / "mod", bundle_stamps=(dict(LEGACY_VRAGE), dict(LEGACY_GAME)),
                   accumulate_bundles=accumulate)
    stage.reset(h)
    d = stage.partial(h, "60000000-0000-4000-8000-000000000001", {"Guid": A1, "Use": {"$Type": "Game2:Keen.Game2.Foo"}},
                      "Game2:T")
    stage.stage_def(h, "XX_Mod/A.partialdef", d)
    src = write(tmp_path / "src.bin", {})
    stage.stage_file(h, src, "XX_Mod/b.bin", {"XX_Mod/b.bin": A2})
    st = h.paths.staged
    return h, [(st / r).read_text(encoding="utf-8") for r in
               ("XX_Mod/A.partialdef", "XX_Mod/A.partialdef.meta", "XX_Mod/b.bin.meta")]


def test_declare_bundles_never_mutates_stamps(tmp_path):
    h, (part, meta_a, meta_b) = _legacy_run(tmp_path, accumulate=False)
    assert part == LEGACY_PARTIAL                       # the file itself declares what it uses
    for meta, g in ((meta_a, A1), (meta_b, A2)):
        assert json.loads(meta)["$Bundles"] == LEGACY_VRAGE and g in meta
    assert h.bundle_stamps == (LEGACY_VRAGE, LEGACY_GAME) and h.stamps() == (LEGACY_VRAGE, LEGACY_GAME)
    own = {"$Bundles": {"VRage": "1"}, "Type": "Game2:X"}
    before = own["$Bundles"]
    stage.declare_bundles(h, own)
    assert before == {"VRage": "1"} and own["$Bundles"] == {"Game2": "4.0.0.3155", "VRage": "1"}


def test_accumulate_bundles_reproduces_legacy_output(tmp_path):
    h, (part, meta_a, meta_b) = _legacy_run(tmp_path, accumulate=True)
    assert part == LEGACY_PARTIAL and meta_a == LEGACY_META % A1 and meta_b == LEGACY_META % A2
    assert h.bundle_stamps == (LEGACY_VRAGE, LEGACY_GAME)           # the pinned pair is untouched
    stage.reset(h)                                                  # a new run starts from the plain stamps
    assert h.stamps()[0] == LEGACY_VRAGE


def test_stage_def_without_asset_id_raises(h):
    override = assetio.envelope(DIFF, {"BaseDefinition": BDEF, "PartialDefinitionKind": "Override",
                                       "Manipulator": {}}, {})
    with pytest.raises(RuntimeError, match="no AssetID"):
        stage.stage_def(h, "XX_Mod/Override of X.partialdef", override)
    assert not (h.paths.staged / "XX_Mod/Override of X.partialdef").exists()


def test_reset_checks_the_hooks(tmp_path):
    from se2modkit.pipeline.hooks import HooksError
    bad = make_hooks(tmp_path / "mod")
    bad.plans["box"] = "not callable"
    with pytest.raises(HooksError, match=r"plans\['box'\]"):
        stage.reset(bad)
