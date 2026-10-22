"""Reused Keen kit parts: the vanilla conveyor port assemblies, per LOD.

Keen's SE2 blocks share standard interface parts. The conveyor ports are separate named objects in every tool
block's FBX (same geometry in Welder400 and Grinder400) at exactly our port positions (sides: x = +-1.25,
y = -1.25; back: y = -2.0, centred), so they drop in unchanged:

| LOD | Source (vanilla_ref/Blocks/Welder/400/Models/NonFractured/) | Objects (by SECTION property) |
|---|---|---|
| 0 | Welder400.fbx      | ConveyorPortSmall_Border_low / _Handle_low (sides), Large_Conveyor_border_low / _Handle_low (back), and the ConveyorsAtlas door faces Fracture_01.024 / .025 / .026 |
| 1 | Welder400_LOD1.fbx | Fracture_01.001 / .002 / .003 (frame + door merged) |
| 2 | Welder400_LOD2.fbx | Fracture_01.052 / .075 / .077 |
| 3 | (none in vanilla)  | our plain yellow plates (design.ports) |

Reusing SE2 vanilla kit parts in SE2 mods is allowed (house rule R3 in docs/style-guide/00-house-rules.md); no
vanilla file is copied into a repository. The files under `vanilla_ref` (read-only hard links to the user's own
install) are imported into the build scene, copied, and the imported objects removed. Nothing is written back. The door faces carry the ConveyorsAtlas UVs (the two-leaf hatch, dog-bone latch, clamps and hinge pins are in
that atlas), so the doors no longer need world-projected UVs.
"""
import os

import bmesh
import bpy

from .config import VANILLA_REF
from .geom import Part

SRC = os.path.join(VANILLA_REF or "", "Blocks", "Welder", "400", "Models", "NonFractured")
FILES = {0: "Welder400.fbx", 1: "Welder400_LOD1.fbx", 2: "Welder400_LOD2.fbx"}
SECTIONS = ("ConveyorLeft", "ConveyorRight", "ConveyorBack")
_CACHE = {}            # LOD -> {section: [bpy mesh (world space, library material names)]}
PROVENANCE = {}        # LOD -> {section: [source object names]} (for the build report and NOTES)


def _base(name):
    return name.split(".")[0]


def _load(L):
    """Import one vanilla LOD file, copy its SECTION objects into world-space meshes, remove the import."""
    from .materials import get_mat
    before = set(bpy.data.objects)
    mats_before = set(bpy.data.materials)
    meshes_before = set(bpy.data.meshes)
    bpy.ops.import_scene.fbx(filepath=os.path.join(SRC, FILES[L]))
    # the import brings Keen's material *names* (PaintedMetalColorable, ...) with default shading; rename them
    # out of the way so our library stand-ins (get_mat) are used and the imported ones can be deleted
    for m in set(bpy.data.materials) - mats_before:
        m.name = "KITIMP_" + m.name
    new = [o for o in bpy.data.objects if o not in before]
    out, prov = {}, {}
    for o in sorted(new, key=lambda o: o.name):
        if o.type != "MESH" or o.get("SECTION") not in SECTIONS:
            continue
        me = o.data.copy()
        me.name = f"KIT_{L}_{o.name}"
        me.transform(o.matrix_world)
        if o.matrix_world.to_3x3().determinant() < 0:
            me.flip_normals()
        # UV0 is what the game reads: keep the first layer only, named like ours (joins need one "UVMap")
        while len(me.uv_layers) > 1:
            me.uv_layers.remove(me.uv_layers[-1])
        me.uv_layers[0].name = "UVMap"
        for i, m in enumerate(me.materials):
            if m:
                me.materials[i] = get_mat(_base(m.name[len("KITIMP_"):] if m.name.startswith("KITIMP_") else m.name))
        out.setdefault(o["SECTION"], []).append(me)
        prov.setdefault(o["SECTION"], []).append(o.name)
    for o in new:
        bpy.data.objects.remove(o)
    keep = {me for ms in out.values() for me in ms}
    for me in set(bpy.data.meshes) - meshes_before - keep:
        bpy.data.meshes.remove(me)
    for m in [m for m in bpy.data.materials if m.name.startswith("KITIMP_")]:
        if m.users == 0:
            bpy.data.materials.remove(m)
    for im in [im for im in bpy.data.images if im.users == 0]:
        bpy.data.images.remove(im)
    _CACHE[L], PROVENANCE[L] = out, prov


class KitPart(Part):
    """A Part backed by a vanilla mesh: the bmesh feeds the checks, the mesh itself is exported unchanged
    (custom normals and UVs as Keen authored them)."""

    def __init__(self, name, meshes, section):
        super().__init__(name, section=section)
        self.kit_meshes = meshes
        self.island_tol = 0.003
        for me in meshes:
            tmp = bmesh.new()
            tmp.from_mesh(me)
            mats = [m.name if m else "" for m in me.materials]
            for f in tmp.faces:
                f.material_index = self.mi(mats[f.material_index] if f.material_index < len(mats) else mats[0])
            tmp.verts.ensure_lookup_table()
            # append into self.bm (material indices already remapped to this Part's list)
            vmap = [self.bm.verts.new(v.co) for v in tmp.verts]
            for f in tmp.faces:
                try:
                    nf = self.bm.faces.new([vmap[v.index] for v in f.verts])
                    nf.material_index = f.material_index
                except ValueError:
                    pass
            tmp.free()

    def merged_mesh(self, name):
        """One mesh of all source meshes (materials unified)."""
        objs = []
        for me in self.kit_meshes:
            ob = bpy.data.objects.new("tmpkit", me.copy())
            bpy.context.scene.collection.objects.link(ob)
            objs.append(ob)
        if len(objs) > 1:
            with bpy.context.temp_override(active_object=objs[0], selected_editable_objects=objs,
                                           selected_objects=objs):
                bpy.ops.object.join()
        me = objs[0].data
        me.name = name
        bpy.data.objects.remove(objs[0])
        return me


def ports(L):
    """Kit port Parts for LOD L (0..2), named Port_<SECTION> like the plain ports (fracture code keys on it)."""
    if L not in FILES:
        return []
    if L not in _CACHE:
        _load(L)
    return [KitPart(f"Port_{s}", _CACHE[L][s], s) for s in SECTIONS if s in _CACHE[L]]


def triangles(L):
    if L not in _CACHE:
        _load(L)
    return {s: sum(sum(len(p.vertices) - 2 for p in me.polygons) for me in ms) for s, ms in _CACHE[L].items()}
