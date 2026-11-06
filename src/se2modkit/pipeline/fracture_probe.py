"""The Blender half of the fracture check (`fracture_check`): on a block as built, before any export, plan its
fracture, cut LOD0 and fit the zone colliders exactly as the exporter does (`fracture_mesh.plan_for`, `cut_lod`,
`export.shape_hulls`), then report each zone's health share, where each seam landed, and LOD0's loose geometry. No
FBX is written and no VRAGE Tools are needed, so it runs headless in seconds where an export takes many minutes.

The cut is the exporter's first try (bisect, wire-end caps); a light block whose cut LOD0 lands near Keen's LOD-count
row may be exported with a cheaper cut, which moves the shares by a fraction of a percent at most. Blender only.
"""

from __future__ import annotations

import bpy

from . import export as XP
from . import fracture as FR
from . import fracture_mesh as FM

__all__ = ["NEAR", "check_block", "loose_geometry", "seam_clearance"]

NEAR = 0.1     # m: only vertices this close to a seam are tested for its zones (zone_index is slow on dense meshes)


def loose_geometry(objs, lod_prefix):
    """{part: {"verts", "edges"}} of the mesh objects that have vertices or edges belonging to no face."""
    out = {}
    for ob in objs:
        if ob.type != "MESH":
            continue
        me = ob.data
        used, fedges = set(), set()
        for p in me.polygons:
            used.update(p.vertices)
            fedges.update(p.edge_keys)
        v = sum(1 for i in range(len(me.vertices)) if i not in used)
        e = sum(1 for ed in me.edges if ed.key not in fedges)
        if v or e:
            out[FM.part_name(ob, lod_prefix)] = {"verts": v, "edges": e}
    return out


def seam_clearance(plan, objs, lod_prefix):
    """[m per seam]: the distance from each seam's plane to the nearest LOD0 vertex in one of its zones (cuttable
    parts only: a keep-whole part goes whole to one side); NEAR when none is that close."""
    pts = []
    for ob in objs:
        if ob.type != "MESH" or FM.part_name(ob, lod_prefix) in plan.keep_whole:
            continue
        mw = ob.matrix_world
        pts += [tuple(mw @ v.co) for v in ob.data.vertices]
    out = []
    for s in plan.seams:
        best = NEAR
        for p in pts:
            d = abs(FM._d(p, s))
            if d < best and plan.zone_index(p) in s.A + s.B:
                best = d
        out.append(best)
    return out


def _remove(objs):
    for o in objs:
        if o.name in bpy.data.objects:
            bpy.data.objects.remove(o)


def check_block(hooks, spec, lods, boxes, coll, cell_groups=None, on_cut=None) -> dict:
    """The block's fracture check report (the format in `fracture_check`). lods: [(collection, objects)] as built;
    boxes: its collision dict; coll: a scratch collection (left empty). on_cut(plan, whole, caps, shares) is called
    with the cut LOD0 and the zone shares before the cut is removed (the ship check's preview)."""
    pre = f"{spec.name}_LOD0_"
    objs = lods[0][1]
    rep = {"block": spec.name, "family": spec.family, "status": "checked", "error": None, "cell_groups": cell_groups,
           "tris": None, "loose": loose_geometry(objs, pre), "zones": [], "seams": []}
    try:
        nominal = FR.plan(hooks, spec, boxes)
    except (FR.FractureError, RuntimeError) as e:
        rep.update(status="error", error=str(e))
        return rep
    if nominal is None:
        rep.update(status="unfractured", error=FR.unfractured_reason(hooks, spec))
        return rep
    made = []
    try:
        plan = FM.plan_for(hooks, spec, lods, boxes)
        whole, caps = FM.cut_lod(hooks, objs, plan, 0, coll, pre, f"{spec.name}_FRCHECK_LOD0")
        made = [whole, *caps.values()]
        rep["tris"] = FM.lod_tris((whole, caps))
        XP.shape_hulls(hooks, spec, plan, (whole, caps), XP._size([o for o in objs if o.type == "MESH"]))
        share = FR.health_shares({z.id: z.shape["volume"] for z in plan.zones}, set(plan.critical()))
        if on_cut is not None:
            on_cut(plan, whole, caps, share)
    except (FR.FractureError, RuntimeError) as e:
        rep.update(status="error", error=str(e))
        return rep
    finally:
        _remove(made)
    rep["zones"] = [{"id": z.id, "label": z.label, "critical": z.critical, "share": share[z.id],
                     "lifted": bool(getattr(z, "min_share", False))} for z in plan.zones]
    clear = seam_clearance(plan, objs, pre)
    for s, s0, c in zip(plan.seams, nominal.seams, clear):
        moved = sum((a - b) * n for a, b, n in zip(s.o, s0.o, s.n))
        rep["seams"].append({"zones": [plan.zones[s.a].id, plan.zones[s.b].id], "snap_mm": round(s.snap * 1000, 1),
                             "moved_mm": round(moved * 1000, 1), "clear_mm": round(c * 1000, 1)})
    return rep
