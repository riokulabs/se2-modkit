"""Preview scene machinery (Blender), shared by every preview set of every mod.

Every scene uses one look: EEVEE, AgX Base Contrast, a neutral four-light rig (key front-right-high, cool fill
front-left, rim from behind, soft top; front = +Y), a 0.25 m floor grid and the Keen-slot material stand-ins
(`preview.mats`) on our blocks and on vanilla blocks alike, so a side-by-side render compares shapes and material
roles, not shaders. Vanilla FBX are imported read only from the `vanilla_ref` folder (user config), never written.

A run first calls `configure(out=..., fast=..., only=...)`; `Shot.render()` then writes `<out>/<shot>.png`.
`output_dir(hooks, set_name)` is the mod's git-ignored preview folder for a set: `<Source/Model>/preview/<set>/`.

`output_dir` and `want` are plain Python; everything else needs Blender.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

try:   # Blender only; the plain helpers below import without it
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
except ImportError:   # pragma: no cover - outside Blender
    bmesh = bpy = Matrix = Vector = None

from . import mats as PM
from .compose import PreviewError

__all__ = ["PreviewError", "Shot", "T", "Rz", "configure", "human", "output_dir", "painted", "settings", "vanilla",
           "vanilla_ref", "want"]

_SET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_STATE = {"out": None, "fast": False, "only": ()}
_PAINTED: dict = {}
_VAN: dict = {}


def output_dir(hooks, set_name: str) -> Path:
    """`<hooks.paths.source_model>/preview/<set_name>/` (git-ignored in the mod; not created here), checked by
    `check_output`."""
    if not _SET_RE.fullmatch(set_name or "") or ".." in set_name:
        raise PreviewError(f"preview set name {set_name!r}: use letters, digits, '_', '.' and '-' only.")
    return check_output(hooks.paths.source_model, Path(hooks.paths.source_model) / "preview" / set_name)


def check_output(source_model: str | os.PathLike, out: str | os.PathLike) -> Path:
    """`out` if it is `<source_model>/preview/<set>` with neither `preview/` nor `<set>/` a symlink and its resolved
    path inside the resolved model source; PreviewError (one sentence) otherwise."""
    sm, out = Path(source_model), Path(out)
    if out.parent != sm / "preview":
        raise PreviewError(f"preview output {out} is not a set folder under {sm / 'preview'}.")
    for p in (out.parent, out):
        if p.is_symlink():
            raise PreviewError(f"{p} is a symlink; previews are written only into a real folder inside the mod.")
    if not out.resolve().is_relative_to(sm.resolve()):
        raise PreviewError(f"preview output {out} resolves outside the model source {sm}.")
    return out


def configure(out: str | os.PathLike | None = None, *, fast: bool | None = None, only=None) -> dict:
    """Set where shots render (`out`), half-resolution low-sample renders (`fast`) and the shot-name prefixes to render
    (`only`, empty = all). Returns the settings."""
    if out is not None:
        _STATE["out"] = Path(out)
    if fast is not None:
        _STATE["fast"] = bool(fast)
    if only is not None:
        _STATE["only"] = tuple(s for s in only if s)
    return dict(_STATE)


def settings() -> dict:
    return dict(_STATE)


def want(name: str) -> bool:
    """Whether a shot is selected (`configure(only=...)` prefixes; all when empty)."""
    only = _STATE["only"]
    return not only or any(name.startswith(o) for o in only)


def vanilla_ref() -> Path:
    """The read-only vanilla reference folder (`vanilla_ref` in the user config); one plain sentence if unset."""
    from ..model import config
    ref = config.VANILLA_REF
    if not ref:
        raise PreviewError("No vanilla reference folder: set vanilla_ref in the se2modkit user config (a read-only "
                           "copy of the Mod SDK's GameData/Vanilla/Assets).")
    return Path(ref)


def T(x, y, z):
    return Matrix.Translation((x, y, z))


def Rz(deg):
    return Matrix.Rotation(math.radians(deg), 4, "Z")


def _exclude(coll):
    def walk(lc):
        if lc.collection == coll:
            lc.exclude = True
            return True
        return any(walk(c) for c in lc.children)
    walk(bpy.context.scene.view_layers[0].layer_collection)


def painted(built: dict, name: str, lod: int = 0, paint: str = "keen"):
    """A copy of one LOD of a built block (`built[name]["lods"][lod] = (collection, objects)`) wearing the preview
    stand-ins for `paint` (cached, excluded collection)."""
    key = (name, lod, paint)
    if key in _PAINTED:
        return _PAINTED[key]
    _, objs = built[name]["lods"][lod]
    c = bpy.data.collections.new(f"pv_{name}_LOD{lod}_{paint}")
    bpy.context.scene.collection.children.link(c)
    for o in objs:
        n = o.copy()
        if o.data is not None:
            n.data = o.data.copy()
        c.objects.link(n)
        PM.apply([n], paint)
    _exclude(c)
    _PAINTED[key] = c
    return c


def vanilla(rel: str, paint: str = "keen"):
    """Import a vanilla FBX (`rel` under the reference's Blocks/) read only into an excluded collection, materials
    swapped for the stand-ins. Returns (collection, bounds lo, bounds hi)."""
    key = (rel, paint)
    if key in _VAN:
        return _VAN[key]
    path = vanilla_ref() / "Blocks" / rel
    if not path.is_file():
        raise PreviewError(f"No vanilla model {path}: check the vanilla_ref folder.")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    c = bpy.data.collections.new("v_" + path.name)
    bpy.context.scene.collection.children.link(c)
    for o in new:
        for uc in list(o.users_collection):
            uc.objects.unlink(o)
        c.objects.link(o)
        if o.type == "MESH":
            PM.apply([o], paint)
    _exclude(c)
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(b) for o in new if o.type == "MESH" for b in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    _VAN[key] = (c, lo, hi)
    return _VAN[key]


def human():
    """A 1.80 m human silhouette (mannequin) for scale."""
    if "pv_human" in bpy.data.collections:
        return bpy.data.collections["pv_human"]
    c = bpy.data.collections.new("pv_human")
    bpy.context.scene.collection.children.link(c)
    bm = bmesh.new()

    def capsule(p0, p1, r):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        m = Matrix.Translation((p0 + p1) / 2) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r, radius2=r, depth=d.length, matrix=m)
        for q in (p0, p1):
            bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=r, matrix=Matrix.Translation(q))
    for sx in (-1, 1):
        capsule((sx * 0.10, 0, 0.08), (sx * 0.10, 0, 0.50), 0.065)      # shins
        capsule((sx * 0.10, 0, 0.50), (sx * 0.10, 0, 0.90), 0.08)       # thighs
        capsule((sx * 0.23, 0, 1.43), (sx * 0.26, 0, 1.12), 0.05)       # upper arms
        capsule((sx * 0.26, 0, 1.12), (sx * 0.27, 0.02, 0.84), 0.042)   # forearms
        capsule((sx * 0.10, 0.06, 0.03), (sx * 0.10, -0.08, 0.03), 0.035)  # feet
    capsule((0, 0, 0.95), (0, 0, 1.40), 0.17)                            # torso
    capsule((0, 0, 1.47), (0, 0, 1.52), 0.05)                            # neck
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=0.11, matrix=Matrix.Translation((0, 0, 1.69)))
    me = bpy.data.meshes.new("pv_human")
    bm.to_mesh(me)
    bm.free()
    for poly in me.polygons:
        poly.use_smooth = True
    me.materials.append(PM.get("Human"))
    c.objects.link(bpy.data.objects.new("pv_human", me))
    _exclude(c)
    return c


# lighting presets: name -> [(light name, location, energy W, size m, colour)], scaled by Shot(light=...)
LIGHTING = {
    "studio": (("key", (3, 5, 6), 900, 5, (1.0, 0.97, 0.93)), ("fill", (-6, 3, 3), 380, 7, (0.88, 0.92, 1.0)),
               ("rim", (0, -6, 5), 450, 4, (0.95, 0.97, 1.0)), ("top", (0, 0.01, 8), 250, 10, (1, 1, 1))),
    "flat": (("top", (0, 0.01, 8), 600, 12, (1, 1, 1)), ("front", (0, 7, 3), 500, 10, (1, 1, 1)),
             ("back", (0, -7, 3), 300, 10, (1, 1, 1))),
    "dark": (("key", (3, 5, 6), 260, 3, (1.0, 0.95, 0.88)), ("rim", (0, -6, 5), 200, 3, (0.8, 0.88, 1.0))),
}


class Shot:
    """One scene: `inst` collections into it, set a `camera`, `render()` to `<out>/<name>.png`."""

    def __init__(self, name, res=(1800, 1150), floor=(-3, 3, -3, 3), world=(0.055, 0.058, 0.063), light=1.0,
                 floor_z=0.0, lighting="studio", light_scale=1.0):
        """`light` scales the lights' energy; `light_scale` the whole rig (positions, sizes and, squared, energy) for
        subjects larger than the ~3 m the presets are set up for."""
        self.name = name
        fast = _STATE["fast"]
        sc = bpy.data.scenes.new(name)
        self.sc = sc
        sc.render.resolution_x, sc.render.resolution_y = res
        sc.render.resolution_percentage = 50 if fast else 100
        sc.render.engine = "BLENDER_EEVEE"
        for k, v in (("taa_render_samples", 16 if fast else 64), ("use_raytracing", True), ("use_shadows", True),
                     ("use_gtao", True), ("fast_gi_method", "GLOBAL_ILLUMINATION")):
            try:
                setattr(sc.eevee, k, v)
            except Exception:
                pass
        try:
            sc.view_settings.view_transform = "AgX"
            sc.view_settings.look = "AgX - Base Contrast"
        except Exception:
            pass
        w = bpy.data.worlds.new("W_" + name)
        sc.world = w
        w.use_nodes = True
        w.node_tree.nodes["Background"].inputs[0].default_value = (*world, 1)
        if lighting not in LIGHTING:
            raise PreviewError(f"unknown lighting preset {lighting!r}: use one of {', '.join(LIGHTING)}.")
        for nm, loc, e, sz, col in LIGHTING[lighting]:
            ld = bpy.data.lights.new(f"{name}_{nm}", "AREA")
            ld.energy, ld.size, ld.color = e * light * light_scale ** 2, sz * light_scale, col
            o = bpy.data.objects.new(ld.name, ld)
            o.location = Vector(loc) * light_scale
            o.rotation_euler = (Vector((0, 0, light_scale)) - o.location).to_track_quat("-Z", "Y").to_euler()
            sc.collection.objects.link(o)
        if floor:
            self.grid(*floor, z=floor_z)

    def grid(self, x0, x1, y0, y1, step=0.25, z=0.0):
        bm = bmesh.new()
        me = bpy.data.meshes.new("floor")

        def box(lo, hi, mi):
            vs = bmesh.ops.create_cube(bm, size=1.0)["verts"]
            bmesh.ops.scale(bm, vec=Vector(hi) - Vector(lo), verts=vs)
            bmesh.ops.translate(bm, vec=(Vector(lo) + Vector(hi)) / 2, verts=vs)
            for f in {f for v in vs for f in v.link_faces}:
                f.material_index = mi
        box((x0, y0, z - 0.01), (x1, y1, z), 0)
        x = x0
        while x <= x1 + 1e-6:
            box((x - 0.003, y0, z), (x + 0.003, y1, z + 0.0006), 1)
            x += step
        y = y0
        while y <= y1 + 1e-6:
            box((x0, y - 0.003, z), (x1, y + 0.003, z + 0.0006), 1)
            y += step
        bm.to_mesh(me)
        bm.free()
        me.materials.append(PM.get("Floor"))
        me.materials.append(PM.get("Darker"))
        self.sc.collection.objects.link(bpy.data.objects.new("floor", me))

    def inst(self, coll, m):
        e = bpy.data.objects.new("i_" + coll.name, None)
        e.instance_type = "COLLECTION"
        e.instance_collection = coll
        e.matrix_world = m
        self.sc.collection.objects.link(e)
        return e

    def link(self, ob):
        self.sc.collection.objects.link(ob)
        return ob

    def camera(self, loc, target, lens=50, ortho=None):
        cam = bpy.data.objects.new("cam_" + self.name, bpy.data.cameras.new("cam_" + self.name))
        cam.location = loc
        cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        cam.data.lens = lens
        cam.data.clip_start = 0.02
        cam.data.clip_end = 200
        if ortho:
            cam.data.type, cam.data.ortho_scale = "ORTHO", ortho
        self.sc.collection.objects.link(cam)
        self.sc.camera = cam
        self.cam = cam
        return cam

    def caption(self, txt, loc, size=0.07):
        cu = bpy.data.curves.new("cap", "FONT")
        cu.body = txt
        cu.size = size
        cu.align_x, cu.align_y = "CENTER", "CENTER"
        cu.materials.append(PM.get("Caption"))
        o = bpy.data.objects.new("cap", cu)
        o.location = loc
        self.sc.collection.objects.link(o)
        con = o.constraints.new("TRACK_TO")
        con.target = self.cam
        con.track_axis, con.up_axis = "TRACK_Z", "UP_Y"
        return o

    def render(self):
        out = _STATE["out"]
        if out is None:
            raise PreviewError("No preview output folder: call preview.base.configure(out=...) first.")
        out.mkdir(parents=True, exist_ok=True)
        path = out / (self.name + ".png")
        self.sc.render.filepath = str(path)
        bpy.ops.render.render(write_still=True, scene=self.sc.name)
        print("WROTE", path)
        return path
