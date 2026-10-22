"""Turning Parts into Blender objects, joining, stats."""
import math

import bmesh
import bpy
from mathutils import Vector

from .config import UV_SCALE
from .materials import get_mat


def world_box_uv(me, scale=UV_SCALE):
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        n = poly.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for li in poly.loop_indices:
            co = me.vertices[me.loops[li].vertex_index].co
            u, v = ((co.y, co.z), (co.x, co.z), (co.x, co.y))[ax]
            uv.data[li].uv = (u * scale, v * scale)


def round_side_edges(me):
    """Edge indices between two faces of the same round band (face attribute "sb_round", see geom.round_id)."""
    att = me.attributes.get("sb_round")
    if not att or att.domain != "FACE":
        return set()
    ids = [d.value for d in att.data]
    ef = {}
    for poly in me.polygons:
        for li in poly.loop_indices:
            ef.setdefault(me.loops[li].edge_index, []).append(poly.index)
    return {e for e, fs in ef.items() if len(fs) == 2 and ids[fs[0]] and ids[fs[0]] == ids[fs[1]]}


def clean_degenerate(ob):
    """Remove zero-area faces (collinear n-gons left by recess cuts and clamp-overlap bevels) and merge
    coincident vertices; realism check S5."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
    bad = [f for f in bm.faces if f.calc_area() < 1e-8]
    if bad:
        bmesh.ops.delete(bm, geom=bad, context="FACES_ONLY")
    bm.to_mesh(ob.data)
    bm.free()


def finalize_mesh(ob, sharp_deg=50, weighted=True):
    """Smooth shading as vanilla ships it: every face smooth, hard edges only above `sharp_deg`, then
    face-area-weighted custom normals (big flat faces stay flat, chamfers and round parts shade smoothly).
    Vanilla FBX: 100 % smooth faces, custom split normals, very few sharp edges."""
    me = ob.data
    for poly in me.polygons:
        poly.use_smooth = True
    me.set_sharp_from_angle(angle=math.radians(sharp_deg))
    side = round_side_edges(me)       # round parts: profile corners may be sharp, side edges never (guide 4.2)
    if side:
        sh = me.attributes.get("sharp_edge")
        if sh:
            for e in side:
                sh.data[e].value = False
    if weighted and len(me.polygons):
        mod = ob.modifiers.new("WeightedNormals", "WEIGHTED_NORMAL")
        mod.mode = "FACE_AREA"
        mod.weight = 50
        mod.keep_sharp = True
        apply_modifiers(ob)
        me = ob.data
    if not me.uv_layers:
        world_box_uv(me)
    elif me.uv_layers[0].name != "UVMap":
        me.uv_layers[0].name = "UVMap"


def apply_loop_normals(ob):
    """The per-loop normal hook (normals.py): when the mesh carries the corner float-vector attribute
    LOOP_NORMAL_LAYER, set its non-zero corners as custom split normals (the others keep what finalize_mesh gave
    them) and remove the attribute. No attribute: nothing changes. -> whether the hook applied."""
    import numpy as np
    from .normals import LOOP_NORMAL_LAYER, merge_loop_normals
    me = ob.data
    att = me.attributes.get(LOOP_NORMAL_LAYER)
    if att is None:
        return False
    if att.domain != "CORNER" or att.data_type != "FLOAT_VECTOR" or not len(me.loops):
        me.attributes.remove(att)
        return False
    n = len(me.loops)
    hook = np.empty(3 * n)
    att.data.foreach_get("vector", hook)
    cur = np.empty(3 * n)
    me.corner_normals.foreach_get("vector", cur)
    hook = hook.reshape(-1, 3)
    out = merge_loop_normals(cur.reshape(-1, 3), hook)
    # a smooth fan shares one normal: where two faces meet with authored normals that disagree (two strands meeting
    # in a crevice), the edge must split the fan, or Blender would average them
    has = np.linalg.norm(hook, axis=1) > 1e-6
    corner = {}
    for poly in me.polygons:
        for li in poly.loop_indices:
            corner[(poly.index, me.loops[li].vertex_index)] = li
    faces_of = {}
    for poly in me.polygons:
        for li in poly.loop_indices:
            faces_of.setdefault(me.loops[li].edge_index, []).append(poly.index)
    sharp = me.attributes.get("sharp_edge") or me.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    for e, fs in faces_of.items():
        if len(fs) != 2:
            continue
        for vi in me.edges[e].vertices:
            a, b = corner[(fs[0], vi)], corner[(fs[1], vi)]
            if has[a] and has[b] and float(out[a] @ out[b]) < SPLIT_COS:
                sharp.data[e].value = True
                break
    me.attributes.remove(me.attributes[LOOP_NORMAL_LAYER])
    me.normals_split_custom_set([tuple(v) for v in out])
    return True


SPLIT_COS = 0.5     # authored corner normals more than 60 deg apart across an edge: the edge is sharp


def apply_modifiers(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    old = ob.data
    mats = list(old.materials)
    new = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    ob.modifiers.clear()
    ob.data = new
    if not new.materials:
        for m in mats:
            new.materials.append(m)
    bpy.data.meshes.remove(old)
    new.name = ob.name


def part_to_object(p, coll, name):
    if getattr(p, "kit_meshes", None):     # reused Keen kit part: the authored mesh, untouched (sb/kit.py)
        p.bm.free()
        me = p.merged_mesh(name)
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        for poly in me.polygons:
            poly.use_smooth = True
        if p.section:
            ob["SECTION"] = p.section
        return ob
    bm = p.bm
    if not p.decal:  # decal quads are open geometry wound to face outwards on purpose
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if me.uv_layers:  # bmesh names new UV layers "Float2"; joins need UVMap so decal UVs stay on UV0
        me.uv_layers[0].name = "UVMap"
    for m in p.mats:
        me.materials.append(get_mat(m))
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    w, seg = p.bevel
    if w > 0 and seg > 0:
        # bevel edges above 30 deg, except the side edges of round parts (bevel weight instead of an angle limit)
        side = round_side_edges(me)
        bw = me.attributes.get("bevel_weight_edge") or me.attributes.new("bevel_weight_edge", "FLOAT", "EDGE")
        me.update()
        ef = {}
        for poly in me.polygons:
            for li in poly.loop_indices:
                ef.setdefault(me.loops[li].edge_index, []).append(poly)
        lim = math.radians(30)
        for e, fs in ef.items():
            if len(fs) == 2 and e not in side and fs[0].normal.angle(fs[1].normal, 0.0) > lim:
                bw.data[e].value = 1.0
        mod = ob.modifiers.new("Bevel", "BEVEL")
        mod.width, mod.segments = w, seg
        mod.limit_method = "WEIGHT"
        mod.use_clamp_overlap = True
        mod.miter_outer = "MITER_ARC"
        apply_modifiers(ob)

    clean_degenerate(ob)
    finalize_mesh(ob, sharp_deg=getattr(p, "sharp_deg", 50))   # crisp stepped chamfers use 30
    apply_loop_normals(ob)     # opt-in: only a Part that wrote the se2_loop_normal layer (normals.py) is affected
    if p.section:
        ob["SECTION"] = p.section
    return ob


def join(objs, name):
    objs = [o for o in objs if o]
    if len(objs) == 1:
        objs[0].name = objs[0].data.name = name
        return objs[0]
    with bpy.context.temp_override(active_object=objs[0], selected_editable_objects=objs, object=objs[0]):
        bpy.ops.object.join()
    ob = objs[0]
    ob.name = ob.data.name = name
    return ob


def duplicate(ob, name, coll):
    new = ob.copy()
    new.data = ob.data.copy()
    new.name = new.data.name = name
    coll.objects.link(new)
    return new


def new_collection(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    return c


def stats(objs):
    objs = [o for o in objs if o.type == "MESH"]
    v = sum(len(o.data.vertices) for o in objs)
    t = sum(len(p.vertices) - 2 for o in objs for p in o.data.polygons)
    mats = sorted({m.name for o in objs for m in o.data.materials if m})
    area = {}
    for o in objs:
        for poly in o.data.polygons:
            if o.data.materials:
                m = o.data.materials[poly.material_index].name
                area[m] = area.get(m, 0.0) + poly.area
    total = sum(area.values()) or 1.0
    area_pct = {m: round(100 * a / total, 1) for m, a in sorted(area.items(), key=lambda kv: -kv[1])}
    return {"objects": len(objs), "verts": v, "tris": t, "materials": mats, "area_pct": area_pct,
            "sections": {o.name: o["SECTION"] for o in objs if "SECTION" in o},
            "groups": sorted({o["Group"] for o in objs if "Group" in o})}


def bounds(objs):
    lo = Vector((1e9,) * 3)
    hi = -lo
    for o in objs:
        if o.type != "MESH":
            continue
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return [round(c, 4) for c in lo], [round(c, 4) for c in hi]
