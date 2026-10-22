"""Subpart stand-ins: a parked moving part baked into the Fractured (and, dented with its zone, the Damaged) model,
never the NonFractured, as vanilla Gyroscope250 does (17 `_Subpart` parts in its Fractured model, 16 in the Damaged,
0 in the NonFractured).

The subpart's exported LOD `src_lods[i]` goes into Fractured LOD i at `at` (its pivot in the block's Blender frame, in
the closed / parked pose), every slot renamed `<Mat>_Subpart`, Group = the zone holding each face's centroid (zone
None: one subpart spans the zones it sits in, as vanilla Gyroscope50 / O2H2Generator250) or the explicit zone
(zone_Hide for a hide material), props Default / AlwaysVisible / SubpartConstruct. The ConditionalSubParts list them
in MeshPartsToHideWhenActive: shown while the block is not Functional (under construction, broken), hidden when the
real subparts spawn. A mod lists its stand-ins per block name in `hooks.standins`.

An auto-zoned stand-in (zone None) on a block of several zones keeps no face across a dead neighbour in one of two
ways: `cut=True` cuts it along the plan's seams and closes each surviving piece with break caps (`cut_standin`), else
the zone-crossing edges are halved `seam_splits` times so the centroid split follows the seam within a few cm (no
break faces).

`StandIn`, `standins_of` and `zone_ranker` are plain Python; `standin_mesh`, `cut_standin`, `merged`,
`compact_materials` and `remove_objects` run in Blender (bpy / bmesh are imported when they are called).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..defs.material_guids import SUBPART_SUFFIX

__all__ = ["STANDIN_PART", "STANDIN_PROPS", "SUBPART_SUFFIX", "StandIn", "compact_materials", "cut_standin",
           "merged", "remove_objects", "standin_mesh", "standins_of", "zone_ranker"]

STANDIN_PROPS = ("Default", "AlwaysVisible", "SubpartConstruct")
STANDIN_PART = "standin"      # the part name a cut stand-in is cut under (in no keep_whole list: every island cuts)


@dataclass(frozen=True)
class StandIn:
    subpart: str                    # the subpart's name (export.json "subparts" key)
    zone: str | None                # None: its faces split by centroid over the zones; else all in this zone
    src_lods: tuple                 # (subpart LOD for FR LOD0, for FR LOD1, ...)
    at: tuple = (0.0, 0.0, 0.0)     # the pivot in the block's Blender frame, closed pose
    yaw: float = 0.0                # turned about Blender +z (game +Y) at the pivot, degrees
    cut: bool = False               # zone None: cut along the plan's seams, each piece closed by break caps
    seam_splits: int = 4            # zone None, not cut: halvings of the zone-crossing edges
    # one copy of the subpart per 4x4 block matrix (row-major nested tuples), merged into ONE stand-in (one mesh part
    # per zone and material for all of them); non-empty: `at` and `yaw` are ignored
    place: tuple = ()

    def __post_init__(self):
        if self.cut and self.zone is not None:
            raise ValueError(f"StandIn {self.subpart}: cut needs zone None (an explicit zone takes it whole)")


def standins_of(hooks, name: str) -> tuple:
    """The stand-ins of block `name` (`hooks.standins`), () without."""
    return tuple(hooks.standins.get(name, ()))


def zone_ranker(zones):
    """f(point, allowed=None) -> the id of the zone holding point (the least violated half spaces, ties to the earlier
    zone: Plan.zone_index's rule) among `allowed` ids (None: all); None when none is allowed. zones: fracture.Zone."""
    zones = list(zones)

    def rank(c, allowed=None):
        cand = [(k, z) for k, z in enumerate(zones) if allowed is None or z.id in allowed]
        if not cand:
            return None
        return min(cand, key=lambda e: (round(e[1].violation(tuple(c)), 12), e[0]))[1].id
    return rank


# ------------------------------------------------------------------------------------------------ Blender
def compact_materials(ob):
    """Drop unused material slots (the FBX would export them and the .model would list them)."""
    me = ob.data
    used = sorted({p.material_index for p in me.polygons})
    mats = [me.materials[i] for i in used]
    remap = {old: new for new, old in enumerate(used)}
    idx = [remap[p.material_index] for p in me.polygons]
    me.materials.clear()
    for m in mats:
        me.materials.append(m)
    for p, i in zip(me.polygons, idx):
        p.material_index = i


def _dup(objs, coll):
    out = []
    for o in objs:
        if o.type != "MESH" or not len(o.data.polygons):
            continue
        n = o.copy()
        n.data = o.data.copy()
        n.matrix_world = o.matrix_world.copy()
        for k in list(n.keys()):
            del n[k]
        coll.objects.link(n)
        out.append(n)
    return out


def merged(objs, name, coll):
    """One object from copies of objs (world transforms baked by the join), unused slots dropped; None for none."""
    from ..model import objects as OB
    dups = _dup(objs, coll)
    if not dups:
        return None
    ob = OB.join(dups, name)
    compact_materials(ob)
    return ob


def remove_objects(objs):
    """Delete the objects (and their meshes once unused)."""
    import bpy
    for o in objs:
        if o and o.name in bpy.data.objects:
            me = o.data
            bpy.data.objects.remove(o)
            if me and me.users == 0:
                bpy.data.meshes.remove(me)


def standin_mesh(hooks, spec, s, si, i, subparts, zones, tmp, plan=None):
    """Stand-in s's subpart LOD s.src_lods[i] as one object at its closed-pose pivot (StandIn.at, turned StandIn.yaw
    about +z there), or (StandIn.place) one copy per block matrix merged into one object; and its break caps {zone
    index: object}. Auto-zoned (s.zone None) on a block of several zones: StandIn.cut cuts it along the plan's seams
    (cut_standin, with caps); otherwise every edge whose ends lie in different zones is halved StandIn.seam_splits
    times, so the faces near a seam are small and the centroid split follows it (no caps). subparts: {subpart: [(coll,
    objects)] per LOD}."""
    import bmesh
    from mathutils import Matrix
    src = subparts[s.subpart][s.src_lods[i]][1]
    if s.place:            # one copy per block matrix, joined (merged copies the copies, then they go)
        copies = []
        for k, m in enumerate(s.place):
            c = merged(src, f"{spec.name}_FR_LOD{i}_SI{si}_{s.subpart}_{k}", tmp)
            c.data.transform(Matrix([list(r) for r in m]))
            copies.append(c)
        whole = merged(copies, f"{spec.name}_FR_LOD{i}_SI{si}_{s.subpart}_all", tmp)
        remove_objects(copies)
    else:
        whole = merged(src, f"{spec.name}_FR_LOD{i}_SI{si}_{s.subpart}_all", tmp)
        whole.data.transform(Matrix.Translation(s.at) @ Matrix.Rotation(math.radians(s.yaw), 4, "Z"))
    if s.zone is not None or len(zones) <= 1:
        return whole, {}
    if s.cut:
        return whole, cut_standin(hooks, whole, plan, i, tmp)
    rank = zone_ranker(zones)
    bm = bmesh.new()
    bm.from_mesh(whole.data)
    for _ in range(s.seam_splits):
        es = [e for e in bm.edges if rank(e.verts[0].co) != rank(e.verts[1].co)]
        if not es:
            break
        bmesh.ops.subdivide_edges(bm, edges=es, cuts=1, use_grid_fill=False)
    ng = [f for f in bm.faces if len(f.verts) > 4]
    if ng:
        bmesh.ops.triangulate(bm, faces=ng, quad_method="BEAUTY", ngon_method="BEAUTY")
    bm.to_mesh(whole.data)
    bm.free()
    return whole, {}


def cut_standin(hooks, whole, plan, lod, tmp):
    """Cut a stand-in in place along the plan's seams as the block's own LOD is (fracture_mesh.cut_object: bisected,
    never the edge-split shortcut; straight, not ragged: a jagged edge face lying on a seam plane would be lifted into
    the next zone) and return its break caps {zone index: object} (fracture_mesh.build_caps, every closed
    cross-section, thin ones too), so the piece left in a live zone is closed where its dead neighbour's piece was."""
    from . import fracture_mesh as FM
    if plan is None:
        raise RuntimeError(f"{whole.name}: a cut stand-in needs the block's fracture plan")
    saved = plan.edge_split
    plan.edge_split = False
    try:
        FM.cut_object(hooks, whole, plan, STANDIN_PART, 1, lod, ragged=False)
        caps = FM.build_caps(whole, plan, tmp, whole.name, thin_caps=True)
    finally:
        plan.edge_split = saved
    for o in [whole] + list(caps.values()):
        FM.drop_degenerate(o)
    return caps
