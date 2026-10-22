"""`pipeline.states`: the build-stage / damage-state evaluation behind the states preview harness, the vanilla
analogues it renders beside a mod's blocks (`hooks.analogue`), and the save-compatibility baseline (zone ids,
criticals, mesh parts, stage hash, definition GUIDs) read from the mod's paths."""

import copy
import json

import pytest

from se2modkit.pipeline import states as SV

M = "2a8225c1-df6c-41a7-ad6f-b58a77bdd88d"   # ConstructionMetal01


def _chg(part, show=None, mat=None, reset=False, damaged=False):
    return {"Key": f"k-{part}-{show}-{mat}", "Value": {"MeshPart": part, "ShowPart": show, "SetMaterial": mat,
                                                     "ResetToSkinMaterial": reset, "ApplyOnlyWhenDamaged": damaged}}


def _stages(last=()):
    return [{"Key": "s0", "Value": {"BuildRatioLowerBound": 0, "Model": "{G}fr", "PartChanges": {"_data": [
                _chg("A", show=False), _chg("B", mat=M)]}}},
            {"Key": "s1", "Value": {"BuildRatioLowerBound": 0.3, "Model": None, "PartChanges": {"_data": [
                _chg("A", show=True, mat=M), _chg("B", reset=True)]}}},
            {"Key": "s2", "Value": {"BuildRatioLowerBound": 1, "Model": "{G}nf", "PartChanges": {"_data": list(last)}}}]


def test_part_state_cumulative():
    s = _stages()
    assert SV.part_state(s, 0.0) == {"A": (False, None), "B": (True, M)}
    assert SV.part_state(s, 0.1)["A"] == (False, None)
    assert SV.part_state(s, 0.3)["A"] == (True, M)
    assert SV.part_state(s, 0.3)["B"] == (True, None)          # ResetToSkinMaterial
    assert SV.part_state(s, 1.0) == {}
    s = _stages([_chg("BrushedMetal", show=False, damaged=True)])
    assert SV.part_state(s, 1.0) == {}                          # ApplyOnlyWhenDamaged: not on a sound block
    assert SV.part_state(s, 1.0, damaged=True) == {"BrushedMetal": (False, None)}


def test_shots():
    assert list(SV.SHOTS) == ["c00", "c10", "c20", "c30", "c53", "c77", "done"]
    assert list(SV.SHOTS.values()) == [0.0, 0.1, 0.2, 0.3, 0.5333, 0.7667, 1.0]
    assert SV.DAMAGE_SHOTS == ("done_damaged", "one_hit", "battered", "broken")
    assert SV.ALL_SHOTS == tuple(SV.SHOTS) + SV.DAMAGE_SHOTS


def test_stages_sha1_ignores_only_the_last_stage_changes():
    a = _stages([_chg("Chrome", show=False, damaged=True)])
    assert SV.stages_sha1(a) == SV.stages_sha1(_stages()) == SV.stages_sha1(_stages([_chg("BrushedMetal", False)]))
    b = copy.deepcopy(a)
    b[1]["Value"]["PartChanges"]["_data"][0]["Value"]["SetMaterial"] = "other"
    assert SV.stages_sha1(b) != SV.stages_sha1(a)
    c = copy.deepcopy(a)
    c[-1]["Value"]["Model"] = "{G}other"
    assert SV.stages_sha1(c) != SV.stages_sha1(a)
    assert SV.last_stage_changes(a) == [a[-1]["Value"]["PartChanges"]["_data"][0]["Value"]]


def test_zone_of_and_mesh_part():
    assert SV.zone_of("Fracture_01") == "Fracture_01"
    assert SV.zone_of("Fracture_03_Hide") == "Fracture_03"
    assert SV.zone_of("Fracture_12_Support") == "Fracture_12"
    assert SV.zone_of("Fracture14_Support") == "Fracture14"      # a vanilla spelling
    assert SV.zone_of(None) is None and SV.zone_of("") is None and SV.zone_of("Scaffold") is None
    assert SV.mesh_part("Fracture_01", "Rubber") == "Fracture_01-Rubber" and SV.mesh_part(None, "Rubber") == "Rubber"


def test_damage_scenario_edges():
    z = lambda i, v, c: {"id": i, "volume": v, "critical": c}  # noqa: E731
    allcrit = {"zones": [z("Fracture_01", 2, True), z("Fracture_02", 1, True)],
               "critical": ["Fracture_01", "Fracture_02"]}
    assert SV.damage_scenario(allcrit, "battered") == {"Fracture_01": "damaged", "Fracture_02": "damaged"}
    assert SV.damage_scenario(allcrit, "broken") == {"Fracture_01": "dead", "Fracture_02": "intact"}
    nocrit = {"zones": [z("Fracture_01", 1, False), z("Fracture_02", 3, False)], "critical": []}
    assert SV.damage_scenario(nocrit, "one_hit") == {"Fracture_01": "intact", "Fracture_02": "damaged"}
    assert SV.damage_scenario(nocrit, "battered") == {"Fracture_01": "dead", "Fracture_02": "damaged"}
    assert SV.damage_scenario(nocrit, "broken") == {}
    assert SV.damage_scenario(nocrit, "done_damaged") == {"Fracture_01": "intact", "Fracture_02": "intact"}
    assert SV.damage_scenario({"zones": [], "critical": []}, "one_hit") == {}
    with pytest.raises(ValueError):
        SV.damage_scenario(nocrit, "c30")


def test_analogues_from_hooks(hooks):
    assert SV.analogues(hooks, "box") == ()
    hooks.analogue["box"] = ("Timer50", "Battery50")
    assert SV.analogues(hooks, "box") == ("Timer50", "Battery50")


def test_effect_dummies_and_dummies_of():
    pe = {"Name": "ParticleEffects", "Type": "e9f28118-a0cf-4c6c-b6ad-2e0719e6cf5a"}
    other = {"Name": "Light", "Type": "x"}
    manip = {"Dummies": {"Changed": [{"Kind": "Insert", "Value": pe}, {"Kind": "Update", "Value": other},
                                     {"Kind": "Remove", "Value": {"Name": "gone"}}]}}
    assert SV.dummies_of(manip) == [pe, other] and SV.effect_dummies(SV.dummies_of(manip)) == [pe]
    assert SV.dummies_of({"Dummies": [{"Value": pe}, other]}) == [pe, other] and SV.dummies_of({}) == []


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def _install(hooks, name, guid, stages):
    d = hooks.paths.assets / hooks.name / name
    _write(d / f"{name}_BlockModelDefinition.partialdef",
           {"$Value": {"Manipulator": {"BuildProgressModels": stages}}})
    _write(d / f"{name}_Block.def", {"$Value": {"Guid": guid}})
    return d


def test_installed_definitions_and_guids(hooks):
    _install(hooks, "XX_Box", "11111111-1111-1111-1111-111111111111", _stages())
    _write(hooks.paths.assets / hooks.name / "XX_Mod_Unlock.def",
           {"$Value": {"Guid": "22222222-2222-2222-2222-222222222222"}})
    assert SV.installed_stages(hooks, "XX_Box") == _stages()
    assert SV.def_guids(hooks, "XX_Box") == ["11111111-1111-1111-1111-111111111111"]
    assert SV.shared_def_guids(hooks, ["XX_Box"]) == ["22222222-2222-2222-2222-222222222222"]
    (hooks.paths.assets / hooks.name / "XX_Box" / "XX_Box_Extra_BlockModelDefinition.partialdef").write_text("{}")
    with pytest.raises(FileNotFoundError, match="XX_Box: expected one"):
        SV.bmd_path(hooks, "XX_Box")


def test_model_paths(hooks):
    d = hooks.paths.models / "XX_Box" / "Fractured"
    d.mkdir(parents=True)
    (d / "XX_Box_Fractured.fbx").write_bytes(b"")
    assert SV.model_fbx(hooks, "XX_Box", "fr") == d / "XX_Box_Fractured.fbx"
    assert SV.model_fbx(hooks, "XX_Box", "dm") is None
    _write(hooks.paths.models / "XX_Box" / "export.json", {"fractured": {"zones": []}})
    assert SV.manifest(hooks, "XX_Box") == {"fractured": {"zones": []}}


def test_analogue_dir_needs_a_reference(tmp_path, monkeypatch):
    blk = tmp_path / "Blocks" / "Timer" / "50"
    blk.mkdir(parents=True)
    (blk / "Timer50_BlockModelDefinition.partialdef").write_text(
        json.dumps({"$Value": {"Manipulator": {"BuildProgressModels": [1]}}}), encoding="utf-8")
    assert SV.analogue_dir("Timer50", vanilla=tmp_path) == blk
    assert SV.analogue_stages("Timer50", vanilla=tmp_path) == [1]
    with pytest.raises(FileNotFoundError, match="Battery50"):
        SV.analogue_dir("Battery50", vanilla=tmp_path)
    from se2modkit.model import config
    monkeypatch.setattr(config, "__getattr__", lambda name: None)
    with pytest.raises(SV.StatesError, match="vanilla_ref"):
        SV.analogue_dir("Timer50")
