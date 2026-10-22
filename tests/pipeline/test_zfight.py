"""`pipeline.zfight`: the bpy-free rules of the z-fight audit (decal elements fighting or crowding in one plane,
polygon overlap, coplanar faces of axis-aligned boxes) and the hook tables the Blender side reads."""

import pytest

from se2modkit.pipeline import zfight as Z


def _els(rows, model="Panel_LOD0", n=(1.0, 0.0, 0.0)):
    return [{"label": lb, "model": model, "n": list(n), "d": d, "box": list(b)} for lb, d, b in rows]


def test_rule_depth_and_facing():
    a = ("a", 0.0, (0.0, 0.0, 0.10, 0.05))
    b = ("b", 0.0, (0.05, 0.02, 0.15, 0.07))
    assert Z.decal_conflicts(_els([a, b]))                                         # same depth, overlapping
    assert Z.decal_conflicts(_els([a, ("b", 0.0003, b[2])]))                       # closer than the epsilon
    assert not Z.decal_conflicts(_els([a, ("b", 0.0006, b[2])]))                   # a sticker >= 0.5 mm proud
    assert not Z.decal_conflicts(_els([a]) + _els([b], n=(-1.0, 0.0, 0.0)))        # opposite facings
    assert not Z.decal_conflicts(_els([a]) + _els([b], model="other"))             # other model
    assert Z.decal_conflicts(_els([a, ("c", 0.0, (0.1005, 0.0, 0.2, 0.05))]))      # crowding: 0.5 mm apart
    assert not Z.decal_conflicts(_els([a, ("c", 0.0, (0.10, 0.0, 0.2, 0.05))]))    # an exact edge joint (stripes)
    assert not Z.decal_conflicts(_els([a, ("c", 0.0, (0.102, 0.0, 0.2, 0.05))]))   # 2 mm clear


def test_overlap_area():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert Z.overlap_area(sq, [(0.5, 0.5), (2, 0.5), (2, 2), (0.5, 2)]) == pytest.approx(0.25)
    assert Z.overlap_area(sq, sq[::-1]) == pytest.approx(1.0)                      # either winding
    assert Z.overlap_area(sq, [(2, 2), (3, 2), (3, 3)]) == 0.0


def test_boxes_coplanar_catches_flush_pads():
    """A clamp pad exactly as tall as the rail it clamps puts its top / bottom faces in the rail's planes: coplanar
    same-facing faces of different boxes. Pads 1 mm proud on both sides do not fight; a box never fights itself."""
    rail = ("rail", (0.0, 0.0, 0.0), (1.0, 0.05, 0.04))
    flush = ("pad", (0.4, 0.03, 0.0), (0.5, 0.06, 0.04))
    proud = ("pad", (0.4, 0.03, -0.001), (0.5, 0.06, 0.041))
    bad = Z.boxes_coplanar([rail, flush])
    assert set(bad) == {("rail", "pad")} and bad[("rail", "pad")] > 0
    assert Z.boxes_coplanar([rail, proud]) == {}
    assert Z.boxes_coplanar([rail]) == {}


def test_decal_api_frames_from_hooks(hooks):
    """The decal-element frames: modkit's own decal API plus the mod's (`hooks.decal_api`), by file and function."""
    hooks.decal_api = {("kit.py", "_stencil")}
    api = Z.decal_api(hooks)
    assert ("decals.py", "element") in api and ("helpers.py", "stencil") in api and ("kit.py", "_stencil") in api
    assert hooks.validate() == []
    hooks.decal_api = {("kit.py",)}
    assert any("decal_api" in e for e in hooks.validate())


def test_audit_of_nothing_is_empty(hooks, synthetic_spec):
    rep = Z.audit_built(hooks, synthetic_spec(), None)
    assert rep == {"elements": [], "decal_geometry": [], "leaf_coplanar": [], "leaf_frame": [], "coplanar": [],
                   "subpart_frame": []}
    assert Z.conflicts(rep) == ([], [], [], [], [], [])
