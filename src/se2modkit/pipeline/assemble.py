"""Generic block assembly inside Blender: Parts -> objects per LOD (the LOD count fitted to Keen's checker), limits,
collision hull objects and the placement report.

`box_verts` is plain Python; the rest runs in Blender (bpy is imported when a function needs it).
"""

from __future__ import annotations

__all__ = ["box_verts", "collision_objects", "fit_lods", "lod_report", "make_lods", "measured_size", "placement",
           "render_bounds"]


def box_verts(lo, hi):
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def make_lods(spec, builder, n_lods, parent=None):
    """[(collection, [objects])] for LOD0..n-1, each LOD in its own collection '<name>_LOD<i>'; builder(spec, lod) ->
    [model.kit Part]."""
    from ..model import objects as OB
    out = []
    for lod in range(n_lods):
        coll = OB.new_collection(f"{spec.name}_LOD{lod}", parent)
        objs = [OB.part_to_object(p, coll, f"{spec.name}_LOD{lod}_{p.name}") for p in builder(spec, lod)]
        out.append((coll, objs))
    return out


def measured_size(objs):
    from ..model import objects as OB
    lo, hi = OB.bounds(objs)
    return tuple(round(h - l, 4) for l, h in zip(lo, hi))


def render_bounds(objs):
    """LOD0 render-mesh AABB in the Blender frame (centred block, +Y front), 6 decimals: [[x0,y0,z0],[x1,y1,z1]]."""
    lo, hi = [9.0] * 3, [-9.0] * 3
    for o in objs:
        if o.type != "MESH":
            continue
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            lo = [min(a, b) for a, b in zip(lo, w)]
            hi = [max(a, b) for a, b in zip(hi, w)]
    return [[round(c, 6) for c in lo], [round(c, 6) for c in hi]]


def fit_lods(spec, builder, module, parent=None):
    """make_lods with the LOD count Keen's checker derives from the built LOD0's bounding box (not the cell box):
    builds once, measures LOD0, and rebuilds with module.LOD_COUNT[name] set when the count differs. `module` is any
    object with n_lods(spec) and a LOD_COUNT dict (a family module, or the runner's LOD table)."""
    import bpy

    from ..model import limits
    from ..model import objects as OB
    lods = make_lods(spec, builder, module.n_lods(spec), parent)
    size = measured_size(lods[0][1])
    n = limits.model_limits(size, OB.stats(lods[0][1])["tris"])["optimal_lods"]
    if n != len(lods):
        for coll, objs in lods:
            for o in objs:
                bpy.data.objects.remove(o)
            bpy.data.collections.remove(coll)
        module.LOD_COUNT[spec.name] = n
        lods = make_lods(spec, builder, n, parent)
    return lods


def lod_report(spec, lods):
    """([per-LOD row: tris, max_tris, materials, max_materials, material_names, area_pct], Keen's limits)."""
    from ..model import limits
    from ..model import objects as OB
    size = measured_size(lods[0][1])   # the checker's key: LOD0 mesh bounds, not the cell box
    st = [OB.stats(objs) for _, objs in lods]
    lim = limits.model_limits(size, st[0]["tris"])
    rows = []
    for i, s in enumerate(st):
        li = lim["lods"][i] if i < len(lim["lods"]) else {"max_tris": 0, "max_materials": 0}
        rows.append({"tris": s["tris"], "max_tris": li["max_tris"], "materials": len(s["materials"]),
                     "max_materials": li["max_materials"], "material_names": s["materials"],
                     "area_pct": s["area_pct"]})
    return rows, lim


def collision_objects(spec, boxes, coll):
    """Blender objects for the collision boxes (regular = '<name>_collision_NN', hints prefixed occupancy /
    mountpoints as the SDK importer expects); returned for previews and a later glTF export."""
    import bpy
    out = []
    for kind, prefix in (("regular", ""), ("occupancy", "occupancy"), ("mount", "mountpoints")):
        for i, (nm, lo, hi) in enumerate(boxes[kind], 1):
            me = bpy.data.meshes.new(f"{prefix}{spec.name}_{nm}")
            v = box_verts(lo, hi)
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            me.from_pydata(v, [], faces)
            ob = bpy.data.objects.new(me.name, me)
            ob.display_type = "WIRE"
            coll.objects.link(ob)
            out.append((kind, ob))
    return out


def placement(spec, lod0_objs, boxes, bounds):
    """model.placement on the LOD0 render mesh + the collision boxes (glTF frame, per-axis grid shift); bounds = the
    block's cell bounds ((lo), (hi)). Returns (the report entry, the raw analysis)."""
    import numpy as np

    from ..model import placement as PL
    b = bounds
    tris = PL.tris_from_objects(lod0_objs)

    def hulls(kind):
        return [(nm, np.array([PL.blender_to_gltf(v) for v in box_verts(lo, hi)])) for nm, lo, hi in boxes[kind]]
    r = PL.analyse(tris, hulls("regular"), hulls("occupancy"), hulls("mount"), bounds=b, shift="bounds")
    rep = {"occupied": r["occupied_in_box"], "total": r["box_cells"],
           "faces": {d: {"groups": f["groups"], "coverage": round(f["box_face_coverage_pct"] / 100.0, 4),
                         # groups of the mount cells on the box plane only (inner steps excluded)
                         "plane_groups": len(PL.build_groups(sorted(set(map(tuple, f["mount_cell_list"]))
                                                                    - set(map(tuple, f["inner_mount_cells"])))))}
                     for d, f in r["faces"].items()},
           "free_cells": [list(c) for c in r["free_cells"]],
           "inner_mounts": {d: [list(c) for c in f["inner_mount_cells"]] for d, f in r["faces"].items()},
           "outside": r["occupied_outside_box"], "shift": r["shift"], "summary": PL.summary_line(spec.name, r)}
    return rep, r
