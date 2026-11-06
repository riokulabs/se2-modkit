"""`se2modkit.describe` inside Blender (`se2mod blender run tests/run_blender_tests.py`): a toy block built in memory
with a lid on its body, a pin buried in it, a floating bolt, a mirrored pair of struts 3 mm off, a corner block whose
box gap (20 mm) is not its real gap (28.3 mm, edge to edge), a collider and a render-hidden stand-in. Skipped outside
Blender."""

import json
import math

import pytest

bpy = pytest.importorskip("bpy")

from se2modkit import blender_live as BL  # noqa: E402
from se2modkit import describe as D  # noqa: E402
from se2modkit import describe_job as J  # noqa: E402

pytestmark = pytest.mark.blender


def _box(name, lo, hi, coll):
    verts = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


@pytest.fixture
def block():
    bpy.ops.wm.read_factory_settings(use_empty=True)   # an earlier test's "Body" would make this one "Body.001"
    top = bpy.data.collections.new("Block")
    plan = bpy.data.collections.new("Plan")
    bpy.context.scene.collection.children.link(top)
    top.children.link(plan)
    plan.hide_render = True
    _box("Body", (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5), top)
    _box("Lid", (-0.3, -0.3, 0.5), (0.3, 0.3, 0.6), top)
    _box("Pin", (-0.1, -0.1, -0.1), (0.1, 0.1, 0.1), top)
    _box("Bolt", (2.0, 0.0, 0.0), (2.01, 0.01, 0.01), top)
    _box("Strut_L", (-0.9, -0.05, -0.05), (-0.7, 0.05, 0.05), top)
    _box("Strut_R", (0.703, -0.05, -0.05), (0.903, 0.05, 0.05), top)
    _box("Corner", (0.52, 0.52, -0.1), (0.72, 0.72, 0.1), top)
    _box("UCX_Body", (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5), top)
    _box("Standin", (-0.6, -0.6, 0.6), (0.6, 0.6, 0.7), plan)
    yield top
    objs = list(top.all_objects)
    meshes = [o.data for o in objs]
    bpy.data.batch_remove(objs)
    bpy.data.batch_remove(meshes + [plan, top])


TARGETS = D.parse_targets('```targets\n[[target]]\nname = "lid to body"\na = "Lid.x"\nb = "Body.x"\nvalue = 0.6\n'
                          'tol = 0.01\n\n[[target]]\nname = "ghost"\na = "Ghost*.z"\nvalue = 1\ntol = 1\n```\n')


def test_describe_scene_reads_the_block(block):
    meshes, objects = len(bpy.data.meshes), len(bpy.data.objects)
    rep = D.describe_scene(["Block"], envelope=[2.5, 2.5, 2.5], targets=TARGETS)
    assert (len(bpy.data.meshes), len(bpy.data.objects)) == (meshes, objects)       # nothing left behind
    by = {p["name"]: p for p in rep["parts"]}
    assert set(by) == {"Body", "Bolt", "Corner", "Lid", "Pin", "Strut_L", "Strut_R", "UCX_Body"}  # no stand-in
    assert [p["name"] for p in rep["parts"]] == sorted(by, key=str.lower)
    assert by["UCX_Body"]["role"] == "collider" and by["Body"]["role"] == "hull"
    assert by["Lid"]["size"] == pytest.approx([600, 600, 100], abs=1e-3)
    assert by["Lid"]["center"] == pytest.approx([0, 0, 550], abs=1e-3) and by["Lid"]["cell"][2] == pytest.approx(2.2)
    assert by["Body"]["id"] in by["Lid"]["contacts"] and by["Lid"]["nearest"]["gap_mm"] == pytest.approx(0, abs=0.01)
    assert by["Corner"]["nearest"]["name"] == "Body"
    assert by["Corner"]["nearest"]["gap_mm"] == pytest.approx(20 * math.sqrt(2), abs=0.01)   # mesh, not box (20)
    assert set(rep["floating"]) == {by[n]["id"] for n in ("Bolt", "Strut_L", "Strut_R", "Corner")}
    (ov,) = rep["overlaps"]
    assert {ov["a"], ov["b"]} == {by["Body"]["id"], by["Pin"]["id"]}
    assert ov["gap_mm"] == pytest.approx(-400, abs=0.01)                           # the pin's corners, 0.4 m deep
    m = by["Strut_L"]["mirror"]
    assert m["axis"] == "x" and m["name"] == "Strut_R" and m["error_mm"] == pytest.approx(3, abs=1e-3)
    assert [(r["axis"], round(r["error_mm"], 3)) for r in rep["mirror_errors"]] == [("x", 3.0)]
    assert by["Lid"]["symmetric"] == {"axes": "xy", "error_mm": pytest.approx(0, abs=1e-6)}
    assert rep["block"]["mid_mm"] == [0, 0, 0] and rep["block"]["parts"] == 8 and rep["block"]["tris"] == 96
    assert rep["block"]["fill"]["z"] == pytest.approx(1100 / 2500 * 100)
    (lid, ghost) = rep["targets"]
    assert lid["ok"] and lid["measured"] == pytest.approx(0.6) and not ghost["ok"]
    text = "\n".join(D.text(rep))
    assert "FLOATING (4)" in text and "OVERLAP (1)" in text and "MIRROR-ERROR (1)" in text
    json.dumps(rep)                                                                 # plain data only


def test_live_entry_point_and_job(block, tmp_path):
    rep = BL.describe_scene(["Block"])
    assert rep["block"]["parts"] == 8 and rep["block"]["mid_from"] == "bbox center"
    spec, status = tmp_path / "spec.json", tmp_path / "status.json"
    spec.write_text(json.dumps({"source": "toy.blend", "collections": ["Block"], "envelope": None,
                                "targets": TARGETS}))
    assert J.main([str(spec), str(status)]) == 0
    got = json.loads(status.read_text())["describe"]
    assert got["source"] == "toy.blend" and [t["ok"] for t in got["targets"]] == [True, False]
    spec.write_text(json.dumps({"source": "toy.blend", "collections": ["Nope"], "envelope": None, "targets": []}))
    J.main([str(spec), str(status)])
    assert "no collection named Nope" in json.loads(status.read_text())["describe"]["error"]


def test_a_part_in_a_hollow_part_is_not_inside_it():
    """A core in the cavity of a closed, thick shell: its corners face the shell's inner walls 0.3 m away."""
    top = bpy.data.collections.new("Hollow")
    bpy.context.scene.collection.children.link(top)
    shell = _box("Shell", (9.0, 9.0, 9.0), (11.0, 11.0, 11.0), top)
    inner = _box("inner", (9.2, 9.2, 9.2), (10.8, 10.8, 10.8), top)
    inner.data.flip_normals()
    with bpy.context.temp_override(active_object=shell, selected_editable_objects=[shell, inner]):
        bpy.ops.object.join()
    _box("Core", (9.5, 9.5, 9.5), (10.5, 10.5, 10.5), top)
    try:
        rep = D.describe_scene(["Hollow"])
        by = {p["name"]: p for p in rep["parts"]}
        assert set(by) == {"Shell", "Core"} and not rep["overlaps"]
        assert by["Core"]["nearest"]["gap_mm"] == pytest.approx(300, abs=0.01)
    finally:
        objs = list(top.all_objects)
        meshes = [o.data for o in objs]
        bpy.data.batch_remove(objs)
        bpy.data.batch_remove([m for m in meshes if m.users == 0] + [top])
