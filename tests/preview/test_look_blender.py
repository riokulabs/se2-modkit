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
                         views=["3q_front", "front"], set_name="hull", mount_face="-Y", close=["Nozzle"])
    report = look_run.run(job)
    for v in ("3q_front", "front"):
        png = out / f"XX_Drive_look_r1_{v}.png"
        assert png.is_file() and shots.load_camera(png) is not None
    assert shots.load_camera(out / "XX_Drive_look_r1_front.png")["ortho"] > 0
    assert report["unmapped_materials"] == ["Material.012"]
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
