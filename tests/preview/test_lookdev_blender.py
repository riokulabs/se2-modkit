"""The Blender half of the look-dev scene (`se2mod blender run tests/run_blender_tests.py -- -m blender
tests/preview/test_lookdev_blender.py`): the hull set with SDK armor, the scale figure, and one eye-view render."""

from __future__ import annotations

from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

from mathutils import Vector  # noqa: E402

from se2modkit.preview import base, lookdev  # noqa: E402

pytestmark = pytest.mark.blender


def _armor_dir() -> Path:
    from se2modkit.env.core import discover_env
    try:
        assets = discover_env().sdk_assets
    except Exception:
        assets = None
    d = Path(assets) / "Armors" if assets else None
    if not d or not (d / lookdev.HULL_PLATE).is_file():
        pytest.skip("no Mod SDK armor found (set SE2_SDK_ASSETS)")
    return d


def test_hull_set_builds_with_sdk_armor_and_figure(tmp_path):
    armor = _armor_dir()
    old = base.settings()
    try:
        base.configure(tmp_path, fast=True)
        scene = lookdev.build_set("hull", (1.25, 1.25, 1.25), name="t_hull", armor_dir=armor)
        names = {o.name for o in scene.shot.sc.collection.all_objects}
        assert any(n.startswith("i_ld_hull") for n in names)
        assert any(n.startswith("i_pv_human") for n in names)
        assert scene.used_armor
        bpy.context.view_layer.update()
        tops = []
        for e in scene.shot.sc.collection.objects:
            if e.instance_type == "COLLECTION" and e.instance_collection.name == "ld_hull_plate":
                for o in e.instance_collection.objects:
                    if o.type == "MESH":
                        m = e.matrix_world @ o.matrix_world
                        tops.append(max((m @ Vector(b)).z for b in o.bound_box))
        assert tops and max(tops) == pytest.approx(0.0, abs=1e-4)      # the deck's top is z = 0
        bpy.ops.mesh.primitive_cube_add(size=2.5, location=(0, 0, 1.25))
        cube = bpy.context.active_object
        for c in list(cube.users_collection):
            c.objects.unlink(cube)
        scene.shot.link(cube)
        loc, target, lens, ortho = lookdev.viewpoint("3q_front", (1.25, 1.25, 1.25), center=(0, 0, 1.25))
        scene.shot.camera(loc, target, lens, ortho=ortho)
        path = scene.shot.render()
    finally:
        base.configure(old["out"] or tmp_path, fast=old["fast"], only=old["only"])
    assert path.is_file() and path.stat().st_size > 1000
