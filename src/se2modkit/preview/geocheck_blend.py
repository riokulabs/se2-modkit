"""Blender side of the geometry checks: turn mesh objects into `pipeline.geocheck.Part`s.

A part's job comes from its custom properties: `se2_role` (mount, strut, pin, lug, ...), `se2_axis` (the pin axis,
in the object's local frame), `se2_point` (a point on that axis, local) and `se2_links` (names, comma separated, of
parts it is meant to sit in). Without them the name prefix decides (`pin_`, `mount_`, ...). UVs are read per triangle
corner from the active UV map.
"""

from __future__ import annotations

import numpy as np

try:   # Blender only
    import bpy
    from mathutils import Matrix, Vector
except ImportError:   # pragma: no cover - outside Blender
    bpy = Matrix = Vector = None

from ..pipeline.geocheck import Part

__all__ = ["parts_from_objects"]


def _prop(ob, key):
    return ob.get(key) if key in ob.keys() else None


def parts_from_objects(objs, place=None) -> list[Part]:
    """One Part per mesh object, in world space after `place` (a 4x4 applied on top of each object's matrix)."""
    place = place if place is not None else Matrix.Identity(4)
    deps = bpy.context.evaluated_depsgraph_get()
    parts = []
    for ob in objs:
        if ob.type != "MESH" or ob.hide_render:   # cutters and planning copies never reach the game
            continue
        ev = ob.evaluated_get(deps)
        me = ev.to_mesh()
        try:
            me.calc_loop_triangles()
            m = place @ ob.matrix_world
            co = np.empty(len(me.vertices) * 3)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            R, t = np.array(m.to_3x3()), np.array(m.translation)
            verts = co @ R.T + t
            tris = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", tris)
            uvs = None
            if me.uv_layers.active is not None and len(me.loop_triangles):
                loops = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
                me.loop_triangles.foreach_get("loops", loops)
                uv = np.empty(len(me.loops) * 2)
                me.uv_layers.active.data.foreach_get("uv", uv)
                uvs = uv.reshape(-1, 2)[loops].reshape(-1, 3, 2)
        finally:
            ev.to_mesh_clear()
        axis = _prop(ob, "se2_axis")
        point = _prop(ob, "se2_point")
        links = _prop(ob, "se2_links") or ""
        parts.append(Part(
            ob.name, verts, tris.reshape(-1, 3), role=_prop(ob, "se2_role"),
            axis=tuple(R @ np.array(list(axis), float)) if axis is not None else None,
            point=tuple(np.array(list(point), float) @ R.T + t) if point is not None else None,
            links=tuple(s.strip() for s in (links.split(",") if isinstance(links, str) else links) if s.strip()),
            uvs=uvs, local=co))
    return parts
