"""`pipeline.fracture`: the fracture plan dispatch on `hooks.plans`, the non-breakable exceptions (`hooks.unfractured`),
zones / seams / hulls and Keen's limits; `pipeline.standins.StandIn` (re-exported from fracture); and the offline
import rules of `pipeline.fracture_rules`."""

import math

import pytest

from pipefix import boxes_of, toy_plan
from se2modkit.pipeline import fracture as FR
from se2modkit.pipeline import fracture_rules as FRR
from se2modkit.pipeline import standins as SI


def test_plan_dispatches_on_hooks_plans(hooks, synthetic_spec):
    spec = synthetic_spec()
    p = FR.plan(hooks, spec, boxes_of(spec))
    assert p.ids() == ["Fracture_01", "Fracture_02"] and p.critical() == ["Fracture_01"]
    assert [z.hull for z in p.zones] == [((0.0, -0.5, -0.5), (0.5, 0.5, 0.5)), ((-0.5, -0.5, -0.5), (0.0, 0.5, 0.5))]
    assert p.keep_whole == ()
    man = p.manifest()
    assert man["seams"][0]["sides"] == [["Fracture_01"], ["Fracture_02"]] and man["zones"][0]["critical"] is True


def test_plan_gets_the_family_keep_whole(hooks, synthetic_spec):
    hooks.keep_whole["box"] = ("screw",)
    spec = synthetic_spec()
    assert FR.plan(hooks, spec, boxes_of(spec)).keep_whole == ("screw",)


def test_plan_unknown_family_raises(hooks, synthetic_spec):
    spec = synthetic_spec(family="lid")
    with pytest.raises(FR.FractureError, match=r"XX_Lid.*'lid'.*hooks\.plans.*\['box'\]"):
        FR.plan(hooks, spec, boxes_of(spec))


def test_plan_none_for_unfractured(hooks, synthetic_spec):
    """A block listed in `hooks.unfractured` (by name, or by its family's callable) keeps no fractures; a family whose
    every block is unfractured needs no plan."""
    spec = synthetic_spec(name="XX_Rods")
    hooks.unfractured["XX_Rods"] = "two independent rods: no fracture connection"
    assert FR.unfractured_reason(hooks, spec) == "two independent rods: no fracture connection"
    assert FR.plan(hooks, spec, boxes_of(spec)) is None
    hooks.unfractured["ladder"] = lambda s: "independent ladders" if s.variant == "Chute" else None
    chute, step = synthetic_spec(family="ladder", variant="Chute"), synthetic_spec(family="ladder", variant="Step")
    assert FR.unfractured_reason(hooks, chute) == "independent ladders" and FR.plan(hooks, chute, {}) is None
    assert FR.unfractured_reason(hooks, step) is None
    with pytest.raises(FR.FractureError, match="ladder"):
        FR.plan(hooks, step, boxes_of(step))


def test_plan_checks_role_tables(hooks, synthetic_spec):
    hooks.roles["box"] = {"glass": ("pane",)}
    spec = synthetic_spec()
    with pytest.raises(ValueError, match="may name only"):
        FR.plan(hooks, spec, boxes_of(spec))


def test_fit_hulls_errors(hooks, synthetic_spec):
    spec = synthetic_spec()
    p = toy_plan(spec, None)
    with pytest.raises(RuntimeError, match="zone Fracture_02 holds no physics box"):
        p.fit_hulls({"regular": [("half", (0.1, -0.5, -0.5), (0.5, 0.5, 0.5))]})
    with pytest.raises(RuntimeError, match="zone Fracture_02 collider is degenerate"):
        toy_plan(spec, None).fit_hulls({"regular": [("half", (0.0, -0.5, -0.5), (0.5, 0.5, 0.5))]})
    p = toy_plan(spec, None)
    p.zones[1].hull_from = "geometry"
    assert p.fit_hulls(boxes_of(spec)) == [("Fracture_01", (0.0, -0.5, -0.5), (0.5, 0.5, 0.5))]


def test_zone_index_and_zone_at(hooks, synthetic_spec):
    spec = synthetic_spec()
    p = FR.plan(hooks, spec, boxes_of(spec))
    assert p.zone_index((0.2, 0, 0)) == 0 and p.zone_index((-0.2, 0, 0)) == 1
    assert p.zone_index((0.0, 0, 0)) == 0                       # a tie goes to the key zone
    zs = p.manifest()["zones"]
    assert FR.Plan.zone_at(zs, (-0.3, 0.1, 0)) == "Fracture_02" and FR.Plan.zone_at(zs, (0, 0, 0)) == "Fracture_01"


def test_move_seam_moves_its_halfspaces(hooks, synthetic_spec):
    spec = synthetic_spec()
    p = toy_plan(spec, None)
    p.move_seam(0, 0.1)
    assert p.seams[0].o == pytest.approx((0.1, 0, 0))
    assert [z.halfspaces[0][0] for z in p.zones] == [pytest.approx((0.1, 0, 0))] * 2
    assert p.seams[0].axis() == 0 and FR.Seam((0, 0, 0), (1, 1, 0), 0, 1).axis() is None


def test_cut_mode_flags():
    p = FR.Plan(None, "box", [], [])
    assert p.cut_mode() == "bisect"
    p.edge_split, p.thin_caps = True, False
    assert p.cut_mode() == "edges, no wire-end caps"


def test_clip_box_and_polytope():
    pts = FR.clip_box((0, 0, 0), (1, 1, 1), [((0.5, 0, 0), (1, 0, 0))])
    assert min(p[0] for p in pts) == pytest.approx(0.5)
    assert FR.clip_box((0, 0, 0), (1, 1, 1), [((2, 0, 0), (1, 0, 0))]) == []
    planes = [(n, 1.0) for n in FR.DOP_AXES]
    verts = FR.polytope_vertices(planes)
    assert len(verts) == 8 and FR.polytope_volume(planes, verts) == pytest.approx(8.0)


def test_shaped_hull_cuts_an_empty_corner():
    """An L of points: the box hull's empty corner is cut away by a diagonal, within the vertex budget."""
    pts = [(x, y, z) for x in (0, 1) for y in (0, 0.2) for z in (0, 1)] + [(x, y, z) for x in (0, 0.2)
                                                                             for y in (0.2, 1) for z in (0, 1)]
    h = FR.shaped_hull(pts, (0, 0, 0), (1, 1, 1), max_verts=16)
    assert h["box_volume"] == pytest.approx(1.0) and h["volume"] < 0.9 and len(h["verts"]) <= 16
    b = FR.box_hull((0, 0, 0), (1, 2, 3))
    assert b["volume"] == 6 and len(b["verts"]) == 8 and len(b["planes"]) == 6


def test_health_shares_and_lift_small_zones():
    assert FR.health_shares({"a": 1.0, "b": 1.0}, {"a"}) == {"a": 0.6667, "b": 0.3333}
    shapes = {"a": FR.box_hull((0, 0, 0), (1, 1, 1)), "b": FR.box_hull((0, 0, 1), (1, 1, 1.002))}
    geo = {"a": ((0, 0, 0), (1, 1, 1)), "b": ((0, 0, 1), (1, 1, 1.002))}
    out = FR.lift_small_zones(shapes, set(), geo, (0, 0, 0), (1, 1, 1.1))
    assert set(out) == {"b"} and out["b"]["volume"] >= 0.01 * 1.0
    assert FR.lift_small_zones(shapes, set(), geo, (0, 0, 0), (1, 1, 1.1), exempt={"b"}) == {}
    with pytest.raises(RuntimeError, match="min_share_exempt"):
        FR.min_share_box((0, 0, 0), (1, 1, 0.001), (0, 0, 0), (1, 1, 1), need=0.5, zone="Fracture_02")


def test_max_fractures_is_keens_table():
    assert [FR.max_fractures((m, 0.1, 0.1)) for m in (0.2, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)] == \
        [1, 1, 2, 4, 5, 10, 15, 15]


def test_fracture_budget_and_lod_side():
    size = (1.0, 0.8, 0.24)
    b = FR.fracture_budget(size, 12000, 3)
    assert len(b) == 3 and all(x > 0 for x in b)
    r = FR.lod_row(size, 12000)
    if r is not None:
        assert b[0] <= int(FR.LOD_ROW_BAND[0] * r)
        assert FR.lod_side(size, 12000, int(r * 0.9)) == "below"
        assert FR.lod_side(size, 12000, int(r * 1.2)) == "above"
        assert FR.lod_side(size, 12000, int(r * 1.05)) == "ambiguous"


def test_dent_seed_is_stable_and_per_mod(hooks):
    import zlib
    assert FR.dent_seed(hooks, "XX_Box") == (zlib.crc32(b"xx_mod/XX_Box") & 0x7FFFFFFF)
    assert FR.dent_seed(hooks, "XX_Box") != FR.dent_seed(hooks, "XX_Lid") and FR.dent_seed(hooks, "XX_Box") > 0


def test_damaged_tris_x_and_slots(hooks):
    hooks.damaged_tris_x["box"] = 1.5
    assert FR.damaged_tris_x(hooks, "box") == 1.5 and FR.damaged_tris_x(hooks, "lid") == 1.2
    assert FR.damaged_slot(hooks, "EmissiveGreen") == "EmissiveOffBroken"
    assert FR.damaged_slot(hooks, "EmissiveGreen_Subpart") == "EmissiveOffBroken"
    hooks.damaged_slots["XX_Lamp"] = "EmissiveOffBroken"
    assert FR.damaged_slot(hooks, "XX_Lamp") == "EmissiveOffBroken" and FR.damaged_slot(hooks, "Rubber") == "Rubber"


def test_min_share_exempt_ids(hooks):
    hooks.min_share_exempt.add(("box", "Fracture_02"))
    hooks.min_share_exempt.add(("lid", "Fracture_01"))
    assert FR.min_share_exempt(hooks, "box") == {"Fracture_02"}


# ------------------------------------------------------------------------------------------------ stand-ins
def test_standin_is_reexported_and_validated():
    assert FR.StandIn is SI.StandIn and FR.SUBPART_SUFFIX == SI.SUBPART_SUFFIX == "_Subpart"
    s = SI.StandIn("Drawer", None, (1, 2), (0.0, 0.1, 0.0), cut=True)
    assert s.yaw == 0.0 and s.seam_splits == 4 and s.place == ()
    with pytest.raises(ValueError, match="Drawer: cut needs zone None"):
        SI.StandIn("Drawer", "Fracture_01", (1, 2), cut=True)


def test_standins_of(hooks):
    s = SI.StandIn("Fan", "Fracture_02", (1, 2))
    hooks.standins["XX_Box"] = (s,)
    assert SI.standins_of(hooks, "XX_Box") == (s,) and SI.standins_of(hooks, "XX_Lid") == ()
    assert SI.STANDIN_PROPS == ("Default", "AlwaysVisible", "SubpartConstruct")


def test_zone_ranker(hooks, synthetic_spec):
    spec = synthetic_spec()
    p = FR.plan(hooks, spec, boxes_of(spec))
    rank = SI.zone_ranker(p.zones)
    assert rank((0.3, 0, 0)) == "Fracture_01" and rank((-0.3, 0, 0)) == "Fracture_02"
    assert rank((0.3, 0, 0), {"Fracture_02"}) == "Fracture_02" and rank((0, 0, 0), set()) is None


# ------------------------------------------------------------------------------------------------ import rules
def test_collider_mesh_group_problems():
    props = {"a": {"Group": "Fracture_01", "ColliderMeshGroups": "Fracture_01|Fracture_01_Hide"},
             "b": {"Group": "Fracture_01_Hide"}, "c": {"Group": "Fracture_02", "ColliderMeshGroups": "Fracture_02"}}
    assert FRR.collider_mesh_group_problems(props, {"Fracture_01", "Fracture_02"}) == []
    props["c"]["ColliderMeshGroups"] = "Fracture_02|Gone"
    out = FRR.collider_mesh_group_problems(props, {"Fracture_01", "Fracture_02", "Fracture_03"})
    assert any("'Gone'" in p for p in out) and any("collider Fracture_03" in p for p in out)


def test_connectivity():
    a, b = ((0, 0, 0), (1, 1, 1)), ((1.05, 0, 0), (2, 1, 1))
    assert FRR.box_gap(a, b) == pytest.approx(0.05)
    assert FRR.connectivity_problems({"A": a, "B": b}) == []
    far = ((1.5, 0, 0), (2, 1, 1))
    assert "2 unconnected groups" in FRR.connectivity_problems({"A": a, "B": far})[0]
    assert "at least 2" in FRR.connectivity_problems({"A": a})[0]


def test_hull_gap_and_fill():
    cube = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
    moved = [(x + 1.3, y, z) for x, y, z in cube]
    assert FRR.hull_gap(cube, moved) == pytest.approx(0.3, abs=1e-6)
    assert FRR.hull_gap(cube, cube) == 0.0
    assert FRR.hull_connectivity_problems({"A": cube, "B": moved}) != []
    assert FRR.hull_fill(cube, cube) == pytest.approx(1.0)


def test_recognition_and_mesh_parts():
    props = {"a": {"Group": "Fracture_01", "ColliderMeshGroups": "Fracture_01"},
             "b": {"Group": "Fracture_02", "ColliderMeshGroups": "Fracture_02"}}
    assert FRR.recognition_problems(props, ["Fracture_01", "Fracture_02", "occupancy_1"], size=(1, 1, 1)) == []
    assert "plain" in FRR.recognition_problems(props, ["Fracture_01", "Fracture_02", "Box"])[0]
    meshes = {"m": {"props": {"Group": "Fracture_01"}, "poly_mat": [0, 1, 1], "materials": ["A", "B"]},
              "s": {"props": {}, "poly_mat": [0], "materials": ["BrushedMetal"]}}
    assert FRR.mesh_parts(meshes) == {"Fracture_01-A", "Fracture_01-B", "BrushedMetal"}


def test_cut_uv_density():
    v = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    meshes = {"cap": {"props": {"ConstructionMeshPreset": "Fracture"}, "verts": v, "polys": [[0, 1, 2, 3]],
                      "uv": [[(0, 0), (0.4, 0), (0.4, 0.4), (0, 0.4)]], "materials": ["FracturedMaterial01"]}}
    assert FRR.cut_uv_density(meshes) == pytest.approx(0.4)
    assert FRR.cut_uv_density({}) is None
    assert math.isclose(FR.CUT_UV_PER_M, 0.4)
