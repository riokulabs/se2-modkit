"""`pipeline.fracture_mesh` and the Blender half of `pipeline.standins`, inside Blender (`se2mod blender run
tests/run_blender_tests.py`): cut a cube on the toy two-zone plan, cap it, dent it, build a scaffold and bake a
stand-in. Skipped outside Blender."""

import pytest

bpy = pytest.importorskip("bpy")

import bmesh  # noqa: E402

from pipefix import boxes_of  # noqa: E402
from se2modkit.model import objects as OB  # noqa: E402
from se2modkit.model.materials import get_mat  # noqa: E402
from se2modkit.pipeline import fracture as FR  # noqa: E402
from se2modkit.pipeline import fracture_mesh as FM  # noqa: E402
from se2modkit.pipeline import scaffold as SC  # noqa: E402
from se2modkit.pipeline import standins as SI  # noqa: E402

pytestmark = pytest.mark.blender


@pytest.fixture
def coll():
    c = OB.new_collection("pipeline_test")
    yield c
    for o in list(c.objects):
        bpy.data.objects.remove(o)
    bpy.data.collections.remove(c)


def _cube(name, coll, size=1.0, cuts=0, mat="PaintedMetalColorable"):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    if cuts:
        bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(get_mat(mat))
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _plan(hooks, spec):
    return FR.plan(hooks, spec, boxes_of(spec))


def _zones(ob):
    return {d.value for d in ob.data.attributes[FM.ZONE].data}


def test_cut_lod_splits_and_caps_a_cube(hooks, synthetic_spec, coll):
    spec = synthetic_spec()
    plan = _plan(hooks, spec)
    cube = _cube(f"{spec.name}_LOD0_body", coll)
    FM.snap_seams(hooks, plan, [cube], f"{spec.name}_LOD0_")
    assert plan.seams[0].jag > 0 and plan.seams[0].reach > plan.seams[0].jag
    whole, caps = FM.cut_lod(hooks, [cube], plan, 0, coll, f"{spec.name}_LOD0_", "cut0")
    assert _zones(whole) == {1, 2}
    assert sorted(caps) == [0, 1] and list(whole[FM.CAPS_PROP]) == [2, 0]
    assert all(c.data.materials[0].name == FR.CUT_MATERIAL for c in caps.values())
    pts = FM.zone_points(whole, caps, 2)
    assert min(p[0] for p in pts[0]) > -0.05 and max(p[0] for p in pts[1]) < 0.05    # the break stays near x = 0
    assert FM.lod_tris((whole, caps)) == FM.tris(whole) + sum(FM.tris(c) for c in caps.values())


def test_cut_roles_and_core_parts(hooks, synthetic_spec, coll):
    """A support part keeps its role on every face; a core part's faces become break faces in its zone's caps."""
    spec = synthetic_spec()
    hooks.roles["box"] = {"support": ("mount",)}
    hooks.core_parts["box"] = ("core",)
    plan = _plan(hooks, spec)
    mount = _cube(f"{spec.name}_LOD0_mount", coll)
    core = _cube(f"{spec.name}_LOD0_core", coll, size=0.6)
    whole, caps = FM.cut_lod(hooks, [mount, core], plan, 1, coll, f"{spec.name}_LOD0_", "cut1")
    support = FR.ROLE_IDS.index("support") + 1
    assert {d.value for d in whole.data.attributes[FM.ROLE].data} == {support}
    names = [m.name.split(".")[0] if m else "" for m in whole.data.materials]
    assert all(names[f.material_index] != FR.CUT_MATERIAL for f in whole.data.polygons)   # moved to the caps
    assert sum(len(c.data.polygons) for c in caps.values()) > 4


def test_strip_attributes(hooks, synthetic_spec, coll):
    spec = synthetic_spec()
    plan = _plan(hooks, spec)
    whole, caps = FM.cut_lod(hooks, [_cube(f"{spec.name}_LOD0_body", coll)], plan, 0, coll, "", "cut2")
    FM.strip_attributes(whole)
    assert FM.CAPS_PROP not in whole.keys()
    assert not any(whole.data.attributes.get(n) for n in (FM.ZONE, FM.ISLAND, FM.SEAM, FM.CUTISL, FM.ROLE))


def test_dent_is_seeded_and_pinned(hooks, coll):
    lo, hi = (-0.6, -0.6, -0.6), (0.6, 0.6, 0.6)
    a, b = _cube("dent_a", coll, cuts=3), _cube("dent_b", coll, cuts=3)
    FM.densify([a], 0.1, 2000)
    FM.densify([b], 0.1, 2000)
    seed = FR.dent_seed(hooks, "XX_Box")
    st = FM.dent(hooks, [a], "box", lo, hi, seed)
    FM.dent(hooks, [b], "box", lo, hi, seed)
    assert st["max_mm"] > 0
    assert [tuple(v.co) for v in a.data.vertices] == [tuple(v.co) for v in b.data.vertices]
    flat = _cube("dent_c", coll)          # every vertex on the bounding box: pinned
    assert FM.dent(hooks, [flat], "box", (-0.5,) * 3, (0.5,) * 3, seed)["max_mm"] == 0


def test_scaffold_object(hooks, coll):
    zones = [{"lo": (-0.5, -0.4, -0.12), "hi": (0.5, 0.4, 0.12)}]
    sp = SC.plan(hooks, "box", zones, zones[0]["lo"], zones[0]["hi"], (1.0, 0.8, 0.24), [12000, 2000, 12],
                 [20950, 3500, 55])
    ob = FM.scaffold_object(sp, 0, coll, "scaffold0")
    assert ob[FM.SCAFFOLD_PROP] == 1 and ob["ConstructionMeshType"] == "Scaffold"
    assert FM.tris(ob) == sp.tris[0]
    assert FM.scaffold_object(sp, 3, coll, "scaffold3") is None


def test_standin_mesh_splits_or_cuts(hooks, synthetic_spec, coll):
    spec = synthetic_spec()
    plan = _plan(hooks, spec)
    sub = _cube("Fan_LOD0", coll, size=0.4)
    subparts = {"Fan": [(coll, [sub])]}
    split = SI.StandIn("Fan", None, (0,), at=(0.1, 0.0, 0.0))
    whole, caps = SI.standin_mesh(hooks, spec, split, 0, 0, subparts, plan.zones, coll, plan)
    assert caps == {} and len(whole.data.polygons) > 6
    xs = [v.co.x for v in whole.data.vertices]
    assert min(xs) == pytest.approx(-0.1) and max(xs) == pytest.approx(0.3)
    cut = SI.StandIn("Fan", None, (0,), at=(0.1, 0.0, 0.0), cut=True)
    whole, caps = SI.standin_mesh(hooks, spec, cut, 1, 0, subparts, plan.zones, coll, plan)
    assert sorted(caps) == [0, 1]
    whole, caps = SI.standin_mesh(hooks, spec, SI.StandIn("Fan", "Fracture_01", (0,)), 2, 0, subparts, plan.zones,
                                  coll, plan)
    assert caps == {} and len(whole.data.polygons) == 6
