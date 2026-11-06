"""The Blender half of `se2mod look` (`se2mod blender run tests/run_blender_tests.py -- -m blender
tests/preview/test_look_blender.py`): a look set from a saved .blend, its cameras, materials and close-up."""

from __future__ import annotations

import hashlib
import json

import pytest

bpy = pytest.importorskip("bpy")

from se2modkit.preview import look, look_run, shots  # noqa: E402

pytestmark = pytest.mark.blender


def _source(tmp_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=2.5, location=(0, 0, 0))
    body = bpy.context.active_object
    body.name = "Body"
    body.data.materials.append(bpy.data.materials.new("PaintedMetalColorable"))
    bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=0.6, location=(0, 1.5, 0), rotation=(1.5708, 0, 0))
    nozzle = bpy.context.active_object
    nozzle.name = "Nozzle"
    nozzle.data.materials.append(bpy.data.materials.new("Material.012"))
    path = tmp_path / "drive.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return path


def test_look_set_from_a_saved_blend(tmp_path):
    src = _source(tmp_path)
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    mtime = src.stat().st_mtime_ns
    out = tmp_path / "look" / "XX_Drive" / "r1"
    job = look.build_job(source=src, block="XX_Drive", out_dir=out, stage="look", rnd=1,
                         views=["3q_front", "front"], set_name="hull", mount_face="-Y", close=["Nozzle", "Nozle"])
    report = look_run.run(job)
    for v in ("3q_front", "front"):
        png = out / f"XX_Drive_look_r1_{v}.png"
        assert png.is_file() and shots.load_camera(png) is not None
    assert shots.load_camera(out / "XX_Drive_look_r1_front.png")["ortho"] > 0
    assert report["unmapped_materials"] == ["Material.012"]
    assert report["close_missing"] == ["Nozle"]
    assert report["tris"] > 0 and report["seconds"] > 0
    geo = json.loads((out / "geocheck.json").read_text())
    assert geo["parts"] == 2 and any(w["check"] == "no_mount" for w in geo["warnings"])
    close = json.loads((out / "close.json").read_text())
    rect = close["XX_Drive_look_r1_3q_front_close-Nozzle"]["rect"]
    assert rect[0] < rect[2] and rect[1] < rect[3]
    assert (out / "XX_Drive_look_r1_3q_front_close-Nozzle.png").is_file()
    # the working file is read, never written
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest and src.stat().st_mtime_ns == mtime


def test_before_set_reuses_its_cameras(tmp_path):
    src = _source(tmp_path)
    r1 = tmp_path / "look" / "XX_Drive" / "r1"
    look_run.run(look.build_job(source=src, block="XX_Drive", out_dir=r1, stage="look", rnd=1, views=["top"],
                                set_name="hull"))
    r2 = tmp_path / "look" / "XX_Drive" / "r2"
    look_run.run(look.build_job(source=src, block="XX_Drive", out_dir=r2, stage="look", rnd=2, views=["top"],
                                set_name="hull", before=r1))
    assert (r2 / "XX_Drive_look_r2_top_before.png").is_file() and (r2 / "XX_Drive_look_r2_top_after.png").is_file()
    assert shots.load_camera(r2 / "XX_Drive_look_r2_top_after.png") == shots.load_camera(r1 / "XX_Drive_look_r1_top.png")
    cam = shots.load_camera(r1 / "XX_Drive_look_r1_top.png")["matrix"]
    assert cam[2][3] > 1.0   # the camera's real place above the block, not the identity of an unevaluated scene


def test_checks_and_counts_see_modifiers(tmp_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    cube = bpy.context.active_object
    cube.name = "mount_plate"
    cube.data.materials.append(bpy.data.materials.new("PaintedMetalColorable"))
    arr = cube.modifiers.new("arr", "ARRAY")
    arr.count = 3
    src = tmp_path / "arrayed.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(src), copy=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    out = tmp_path / "look" / "XX_Arr" / "r1"
    report = look_run.run(look.build_job(source=src, block="XX_Arr", out_dir=out, stage="look", rnd=1,
                                         views=["front"], set_name="hull"))
    assert report["tris"] == 36
    assert json.loads((out / "geocheck.json").read_text())["tris"] == 36


def test_render_hidden_helpers_stay_out_of_bounds_and_counts(tmp_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    body = bpy.context.active_object
    body.data.materials.append(bpy.data.materials.new("PaintedMetalColorable"))
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(20, 0, 0))
    cutter = bpy.context.active_object
    cutter.name = "cutter_far"
    cutter.hide_render = True
    src = tmp_path / "cut.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(src), copy=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    out = tmp_path / "look" / "XX_Cut" / "r1"
    report = look_run.run(look.build_job(source=src, block="XX_Cut", out_dir=out, stage="look", rnd=1,
                                         views=["front"], set_name="hull"))
    assert report["tris"] == 12
    assert max(report["half_extents"]) == pytest.approx(0.5, abs=1e-3)


def test_cutters_and_render_hidden_collections_stay_out(tmp_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    top = bpy.data.collections.new("DRIVE")
    bpy.context.scene.collection.children.link(top)
    hidden = bpy.data.collections.new("PLAN")
    top.children.link(hidden)
    hidden.hide_render = True

    def cube(name, loc, coll):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
        ob = bpy.context.active_object
        ob.name = name
        ob.data.materials.append(bpy.data.materials.new("PaintedMetalColorable"))
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        coll.objects.link(ob)
    cube("body", (0, 0, 0), top)
    cube("cutter_port", (30, 0, 0), top)          # a visible boolean cutter, by name
    cube("plan_copy", (0, 30, 0), hidden)          # inside a render-hidden collection
    src = tmp_path / "coll.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(src), copy=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    out = tmp_path / "look" / "XX_Coll" / "r1"
    report = look_run.run(look.build_job(source=src, block="XX_Coll", out_dir=out, stage="look", rnd=1,
                                         views=["front"], set_name="hull", collection="DRIVE"))
    assert report["tris"] == 12
    assert max(report["half_extents"]) == pytest.approx(0.5, abs=1e-3)


def test_a_geometry_check_error_still_writes_the_report(tmp_path, monkeypatch):
    from se2modkit.pipeline import geocheck as GC

    def broken(*a, **kw):
        raise ValueError("degenerate mesh")
    monkeypatch.setattr(GC, "run", broken)
    src = _source(tmp_path)
    out = tmp_path / "look" / "XX_Drive" / "r1"
    look_run.run(look.build_job(source=src, block="XX_Drive", out_dir=out, stage="look", rnd=1, views=["front"],
                                set_name="hull"))
    assert (out / "report.json").is_file()
    geo = json.loads((out / "geocheck.json").read_text())
    assert geo["failures"][0]["check"] == "error" and "degenerate mesh" in geo["failures"][0]["detail"]
