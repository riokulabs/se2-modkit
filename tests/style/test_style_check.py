"""`style.check`: findings from a synthetic build report against a profile (hand-written fixtures, no game data)."""

import copy

import pytest

from se2modkit.style import check, profile, waivers
from se2modkit.style.check import Finding

VANILLA = profile.load("vanilla")
NONE = waivers.Waivers(())


def _lod(area_pct, materials=None, tris=1000, max_tris=4000, max_materials=18):
    names = sorted(area_pct) if materials is None else materials
    return {"area_pct": area_pct, "material_names": names, "materials": len(names), "tris": tris,
            "max_tris": max_tris, "max_materials": max_materials}


def _good_block():
    """Every measurement present and inside the vanilla profile."""
    return {
        "lods": [_lod({"PaintedMetalColorable": 60.0, "PaintedMetalDarker": 35.8, "EmissiveOff": 0.2,
                       "AtlasDecal_Parts01": 1.0, "MetalDull": 3.0}),
                 _lod({"PaintedMetalColorable": 62.0, "PaintedMetalDarker": 38.0}, tris=300, max_tris=1000),
                 _lod({"PaintedMetalColorable": 100.0}, tris=12, max_tris=32, max_materials=1)],
        "style": {"hazard_share": 0.01},
        "damage": {"dm_tris": 1100, "fr_tris": 1000, "dent_mm": 8.0},
        "fracture": {"zone_health": {"Fracture_01": 0.4, "Fracture_02": 0.6},
                     "interior": {"Fracture_01": {"exposes": True, "cover": 0.95, "interior_tris": 120, "core_m2": 0.3},
                                  "Fracture_02": {"exposes": False, "cover": None, "interior_tris": 0,
                                                  "core_m2": 0.0}}},
    }


def _report(**blocks):
    return {"blocks": blocks}


def _keys(findings):
    return {(f.block, f.key, f.level) for f in findings}


def test_good_block_has_no_findings():
    res = check.run(_report(B=_good_block()), VANILLA, NONE)
    assert res.findings == []
    assert res.not_measured == {}


def test_check_emissive_fail():
    b = _good_block()
    b["lods"][0]["area_pct"]["EmissiveOff"] = 0.5
    b["lods"][0]["area_pct"]["PaintedMetalDarker"] = 35.5
    found = check.check(_report(B=b), VANILLA, NONE)
    assert found == [Finding("B", "emissive.max_share", 0.005, 0.003, "fail", None)]


def test_emissive_share_field_wins_and_lights_are_excepted():
    b = _good_block()
    b["style"]["emissive_share"] = 0.001
    b["lods"][0]["area_pct"]["EmissiveFlood"] = 2.0   # would fail, but the measured field wins
    assert check.check(_report(B=b), VANILLA, NONE) == []
    b2 = _good_block()
    b2["lods"][0]["area_pct"]["EmissiveFlood"] = 2.0
    assert ("B", "emissive.max_share", "fail") in _keys(check.check(_report(B=b2), VANILLA, NONE))
    # lights are excepted only by a profile's own list (a mod cannot grow it; it waives a block instead)
    lit = profile.Profile("lit", ("lit",), {**VANILLA.data, "emissive": {**VANILLA.data["emissive"],
                                                                        "light_materials": ["EmissiveFlood"]}})
    assert check.check(_report(B=b2), lit, NONE) == []


def test_paint_share_fail():
    b = _good_block()
    b["lods"][0]["area_pct"].update(PaintedMetalColorable=30.0, PaintedMetalDarker=65.8)
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.value, f.limit, f.level) == ("paint.colorable_min_share", 0.3, 0.45, "fail")


def test_materials_per_lod_fail():
    b = _good_block()
    b["lods"][1] = _lod({f"M{i}": 10.0 for i in range(9)})
    b["lods"][1]["area_pct"]["PaintedMetalColorable"] = 60.0
    b["lods"][1]["material_names"] = sorted(b["lods"][1]["area_pct"])
    b["lods"][1]["materials"] = 10
    assert ("B", "materials.max_per_lod", "fail") in _keys(check.check(_report(B=b), VANILLA, NONE))


def test_hazard_budget_and_drives_guns_allowance():
    b = _good_block()
    b["style"]["hazard_share"] = 0.06
    assert ("B", "hazard.max_share", "fail") in _keys(check.check(_report(B=b), VANILLA, NONE))
    b["style"]["archetype"] = "thruster"
    assert check.check(_report(B=b), VANILLA, NONE) == []
    b["style"]["hazard_share"] = 0.09
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.limit) == ("hazard.max_share_drives_guns", 0.08)


def test_check_dm_ratio_warn():
    b = _good_block()
    b["damage"]["dm_tris"] = 2000
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.block, f.key, f.value, f.limit, f.level) == ("B", "dm.tri_ratio_max", 2.0, 1.2, "warn")


def test_dent_band_warn_both_sides():
    b = _good_block()
    b["damage"]["dent_mm"] = 2.4
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.limit, f.level) == ("dm.dent_mm", 5, "warn")
    b["damage"]["dent_mm"] = 20.0
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert f.limit == 15


def test_zone_health_warn():
    b = _good_block()
    b["fracture"]["zone_health"] = {"Fracture_01": 0.9994, "Fracture_08": 0.0006}
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.value, f.level) == ("fracture.min_zone_health", 0.0006, "warn")
    assert "Fracture_08" in f.where


def test_keen_limits_warn():
    b = _good_block()
    b["lods"][1]["tris"] = 1200
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.value, f.limit, f.level) == ("limits.tris", 1200, 1000, "warn")
    assert f.where == "LOD1"


def test_decals_past_max_lod_warn():
    b = _good_block()
    b["lods"][2] = _lod({"PaintedMetalColorable": 99.0, "AtlasFont_BarlowCondensed": 1.0}, tris=12, max_tris=32,
                        max_materials=2)
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.value, f.limit, f.level, f.where) == ("decal.max_lod", 2, 1, "warn", "LOD2")


def test_interior_hollow_fails():
    b = _good_block()
    b["fracture"]["interior"]["Fracture_01"]["cover"] = 0.1
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.value, f.limit, f.level, f.where) == ("interior.min_cover", 0.1, 0.8, "fail", "Fracture_01")


def test_interior_with_no_seam_cover_fails():
    b = _good_block()
    b["fracture"]["interior"]["Fracture_01"]["cover"] = None
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.value, f.level) == ("interior.min_cover", 0.0, "fail")


def test_interior_modelled_machine_fails():
    b = _good_block()
    b["fracture"]["interior"]["Fracture_01"]["interior_tris"] = 2342
    f, = check.check(_report(B=b), VANILLA, NONE)
    assert (f.key, f.value, f.limit, f.level) == ("interior.max_detail_tris", 2342, 800, "fail")


def test_missing_measurements_are_not_measured_never_a_pass():
    b = _good_block()
    del b["fracture"]["interior"]
    del b["style"]
    del b["damage"]
    res = check.run(_report(B=b), VANILLA, NONE)
    assert res.findings == []
    assert {"interior.min_cover", "hazard.max_share", "dm.tri_ratio_max", "dm.dent_mm"} <= set(res.not_measured)
    assert res.not_measured["interior.min_cover"] == ["B"]
    assert not res.passed_all


def test_extra_profile_checks_are_not_measured():
    res = check.run(_report(B=_good_block()), profile.load("industrial"), NONE)
    assert {"faction_parity", "orientation", "rationale_coverage", "hazard_tags"} <= set(res.not_measured)


def test_no_believable_purpose_field():
    """"Every part has a believable purpose" is a human review item: no check reads a report field for it."""
    assert not [k for k in check.CHECKS if "purpose" in k or "believ" in k]
    assert not [k for k in check.REPORT_KEYS if "purpose" in k or "believ" in k]


def test_report_without_blocks_is_an_error():
    with pytest.raises(check.StyleError, match="blocks"):
        check.run({"nope": 1}, VANILLA, NONE)


def test_run_does_not_mutate_report():
    r = _report(B=_good_block())
    before = copy.deepcopy(r)
    check.run(r, VANILLA, NONE)
    assert r == before
