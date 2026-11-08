"""Blender side of a look set's measured views (`preview.diag`): renders the raw tiles into `<set>/diag/` and writes
`diag/diag.json`; `diag.compose` draws the sheets from them.

One extra scene with nothing but the block (its collection linked in, never copied; the cameras move to it rather
than the block to them): Workbench for the clay, part colors and the
orthographic views (a fraction of a second each), EEVEE with a flat override material for the wireframe and the
section cuts (the cut is the orthographic camera's near clip plane through the block's center; a face seen from
behind is the inside of a part, drawn red). Called by `look_run` after the look shots; the source is never saved.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

from se2modkit.preview import diag as DG
from se2modkit.preview import lookdev, shots

__all__ = ["render"]

LENS = 50.0


def _scene(name, holder, res):
    sc = bpy.data.scenes.new(name)
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.image_settings.color_mode = "RGBA"
    try:
        sc.view_settings.view_transform = "Standard"
        sc.view_settings.look = "None"
    except Exception:
        pass
    # the block's own collection, not an instance of it: Workbench and EEVEE give an instance's parts the
    # instancer's object color, and the part colors must be each part's own
    sc.collection.children.link(holder)
    cam = bpy.data.objects.new(f"cam_{name}", bpy.data.cameras.new(f"cam_{name}"))
    cam.data.clip_start, cam.data.clip_end = 0.02, 500
    sc.collection.objects.link(cam)
    sc.camera = cam
    return sc, cam


def _aim(cam, loc, tgt, frame, lens=LENS, ortho=None, clip=0.02):
    """Point the camera from `loc` at `tgt` (look-frame coordinates); `frame` takes the look frame to the scene's (the
    block stays where the file has it, so the camera moves instead). The matrix is set by hand: this scene is never
    the active one, so Blender would not evaluate it for the projections that follow."""
    rot = (Vector(tgt) - Vector(loc)).to_track_quat("-Z", "Y").to_matrix().to_4x4()
    cam.matrix_world = frame @ Matrix.Translation(loc) @ rot
    cam.data.type = "ORTHO" if ortho else "PERSP"
    if ortho:
        cam.data.ortho_scale = ortho
    cam.data.lens = lens
    cam.data.clip_start = clip


def _workbench(sc, color_type):
    sc.render.engine = "BLENDER_WORKBENCH"
    s = sc.display.shading
    s.light, s.color_type = "STUDIO", color_type
    s.single_color = (0.78, 0.78, 0.78)
    s.show_cavity, s.cavity_type = True, "BOTH"
    s.show_backface_culling = False
    s.show_shadows = False
    sc.view_layers[0].material_override = None


def _flat(name, back=None, wire=False):
    """An emission material: white with black triangle edges (`wire`), or gray shaded by facing with `back` on faces
    seen from behind."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if m.node_tree is None:     # Blender before 5 makes a material's node tree only on request
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(em.outputs[0], out.inputs[0])
    if wire:
        w = nt.nodes.new("ShaderNodeWireframe")
        w.use_pixel_size = True
        w.inputs[0].default_value = 1.0
        inv = nt.nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        nt.links.new(w.outputs[0], inv.inputs[1])
        nt.links.new(inv.outputs[0], em.inputs[0])
        return m
    lw = nt.nodes.new("ShaderNodeLayerWeight")
    shade = nt.nodes.new("ShaderNodeMapRange")       # facing 0 (square on) .. 1 (grazing) -> 0.85 .. 0.35 gray
    shade.inputs["To Min"].default_value, shade.inputs["To Max"].default_value = 0.85, 0.35
    nt.links.new(lw.outputs["Facing"], shade.inputs["Value"])
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[7].default_value = (*back, 1.0)
    nt.links.new(shade.outputs[0], mix.inputs[6])
    nt.links.new(geo.outputs["Backfacing"], mix.inputs[0])
    nt.links.new(mix.outputs[2], em.inputs[0])
    return m


def _eevee(sc, mat):
    sc.render.engine = "BLENDER_EEVEE"
    try:
        sc.eevee.taa_render_samples = 8
    except Exception:
        pass
    sc.view_layers[0].material_override = mat


def _render(sc, path: Path) -> str:
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True, scene=sc.name)
    return path


def _rect(sc, cam, lo, hi, frame=None):
    """Pixel rect [x0, y0, x1, y1] (top-left origin) of a look-frame box, unclamped."""
    frame = frame if frame is not None else Matrix.Identity(4)
    r = sc.render
    w, h = r.resolution_x * r.resolution_percentage / 100, r.resolution_y * r.resolution_percentage / 100
    uv = [world_to_camera_view(sc, cam, frame @ Vector((x, y, z))) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
          for z in (lo[2], hi[2])]
    xs, ys = [p.x * w for p in uv], [(1 - p.y) * h for p in uv]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def _axes(sc, cam, center, frame):
    """(horizontal, vertical) look-frame axis index of an orthographic view."""
    c = world_to_camera_view(sc, cam, frame @ Vector(center))
    moves = []
    for i in range(3):
        p = list(center)
        p[i] += 1.0
        q = world_to_camera_view(sc, cam, frame @ Vector(p))
        moves.append((abs(q.x - c.x), abs(q.y - c.y)))
    return max(range(3), key=lambda i: moves[i][0]), max(range(3), key=lambda i: moves[i][1])


def render(job: dict, holder, objs, place, half, out: Path, *, look_scene=None, look_cam=None, look_png=None) -> dict:
    """Render the tiles and write `diag/diag.json`. `look_scene`/`look_cam`/`look_png`: the 3/4 front shot, for the
    gameplay-distance strip (left out when the set has none)."""
    t0 = time.monotonic()
    d = Path(out) / "diag"
    d.mkdir(parents=True, exist_ok=True)
    tile, ortho_res = DG.TILE, DG.ORTHO
    sc, cam = _scene(f"diag_{job['block']}", holder, tile)
    frame = place.inverted()
    # parts: numbered in describe's order, each object colored by its name (Workbench "object" colors)
    raw = []
    for o in objs:
        colls = [c.name for c in o.users_collection if c != holder]
        raw.append({"name": o.name, "collection": colls[0] if colls else ""})
    parts = DG.number_parts(raw)
    by_name = {o.name: o for o in objs}
    for p in parts:
        by_name[p["name"]].color = (*[c / 255 for c in p["color"]], 1.0)
    center = (0.0, 0.0, half[2])
    # 3/4 front, perspective, fit to the bounds
    direction = shots.VIEWS["3q_front"].direction
    dist = shots.fit_distance(half, direction, LENS, tile[0] / tile[1], 0.85)
    _aim(cam, tuple(center[i] + direction[i] * dist for i in range(3)), center, frame)
    _workbench(sc, "SINGLE")
    tiles = {"clay": _render(sc, d / "clay.png")}
    _workbench(sc, "OBJECT")
    tiles["parts"] = _render(sc, d / "parts.png")
    _eevee(sc, _flat("diag_wire", wire=True))
    tiles["wire"] = _render(sc, d / "wire.png")
    deps = bpy.context.evaluated_depsgraph_get()
    for p in parts:
        o = by_name[p["name"]]
        pts = [o.matrix_world @ Vector(b) for b in o.evaluated_get(deps).bound_box]
        mid = sum(pts, Vector()) / len(pts)
        uv = world_to_camera_view(sc, cam, mid)
        inside = 0 <= uv.x <= 1 and 0 <= uv.y <= 1 and uv.z > 0
        p["xy"] = [round(uv.x * tile[0], 1), round((1 - uv.y) * tile[1], 1)] if inside else None
    # section cuts: the near clip plane through the center
    sections = []
    cut = _flat("diag_section", back=(0.8, 0.012, 0.012))
    for c in DG.section_cuts(half):
        loc, tgt, _lens, ortho = lookdev.viewpoint(c["view"], half, center=center, aspect=tile[0] / tile[1], fill=0.85)
        span = (Vector(loc) - Vector(tgt)).length
        _aim(cam, loc, tgt, frame, ortho=ortho, clip=span)
        _eevee(sc, cut)
        png = _render(sc, d / f"section_{c['normal']}.png")
        sections.append({"png": str(png.relative_to(out)), "label": c["label"], "normal": c["normal"]})
    # measured orthographic views, framed on the bounds and the envelope together
    env = DG.envelope_box(half, envelope=job.get("envelope"), mount_face=job["mount_face"],
                          origin=tuple(place.translation), grid=job["grid"])
    blo = [-half[0], -half[1], 0.0]
    bhi = [half[0], half[1], 2 * half[2]]
    # a whole-cell box more than twice the block's size would shrink the block to a corner: named, not drawn
    drawn = env["from"] == "--envelope" or max(env["size_m"]) <= 2 * max(2 * h for h in half)
    flo = [min(blo[i], env["lo"][i]) if drawn else blo[i] for i in range(3)]
    fhi = [max(bhi[i], env["hi"][i]) if drawn else bhi[i] for i in range(3)]
    fhalf = tuple((fhi[i] - flo[i]) / 2 for i in range(3))
    fcenter = tuple((fhi[i] + flo[i]) / 2 for i in range(3))
    sc.render.resolution_x, sc.render.resolution_y = ortho_res
    _workbench(sc, "SINGLE")
    ortho = {}
    for v in DG.ORTHO_VIEWS:
        loc, tgt, _lens, scale = lookdev.viewpoint(v, fhalf, center=fcenter, aspect=ortho_res[0] / ortho_res[1],
                                                   fill=0.82)
        _aim(cam, loc, tgt, frame, ortho=scale)
        png = _render(sc, d / f"ortho_{v}.png")
        h, vv = _axes(sc, cam, fcenter, frame)
        ortho[v] = {"png": str(png.relative_to(out)), "bbox": _rect(sc, cam, blo, bhi, frame),
                    "envelope": _rect(sc, cam, env["lo"], env["hi"], frame) if drawn else None, "h_axis": h, "v_axis": vv,
                    "px_per_m": round(ortho_res[0] / scale, 3)}
    distance = None
    if look_cam is not None and look_png is not None:
        look_cam.matrix_world = Matrix.Translation(look_cam.location) @ look_cam.rotation_euler.to_matrix().to_4x4()
        distance = {"shot": Path(look_png).name, "rect": _rect(look_scene, look_cam, blo, bhi),
                    "targets": DG.distance_plan(half, direction)}
    size = [2000 * h for h in half]
    rot = lookdev.mount_rotation(job["mount_face"])
    src = [sum(abs(rot[i][j]) * size[i] for i in range(3)) for j in range(3)]   # back to the file's axes
    info = {"tile": list(tile), "tiles": {k: str(Path(v).relative_to(out)) for k, v in tiles.items()},
            "sections": sections, "parts": parts, "distance": distance,
            "size_mm": [round(v, 1) for v in size], "size_mm_source": [round(v, 1) for v in src],
            "envelope": {**{k: env[k] for k in ("from", "label")}, "drawn": drawn, "size_mm": [round(v * 1000, 1) for v in env["size_m"]],
                         **({"cells": env["cells"]} if "cells" in env else {})},
            "ortho": ortho}
    sc.view_layers[0].material_override = None
    info["seconds"] = round(time.monotonic() - t0, 1)
    (d / "diag.json").write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    print(f"DIAG {d} {info['seconds']} s")
    return info
