"""Blender half of the geometry checks (`se2mod blender run tests/run_blender_tests.py -- -m blender
tests/preview/test_geocheck_blender.py`): parts, tags and UVs read from objects."""

from __future__ import annotations

import pytest

bpy = pytest.importorskip("bpy")

from se2modkit.pipeline import geocheck as GC  # noqa: E402
from se2modkit.preview.geocheck_blend import parts_from_objects  # noqa: E402

pytestmark = pytest.mark.blender


def test_parts_carry_tags_and_world_positions():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))
    base = bpy.context.active_object
    base.name = "Base"
    base["se2_role"] = "mount"
    bpy.ops.mesh.primitive_cylinder_add(radius=0.1, depth=1.0, location=(0, 0, 2.5), rotation=(0, 1.5708, 0))
    pin = bpy.context.active_object
    pin.name = "pin_main"
    pin["se2_axis"] = (0.0, 0.0, 1.0)                     # local Z, which the rotation turns to world X
    pin["se2_links"] = "Base, Other"
    parts = {p.name: p for p in parts_from_objects([base, pin])}
    assert parts["Base"].role == "mount" and parts["pin_main"].role == "pin"
    assert parts["Base"].verts[:, 2].min() == pytest.approx(0.0, abs=1e-5)
    assert abs(parts["pin_main"].axis[0]) == pytest.approx(1.0, abs=1e-4)
    assert parts["pin_main"].links == ("Base", "Other")
    assert parts["Base"].uvs is not None and parts["Base"].uvs.shape == (12, 3, 2)
    report = GC.run(parts.values())
    assert any(f["check"] == "load_path" and f["parts"] == ["pin_main"] for f in report.failures) is False


def test_objects_hidden_from_render_are_not_parts():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    shown = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    cutter = bpy.context.active_object
    cutter.name = "cutter_hole"
    cutter.hide_render = True
    assert [p.name for p in parts_from_objects([shown, cutter])] == [shown.name]
