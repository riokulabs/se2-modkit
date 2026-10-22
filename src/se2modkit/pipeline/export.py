"""Export one built block to <models>/<name>/ (`hooks.paths.models`) through Keen's VRAGE Tools add-on (windowed Blender).

NonFractured/  <name>.fbx, <name>_LOD1..n.fbx, <name>_collision.gltf/.bin (regular + occupancy* + mountpoints*)
Fractured/     <name>_Fractured.fbx, _LOD1..n.fbx, <name>_Fractured_collision.gltf/.bin (one hull per fracture)
Damaged/       <name>_Damaged.fbx, _LOD1..n.fbx, <name>_Damaged_collision.gltf/.bin (every block: vanilla's
               "Deformed" model, the Fractured model without its scaffold, densified (at most
               fracture.damaged_tris_x(hooks, family) x those triangles) and dented; the unfractured blocks' too:
               their dented debris, as vanilla's non-breakable ControlPanel25 / Sensor25 have one)
export.json    the manifest the bpy-free integration step reads (files, used materials, tris, fracture zones and
               plan, collider volumes and health shares, construction mesh parts, the Damaged model)

Per LOD the measured parts are merged into ONE object (NonFractured) or
one object per fracture group (Fractured), so the mesh-part count is one per material, as in vanilla. Mesh parts are
therefore named by material slot: indicator lamps and screens are slots named exactly as the vanilla definitions'
mesh parts, so nothing has to stay a separate object. A re-export keeps
the previous bytes of every file (NonFractured, Fractured, Damaged, subpart) whose content did not change
(_restore_unchanged).

Two kinds of Fractured model:
* PLANNED (a family with a fracture plan, `hooks.plans`; fracture.py, fracture_mesh.py): the LOD is cut along the
  plan's seams, every cut cross-section closed by a vanilla FracturedMaterial01 break face
  (Fracture_NN_Cut: Group Fracture_NN, Split / HiddenUntilSecondSequenceStart / Fracture), one convex collider per
  zone shaped to the zone's cut LOD0 geometry (fracture.shaped_hull: health is the collider's volume share); when the
  cut LOD0 needs one more LOD than the NonFractured (Keen keys the count on each model's own LOD0), the builder's
  mid-detail objects (`mid`) are the extra LOD. The Damaged model is a copy, densified and dented (fracture_mesh.dent).
* UNFRACTURED (the non-breakable exception, `hooks.unfractured`): no zones, the
  Fractured model's colliders are the plain physics boxes; the Damaged model is the same copy, densified and dented,
  with a copy of those boxes (_export_unfractured_damaged).
Each face's construction role (fracture.role_of) picks its object per zone: the skin (Group
Fracture_NN), decals, stencils and lamps in Fracture_NN_Hide (AlwaysHidden while under construction, as vanilla), the
family's supports / frames in Fracture_NN_Support / _Frame, glass as its own object in Fracture_NN with the
WindowGlass preset (fracture.ROLE_PROPS). export.json records the LOD0 triangles per role (role_tris) and each zone's
per-role bounds (role_bounds).
ColliderMeshGroups lists only the groups the LOD0 has: one missing group makes the SDK importer drop EVERY fracture
(with a warning) and check the model as unfractured (8 mesh parts).

Every public function takes the mod's `ModHooks` first. Blender only (bpy at module level, like fracture_mesh).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import bmesh
import bpy

from ..defs.material_guids import SUBPART_SUFFIX, damaged_table
from ..model import export as EX
from ..model import objects as OB
from . import fracture as FR
from . import fracture_mesh as FM
from .fracture import ROLE_IDS, ROLE_SUFFIX, role_props, skin_carrier
from .standins import STANDIN_PROPS, compact_materials, merged, standin_mesh, standins_of, zone_ranker
from .standins import remove_objects as _remove

FILE_EXTS = (".fbx", ".gltf", ".bin")

__all__ = ["bake_standins", "built_specs", "export_block", "export_subparts", "fracture_zones", "keep_faces",
           "keep_zone", "lod_name", "shape_hulls", "shift", "standin_tris", "zone_of"]


def shift(spec):
    """Per-axis .model LOD0 translation (game axes): 0.125 m on even cell counts, 0 on odd ones."""
    return [0.125 if n % 2 == 0 else 0.0 for n in spec.cells]


def fracture_zones(hooks, boxes, spec=None):
    """The simple fracture of a block without a plan: [(id, lo, hi)] in the Blender frame, one zone per regular
    physics box; none for a one-box block or a non-breakable exception (`hooks.unfractured`)."""
    if len(boxes["regular"]) < 2:
        return []
    if spec is not None and FR.unfractured_reason(hooks, spec):
        return []
    return [(f"Fracture_{i:02d}", tuple(lo), tuple(hi)) for i, (_, lo, hi) in enumerate(boxes["regular"], 1)]


def _box_dist(p, lo, hi):
    return sum(max(lo[i] - p[i], 0.0, p[i] - hi[i]) ** 2 for i in range(3)) ** 0.5


def zone_of(p, zones):
    """Zone nearest to point p (Blender frame); ties go to the lower id."""
    return min(zones, key=lambda z: (_box_dist(p, z[1], z[2]), z[0]))[0]


def keep_faces(ob, keep):
    """Delete the faces for which keep(material name, world centroid) is false; None if nothing is left."""
    me = ob.data
    names = [m.name if m else "" for m in me.materials]
    mw = ob.matrix_world
    bm = bmesh.new()
    bm.from_mesh(me)
    drop = [f for f in bm.faces if not keep(names[f.material_index], mw @ f.calc_center_median())]
    if drop:
        bmesh.ops.delete(bm, geom=drop, context="FACES")
    left = len(bm.faces)
    bm.to_mesh(me)
    bm.free()
    if not left:
        bpy.data.objects.remove(ob)
        return None
    compact_materials(ob)
    return ob


def lod_name(stem, i):
    return f"{stem}.fbx" if i == 0 else f"{stem}_LOD{i}.fbx"


def _materials(ob_list):
    return sorted({m.name for o in ob_list for m in o.data.materials if m})


def _tris(ob_list):
    return sum(len(p.vertices) - 2 for o in ob_list for p in o.data.polygons)


def _world_tris(ob):
    """The object's triangles in the block frame, [(p0, p1, p2)] of xyz tuples."""
    me = ob.data
    me.calc_loop_triangles()
    m = ob.matrix_world
    return [tuple(tuple(m @ me.vertices[i].co) for i in t.vertices) for t in me.loop_triangles]


def _box_object(name, lo, hi, coll):
    from .assemble import box_verts
    old = bpy.data.objects.get(name)
    if old:
        _remove([old])
    me = bpy.data.meshes.new(name)
    me.from_pydata(box_verts(lo, hi), [], [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4),
                                          (1, 5, 7, 3)])
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    assert ob.name == name, ob.name   # glTF node name must equal the fracture Group
    return ob


def _snapshot(d, exts=FILE_EXTS):
    """{path: bytes} of a folder's exported files before they are rewritten (see _restore_unchanged)."""
    d = Path(d)
    if not d.is_dir():
        return {}
    return {str(f): f.read_bytes() for f in sorted(d.iterdir()) if f.name.endswith(exts)}


def _clear(d):
    """Delete a folder's exported files (FILE_EXTS) before the re-export."""
    d = Path(d)
    if d.is_dir():
        for f in d.iterdir():
            if f.name.endswith(FILE_EXTS):
                f.unlink()


def _gltf_key(g):
    """A collision glTF's content without Blender's datablock-name suffixes on its MESH names ("Fracture_01.047": a
    hull mesh named again in a long multi-block session; only the node names bind the colliders), so a re-export of
    one block and of every block compare alike)."""
    for m in g.get("meshes", []):
        if "name" in m:
            m["name"] = re.sub(r"\.\d{3}$", "", m["name"])
    return g


def _restore_unchanged(old):
    """Put back the previous bytes of every rewritten file whose CONTENT is unchanged (an FBX: same objects, meshes
    and connections, fracture_rules.fbx_content; a glTF / bin: same JSON / bytes), so a re-export does not churn the
    intact models' LFS objects and .meta hashes with new timestamps and uids)."""
    import tempfile

    from . import fracture_rules as FRR
    for path, data in old.items():
        if not os.path.exists(path):
            continue
        new = Path(path).read_bytes()
        if new == data:
            continue
        same = False
        if path.endswith(".fbx"):
            with tempfile.NamedTemporaryFile(suffix=".fbx", delete=False) as t:
                t.write(data)
            try:
                same = FRR.fbx_same_content(t.name, path)
            finally:
                os.remove(t.name)
        elif path.endswith(".gltf"):
            same = _gltf_key(json.loads(new)) == _gltf_key(json.loads(data))
        if same:
            with open(path, "wb") as f:
                f.write(data)


def export_subparts(hooks, spec, subparts, ops, tmp, collision=None, *, out_dir=None):
    """Animated subparts: <models>/<name>/Subparts/<sub>/<name>_<sub>[_LODn].fbx, one merged object per LOD, the
    object's origin = the pivot (no fracture, no shift). Vanilla subparts are visual only, except door leaves:
    collision = {sub: [(name, lo, hi)]} (subpart frame) exports <name>_<sub>_collision.gltf/.bin like a block's
    collision (the DoorBlockCollider compounds a leaf's model collider while the door is closed). out_dir: the models
    folder (default `hooks.paths.models`). Returns the manifest entry {sub: {"lods": [{file, materials, tris,
    objects}], "collision"?: {...}}}."""
    out = {}
    collision = collision or {}
    root = Path(out_dir or hooks.paths.models)
    for sub, lods in subparts.items():
        d = root / spec.name / "Subparts" / sub
        d.mkdir(parents=True, exist_ok=True)
        old = _snapshot(d)
        _clear(d)
        stem = f"{spec.name}_{sub}"
        rows = []
        for i, (_, objs) in enumerate(lods):
            ob = merged(objs, f"{stem}_LOD{i}_export", tmp)
            fn = lod_name(stem, i)
            EX.export_fbx([ob], str(d / fn), ops)
            rows.append({"file": fn, "materials": _materials([ob]), "tris": _tris([ob]), "objects": 1})
            _remove([ob])
        out[sub] = {"dir": f"Subparts/{sub}", "lods": rows}
        if collision.get(sub):
            hulls = [_box_object(f"{stem}_{nm}", lo, hi, tmp) for nm, lo, hi in collision[sub]]
            fn = f"{stem}_collision.gltf"
            info = EX.export_collision(hulls, str(d / fn), ops)
            out[sub]["collision"] = dict(info, file=fn, bin=f"{stem}_collision.bin",
                                         boxes=[[nm, [round(v, 5) for v in lo], [round(v, 5) for v in hi]]
                                                for nm, lo, hi in collision[sub]])
            _remove(hulls)
        _restore_unchanged(old)
    return out


def export_block(hooks, spec, lods, cobjs, boxes, ops, subparts=None, mid=None, subpart_collision=None, *,
                 out_dir=None):
    """Export one built block to <out_dir>/<name>/ (out_dir default `hooks.paths.models`) and return its manifest.
    lods: [(coll, objs)] LOD0..n-1 as built; cobjs: [(kind, ob)] collision boxes; boxes: the collision dict;
    subparts: {name: [(coll, objs)] per LOD} (doors, drawers, leaves) or None; mid: the builder's mid-detail objects,
    the extra LOD a light piece's cut Fractured / Damaged models may need; subpart_collision: {sub: [(name, lo, hi)]}
    (subpart colliders, export_subparts). ops: model.export.vrt_ops() (None: VRAGE Tools are not loaded)."""
    if ops is None:
        raise RuntimeError("VRAGE Tools are not available: run Blender windowed (se2mod blender run --windowed), "
                           "not in the background.")
    out = Path(out_dir or hooks.paths.models) / spec.name
    nf_dir, fr_dir, dm_dir = (str(out / d) for d in ("NonFractured", "Fractured", "Damaged"))
    old = _snapshot(nf_dir)
    old_frdm = {**_snapshot(fr_dir), **_snapshot(dm_dir)}   # (FR / DM too: header-only rewrites)
    for d in (nf_dir, fr_dir, dm_dir):
        _clear(d)
    for d in (nf_dir, fr_dir):
        os.makedirs(d, exist_ok=True)
    tmp = OB.new_collection(f"{spec.name}_export")
    man = {"name": spec.name, "cells": list(spec.cells), "shift": shift(spec),
           "nonfractured": {"lods": []}, "fractured": {"lods": []}}
    made = []

    # ---------------------------------------------------------------- NonFractured: one object per LOD
    for i, (_, objs) in enumerate(lods):
        ob = merged(objs, f"{spec.name}_NF_LOD{i}", tmp)
        made.append(ob)
        fn = lod_name(spec.name, i)
        EX.export_fbx([ob], os.path.join(nf_dir, fn), ops)
        man["nonfractured"]["lods"].append({"file": fn, "materials": _materials([ob]), "tris": _tris([ob]),
                                            "objects": 1})
    col = [ob for _, ob in cobjs]
    info = EX.export_collision(col, os.path.join(nf_dir, f"{spec.name}_collision.gltf"), ops)
    man["nonfractured"]["collision"] = dict(info, file=f"{spec.name}_collision.gltf", bin=f"{spec.name}_collision.bin")
    man["nonfractured"]["size"] = list(_size([o for o in lods[0][1] if o.type == "MESH"]))
    _restore_unchanged(old)

    # ---------------------------------------------------------------- Fractured (+ Damaged)
    plan = FM.plan_for(hooks, spec, lods, boxes)
    os.makedirs(dm_dir, exist_ok=True)
    if plan is None:
        hulls = _export_simple(hooks, spec, lods, boxes, ops, tmp, man, fr_dir, dm_dir, made)
    else:
        hulls = _export_planned(hooks, spec, lods, plan, ops, tmp, man, fr_dir, dm_dir, made, mid, subparts)
    _restore_unchanged(old_frdm)
    if subparts:
        man["subparts"] = export_subparts(hooks, spec, subparts, ops, tmp, subpart_collision, out_dir=out.parent)
    _remove(made + hulls)
    bpy.data.collections.remove(tmp)
    with open(out / "export.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, indent=1, sort_keys=True)
        f.write("\n")
    return man


def _size(objs):
    lo, hi = OB.bounds(objs)
    return tuple(round(h - l, 5) for l, h in zip(lo, hi))


def _construction_parts(lod_objs):
    parts = {}
    for o in lod_objs:
        g = o.get("Group")
        for m in o.data.materials:
            parts[f"{g}-{m.name}" if g else m.name] = [o.get("ConstructionMeshType", "Default"),
                                                      o.get("ConstructionMeshVisibility", "AlwaysVisible"),
                                                      o.get("ConstructionMeshPreset")]
    return parts


def _export_simple(hooks, spec, lods, boxes, ops, tmp, man, fr_dir, dm_dir, made):
    """The simple fracture (a block without a plan, or the unfractured exception, `hooks.unfractured`): one zone per
    physics box, faces split along their existing edges; an unfractured block also gets its dented Damaged model
    (_export_unfractured_damaged). Returns the collider objects."""
    zones = fracture_zones(hooks, boxes, spec)
    man["fractured"]["zones"] = [{"id": z, "lo": [round(v, 5) for v in lo], "hi": [round(v, 5) for v in hi]}
                                 for z, lo, hi in zones]
    man["fractured"]["kind"] = "simple"
    last = len(lods) - 1
    parts, fr_lods = {}, []
    for i, (_, objs) in enumerate(lods):
        lod_objs = []
        if i == last:   # the LastLOD box, no group (vanilla)
            ob = merged(objs, f"{spec.name}_FR_LOD{i}", tmp)
            lod_objs.append(ob)
        else:
            whole = merged(objs, f"{spec.name}_FR_LOD{i}_all", tmp)
            for z, _, _ in zones or [(None, None, None)]:
                pieces = {}
                for role in ROLE_IDS:   # (merged objects: no part names left, so only the material roles occur)
                    ob = whole.copy()
                    ob.data = whole.data.copy()
                    ob.name = f"{spec.name}_FR_LOD{i}_{z or 'Body'}{_role_tag(role)}"
                    tmp.objects.link(ob)
                    pieces[role] = keep_faces(ob, lambda m, c, z=z, r=role: FR.role_of(hooks, spec.family, "", m) == r
                                              and (z is None or zone_of(c, zones) == z))
                if i == 0:
                    _note_roles(man, z, pieces)
                if z is None:   # unfractured: no Group; parts are the material names (the roles are disjoint)
                    for role in ROLE_IDS:
                        if pieces[role]:
                            for k, v in role_props(role).items():
                                pieces[role][k] = v
                            lod_objs.append(pieces[role])
                    continue
                _promote(pieces, i, spec.name, z)
                _group(pieces, None, z, lod_objs)
            _remove([whole])
        made += lod_objs
        if i == 0:
            parts = _construction_parts(lod_objs)
        fn = lod_name(f"{spec.name}_Fractured", i)
        EX.export_fbx(lod_objs, os.path.join(fr_dir, fn), ops)
        fr_lods.append(lod_objs)
        man["fractured"]["lods"].append({"file": fn, "materials": _materials(lod_objs), "tris": _tris(lod_objs),
                                         "objects": len(lod_objs),
                                         "groups": sorted({o["Group"] for o in lod_objs if "Group" in o})})
    man["fractured"]["parts"] = dict(sorted(parts.items()))
    if zones:
        hulls = [_box_object(z, lo, hi, tmp) for z, lo, hi in zones]
    else:   # unfractured: the physics boxes under plain (non-fracture) names
        hulls = [_box_object(f"{spec.name}_Fractured_{nm}", lo, hi, tmp) for nm, lo, hi in boxes["regular"]]
    info = EX.export_collision(hulls, os.path.join(fr_dir, f"{spec.name}_Fractured_collision.gltf"), ops)
    man["fractured"]["collision"] = dict(info, file=f"{spec.name}_Fractured_collision.gltf",
                                         bin=f"{spec.name}_Fractured_collision.bin")
    if not zones:
        hulls += _export_unfractured_damaged(hooks, spec, lods, boxes, ops, tmp, man, fr_lods, dm_dir, made)
    elif os.path.isdir(dm_dir):
        shutil.rmtree(dm_dir)
    return hulls


def _export_unfractured_damaged(hooks, spec, lods, boxes, ops, tmp, man, fr_lods, dm_dir, made):
    """The dented debris of an unfractured, non-breakable block: its Fractured LODs copied (the same mesh parts: they
    have no scaffold), every LOD but the LastLOD box densified to at most damaged_tris_x(hooks, family) x its
    triangles (within the fractured budget) and dented with the family's `hooks.dent`, seeded by the block name; the
    colliders a copy of the Fractured model's plain physics boxes. Sets man["damaged"]; returns the collider objects."""
    fam = spec.family
    lo, hi = OB.bounds([o for o in lods[0][1] if o.type == "MESH"])
    budget = FR.fracture_budget(tuple(man["nonfractured"]["size"]), _tris(fr_lods[0]), len(fr_lods))
    dm, stats, dm_size = {"lods": []}, {}, None
    for i, lod_objs in enumerate(fr_lods):
        dm_objs = []
        for o in lod_objs:
            c = o.copy()
            c.data = o.data.copy()
            for k in o.keys():
                c[k] = o[k]
            c.name = o.name.replace("_FR_", "_DM_")
            tmp.objects.link(c)
            dm_objs.append(c)
        if i < len(fr_lods) - 1:
            cap = min(budget[i], int(FR.damaged_tris_x(hooks, fam) * _tris(lod_objs)))
            FM.densify(dm_objs, _dent(hooks, fam)["max_edge"] * (1 + i), cap)
            st = FM.dent(hooks, dm_objs, fam, lo, hi, FR.dent_seed(hooks, spec.name))
            if i == 0:
                stats = st
        if i == 0:
            dm_size = _size(dm_objs)
        made += dm_objs
        fn = lod_name(f"{spec.name}_Damaged", i)
        EX.export_fbx(dm_objs, os.path.join(dm_dir, fn), ops)
        dm["lods"].append({"file": fn, "materials": _materials(dm_objs), "tris": _tris(dm_objs),
                           "objects": len(dm_objs), "groups": []})
    dm.update(size=list(dm_size), dent=stats, slots=damaged_table(hooks), budget=budget)
    hulls = [_box_object(f"{spec.name}_Damaged_{nm}", lo_, hi_, tmp) for nm, lo_, hi_ in boxes["regular"]]
    info = EX.export_collision(hulls, os.path.join(dm_dir, f"{spec.name}_Damaged_collision.gltf"), ops)
    dm["collision"] = dict(info, file=f"{spec.name}_Damaged_collision.gltf", bin=f"{spec.name}_Damaged_collision.bin")
    man["damaged"] = dm
    return hulls


def _dent(hooks, family):
    """The family's dent parameters (`hooks.dent`), with a plain error when the mod has none."""
    try:
        return hooks.dent[family]
    except KeyError:
        raise RuntimeError(f"No dent parameters for family {family!r}: set hooks.dent[{family!r}] = dict(amp, freq, "
                           "fine, fine_freq, max_edge).") from None


def _role_tag(role):
    """Object-name tag of a role's piece: its group suffix, or _Glass (glass shares the skin's group)."""
    return ROLE_SUFFIX[role] or ("" if role == "skin" else "_" + role.capitalize())


def _promote(pieces, lod, block, z):
    """{role: object | None} of one zone at one LOD: the skin carries the zone's Group and ColliderMeshGroups, so a
    lower LOD without skin faces promotes its first present role to skin (fracture.skin_carrier); a zone without a
    LOD0 skin raises there, by name."""
    r = skin_carrier({k for k, v in pieces.items() if v}, lod, block, z)
    if r is None:
        raise RuntimeError(f"{block}: fracture zone {z} has no LOD{lod} geometry")
    if r != "skin":
        pieces["skin"], pieces[r] = pieces[r], None


def _note_roles(man, z, pieces):
    """export.json: LOD0 triangles per role (role_tris) and the zone's per-role bounds (role_bounds, Blender frame:
    [x0, y0, z0, x1, y1, z1])."""
    fr = man["fractured"]
    rt = fr.setdefault("role_tris", {r: 0 for r in ROLE_IDS})
    rb = fr.setdefault("role_bounds", {})
    for role, ob in pieces.items():
        if not ob:
            continue
        rt[role] += _tris([ob])
        if z is not None:
            lo, hi = OB.bounds([ob])
            rb.setdefault(z, {})[role] = [round(v, 5) for v in list(lo) + list(hi)]


def _split_cut(cut, tmp):
    """(visible, hidden) break faces of one zone's cap object: the faces fracture_mesh.build_caps tagged role =
    hide (the caps of a part hidden under construction) moved to their own object; either may be None."""
    ROLE = FM.ROLE
    if cut is None:
        return None, None
    attr = cut.data.attributes.get(ROLE)
    if attr is None or not any(d.value for d in attr.data):
        return cut, None
    hid = cut.copy()
    hid.data = cut.data.copy()
    hid.name = cut.name + "_Hide"
    tmp.objects.link(hid)
    out = []
    for ob, want in ((cut, False), (hid, True)):
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        rl = bm.faces.layers.int[ROLE]
        drop = [f for f in bm.faces if bool(f[rl]) != want]
        if drop:
            bmesh.ops.delete(bm, geom=drop, context="FACES")
        left = len(bm.faces)
        bm.to_mesh(ob.data)
        bm.free()
        out.append(ob if left else None)    # (an emptied cap object stays for the caller's clean-up: caps[zi])
    return tuple(out)


def _group(pieces, cut, z, lod_objs, cut_hide=None):
    """Group props of one zone's objects (pieces: {role: object | None}, after _promote), vanilla-style: each role's
    object in z + its ROLE_SUFFIX group with the role's props; the skin carries ColliderMeshGroups = z and every
    present suffixed group (only groups that exist: the importer warns about listed groups without meshes); the
    break faces are Split / Fracture in z, those of a hidden part (cut_hide, _split_cut) go with it in z_Hide,
    AlwaysHidden (into the skin's group when the hide piece was promoted to skin)."""
    present = [r for r in ROLE_IDS if pieces.get(r)]
    for role in present:
        ob = pieces[role]
        ob["Group"] = z + ROLE_SUFFIX[role]
        if role == "skin":
            ob["ColliderMeshGroups"] = "|".join(dict.fromkeys(z + ROLE_SUFFIX[r] for r in present))
        for k, v in role_props(role).items():
            ob[k] = v
        lod_objs.append(ob)
    if cut:
        cut["Group"] = z
        for k, v in FR.CUT_PROPS.items():
            cut[k] = v
        lod_objs.append(cut)
    if cut_hide:
        role = "hide" if pieces.get("hide") else "skin"
        cut_hide["Group"] = z + ROLE_SUFFIX[role]
        for k, v in role_props(role).items():
            cut_hide[k] = v
        lod_objs.append(cut_hide)


def standin_tris(hooks, spec, subparts, n_cut, zones=(), plan=None):
    """Triangles per cut FR LOD (at least 2 entries: scaffold.plan reads LOD0 and LOD1) the block's stand-ins
    (`hooks.standins`) will add, cut and caps included: what bake_standins bakes, known before the scaffold is
    planned."""
    out = [0] * max(2, n_cut)
    coll = OB.new_collection(f"{spec.name}_standin_count")
    for si, s in enumerate(standins_of(hooks, spec.name)):
        for i in range(min(len(s.src_lods), n_cut)):
            ob, caps = standin_mesh(hooks, spec, s, si, i, subparts, zones, coll, plan)
            out[i] += _tris([ob] + list(caps.values()))
            _remove([ob] + list(caps.values()))
    bpy.data.collections.remove(coll)
    return tuple(out)


def _standin_material(m):
    """The `<Mat>_Subpart` copy of material m (one per base material: the .model maps it to the base's GUID)."""
    name = m.name.split(".")[0] + SUBPART_SUFFIX
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = m.copy()
        mat.name = name
    return mat


def bake_standins(hooks, spec, fr_lods, tmp, subparts, zones=(), plan=None):
    """Bake the block's subpart stand-ins (`hooks.standins`: the parked moving parts) into its
    cut Fractured LODs (fr_lods: [[objects]] per cut LOD, the LastLOD box not yet appended); the Damaged copy keeps
    them (dented with their zone). Each is the subpart's built LOD src_lods[i] (subparts: {sub: [(coll, objs)] per
    LOD}, the very objects export_subparts writes, origin = pivot) moved to its pivot in the closed pose (StandIn.at),
    every material slot renamed `<m>_Subpart`, props Default / AlwaysVisible / SubpartConstruct (standins.standin_mesh:
    an auto-zoned one cut along the seams first, with its break caps, or split along them).
    Zones (vanilla Gyroscope50 / O2H2Generator250: one subpart's stand-ins span the zones they sit in):
    StandIn.zone None (cut) splits the faces by centroid over `zones` (fracture.Zone list: the least violated half
    spaces, Plan.zone_index's rule, as the block's own faces); an explicit zone takes them all. On a lower LOD a face
    only goes to a zone whose LOD0 has that stand-in part (else the least violated one that has), so the LOD0 part
    list is every LOD's and the staged hide list covers all. Each (zone, hide) piece is one object
    in Group zone or zone_Hide (HIDE_PREFIXES); the zone's skin lists a new group in its ColliderMeshGroups. Returns
    the manifest entry {"parts": [mesh part names], "tris": {lod: tris}, "items": [...]}, or None."""
    items = standins_of(hooks, spec.name)
    if not items:
        return None
    if not subparts:
        raise RuntimeError(f"{spec.name}: stand-ins {[s.subpart for s in items]} but no built subparts")
    zones = list(zones)
    rank = zone_ranker(zones)

    parts, tris = {}, {}         # parts: {(si, mesh part): None} of LOD0 per stand-in (lower LODs map onto them)
    for si, s in enumerate(items):
        for i, src in enumerate(s.src_lods):
            if i >= len(fr_lods):
                continue
            whole, caps = standin_mesh(hooks, spec, s, si, i, subparts, zones, tmp, plan)
            lod0 = {p for (k, p) in parts if k == si}

            def zone_of(m, c, s=s, i=i, lod0=lod0):
                if s.zone:
                    return s.zone
                if i == 0:
                    return rank(c)
                g = ("_Hide" if FR.is_hide(m) else "")
                ok = {z.id for z in zones if f"{z.id}{g}-{m}{SUBPART_SUFFIX}" in lod0}
                return rank(c, ok) or rank(c)
            me = whole.data
            names = [m.name.split(".")[0] if m else "" for m in me.materials]
            keys = {(zone_of(names[p.material_index], tuple(p.center)), FR.is_hide(names[p.material_index]))
                    for p in me.polygons}
            pieces = []
            for z, hide in sorted(keys):
                group = z + ("_Hide" if hide else "")
                ob = whole.copy()
                ob.data = whole.data.copy()
                ob.name = f"{spec.name}_FR_LOD{i}_SI{si}_{group}_{s.subpart}{SUBPART_SUFFIX}"
                tmp.objects.link(ob)
                ob = keep_faces(ob, lambda m, c, z=z, h=hide: FR.is_hide(m.split(".")[0]) == h
                                and zone_of(m.split(".")[0], tuple(c)) == z)
                pieces.append((z, group, ob))
            for zi, cap in sorted(caps.items()):     # (StandIn.cut: the break faces, in their own zone's group)
                z = zones[zi].id
                cap.name = f"{spec.name}_FR_LOD{i}_SI{si}_{z}_{s.subpart}_Cut{SUBPART_SUFFIX}"
                if i and not {f"{z}-{m.name.split('.')[0]}{SUBPART_SUFFIX}" for m in cap.data.materials} <= lod0:
                    _remove([cap])                    # (a lower LOD's cap where LOD0 has none: the LOD0 list rules)
                    continue
                pieces.append((z, z, cap))
            for z, group, ob in pieces:
                if ob is None:
                    continue
                for k, mat in enumerate(ob.data.materials):
                    ob.data.materials[k] = _standin_material(mat)
                ob["Group"] = group
                for k, v in zip(("ConstructionMeshType", "ConstructionMeshVisibility", "ConstructionMeshPreset"),
                                STANDIN_PROPS):
                    ob[k] = v
                skin = [o for o in fr_lods[i] if o.get("Group") == z and o.get("ColliderMeshGroups")]
                if not skin:
                    raise RuntimeError(f"{spec.name}: stand-in {s.subpart} in zone {z}, which has no LOD{i} skin")
                cmg = skin[0]["ColliderMeshGroups"].split("|")
                if group not in cmg:      # (kept in role order: the zone, then its suffixed groups in ROLE_IDS order)
                    order = [z + FR.ROLE_SUFFIX[r] for r in FR.ROLE_IDS]
                    odd = [g for g in cmg + [group] if g not in order]
                    if odd:
                        raise RuntimeError(f"{spec.name}: zone {z} skin ColliderMeshGroups {odd} are not {z} + a role "
                                           f"suffix ({order}): cannot keep them in role order")
                    skin[0]["ColliderMeshGroups"] = "|".join(sorted(cmg + [group], key=order.index))
                fr_lods[i].append(ob)
                tris[str(i)] = tris.get(str(i), 0) + _tris([ob])
                got = {f"{group}-{m.name}" for m in ob.data.materials}
                if i == 0:
                    parts.update({(si, p): None for p in got})
                elif not got <= lod0:
                    raise RuntimeError(f"{spec.name}: stand-in {s.subpart} LOD{i} parts {sorted(got - lod0)} "
                                       f"not in its LOD0 (the hide list and the stages are LOD0's)")
            _remove([whole])
    return {"parts": sorted({p for _, p in parts}), "tris": tris,
            "items": [{"subpart": s.subpart, "zone": s.zone, "src_lods": list(s.src_lods), "at": list(s.at),
                       "yaw": s.yaw}
                      for s in items]}


def keep_zone(ob, zi, role):
    """keep_faces on the zone / role face attributes (fracture_mesh.ZONE / ROLE, written by fracture_mesh.cut_lod):
    zone index zi, faces of `role`."""
    ZONE, ROLE = FM.ZONE, FM.ROLE
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    lay, rl = bm.faces.layers.int[ZONE], bm.faces.layers.int[ROLE]
    ri = ROLE_IDS.index(role) + 1
    drop = [f for f in bm.faces if f[lay] != zi + 1 or f[rl] != ri]
    if drop:
        bmesh.ops.delete(bm, geom=drop, context="FACES")
    left = len(bm.faces)
    bm.to_mesh(me)
    bm.free()
    if not left:
        bpy.data.objects.remove(ob)
        return None
    compact_materials(ob)
    return ob


def shape_hulls(hooks, spec, plan, lod0, size):
    """Each zone's collider shaped to its cut LOD0 geometry (fracture.shaped_hull), inside the block's cells, the
    block's total hull vertices within Keen's fractured collider budget; z.hull = its bounding box."""
    from ..model import limits
    whole, caps = lod0
    half = [0.125 * spec.cells[i] for i in (0, 2, 1)]          # game cells (x, up, depth) -> Blender (x, y, z)
    lo, hi = [-h for h in half], half
    max_v = limits.collider_limits(size, fractured=True)[1] // len(plan.zones)
    geo = {}
    for z, pts in zip(plan.zones, FM.zone_points(whole, caps, len(plan.zones))):
        if not pts:
            raise RuntimeError(f"{spec.name}: zone {z.id} has no LOD0 geometry for its collider")
        z.shape = FR.shaped_hull(pts, lo, hi, max_v, halfspaces=z.halfspaces)
        geo[z.id] = (tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3)))
    # a zone under fracture.MIN_ZONE_SHARE of the health gets a box hull (at most MIN_HULL_T beyond its geometry),
    # except hooks.min_share_exempt
    exempt = FR.min_share_exempt(hooks, plan.family)
    try:
        lifted = FR.lift_small_zones({z.id: z.shape for z in plan.zones}, set(plan.critical()), geo, lo, hi, exempt)
    except RuntimeError as e:
        raise RuntimeError(f"{spec.name}: {e}") from None
    for z in plan.zones:
        z.min_share = z.id in lifted
        if z.min_share:
            z.shape = lifted[z.id]
        vs = z.shape["verts"]
        z.hull = (tuple(round(min(v[i] for v in vs), 5) for i in range(3)),
                  tuple(round(max(v[i] for v in vs), 5) for i in range(3)))
        if min(b - a for a, b in zip(*z.hull)) < 0.004:
            raise RuntimeError(f"{spec.name}: zone {z.id} collider is degenerate {z.hull}")


def _plan_scaffold(hooks, spec, plan, lods, res, size, nf_t0, n_fr, budget, reserve=(0, 0)):
    """The block's construction scaffold (pipeline.scaffold) on the cut LODs, or None (no edges-only cage fits a
    light block's budget). The cut is final here: the plan only ever shrinks by pitch, so the FR LOD0 with the
    cage keeps Keen's LOD count for the FR's own size (the cage included: it may stand OUTSET proud of the cells)
    and the cut's side of the NonFractured row. Without room for a proud cage (a size or row
    change), the cage stays within the cells."""
    from . import scaffold as SC
    # the envelope: the cut LOD0's own geometry (its render bounds) within the block's cells (a block's mounting ears
    # overhang its cells); where that fills the cell (within 2 mm of its face) the cage stands OUTSET proud of it, as
    # vanilla's do (scaffold.reach_cells), so it reads outside the skin instead of hiding behind it
    whole, caps = res[0]
    glo, ghi = OB.bounds([whole] + list(caps.values()))
    half = [0.125 * spec.cells[i] for i in (0, 2, 1)]          # game cells (x, up, depth) -> Blender (x, y, z)
    lo = [max(a, -h) for a, h in zip(glo, half)]
    hi = [min(b, h) for b, h in zip(ghi, half)]
    zones = [{"lo": z.hull[0], "hi": z.hull[1]} for z in plan.zones]
    fr_tris = [FM.lod_tris(r) for r in res] + [_tris([o for o in lods[-1][1] if o.type == "MESH"])]
    # proud on every axis first; where that would move the FR across a row of Keen's LOD table (keyed on the
    # model's surface: a thin 1 m slab grown 8 mm can ask for one more LOD) as many axes as possible
    from itertools import combinations
    for axes in (c for n in (3, 2, 1, 0) for c in combinations(range(3), n)):
        elo, ehi = SC.reach_cells(lo, hi, half, axes)
        zs = [dict(zip(("lo", "hi"), SC.reach_cells(z["lo"], z["hi"], half, axes))) for z in zones]
        sp = _fit_scaffold(hooks, plan.family, zs, elo, ehi, glo, ghi, size, nf_t0, n_fr, fr_tris, budget, reserve)
        if sp:
            sp.proud = list(axes)
            return sp
    return None


def _fit_scaffold(hooks, family, zones, lo, hi, glo, ghi, size, nf_t0, n_fr, fr_tris, budget, reserve=(0, 0)):
    """scaffold.plan on this envelope, its LOD0 shrunk (a lower cap: a coarser pitch) until the FR LOD0 with the cage
    keeps Keen's LOD count for the FR's own size (geometry and cage), the NF-row side of the cut and budget[0].
    reserve: the stand-in triangles per LOD still to come (bake_standins), counted as there."""
    from ..model import limits
    from . import scaffold as SC
    t0 = fr_tris[0] + reserve[0]
    side = FR.lod_side(size, nf_t0, t0)

    def fr_size(boxes):
        blo = [min([g] + [b[0][i] for b in boxes]) for i, g in enumerate(glo)]
        bhi = [max([g] + [b[1][i] for b in boxes]) for i, g in enumerate(ghi)]
        return tuple(round(b - a, 5) for a, b in zip(blo, bhi))
    est = fr_size([(lo, hi)])
    cap = None
    while True:
        sp = SC.plan(hooks, family, zones, tuple(lo), tuple(hi), est, fr_tris, budget, reserve=reserve, cap=cap)
        if sp is None:
            return None
        t = t0 + sp.tris[0]
        fs = fr_size(sp.boxes)
        if limits.optimal_lods(fs, t)[0] == n_fr and FR.lod_side(size, nf_t0, t) == side and t <= budget[0]:
            return sp
        cap = sp.tris[0] - 1


def _export_planned(hooks, spec, lods, plan, ops, tmp, man, fr_dir, dm_dir, made, mid, subparts=None):
    """The cut Fractured model and the dented Damaged model of a planned block (pipeline.fracture), with the block's
    subpart stand-ins (`hooks.standins`, bake_standins; subparts: the built subpart LODs). Returns the collider
    objects."""
    from ..model import limits
    size = tuple(man["nonfractured"]["size"])
    n = len(lods)
    if n < 2:
        raise RuntimeError(f"{spec.name}: a fracture plan needs at least two LODs (the last one is the LastLOD box); "
                           f"Keen's checker gives this block {n}: add detail to LOD0 or leave its family unplanned.")
    src = [(f"{spec.name}_LOD{i}_", objs) for i, (_, objs) in enumerate(lods)]
    nf_t0 = man["nonfractured"]["lods"][0]["tris"]
    added_mid = False
    # a light block's cut LOD0 must land clearly on one side of Keen's LOD-count row (fracture.lod_side): clearly
    # above takes one more LOD (the builder's mid detail); in between, the cheaper cuts are tried in turn
    for edge_split, thin_caps in ((False, True), (False, False), (True, True), (True, False)):
        plan.edge_split, plan.thin_caps = edge_split, thin_caps
        res = [FM.cut_lod(hooks, objs, plan, i, tmp, pre, f"{spec.name}_FR_LOD{i}_all")
               for i, (pre, objs) in enumerate(src[:-1])]
        t0 = FM.lod_tris(res[0])
        side = FR.lod_side(size, nf_t0, t0)
        if side == "above" and mid and not edge_split:
            res.insert(1, FM.cut_lod(hooks, mid, plan, 1, tmp, f"{spec.name}_MID_", f"{spec.name}_FR_MID_all"))
            added_mid = True
            break
        if side == "below":
            break
        for whole, caps in res:
            _remove([whole] + list(caps.values()))
    else:
        raise RuntimeError(f"{spec.name}: no cut keeps LOD0 ({t0} tris) clear of Keen's LOD-count row")
    need = limits.optimal_lods(size, t0)[0]
    if need != n + int(added_mid):
        raise RuntimeError(f"{spec.name}: cut LOD0 has {t0} tris, Keen expects {need} LODs (have {n + added_mid})")
    n_fr = len(res) + 1
    budget = FR.fracture_budget(size, t0, n_fr)
    for i, r in enumerate(res):   # a lower LOD over its budget (many thin cut posts) caps
        for m in (FR.THIN_CAP, 3 * FR.THIN_CAP, 9 * FR.THIN_CAP):   # only its bigger cut loops
            if i == 0 or FM.lod_tris(res[i]) <= budget[i]:
                break
            _remove([res[i][0]] + list(res[i][1].values()))
            pre, objs = (f"{spec.name}_MID_", mid) if (added_mid and i == 1) else src[i - int(added_mid and i > 1)]
            res[i] = FM.cut_lod(hooks, objs, plan, i, tmp, pre, f"{spec.name}_FR_LOD{i}_all", caps_min=m)
    reserve = standin_tris(hooks, spec, subparts, len(res), plan.zones, plan)   # (the stand-ins count in the budget)
    over = [(i, FM.lod_tris(r) + reserve[i], budget[i]) for i, r in enumerate(res)
            if FM.lod_tris(r) + reserve[i] > budget[i]]
    if over:
        raise RuntimeError(f"{spec.name}: fractured LODs over budget (lod, tris, budget): {over}")
    shape_hulls(hooks, spec, plan, res[0], size)
    sp = _plan_scaffold(hooks, spec, plan, lods, res, size, nf_t0, n_fr, budget, reserve)
    fr_lods, parts, caps_stats = [], {}, []
    interior_tris = {}   # zone -> (LOD0 model tris, break-cap tris): interior.survey (export.json "interior")
    for i, (whole, caps) in enumerate(res):
        lod_objs = []
        for zi, z in enumerate(plan.zones):
            pieces = {}
            for role in ROLE_IDS:
                ob = whole.copy()
                ob.data = whole.data.copy()
                ob.name = f"{spec.name}_FR_LOD{i}_{z.id}{_role_tag(role)}"
                tmp.objects.link(ob)
                pieces[role] = keep_zone(ob, zi, role)
            cut = caps.get(zi)
            if cut:
                cut.name = f"{spec.name}_FR_LOD{i}_{z.id}_Cut"
            cut, cut_hide = _split_cut(cut, tmp)
            if i == 0:
                _note_roles(man, z.id, pieces)
                interior_tris[z.id] = ([t for ob in pieces.values() if ob for t in _world_tris(ob)],
                                       [t for ob in (cut, cut_hide) if ob for t in _world_tris(ob)])
            # every zone keeps a skin at every LOD but the LastLOD box (its collider needs a mesh): _promote
            _promote(pieces, i, spec.name, z.id)
            _group(pieces, cut, z.id, lod_objs, cut_hide)
        if i == 0:   # what a broken zone shows inside (pipeline.interior: Keen's way), on the cut LOD0
            from . import interior as IN
            pts = [p for body, _ in interior_tris.values() for t in body for p in t]
            lo = [min(p[k] for p in pts) for k in range(3)]
            hi = [max(p[k] for p in pts) for k in range(3)]
            seams = [(s["o"], s["n"], s["sides"][0], s["sides"][1]) for s in plan.manifest()["seams"]]
            man["fractured"]["interior"] = IN.survey(lo, hi, {z: v[0] for z, v in interior_tris.items()},
                                                     {z: v[1] for z, v in interior_tris.items()}, seams)
        scaf = FM.scaffold_object(sp, i, tmp, f"{spec.name}_FR_LOD{i}_Scaffold")
        if scaf:      # ungrouped, after the zones (the hulls were fitted without it: it has no collider)
            lod_objs.append(scaf)
        caps_stats.append(list(whole[FM.CAPS_PROP]))
        for o in [whole] + [c for c in caps.values() if c not in lod_objs]:
            if o.name in bpy.data.objects:
                _remove([o])
        fr_lods.append(lod_objs)
    standins = bake_standins(hooks, spec, fr_lods, tmp, subparts, plan.zones, plan)   # (by zone; after the cage)
    fr_lods.append([merged(lods[-1][1], f"{spec.name}_FR_LOD{n_fr - 1}", tmp)])   # the LastLOD box, no group
    scaffolds = {o.name for lod_objs in fr_lods for o in lod_objs if o.get(FM.SCAFFOLD_PROP)}
    for i, lod_objs in enumerate(fr_lods):
        for o in lod_objs:
            FM.strip_attributes(o)
        made += lod_objs
        if i == 0:
            parts = _construction_parts(lod_objs)
        fn = lod_name(f"{spec.name}_Fractured", i)
        EX.export_fbx(lod_objs, os.path.join(fr_dir, fn), ops)
        man["fractured"]["lods"].append({"file": fn, "materials": _materials(lod_objs), "tris": _tris(lod_objs),
                                         "objects": len(lod_objs),
                                         "groups": sorted({o["Group"] for o in lod_objs if "Group" in o})})
    pm = plan.manifest()
    vols = {z.id: z.shape["volume"] for z in plan.zones}
    share = FR.health_shares(vols, set(plan.critical()))
    for zm, z in zip(pm["zones"], plan.zones):
        zm.update(lo=list(z.hull[0]), hi=list(z.hull[1]), volume=round(z.shape["volume"], 6),
                  box_volume=round(z.shape["box_volume"], 6), hull_verts=len(z.shape["verts"]),
                  hull_planes=len(z.shape["planes"]), health_share=share[z.id])
        if getattr(z, "min_share", False):
            zm["min_share"] = True
    man["fractured"].update(kind="cut", zones=pm["zones"], seams=pm["seams"], supporting=pm["supporting"],
                            critical=plan.critical(), parts=dict(sorted(parts.items())), caps=caps_stats,
                            budget=budget, mid_lod=added_mid, size=list(_size(fr_lods[0])), cut_mode=plan.cut_mode(),
                            keep_whole=list(plan.keep_whole),
                            scaffold=sp.manifest() if sp else None)
    if standins:
        man["fractured"]["standins"] = standins
    hulls = [FM.hull_object(z.id, z.shape["verts"], tmp) for z in plan.zones]
    info = EX.export_collision(hulls, os.path.join(fr_dir, f"{spec.name}_Fractured_collision.gltf"), ops)
    man["fractured"]["collision"] = dict(info, file=f"{spec.name}_Fractured_collision.gltf",
                                         bin=f"{spec.name}_Fractured_collision.bin")

    # ---------------------------------------------------------------- Damaged: the same, densified and dented
    lo, hi = OB.bounds([o for o in lods[0][1] if o.type == "MESH"])
    dm = {"lods": []}
    dm_objs_all = []
    stats = {}
    for i, lod_objs in enumerate(fr_lods):
        dm_objs = []
        lod_objs = [o for o in lod_objs if o.name not in scaffolds]   # vanilla: the scaffold is FR-only
        for o in lod_objs:
            c = o.copy()
            c.data = o.data.copy()
            for k in o.keys():
                c[k] = o[k]
            c.name = o.name.replace("_FR_", "_DM_")
            tmp.objects.link(c)
            dm_objs.append(c)
        if i < len(fr_lods) - 1:     # densified to at most damaged_tris_x x the Fractured LOD without its scaffold
            cap = min(budget[i], int(FR.damaged_tris_x(hooks, plan.family) * _tris(lod_objs)))
            FM.densify(dm_objs, _dent(hooks, plan.family)["max_edge"] * (1 + i), cap)
            st = FM.dent(hooks, dm_objs, plan.family, lo, hi, FR.dent_seed(hooks, spec.name))
            if i == 0:
                stats = st
        if i == 0:
            dm_size = _size(dm_objs)      # (the LOD0 objects: FR LOD0 has one more, the scaffold)
        dm_objs_all += dm_objs
        fn = lod_name(f"{spec.name}_Damaged", i)
        EX.export_fbx(dm_objs, os.path.join(dm_dir, fn), ops)
        dm["lods"].append({"file": fn, "materials": _materials(dm_objs), "tris": _tris(dm_objs),
                           "objects": len(dm_objs), "groups": sorted({o["Group"] for o in dm_objs if "Group" in o})})
    dm["size"] = list(dm_size)
    dm["dent"] = stats
    dm["slots"] = damaged_table(hooks)
    hulls_dm = [FM.hull_object(z.id, z.shape["verts"], tmp) for z in plan.zones]
    info = EX.export_collision(hulls_dm, os.path.join(dm_dir, f"{spec.name}_Damaged_collision.gltf"), ops)
    dm["collision"] = dict(info, file=f"{spec.name}_Damaged_collision.gltf", bin=f"{spec.name}_Damaged_collision.bin")
    man["damaged"] = dm
    made += dm_objs_all
    return hulls_dm


def built_specs(report, blocks):
    """The blocks (specs) that have a real model in build_report.json (a placement measured)."""
    return [b for b in blocks if report["blocks"].get(b.name, {}).get("placement", {}).get("total")]
