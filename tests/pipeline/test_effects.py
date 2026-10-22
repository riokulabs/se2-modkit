"""`pipeline.effects`: which blocks spark (`hooks.spark_kind`), one emitter per zone (`hooks.per_zone`), the vanilla
band each kind copies and the ParticleEffects-type dummies the bands bind to."""

import json

import pytest

from se2modkit.pipeline import effects as EF


def _zone(zid, lo, hi, share=0.0):
    return {"id": zid, "lo": lo, "hi": hi, "health_share": share}


def test_spark_kind_default_none(hooks, synthetic_spec):
    assert EF.spark_kind(hooks, synthetic_spec()) is None
    assert EF.per_zone(hooks, synthetic_spec()) is False


def test_spark_kind_and_per_zone_from_hooks(hooks, synthetic_spec):
    hooks.spark_kind["box"] = lambda s: "breaker" if s.variant == "Live" else None
    hooks.per_zone["box"] = lambda s: s.size[0] > 1.0
    assert EF.spark_kind(hooks, synthetic_spec(variant="Live")) == "breaker"
    assert EF.spark_kind(hooks, synthetic_spec()) is None
    assert EF.per_zone(hooks, synthetic_spec(size=(2.0, 0.5, 0.5))) is True
    assert EF.per_zone(hooks, synthetic_spec()) is False
    assert EF.spark_kind(hooks, synthetic_spec(family="lid")) is None


def test_blender_to_game():
    assert EF.blender_to_game((1.0, 2.0, 3.0), (0.5, 0.0, -0.5)) == (1.5, 3.0, -2.5)


def test_centre_zone():
    a = _zone("Fracture_01", (-1, -0.5, -0.5), (0, 0.5, 0.5), 0.4)
    b = _zone("Fracture_02", (0, -0.5, -0.5), (1, 0.5, 0.5), 0.6)
    assert EF.centre_zone([a, b]) == "Fracture_02"         # both hold the centre: the larger share
    c = _zone("Fracture_03", (2, 0, 0), (3, 1, 1), 0.9)
    assert EF.centre_zone([c, _zone("Fracture_04", (4, 0, 0), (5, 1, 1), 0.1)]) == "Fracture_03"   # none: largest


def test_emitter_zones_groups_along_the_long_axis():
    zs = [_zone(f"Fracture_{k + 1:02d}", (0, 0, k * 0.25 - 1.0), (0.5, 0.5, k * 0.25 - 0.75), 0.1 + 0.01 * (k == 2))
          for k in range(8)]
    # centre zone: the one holding z = 0 (k = 4: z 0 .. 0.25, also k = 3 touches it at 0)
    c = EF.centre_zone(zs)
    out = EF.emitter_zones(zs, c)
    assert len(out) == 2 and all(z["id"] != c for z in out)
    few = zs[:3]
    assert [z["id"] for z in EF.emitter_zones(few, "Fracture_01")] == ["Fracture_02", "Fracture_03"]


def test_zone_dummies():
    a = _zone("Fracture_01", (-1, -0.5, -0.5), (0, 0.5, 0.5), 0.4)
    b = _zone("Fracture_02", (0, -0.5, -0.5), (1, 0.5, 0.5), 0.6)
    fr = {"zones": [a, b], "role_bounds": {"Fracture_01": {"hide": [-0.8, 0, 0, -0.6, 0.2, 0.2]}}}
    out = EF.zone_dummies(fr, (0.0, 0.0, 0.0), per_zone=False)
    assert out == [("ParticleEffects", (0.0, 0.0, 0.0), "Fracture_02")]
    out = EF.zone_dummies(fr, (0.0, 0.0, 0.0), per_zone=True, prefer_role="hide")
    assert out[1] == ("ParticleEffects_Fracture_01", EF.blender_to_game((-0.7, 0.1, 0.1), (0, 0, 0)), "Fracture_01")
    out = EF.zone_dummies(fr, (0.0, 0.0, 0.0), per_zone=True)
    assert out[1][1] == EF.blender_to_game((-0.5, 0.0, 0.0), (0, 0, 0))


def test_vanilla_band_reads_the_reference(tmp_path):
    rel = EF.SPARK_SOURCES["breaker"]
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    band = {"Upper": 0.99, "Effect": "x"}
    p.write_text(json.dumps({"$Value": {"Manipulator": {"OnDamagedBlockParticleEffect": {
        "Changed": [{"Value": band}]}}}}), encoding="utf-8")
    assert EF.vanilla_band("breaker", vanilla=tmp_path) == band


def test_vanilla_band_without_reference_is_one_sentence(monkeypatch):
    from se2modkit.model import config
    monkeypatch.setattr(config, "__getattr__", lambda name: None)
    with pytest.raises(EF.EffectsError, match="vanilla_ref"):
        EF.vanilla_effects("cable")
