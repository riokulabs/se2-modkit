"""Build-menu and inventory icons (Blender): the vanilla 4.0 look.

Vanilla icons (the SDK's Blocks/**/<name>_Icon.png, MainMenuData/UI/Terminal/Components/**) are 200 x 200 RGBA: the
subject alone on a transparent ground, a 3/4 view from the front and above with mild perspective, a bright studio
light (a large key from the upper left, a soft fill, a top light and a cool rim that cuts the edges out of a dark
G-screen tile), the paint a light grey; blocks fill ~70-85 % of the tile, components ~65 %.

    icon(name, collection, out_png, direction=DEFAULT_DIRECTION, fill=DEFAULT_FILL, exposure=EXPOSURE)

renders one subject (a collection, e.g. `base.painted(built, name, 0, "keen")`): the camera is aimed along
`direction` (from the subject to the camera, +Y = front) at the distance where the bounds' larger projected extent
spans `fill` of the frame, and the lens is shifted to centre it. Icons are package files: a mod stages them from
where it keeps them (they are the one rendered output that is committed).
"""

from __future__ import annotations

from pathlib import Path

try:   # Blender only
    import bpy
    from mathutils import Matrix, Vector
except ImportError:   # pragma: no cover - outside Blender
    bpy = Matrix = Vector = None

__all__ = ["DEFAULT_DIRECTION", "DEFAULT_FILL", "EXPOSURE", "Icon", "aim", "centre", "fit_distance", "icon",
           "scene_bounds", "shoot"]

SIZE = 200
LENS = 70
EXPOSURE = -0.25          # light grey paint, crisp highlights
DEFAULT_DIRECTION = (-0.62, 0.78, 0.55)
DEFAULT_FILL = 0.80


class Icon:
    """A transparent SIZE x SIZE icon scene with the vanilla-like studio light."""

    def __init__(self, name, size=SIZE, exposure=EXPOSURE):
        self.name = name
        sc = bpy.data.scenes.new("icon_" + name)
        self.sc = sc
        r = sc.render
        r.resolution_x = r.resolution_y = size
        r.resolution_percentage = 100
        r.film_transparent = True
        r.filter_size = 1.2
        r.engine = "BLENDER_EEVEE"
        for k, v in (("taa_render_samples", 96), ("use_raytracing", True), ("use_shadows", True),
                     ("use_gtao", True), ("fast_gi_method", "GLOBAL_ILLUMINATION")):
            try:
                setattr(sc.eevee, k, v)
            except Exception:
                pass
        try:
            sc.view_settings.view_transform = "AgX"
            sc.view_settings.look = "AgX - Medium High Contrast"
            sc.view_settings.exposure = exposure
        except Exception:
            pass
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"
        r.image_settings.color_depth = "8"
        for a in [a for a in dir(r) if a.startswith("use_stamp")]:
            try:
                setattr(r, a, False)
            except (AttributeError, TypeError):
                pass
        w = bpy.data.worlds.new("W_icon_" + name)
        sc.world = w
        w.use_nodes = True
        w.node_tree.nodes["Background"].inputs[0].default_value = (0.30, 0.31, 0.33, 1)
        w.node_tree.nodes["Background"].inputs[1].default_value = 0.35
        cam = bpy.data.objects.new("cam_icon_" + name, bpy.data.cameras.new("cam_icon_" + name))
        cam.data.lens = LENS
        cam.data.clip_start, cam.data.clip_end = 0.005, 200
        sc.collection.objects.link(cam)
        sc.camera = self.cam = cam

    def inst(self, coll, m=None):
        e = bpy.data.objects.new("i_" + coll.name, None)
        e.instance_type = "COLLECTION"
        e.instance_collection = coll
        e.matrix_world = m if m is not None else Matrix.Identity(4)
        self.sc.collection.objects.link(e)
        return e

    def lights(self, tgt, scale):
        """Key upper-left (camera side), fill right, top, cool rim from behind: all aimed at the subject."""
        cd = (self.cam.location - Vector(tgt)).normalized()
        right = cd.cross(Vector((0, 0, 1))).normalized()
        for nm, off, e, sz, col in (("key", -1.1 * right + 0.6 * cd + Vector((0, 0, 0.9)), 460, 2.0, (1.0, 0.98, 0.95)),
                                    ("fill", 1.2 * right + 0.8 * cd + Vector((0, 0, 0.1)), 90, 3.5, (0.92, 0.95, 1.0)),
                                    ("top", Vector((0, 0, 1.6)), 45, 3.0, (1, 1, 1)),
                                    ("rim", -1.3 * cd + 0.4 * right + Vector((0, 0, 0.9)), 420, 2.0, (0.85, 0.92, 1.0))):
            ld = bpy.data.lights.new(f"{self.name}_{nm}", "AREA")
            ld.energy, ld.size, ld.color = e * scale * scale, sz * scale, col
            o = bpy.data.objects.new(ld.name, ld)
            o.location = Vector(tgt) + off * 2.2 * scale
            o.rotation_euler = (Vector(tgt) - o.location).to_track_quat("-Z", "Y").to_euler()
            self.sc.collection.objects.link(o)

    def render(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.sc.render.filepath = str(path)
        bpy.ops.render.render(write_still=True, scene=self.sc.name)
        print("WROTE", path)
        return path


# ------------------------------------------------------------------------------------------------ framing
def scene_bounds(sc):
    pts = []
    for e in sc.collection.all_objects:
        if e.instance_type == "COLLECTION" and e.instance_collection:
            for o in e.instance_collection.all_objects:
                if o.type == "MESH":
                    pts += [e.matrix_world @ o.matrix_world @ Vector(c) for c in o.bound_box]
        elif e.type == "MESH":
            pts += [e.matrix_world @ Vector(c) for c in e.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


def corners(lo, hi):
    return [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]


def project(sc, cam, pts):
    from bpy_extras.object_utils import world_to_camera_view
    sc.view_layers[0].update()
    cam.data.shift_x = cam.data.shift_y = 0.0
    p = [world_to_camera_view(sc, cam, q) for q in pts]
    return min(q.x for q in p), max(q.x for q in p), min(q.y for q in p), max(q.y for q in p)


def aim(cam, target, direction, dist):
    d = Vector(direction).normalized()
    cam.location = Vector(target) + d * dist
    q = (-d).to_track_quat("-Z", "Y")
    cam.matrix_world = Matrix.Translation(cam.location) @ q.to_matrix().to_4x4()   # icon scenes are not active


def fit_distance(sc, cam, lo, hi, direction, fill):
    """Camera distance along direction at which the bounds' larger projected extent spans `fill` of the frame."""
    tgt = (lo + hi) / 2
    dist = 3.0 * (hi - lo).length
    for _ in range(6):
        aim(cam, tgt, direction, dist)
        x0, x1, y0, y1 = project(sc, cam, corners(lo, hi))
        dist *= max(x1 - x0, y1 - y0) / fill
    return dist


def centre(sc, cam, lo, hi):
    """Lens shift that centres the bounds' projection (the view direction is unchanged)."""
    x0, x1, y0, y1 = project(sc, cam, corners(lo, hi))
    cam.data.shift_x = (x0 + x1) / 2 - 0.5
    cam.data.shift_y = (y0 + y1) / 2 - 0.5


def shoot(s, direction, fill, out):
    lo, hi = scene_bounds(s.sc)
    d = fit_distance(s.sc, s.cam, lo, hi, direction, fill)
    aim(s.cam, (lo + hi) / 2, direction, d)
    centre(s.sc, s.cam, lo, hi)
    s.lights((lo + hi) / 2, max(0.35, (hi - lo).length / 2.2))
    return s.render(out)


def icon(name, collection, out, direction=DEFAULT_DIRECTION, fill=DEFAULT_FILL, exposure=EXPOSURE, size=SIZE):
    """One icon of `collection` to `out` (PNG)."""
    s = Icon(name, size=size, exposure=exposure)
    s.inst(collection)
    return shoot(s, direction, fill, out)
