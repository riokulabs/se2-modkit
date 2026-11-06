"""The Blender half of the ship check (`ship_check`, run by `Runner.check_ship`): what a block's export takes, which
working-file collections it came from, and a tinted exploded picture of its fracture zones. Blender only.

capture_sources()   a context in which every `bpy.data.libraries.load` (a mod's block builder appending its collection
                    from a working .blend) is recorded as it lands: each collection with every object as saved, its
                    viewport and render state, and whether it ships. The rule: the loaded collection is the block (the
                    builder took it by name), so it ships whatever its own flags; below it, an object ships when it is
                    geometry and neither it nor a collection between it and the block is hidden from renders, and it
                    is not a boolean operand of another object. Viewport hiding never matters (the export takes render
                    visibility), which is why an object hidden on screen but shipping is reported.
shipping_set(built) every model the export takes (the block and subpart LODs as built) and the collision objects.
render_exploded(...) the fracture zones of the cut LOD0 (`fracture_mesh.cut_lod`), each in its own color, pushed out
                    from the block's center, each with its bounding box drawn, and a legend of zone names and health
                    shares: Workbench, in a scene of its own (headless, no GPU job needed), removed afterwards.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector

from . import fracture_mesh as FM

__all__ = ["GEOMETRY", "PALETTE", "Sources", "capture_sources", "render_exploded", "shipping_set", "zone_bounds"]

GEOMETRY = ("MESH", "CURVE", "SURFACE", "META", "FONT")
# Tableau 10: ten hues that stay apart under Workbench shading (the first palette had two near-identical olives)
PALETTE = ((0.84, 0.15, 0.16), (0.12, 0.47, 0.71), (1.00, 0.50, 0.05), (0.17, 0.63, 0.17), (0.58, 0.40, 0.74),
           (0.55, 0.34, 0.29), (0.89, 0.47, 0.76), (0.50, 0.50, 0.50), (0.74, 0.74, 0.13), (0.09, 0.75, 0.81))
RES = (1600, 1000)
LEGEND = 0.42         # share of the picture's width kept for the legend, on the left
EXPLODE = 0.5         # each zone moves this share of its center's distance from the block's center ...
GAP = 0.08            # ... plus this share of the block's diagonal


class Sources:
    """The working-file collections loaded while it was active: [{"blend", "collection", "objects"}]."""

    def __init__(self):
        self.entries: list[dict] = []

    def record(self, blend: str, dst) -> None:
        for c in getattr(dst, "collections", None) or ():
            if c is not None:
                self.entries.append({"blend": blend, "collection": c.name, "objects": _objects(c)})
        loose = [o for o in getattr(dst, "objects", None) or () if o is not None]
        if loose:
            self.entries.append({"blend": blend, "collection": "(objects)",
                                 "objects": [_object(o, o.name, False, False, set()) for o in loose]})

    def report(self) -> list[dict]:
        return list(self.entries)


def _object(o, path, coll_hidden_v, coll_hidden_r, cutters) -> dict:
    why = ("render-hidden collection" if coll_hidden_r else "render-hidden" if o.hide_render else
           "boolean cutter" if o.name in cutters else None if o.type in GEOMETRY else f"not geometry ({o.type})")
    return {"name": o.name, "path": path, "type": o.type, "ships": why is None, "why": why,
            "viewport_hidden": bool(coll_hidden_v or o.hide_viewport)}


def _objects(top) -> list[dict]:
    """Every object under the loaded collection `top`, as saved (see the module doc for what ships)."""
    every = [o for o in top.all_objects if o is not None]
    cutters = set()
    for o in every:
        for m in getattr(o, "modifiers", ()):
            if m.type == "BOOLEAN":
                if getattr(m, "object", None) is not None:
                    cutters.add(m.object.name)
                if getattr(m, "collection", None) is not None:
                    cutters.update(x.name for x in m.collection.all_objects if x is not None)
    out, seen = [], set()

    def walk(c, path, hid_v, hid_r):
        for o in c.objects:
            if o is not None and o.name not in seen:
                seen.add(o.name)
                out.append(_object(o, f"{path}/{o.name}", hid_v, hid_r, cutters))
        for ch in c.children:
            walk(ch, f"{path}/{ch.name}", hid_v or ch.hide_viewport, hid_r or ch.hide_render)
    walk(top, top.name, top.hide_viewport, False)
    return out


class _Load:
    """The context `libraries.load` returns, recording what it loaded on a clean exit."""

    def __init__(self, cm, blend, sink):
        self.cm, self.blend, self.sink, self.dst = cm, blend, sink, None

    def __enter__(self):
        src, self.dst = self.cm.__enter__()
        return src, self.dst

    def __exit__(self, *exc):
        r = self.cm.__exit__(*exc)
        if exc[0] is None and self.dst is not None:
            self.sink.record(self.blend, self.dst)
        return r


@contextmanager
def capture_sources():
    """Record every working-file load in the block (yields a `Sources`). Wraps `BlendDataLibraries.load` the way
    `incremental.install_bpy_wrappers` does (Blender hands the method over unbound, so the previous one is looked up
    through the collection proxy), on top of whatever is there, and puts it back after."""
    t = bpy.types.BlendDataLibraries
    prev = t.load
    sink = Sources()

    def load(*a, **k):
        rest = a[1:] if a and isinstance(a[0], t) else a
        filepath = rest[0] if rest else k.get("filepath")
        t.load = prev
        try:
            fn = bpy.data.libraries.load
        finally:
            t.load = load
        blend = os.path.abspath(bpy.path.abspath(os.fsdecode(filepath))) if filepath else ""
        return _Load(fn(*rest, **k), blend, sink)
    load._se2_read = getattr(prev, "_se2_read", False)
    t.load = load
    try:
        yield sink
    finally:
        t.load = prev


def _tris(ob) -> int:
    return sum(len(p.vertices) - 2 for p in ob.data.polygons) if ob.type == "MESH" else 0


def shipping_set(built) -> tuple[list[dict], list[str]]:
    """([{"model", "collection", "objects": [{"name", "tris"}]}], [collision object names]): what `Runner.export`
    hands the exporter for this block, whatever its viewport state."""
    models = [(f"LOD{i}", c, objs) for i, (c, objs) in enumerate(built["lods"])]
    for name, lods in (built.get("subparts") or {}).items():
        models += [(f"{name}_LOD{i}", c, objs) for i, (c, objs) in enumerate(lods)]
    out = [{"model": m, "collection": getattr(c, "name", str(c)),
            "objects": [{"name": o.name, "tris": _tris(o)} for o in objs]} for m, c, objs in models]
    coll = [(c[1] if isinstance(c, tuple) else c).name for c in built.get("collision") or ()]
    return out, coll


# ------------------------------------------------------------------------------------------------ the picture
def _zone_object(src, zi, name, coll):
    """A copy of `src` (a cut LOD or a cap) in the world frame, keeping only the faces of zone zi (None: all)."""
    me = src.data.copy()
    bm = bmesh.new()
    bm.from_mesh(me)
    if zi is not None:
        layer = bm.faces.layers.int.get(FM.ZONE)
        drop = [f for f in bm.faces if layer is None or f[layer] != zi + 1]
        bmesh.ops.delete(bm, geom=drop, context="FACES")
    bm.transform(src.matrix_world)
    bm.to_mesh(me)
    bm.free()
    if not len(me.polygons):
        bpy.data.meshes.remove(me)
        return None
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _bounds(objs):
    pts = []
    for o in objs:
        co = np.empty(len(o.data.vertices) * 3)
        o.data.vertices.foreach_get("co", co)
        pts.append(co.reshape(-1, 3))
    pts = np.concatenate(pts)
    return pts.min(0), pts.max(0)


def zone_bounds(plan, whole, caps) -> dict:
    """{zone id: [lo, hi]} of each zone's faces and break faces in the block frame (m)."""
    scratch = bpy.data.collections.new("SE2_ShipBounds")
    try:
        out = {}
        for zi, z in enumerate(plan.zones):
            objs = [o for o in (_zone_object(whole, zi, "b", scratch),
                                caps.get(zi) and _zone_object(caps[zi], None, "c", scratch)) if o]
            if objs:
                lo, hi = _bounds(objs)
                out[z.id] = [[round(float(x), 4) for x in lo], [round(float(x), 4) for x in hi]]
            _remove(objs)
        return out
    finally:
        bpy.data.collections.remove(scratch)


def _remove(objs):
    meshes = [o.data for o in objs if o.type == "MESH"]
    bpy.data.batch_remove(objs)
    bpy.data.batch_remove([m for m in meshes if m.users == 0])


def _box_frame(name, lo, hi, t, coll):
    """The 12 edges of the box [lo, hi] as square beams `t` thick (Workbench draws faces, not bare edges)."""
    verts, faces = [], []

    def beam(a, b):
        blo = [min(a[i], b[i]) - t / 2 for i in range(3)]
        bhi = [max(a[i], b[i]) + t / 2 for i in range(3)]
        k = len(verts)
        verts.extend((x, y, z) for x in (blo[0], bhi[0]) for y in (blo[1], bhi[1]) for z in (blo[2], bhi[2]))
        faces.extend([(k, k + 1, k + 3, k + 2), (k + 4, k + 6, k + 7, k + 5), (k, k + 4, k + 5, k + 1),
                      (k + 2, k + 3, k + 7, k + 6), (k, k + 2, k + 6, k + 4), (k + 1, k + 5, k + 7, k + 3)])
    c = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    for i in range(8):
        for j in range(i + 1, 8):
            if sum(c[i][k] != c[j][k] for k in range(3)) == 1:
                beam(c[i], c[j])
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _text(name, body, size, color, cam, loc, coll):
    cu = bpy.data.curves.new(name, "FONT")
    cu.body, cu.size = body, size
    ob = bpy.data.objects.new(name, cu)
    ob.color = (*color, 1.0)
    coll.objects.link(ob)
    ob.parent = cam
    ob.location = loc
    return ob


def _swatch(name, size, color, cam, loc, coll):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0)], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new(name, me)
    ob.color = (*color, 1.0)
    coll.objects.link(ob)
    ob.parent = cam
    ob.location = loc
    return ob


def render_exploded(title, plan, whole, caps, shares, path: Path) -> Path:
    """Render the picture (see the module doc) to `path` (PNG). shares: {zone id: health share}."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.data.scenes.new("SE2_ShipPreview")
    world = bpy.data.worlds.new("SE2_ShipPreview")
    coll = scene.collection
    made, extra = [], []
    try:
        zones = []
        for zi, z in enumerate(plan.zones):
            objs = [o for o in (_zone_object(whole, zi, f"zone_{z.id}", coll),
                                caps.get(zi) and _zone_object(caps[zi], None, f"zone_{z.id}_break", coll)) if o]
            made += objs
            if objs:
                zones.append((zi, z, objs, *_bounds(objs)))
        if not zones:
            raise RuntimeError("the cut LOD0 has no zone faces")
        lo = np.min([z[3] for z in zones], axis=0)
        hi = np.max([z[4] for z in zones], axis=0)
        center, diag = (lo + hi) / 2, float(np.linalg.norm(hi - lo)) or 1.0
        corners = []
        for zi, z, objs, zlo, zhi in zones:
            d = (zlo + zhi) / 2 - center
            n = float(np.linalg.norm(d))
            off = d * EXPLODE + (d / n * GAP * diag if n > 1e-9 else 0.0)
            col = PALETTE[zi % len(PALETTE)]
            for o in objs:
                o.location = Vector(off)
                o.color = (*col, 1.0)
            frame = _box_frame(f"box_{z.id}", zlo + off, zhi + off, 0.004 * diag, coll)
            frame.color = (*(c * 0.55 for c in col), 1.0)
            made.append(frame)
            corners += [(x, y, w) for x in (zlo[0] + off[0], zhi[0] + off[0]) for y in (zlo[1] + off[1],
                        zhi[1] + off[1]) for w in (zlo[2] + off[2], zhi[2] + off[2])]
        # camera: orthographic, from the front-right and above (the block frame's +Y is its front), fitted to the
        # exploded boxes, the model in the right part of the picture and the legend on the left
        view = Vector((0.85, 1.25, 0.8)).normalized()
        right = (-view).cross(Vector((0, 0, 1))).normalized()
        up = right.cross(-view)
        c = Vector(center)
        xs = [(Vector(p) - c).dot(right) for p in corners]
        ys = [(Vector(p) - c).dot(up) for p in corners]
        aspect = RES[0] / RES[1]
        S = max((max(xs) - min(xs)) / (1 - LEGEND - 0.06), (max(ys) - min(ys)) * aspect / 0.88, 1e-3)
        mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        dist = max(10.0, 4 * diag)
        cam = bpy.data.objects.new("SE2_ShipCam", bpy.data.cameras.new("SE2_ShipCam"))
        extra.append(cam.data)
        made.append(cam)
        coll.objects.link(cam)
        cam.data.type, cam.data.ortho_scale = "ORTHO", S
        cam.data.clip_start, cam.data.clip_end = 0.01, dist * 3
        cam.location = c + right * (mx - S * LEGEND / 2) + up * my + view * dist
        cam.rotation_euler = (-view).to_track_quat("-Z", "Y").to_euler()
        scene.camera = cam
        h = S / aspect
        size = 0.026 * S
        line = size * 1.6
        x0, y = -S / 2 + 0.03 * S, h / 2 - 0.05 * h - size
        made.append(_text("legend_title", title, size * 1.1, (0.95, 0.95, 0.95), cam, (x0, y, -1.0), coll))
        for zi, z, *_ in zones:
            y -= line
            col = PALETTE[zi % len(PALETTE)]
            tags = ", critical" if z.critical else ""
            made.append(_swatch(f"legend_sw_{z.id}", size, col, cam, (x0, y, -1.0), coll))
            made.append(_text(f"legend_{z.id}", f"{z.id}  {shares.get(z.id, 0.0) * 100:.1f}%  {z.label}{tags}", size,
                              (0.9, 0.9, 0.9), cam, (x0 + size * 1.6, y, -1.0), coll))
        extra += [o.data for o in made if o.type == "FONT"]
        scene.world = world
        world.color = (0.07, 0.075, 0.085)
        r = scene.render
        r.engine = "BLENDER_WORKBENCH"
        r.resolution_x, r.resolution_y, r.resolution_percentage = *RES, 100
        r.image_settings.file_format = "PNG"
        r.filepath = str(path)
        sh = scene.display.shading
        sh.light, sh.color_type = "STUDIO", "OBJECT"
        sh.show_object_outline = True
        bpy.ops.render.render(write_still=True, scene=scene.name)
        if not path.is_file():
            raise RuntimeError(f"the render wrote no {path.name}")
        return path
    finally:
        meshes = [o.data for o in made if o.type == "MESH"]
        bpy.data.batch_remove(made)
        bpy.data.batch_remove([m for m in meshes if m.users == 0] + [d for d in extra if d.users == 0])
        bpy.data.scenes.remove(scene)
        bpy.data.worlds.remove(world)
