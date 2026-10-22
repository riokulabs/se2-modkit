"""`pipeline.ModHooks`: the per-mod registry the pipeline reads instead of module-level tables."""

import dataclasses

import pytest

from hookfix import STAMPS, Spec, make_hooks
from se2modkit.env.errors import EnvError
from se2modkit.pipeline import ModHooks
from se2modkit.pipeline.hooks import HooksError
from se2modkit.project import ModConfig

TABLES = ("keep_whole", "roles", "dent", "jag", "plans", "unfractured", "unfractured_boxes", "standins",
          "min_share_exempt", "core_parts",
          "scaffold_zone_boxes", "spark_kind", "per_zone", "analogue", "coplanar_subparts", "leaf_frame",
          "spawned_subparts", "decal_api", "connect_roots", "realism_manual", "material_slots", "damaged_slots", "clone_also", "clone_drop")


def _plan(spec, boxes):
    return None


def test_hooks_defaults_empty(tmp_path):
    h = make_hooks(tmp_path, bundle_stamps=None)
    for name in TABLES:
        v = getattr(h, name)
        assert v == type(v)() and isinstance(v, (dict, set, tuple)), name
    assert h.damaged_tris_x == {"default": 1.2} and h.tris_x("box") == 1.2
    assert h.bundle_stamps is None and h.accumulate_bundles is False
    assert h.validate() == []


def test_hooks_fields_are_the_documented_api():
    names = {f.name for f in dataclasses.fields(ModHooks) if f.init}
    assert names == {"prefix", "name", "paths", "bundle_stamps", "accumulate_bundles", "damaged_tris_x",
                     "zfight_bury_d", *TABLES}


@pytest.mark.parametrize("prefix", ["XX", "", "_", "xx_", "X_Y", "1X_"])
def test_hooks_validate_rejects_bad_prefix(tmp_path, prefix):
    h = make_hooks(tmp_path)
    h.prefix = prefix
    errs = h.validate()
    assert len(errs) == 1 and "prefix" in errs[0] and repr(prefix) in errs[0]


def test_hooks_are_not_module_globals(tmp_path):
    a, b = make_hooks(tmp_path / "a"), make_hooks(tmp_path / "b")
    for name in (*TABLES, "damaged_tris_x"):
        if not isinstance(getattr(a, name), tuple):        # tuples are immutable (and () is a singleton)
            assert getattr(a, name) is not getattr(b, name), name
    a.roles["box"] = {"support": ("Frame",)}
    a.scaffold_zone_boxes["box"] = 0.025
    a.damaged_tris_x["box"] = 1.5
    a.connect_roots.add("Frame")
    assert b.roles == {} and b.scaffold_zone_boxes == {} and b.damaged_tris_x == {"default": 1.2}
    assert b.connect_roots == set()


def test_hooks_validate_callables(tmp_path):
    h = make_hooks(tmp_path)
    h.plans["box"] = _plan
    h.spark_kind["box"] = "cable"
    h.per_zone["box"] = None
    h.leaf_frame["box"] = lambda spec, built: []
    h.clone_also["Vanilla50"] = 3
    errs = h.validate()
    assert sorted(e.split(":")[0] for e in errs) == ["clone_also['Vanilla50']", "per_zone['box']",
                                                     "spark_kind['box']"]


def test_hooks_validate_family_keys(tmp_path):
    h = make_hooks(tmp_path)
    h.plans["box"] = _plan
    h.dent["box"] = dict(amp=0.01)
    h.jag["lid"] = dict(step=0.1)
    h.keep_whole["lid"] = ("hw",)
    h.roles["box"] = {"support": ("Frame",)}
    h.min_share_exempt.add(("lid", 2))
    errs = h.validate()
    assert len(errs) == 3 and all("'lid'" in e and "plans" in e for e in errs)
    h.plans["lid"] = _plan
    assert h.validate() == []


def test_hooks_validate_material_slots(tmp_path):
    h = make_hooks(tmp_path)
    h.material_slots["XX_Glow"] = ("not-a-guid", "EmissiveOff.def")
    h.damaged_slots["XX_Glow"] = "NoSuchSlot"
    errs = h.validate()
    assert any("material_slots['XX_Glow']" in e for e in errs)
    assert any("damaged_slots['XX_Glow']" in e and "NoSuchSlot" in e for e in errs)


def test_hooks_stamps_pinned_or_from_env(tmp_path, monkeypatch):
    assert make_hooks(tmp_path).stamps() == STAMPS
    h = make_hooks(tmp_path, bundle_stamps=None)
    with pytest.raises(EnvError, match="bundle stamps"):
        h.stamps()
    from se2modkit.pipeline import hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "bundle_stamps", lambda env: ({"VRage": env}, {"Game2": env}))
    assert h.stamps("ENV") == ({"VRage": "ENV"}, {"Game2": "ENV"})
    assert h.stamps() == ({"VRage": "ENV"}, {"Game2": "ENV"})   # resolved once per registry


def test_hooks_for_mod(tmp_path):
    h0 = make_hooks(tmp_path)
    mod = ModConfig(name="XX_Mod", root=tmp_path, profile="vanilla", prefix="XX_", gates=(), style={}, paths=h0.paths)
    h = ModHooks.for_mod(mod, bundle_stamps=STAMPS)
    assert (h.prefix, h.name, h.paths, h.bundle_stamps) == ("XX_", "XX_Mod", h0.paths, STAMPS)


def test_stamps_are_copies_by_default(tmp_path):
    h = make_hooks(tmp_path)
    a = h.stamps()
    a[0]["Game2"] = "x"
    a[1].clear()
    assert h.stamps() == STAMPS and h.bundle_stamps == STAMPS
    assert h.stamps()[0] is not h.stamps()[0]


def test_stamps_shared_per_run_when_accumulating(tmp_path):
    h = make_hooks(tmp_path, accumulate_bundles=True)
    a = h.stamps()
    assert h.stamps() is a and a[0] is not h.bundle_stamps[0]
    a[0]["Game2"] = "x"
    assert h.stamps()[0]["Game2"] == "x" and h.bundle_stamps == STAMPS   # the pinned pair itself never changes


def test_scaffold_zone_boxes_accepts_a_set(tmp_path):
    h = make_hooks(tmp_path, scaffold_zone_boxes={"tray", "door"})
    assert h.scaffold_zone_boxes == {"door": 0.0, "tray": 0.0}
    h = make_hooks(tmp_path, scaffold_zone_boxes={"tray": 0.0, "door": 0.025})
    assert h.scaffold_zone_boxes == {"tray": 0.0, "door": 0.025} and h.validate() == []
    h.scaffold_zone_boxes["lid"] = -1
    assert any("scaffold_zone_boxes['lid']" in e for e in h.validate())


def test_unfractured_reason_and_tris_x(tmp_path):
    h = make_hooks(tmp_path, unfractured={"XX_Rods": "two rods, no connection",
                                          "tray": lambda spec: "loose" if spec.name.endswith("Chute") else None},
                   damaged_tris_x={"default": 1.2, "deck": 1.5}, unfractured_boxes=["rear_stop"])
    assert h.unfractured_reason(Spec("XX_Rods", family="tray")) == "two rods, no connection"
    assert h.unfractured_reason(Spec("XX_Chute", family="tray")) == "loose"
    assert h.unfractured_reason(Spec("XX_Tray", family="tray")) is None
    assert h.unfractured_reason(Spec("XX_Box")) is None
    assert h.tris_x("deck") == 1.5 and h.tris_x("box") == 1.2
    assert h.unfractured_boxes == ("rear_stop",) and h.validate() == []


def test_validate_fracture_extras(tmp_path):
    h = make_hooks(tmp_path, unfractured={"XX_Box": 3}, damaged_tris_x={"deck": 0}, unfractured_boxes=(1,))
    h.connect_roots.add(2)
    errs = " ".join(h.validate())
    for want in ("unfractured['XX_Box']", 'needs a numeric "default"', "damaged_tris_x['deck']",
                 "unfractured_boxes", "connect_roots"):
        assert want in errs, want


def test_check_raises_one_error(tmp_path):
    h = make_hooks(tmp_path)
    h.check()
    h.prefix = "xx"
    with pytest.raises(HooksError, match="not usable: prefix 'xx'"):
        h.check()
