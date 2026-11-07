"""Blender side of `se2mod look`: `blender -b --python look_run.py -- JOB.json` (the job comes from `look.build_job`).

Appends the source into this session (never opens it for writing and never saves anything), maps its materials to
the stand-ins, stands the block on the look-dev deck by its mount face, renders each view with the shot standard's
names and saved cameras, frames the close-ups and records where each one sits in the 3/4 front view, and writes
`report.json`. `look.finish` does the rest outside Blender.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

from se2modkit.preview import base, look, lookdev, shots
from se2modkit.preview import mats as PM

__all__ = ["main", "run"]


def _load(job: dict) -> bpy.types.Collection:
    """The source's renderable objects in one holder collection, kept in the working view layer so they evaluate."""
    src, kind, want = job["source"], job["kind"], job["collection"]
    holder = bpy.data.collections.new("look_subject")
    bpy.context.scene.collection.children.link(holder)
    before = set(bpy.data.objects)
    if kind == "blend":
        with bpy.data.libraries.load(src, link=False) as (data_from, data_to):
            if want:
                if want not in data_from.collections:
                    raise SystemExit(f"ERROR: {Path(src).name} has no collection {want!r}")
                data_to.collections = [want]
            else:
                data_to.objects = list(data_from.objects)
        if want:
            holder.children.link(data_to.collections[0])
    elif kind in ("glb", "gltf"):
        bpy.ops.import_scene.gltf(filepath=src)
    else:
        bpy.ops.import_scene.fbx(filepath=src)
    new = [o for o in bpy.data.objects if o not in before]
    if kind == "blend":
        _bring_in(data_to.collections[0] if want else None, new)
    for o in new:
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o)
            continue
        if not want:
            for uc in list(o.users_collection):
                uc.objects.unlink(o)
            holder.objects.link(o)
    # Left in the working scene's view layer on purpose: Blender evaluates modifiers only for collections in a view
    # layer, and the checks, the bounds and the triangle count must see what the game will. The shots render their
    # own scenes, which instance this collection, so the working scene is never rendered.
    bpy.context.view_layer.update()
    return holder


def _bring_in(coll, objs) -> None:
    """Read a working file's block as a build reads it: the block's own collection renders (a saved look-dev render
    may have hidden it; its sub-collections keep their own choice), and it, the collections inside it and its objects
    are enabled in viewports. Blender does not evaluate a collection or object disabled in viewports: its objects keep
    the position of their last evaluation and come out without modifiers, so the bounds, close-ups and checks would
    read an older model than the one that ships. The source file is never written, so nothing is put back."""
    if coll is not None:
        coll.hide_render = False
        for c in (coll, *coll.children_recursive):
            c.hide_viewport = False
        objs = [o for o in coll.all_objects if o is not None]
    for o in objs:
        o.hide_viewport = False


def _render_meshes(coll) -> list:
    """The meshes a render of the collection shows, the same set a mod build exports: collections and objects hidden
    from render, and boolean cutters (`cutter*`), stay out of the bounds, the counts and the checks."""
    out = []

    def visit(c):
        if c.hide_render:
            return
        out.extend(o for o in c.objects
                   if o.type == "MESH" and not o.hide_render and not o.name.startswith("cutter"))
        for child in c.children:
            visit(child)
    visit(coll)
    return list(dict.fromkeys(out))


def _materials(objs, table: dict) -> list[str]:
    unmapped = []
    for o in objs:
        if o.type != "MESH":
            continue
        for s in o.material_slots:
            if s.material is None:
                continue
            key, missing = look.material_key(s.material.name, table)
            if missing and s.material.name not in unmapped:
                unmapped.append(s.material.name)
            s.material = PM.get(key)
    return unmapped


def _points(objs, m=Matrix.Identity(4)):
    deps = bpy.context.evaluated_depsgraph_get()
    return [m @ o.matrix_world @ Vector(b) for o in objs if o.type == "MESH" for b in o.evaluated_get(deps).bound_box]


def _tris(objs) -> int:
    deps = bpy.context.evaluated_depsgraph_get()
    n = 0
    for o in objs:
        if o.type == "MESH":
            me = o.evaluated_get(deps).to_mesh()
            me.calc_loop_triangles()
            n += len(me.loop_triangles)
            o.evaluated_get(deps).to_mesh_clear()
    return n


def _pixel_rect(sc, cam, pts):
    r = sc.render
    w, h = r.resolution_x * r.resolution_percentage / 100, r.resolution_y * r.resolution_percentage / 100
    uv = [world_to_camera_view(sc, cam, p) for p in pts]
    xs, ys = [p.x * w for p in uv], [(1 - p.y) * h for p in uv]
    cx = lambda v: round(min(w, max(0.0, v)))           # noqa: E731  (a part outside the frame clamps to its edge)
    cy = lambda v: round(min(h, max(0.0, v)))           # noqa: E731
    return [cx(min(xs)), cy(min(ys)), cx(max(xs)), cy(max(ys))]


def _shoot(shot, name, loc, tgt, lens, ortho, out):
    cam = shot.camera(loc, tgt, lens, ortho=ortho)
    shot.name = name
    path = shot.render()
    # from the camera's own location and rotation: the look scene is not the active one, so matrix_world is never
    # evaluated and would save as the identity (a later --before round would then render from inside the block)
    m = Matrix.Translation(cam.location) @ cam.rotation_euler.to_matrix().to_4x4()
    shots.save_camera(path, [list(r) for r in m], cam.data.lens,
                      cam.data.ortho_scale if cam.data.type == "ORTHO" else None)
    return cam, path


def run(job: dict) -> dict:
    t0 = time.monotonic()
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    base.configure(out, fast=job["fast"])
    holder = _load(job)
    objs = _render_meshes(holder)
    if not objs:
        raise SystemExit(f"ERROR: nothing to render in {Path(job['source']).name}")
    unmapped = _materials(objs, job["materials"])
    bpy.context.view_layer.update()
    rot = Matrix(lookdev.mount_rotation(job["mount_face"])).to_4x4()
    pts = _points(objs, rot)
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    place = Matrix.Translation(lookdev.anchor_offset(lo, hi)) @ rot
    half = tuple((hi - lo) / 2)
    center = (0.0, 0.0, half[2])
    block, stage, rnd = job["block"], job["stage"], job["round"]
    sc = lookdev.build_set(job["set"], half, name=f"look_{block}", grid=job["grid"], neighbors=job["neighbors"],
                           armor_dir=Path(job["armor_dir"]) if job["armor_dir"] else None)
    sc.shot.inst(holder, place)
    cams = {}
    for view in job["views"]:
        before = job["before"].get(view)
        extra = "after" if before else ""
        name = shots.shot_name(block, stage, rnd, view, extra)
        if before:
            c = before["camera"]
            cam = sc.shot.camera((0, 0, 0), (0, 0, -1), c["lens"], ortho=c.get("ortho"))
            cam.matrix_world = Matrix(c["matrix"])
            sc.shot.name = name
            path = sc.shot.render()
            shots.save_camera(path, c["matrix"], c["lens"], c.get("ortho"))
            dst = out / f"{shots.shot_name(block, stage, rnd, view, 'before')}.png"
            shutil.copyfile(before["png"], dst)
            shots.save_camera(dst, c["matrix"], c["lens"], c.get("ortho"))
        else:
            loc, tgt, lens, ortho = lookdev.viewpoint(view, half, center=center)
            cam, _ = _shoot(sc.shot, name, loc, tgt, lens, ortho, out)
        cams[view] = cam
    close, close_missing = {}, []
    if job["close"]:
        if "3q_front" not in cams:
            loc, tgt, lens, ortho = lookdev.viewpoint("3q_front", half, center=center)
            cams["3q_front"], _ = _shoot(sc.shot, shots.shot_name(block, stage, rnd, "3q_front"), loc, tgt, lens,
                                         ortho, out)
        overview = shots.shot_name(block, stage, rnd, "3q_front", "after" if job["before"].get("3q_front") else "")
        for part in job["close"]:
            names = look.close_hits([o.name for o in objs], part)
            hits = [o for o in objs if o.name in names]
            if not hits:
                print(f"ERROR: no part named {part!r} for a close-up")
                close_missing.append(part)
                continue
            ppts = _points(hits, place)
            plo = Vector([min(p[i] for p in ppts) for i in range(3)])
            phi = Vector([max(p[i] for p in ppts) for i in range(3)])
            phalf, pc = tuple((phi - plo) / 2), (plo + phi) / 2
            d = Vector(shots.VIEWS["3q_front"].direction)
            lens = lookdev.game_lens()
            dist = shots.fit_distance(phalf, tuple(d), lens, 16 / 9, 0.6)
            name = shots.shot_name(block, stage, rnd, "3q_front", f"close-{part}")
            _shoot(sc.shot, name, tuple(pc + d * dist), tuple(pc), lens, None, out)
            close[name] = {"overview": f"{overview}.png", "rect": _pixel_rect(sc.shot.sc, cams["3q_front"], ppts)}
        (out / "close.json").write_text(json.dumps(close, indent=1) + "\n", encoding="utf-8")
    if job.get("geocheck", True):
        from se2modkit.pipeline import geocheck as GC
        from se2modkit.preview.geocheck_blend import parts_from_objects
        GC.guarded(lambda: GC.run(parts_from_objects(objs, place), tris_budget=job.get("tris_budget"))).write(
            out / "geocheck.json")
    report = {"source": job["source"], "set": job["set"], "views": job["views"], "seconds": 0.0,
              "unmapped_materials": unmapped, "close_missing": close_missing, "tris": _tris(objs),
              "half_extents": list(half)}
    report["seconds"] = round(time.monotonic() - t0, 1)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"LOOK {out} {report['seconds']} s")
    return report


def main(argv=None) -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if argv is None and "--" in sys.argv else (argv or [])
    run(json.loads(Path(argv[0]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
