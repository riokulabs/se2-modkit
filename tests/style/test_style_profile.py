"""Style profiles: TOML files with `extends`; a mod's `[tool.se2modkit.style]` overrides may only tighten."""

import pytest

from se2modkit.model import limits
from se2modkit.style import profile
from se2modkit.style.profile import ProfileError

VANILLA = {
    "paint.colorable_min_share": 0.45,
    "emissive.max_share": 0.003,
    "materials.max_per_lod": 8,
    "edge.chamfer_large": 0.09,
    "edge.chamfer_small": 0.015,
    "edge.shadow_gap": 0.016,
    "mount.keep_out": 0.027,
    "hw.bolt_r": 0.009,
    "hw.led_size": 0.012,
    "decal.offset": 0.002,
    "decal.max_lod": 1,
    "hazard.max_share": 0.04,
    "hazard.max_share_drives_guns": 0.08,
    "dm.tri_ratio_max": 1.2,
    "dm.dent_mm": [5, 15],
    "fracture.min_zone_health": 0.005,
}


@pytest.mark.parametrize("key, value", sorted(VANILLA.items()))
def test_vanilla_values(key, value):
    assert profile.load("vanilla").get(key) == value


def test_vanilla_limits_come_from_model_limits():
    p = profile.load("vanilla")
    assert p.get("limits.fracture_tri_mult") == limits.FRACTURE_MULT
    assert p.get("limits.fracture_materials") == 32


def test_industrial_extends_vanilla():
    p = profile.load("industrial")
    assert p.name == "Industrial"
    assert p.get("paint.colorable_min_share") == 0.45
    assert p.get("geometry.plate_row") == 0.625
    assert p.get("geometry.deck_height") == 2.5
    assert p.get("stencil.id_h") == 0.06
    assert p.get("finish.decorative_emissive") is False
    assert set(p.get("checks.extra")) == {"faction_parity", "orientation", "rationale_coverage", "hazard_tags"}
    assert p.chain == ("industrial", "vanilla")


def test_unknown_profile_names_the_known_ones():
    with pytest.raises(ProfileError, match="vanilla"):
        profile.load("nope")


def test_missing_key_is_a_profile_error():
    with pytest.raises(ProfileError, match="paint.nope"):
        profile.load("vanilla").get("paint.nope")


def test_override_may_tighten():
    p = profile.load("vanilla", overrides={"paint": {"colorable_min_share": 0.5}, "emissive": {"max_share": 0.002},
                                           "dm": {"dent_mm": [6, 12]}})
    assert p.get("paint.colorable_min_share") == 0.5
    assert p.get("emissive.max_share") == 0.002
    assert p.get("dm.dent_mm") == [6, 12]


def test_override_may_add_roles():
    p = profile.load("vanilla", overrides={"roles": {"copper": "Copper"}, "checks": {"extra": ["orientation"]}})
    assert p.get("roles.copper") == "Copper"
    assert p.get("roles.paint") == "PaintedMetalColorable"
    assert p.get("checks.extra") == ["orientation"]


@pytest.mark.parametrize("overrides, key", [
    ({"paint": {"colorable_min_share": 0.30}}, "paint.colorable_min_share"),
    ({"emissive": {"max_share": 0.01}}, "emissive.max_share"),
    ({"dm": {"dent_mm": [3, 15]}}, "dm.dent_mm"),
    ({"hw": {"led_size": 0.016}}, "hw.led_size"),
    ({"roles": {"paint": "PaintedMetalDarker"}}, "roles.paint"),
    ({"paint": {"bogus": 1}}, "paint.bogus"),
    ({"emissive": {"light_materials": ["EmissiveFlood"]}}, "emissive.light_materials"),
    ({"hazard": {"high_archetypes": ["thruster", "weapon", "cabinet"]}}, "hazard.high_archetypes"),
])
def test_override_may_not_relax(overrides, key):
    with pytest.raises(ProfileError, match=key.replace(".", r"\.")):
        profile.load("vanilla", overrides=overrides)


def test_control_keys_are_not_overrides():
    p = profile.load("vanilla", overrides={"warn_only": True, "waivers": "style-waivers.toml"})
    assert p.get("paint.colorable_min_share") == 0.45


def test_extends_cycle_is_an_error(tmp_path):
    (tmp_path / "a.toml").write_text('extends = "b"\nname = "A"\n', encoding="utf-8")
    (tmp_path / "b.toml").write_text('extends = "a"\nname = "B"\n', encoding="utf-8")
    with pytest.raises(ProfileError, match="cycle"):
        profile.load("a", search=[tmp_path])
