"""The Blender half of the ship check (`se2mod blender run tests/run_blender_tests.py`): a toy block appended from a
working .blend, as a hand-modeled block's builder does, with a plate in a second collection lying in the plane of the
body's top, a render-hidden stand-in and a part hidden only in the viewport. Skipped outside Blender."""

import json

import pytest

bpy = pytest.importorskip("bpy")

from pipefix import SyntheticSpec  # noqa: E402
from se2modkit.model import objects as OB  # noqa: E402
from se2modkit.model.materials import get_mat  # noqa: E402
from se2modkit.pipeline import report as RP  # noqa: E402
from se2modkit.pipeline.runner import Runner  # noqa: E402

pytestmark = pytest.mark.blender


class Spec(SyntheticSpec):
    @property
    def cells(self):
        return tuple(max(1, round(s / 0.25)) for s in self.size)


def _box(name, lo, hi, coll):
    verts = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    me.materials.append(get_mat("PaintedMetalColorable"))
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _working_file(path):
    """Box_v1: the body; its child Box_belt: a plate whose top lies in the body's top plane, and a rib hidden in the
    viewport only; its child Plan (hidden from renders): the layout plan's stand-in plate."""
    top = bpy.data.collections.new("Box_v1")
    belt = bpy.data.collections.new("Box_belt")
    plan = bpy.data.collections.new("Plan")
    top.children.link(belt)
    top.children.link(plan)
    plan.hide_render = True
    _box("body", (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5), top)
    _box("plate", (-0.3, -0.3, 0.45), (0.3, 0.3, 0.5), belt)
    _box("rib", (0.5, -0.1, -0.4), (0.55, 0.1, 0.4), belt).hide_viewport = True
    _box("standin_plate", (-0.4, -0.4, 0.5), (0.4, 0.4, 0.52), plan)
    bpy.data.libraries.write(str(path), {top}, fake_user=True)
    objs = list(top.all_objects)
    meshes = [o.data for o in objs]
    bpy.data.batch_remove(objs)
    bpy.data.batch_remove(meshes + [top, belt, plan])


def _render_objects(c, out):
    if c.hide_render:
        return out
    out += [o for o in c.objects if o.type == "MESH" and not o.hide_render]
    for ch in c.children:
        _render_objects(ch, out)
    return out


def _builder(path):
    def build(runner, spec, render):
        with bpy.data.libraries.load(str(path), link=False) as (src, dst):
            dst.collections = ["Box_v1"]
        top = dst.collections[0]
        root = OB.new_collection(spec.name)
        lod0 = OB.new_collection(f"{spec.name}_LOD0", root)
        objs = []
        for o in _render_objects(top, []):
            me = o.data.copy()
            me.transform(o.matrix_world)
            ob = bpy.data.objects.new(f"{spec.name}_LOD0_{o.name}", me)
            lod0.objects.link(ob)
            objs.append(ob)
        drop = list(top.all_objects)
        bpy.data.batch_remove(drop + [o.data for o in drop] + list(top.children_recursive) + [top])
        boxes = {"regular": [("body", (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))], "occupancy": [], "mount": []}
        runner.built[spec.name] = {"lods": [(lod0, objs)], "collision": [], "boxes": boxes, "root": root}
        entry = RP.empty_entry(spec)
        entry["placement"] = {"cell_groups": 1}
        return entry
    return build


@pytest.fixture
def clean():
    yield
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def test_ship_check_reports_every_shipped_collection_coplanar_across_them_and_an_exploded_preview(
        hooks, clean, tmp_path, capsys):
    blend = tmp_path / "work" / "toy.blend"
    blend.parent.mkdir()
    _working_file(blend)
    spec = Spec("XX_Box", "box")
    r = Runner(hooks=hooks, builders={}, block_builders={"box": _builder(blend)}, catalogue=lambda: [spec])
    status, out = tmp_path / "status.json", tmp_path / "ship"
    assert r.main(["XX_Box", "--check-ship", f"--ship-out={out}", f"--status={status}"]) == 0
    assert "capture_sources" not in bpy.types.BlendDataLibraries.load.__qualname__     # the loader is put back
    (rep,) = json.loads(status.read_text(encoding="utf-8"))["ship"]
    assert rep["status"] == "checked", rep["error"]

    (src,) = rep["sources"]
    assert src["blend"] == str(blend) and src["collection"] == "Box_v1"
    ships = {o["path"]: o for o in src["objects"]}
    assert ships["Box_v1/body"]["ships"] and ships["Box_v1/Box_belt/plate"]["ships"]
    assert ships["Box_v1/Box_belt/rib"]["ships"] and ships["Box_v1/Box_belt/rib"]["viewport_hidden"]
    standin = ships["Box_v1/Plan/standin_plate"]
    assert not standin["ships"] and standin["why"] == "render-hidden collection"
    (lod0,) = rep["shipping"]
    assert sorted(o["name"] for o in lod0["objects"]) == ["XX_Box_LOD0_body", "XX_Box_LOD0_plate", "XX_Box_LOD0_rib"]

    rows = rep["zfight"]["coplanar"]                                     # the body (Box_v1) and the plate (Box_belt)
    assert any({row["a"].split("/")[0], row["b"].split("/")[0]} == {"XX_Box_LOD0_body", "XX_Box_LOD0_plate"}
               for row in rows)
    assert not any("standin" in row["a"] + row["b"] for row in rows)    # the stand-in never ships

    zones = {z["id"]: z for z in rep["fracture"]["zones"]}
    assert set(zones) == {"Fracture_01", "Fracture_02"} == set(rep["zone_bounds"])
    lo, hi = rep["zone_bounds"]["Fracture_01"]
    assert lo[0] == pytest.approx(0.0, abs=0.03) and hi[0] == pytest.approx(0.55, abs=1e-3)
    png = out / "XX_Box" / "exploded.png"
    assert rep["preview"] == str(png) and rep["preview_error"] is None
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    img = bpy.data.images.load(str(png))
    try:
        assert tuple(img.size) == (1600, 1000)
    finally:
        bpy.data.images.remove(img)
    assert "SE2_ShipPreview" not in bpy.data.scenes                      # the picture's scene is gone

    text = capsys.readouterr().out
    assert "FAIL XX_Box" in text and "standin_plate (render-hidden collection)" in text
    assert "rib is hidden in the viewport but ships" in text
    assert not (hooks.paths.models / "XX_Box").exists() and not hooks.paths.report.exists()


def test_loose_loaded_objects_follow_the_same_ship_rule(clean):
    from se2modkit.pipeline import ship_probe as SP
    scratch = bpy.data.collections.new("Scratch")
    body = _box("body", (0, 0, 0), (1, 1, 1), scratch)
    hidden = _box("hidden", (0, 0, 0), (1, 1, 1), scratch)
    hidden.hide_render = True
    hidden.hide_viewport = True
    empty = bpy.data.objects.new("anchor", None)
    sink = SP.Sources()
    sink.record("/work/box.blend", type("Dst", (), {"collections": [], "objects": [body, hidden, empty]})())
    (entry,) = sink.report()
    assert entry["collection"] == "(objects)"
    got = {o["name"]: (o["ships"], o["why"], o["viewport_hidden"], o["path"]) for o in entry["objects"]}
    assert got == {"body": (True, None, False, "body"), "hidden": (False, "render-hidden", True, "hidden"),
                   "anchor": (False, "not geometry (EMPTY)", False, "anchor")}
    bpy.data.objects.remove(empty)
