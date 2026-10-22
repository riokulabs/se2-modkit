"""`pipeline.scaffold`: the construction scaffold, the cage the welders work from below 30 %: an ungrouped BrushedMetal
lattice in the Fractured model only, within the block's cells, never pushing the Fractured model over its LOD row.
`hooks.scaffold_zone_boxes` picks the families caged along their zone hulls instead of one envelope box."""

import pytest

from se2modkit.model import limits
from se2modkit.pipeline import scaffold as SC


def _box(lo, hi):
    return [(tuple(lo), tuple(hi))]


def test_cage_unit_box():
    unit = _box((0, 0, 0), (1, 1, 1))
    assert len(SC.cage(unit, 1.0, 0)) == 12
    assert len(SC.cage(unit, 0.5, 0)) == 24        # + one interior line per direction on each of the 6 faces
    assert len(SC.cage(unit, SC.EDGES_ONLY, 0)) == 12
    two = unit + _box((1, 0, 0), (2, 1, 1))
    m = SC.cage(two, 1.0, 0)
    key = [tuple(sorted((tuple(round(c, 6) for c in a), tuple(round(c, 6) for c in b)))) for a, b in m]
    assert len(key) == len(set(key))
    # the union's 12 edges (the long ones merged across the joint) + the joint's ring on the surface
    assert len(m) == 16


def test_cage_drops_inner_faces_and_stays_inside():
    """Two boxes of an L: no member runs across their shared face's interior, and every member is pulled `inset`
    inside the union (ends too), so a tube of that radius never leaves the block's outline."""
    boxes = _box((0, 0, 0), (1, 1, 0.2)) + _box((0, 1, 0), (0.4, 2, 0.2))
    r = 0.01
    for a, b in SC.cage(boxes, 0.25, r):
        for p in (a, b):
            assert any(all(lo[i] - 1e-9 <= p[i] <= hi[i] + 1e-9 for i in range(3)) for lo, hi in boxes), p
        mid = [(x + y) / 2 for x, y in zip(a, b)]
        assert not (0.0 + r < mid[0] < 0.4 - r and abs(mid[1] - 1.0) < 1e-6 and r < mid[2] < 0.2 - r), (a, b)
    lo = [min(p[i] for m in SC.cage(boxes, 0.25, r) for p in m) for i in range(3)]
    hi = [max(p[i] for m in SC.cage(boxes, 0.25, r) for p in m) for i in range(3)]
    assert lo == pytest.approx([r, r, r]) and hi == pytest.approx([1 - r, 2 - r, 0.2 - r])


def test_snap_merges_near_coincident_faces():
    """Two zone hulls meeting a few mm apart share one row of members, not a doubled bar."""
    boxes = SC._snap_boxes(_box((0, 0, 0), (0.5, 1, 0.2)) + _box((0.503, 0, 0), (1, 1, 0.2)), 0.012)
    assert boxes[0][1][0] == boxes[1][0][0]


def test_reach_cells():
    """A face the block fills (within FILLS of its cell face) moves OUTSET past the cell; others stay."""
    lo, hi = SC.reach_cells((-0.499, -0.3, -0.1245), (0.4995, 0.2, 0.0), (0.5, 0.375, 0.125))
    assert lo == pytest.approx((-0.504, -0.3, -0.129)) and hi == pytest.approx((0.504, 0.2, 0.0))


def test_row_margin_is_the_last_safe_k():
    size = (0.5, 0.498, 0.248)                           # a light block whose LOD0 sits near a row
    t0 = 1144
    k = SC.row_margin(size, t0, 10_000)
    assert limits.optimal_lods(size, t0 + k) == limits.optimal_lods(size, t0)
    assert limits.optimal_lods(size, t0 + k + 1) != limits.optimal_lods(size, t0)
    assert SC.row_margin(size, t0, 0) == 0


def test_plan_shrinks_by_pitch_and_reserves_room(hooks):
    """A tighter budget never changes the boxes, only the pitch (then the tube sides); a stand-in reserve is counted
    as FR triangles already there; no room at all is None."""
    zones = [{"lo": (-0.5, -0.4, -0.12), "hi": (0.5, 0.4, 0.12)}]
    lo, hi = zones[0]["lo"], zones[0]["hi"]
    size = (1.0, 0.8, 0.24)
    big = SC.plan(hooks, "box", zones, lo, hi, size, [12000, 2000, 12], [20950, 3500, 55])
    tight = SC.plan(hooks, "box", zones, lo, hi, size, [12000, 2000, 12], [20950, 3500, 55], cap=big.tris[0] - 1)
    assert tight.boxes == big.boxes and tight.tris[0] < big.tris[0]
    assert tight.pitch > big.pitch or tight.sides[0] < big.sides[0]
    res = SC.plan(hooks, "box", zones, lo, hi, size, [12000, 2000, 12], [20950, 3500, 55], reserve=(2000, 0))
    assert res.tris[0] <= SC.budget(size, 14000, 20950)
    assert SC.plan(hooks, "box", zones, lo, hi, size, [20000, 2000, 12], [20950, 3500, 55]) is None
    assert big.sides == {0: 8, 1: 4} and big.tris == {0: 16 * len(big.members), 1: 8 * len(big.lod_members[1])}
    man = big.manifest()
    assert man["members"] == len(big.members) and man["tris"] == {"0": big.tris[0], "1": big.tris[1]}


def test_boxes_for_envelope_by_default(hooks):
    zones = [{"lo": (-0.5, -0.5, -0.5), "hi": (0.0, 0.5, 0.5)}, {"lo": (0.0, -0.2, -0.5), "hi": (0.5, 0.2, 0.5)}]
    lo, hi = (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)
    assert SC.boxes_for(hooks, "box", zones, lo, hi) == [(lo, hi)]


def test_boxes_for_zone_hulls_with_threshold(hooks):
    """A `scaffold_zone_boxes` family is caged along its zone hulls (clipped to the envelope); a hull thinner than the
    family's threshold gets no box (a door's sill strip: its tubes would lie across the threshold)."""
    zones = [{"lo": (-0.6, -0.5, -0.5), "hi": (-0.3, 0.5, 0.5)},       # a post, reaching past the envelope
             {"lo": (0.3, -0.5, -0.5), "hi": (0.5, 0.5, 0.5)},         # the other post
             {"lo": (-0.3, -0.5, -0.5), "hi": (0.3, 0.5, -0.49)}]      # a 1 cm sill
    lo, hi = (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)
    hooks.scaffold_zone_boxes["box"] = 0.0
    every = SC.boxes_for(hooks, "box", zones, lo, hi)
    assert every[0] == ((-0.5, -0.5, -0.5), (-0.3, 0.5, 0.5)) and len(every) == 3
    hooks.scaffold_zone_boxes["box"] = 0.025
    assert len(SC.boxes_for(hooks, "box", zones, lo, hi)) == 2


def test_hooks_set_form_means_every_hull(tmp_path):
    from hookfix import make_hooks
    h = make_hooks(tmp_path, scaffold_zone_boxes={"box"})
    assert h.scaffold_zone_boxes == {"box": 0.0}
    zones = [{"lo": (-0.5, -0.5, -0.5), "hi": (0.0, 0.5, -0.495)}, {"lo": (0.0, -0.5, -0.5), "hi": (0.5, 0.5, 0.5)}]
    assert len(SC.boxes_for(h, "box", zones, (-0.5,) * 3, (0.5,) * 3)) == 2


def test_natural_pitch_and_radius():
    assert SC.natural_pitch((0, 0, 0), (1.0, 0.2, 0.2)) == 0.25
    assert SC.natural_pitch((0, 0, 0), (0.5, 0.5, 0.5)) == 0.125
    assert SC.radius_for((0, 0, 0), (1.0, 0.2, 0.2)) == 0.010 and SC.radius_for((0, 0, 0), (0.5,) * 3) == 0.006
    assert SC.tris_of([((0, 0, 0), (1, 0, 0))] * 3, 8) == 48
    assert SC.PROPS == ("Scaffold", "AlwaysVisible", "Scaffolding") and SC.MATERIAL == "BrushedMetal"
