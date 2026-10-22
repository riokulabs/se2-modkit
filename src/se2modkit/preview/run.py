"""Blender entry of `se2mod preview`: render the shots of one job (`preview.plan.job`) from the exported models.

    blender -b --python <se2modkit>/preview/run.py -- JOB.json      (se2mod preview writes the job and runs this)

Per shot and block (or once for the shot's blocks side by side, `together`): the block's exported NonFractured LOD
(state intact) or its Fractured / Damaged LOD0 composed by `preview.states.compose` (construction ratio, damage
scenario, explicit zone states, debris), stood on the floor grid centred at the origin, the camera preset fitted to
the bounds (or the custom camera relative to the centre), one `base.Shot` render into the job's output folder. Prints
`WROTE <png>` per image and `PREVIEW DONE <n>` at the end; a problem is one `ERROR: <sentence>` line and exit 2.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from se2modkit.pipeline.hooks import ModHooks
from se2modkit.preview import base as PB
from se2modkit.preview import mats as PM
from se2modkit.preview import plan as PL
from se2modkit.preview import states as ST
from se2modkit.project import ModPaths

GAP = 0.5   # m between blocks shown together


class JobError(Exception):
    pass


def hooks_of(job: dict) -> ModHooks:
    p = {k: (Path(v) if v else None) for k, v in job["mod"]["paths"].items()}
    return ModHooks(prefix=job["mod"]["prefix"], name=job["mod"]["name"], paths=ModPaths(**p))


def shot_of(d: dict) -> PL.Shot:
    d = dict(d, blocks=tuple(d["blocks"]), resolution=tuple(d["resolution"]))
    return PL.Shot(**d)


def inside(base: Path, path: Path, what: str) -> Path:
    try:
        return PL.contained(base, path, what)
    except PL.PlanError as e:
        raise JobError(str(e)) from None


def nf_fbx(hooks, block: str, lod: int) -> Path:
    inside(hooks.paths.models, Path(hooks.paths.models) / block, "the block folder")
    d = Path(hooks.paths.models) / block / "NonFractured"
    p = d / (f"{block}.fbx" if lod == 0 else f"{block}_LOD{lod}.fbx")
    if not p.is_file():
        raise JobError(f"{block} has no exported model {p.name} under {d.parent}: export the block first "
                       f"(se2mod blender run --windowed <build script> -- {block} --no-render --export).")
    return p


def add_block(hooks, sc, block: str, shot: PL.Shot, keep: list) -> list:
    """Link the block's parts for this shot's state into scene sc; returns the new objects."""
    before = set(sc.collection.objects)
    if shot.state == "intact":
        for o in ST.load(nf_fbx(hooks, block, shot.lod)):
            PM.apply([o], shot.paint)
            sc.collection.objects.link(o)
    else:
        inside(hooks.paths.models, Path(hooks.paths.models) / block, "the block folder")
        try:
            subj = ST.ours(hooks, block, "")
        except (FileNotFoundError, OSError) as e:
            raise JobError(f"{block}: {e}") from None
        name = PL.STATES[shot.state][0] or "c00"
        ST.compose(hooks, sc, subj, name, ratio=shot.stage, zones=shot.zones, paint=shot.paint)
        keep.append(subj)
    return [o for o in sc.collection.objects if o not in before and o.type == "MESH"]


def _move(objs, offset):
    m = Matrix.Translation(offset)
    for o in objs:
        o.matrix_world = m @ o.matrix_world


def fit_camera(s: PB.Shot, objs, shot: PL.Shot):
    from bpy_extras.object_utils import world_to_camera_view
    lo, hi = ST.bounds(objs)
    c, size = (lo + hi) / 2, hi - lo
    cam = shot.camera
    if isinstance(cam, dict):
        s.camera(tuple(c + Vector(cam["position"])), tuple(c + Vector(cam["target"])), cam.get("lens", shot.lens),
                 ortho=cam.get("ortho"))
        return
    d, ortho = PL.CAMERAS[cam]
    d = Vector(d).normalized()
    if ortho:
        across = [abs(size[i]) for i in range(3) if abs(d[i]) < 0.5]
        s.camera(tuple(c + d * (size.length * 2 + 1)), tuple(c), shot.lens, ortho=max(across) / shot.fill)
        return
    s.camera(tuple(c + d * size.length * 2), tuple(c), shot.lens)
    dist = size.length * 2
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    for _ in range(6):
        s.cam.location = c + d * dist
        s.cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        s.sc.view_layers[0].update()
        p = [world_to_camera_view(s.sc, s.cam, q) for q in corners]
        span = max(max(q.x for q in p) - min(q.x for q in p), max(q.y for q in p) - min(q.y for q in p))
        dist *= span / shot.fill


def render_one(hooks, shot: PL.Shot, blocks: tuple[str, ...], fname: str) -> Path:
    out = PB.settings()["out"]
    inside(out, out / f"{fname}.png", "the image")
    s = PB.Shot(fname, res=shot.resolution, floor=None, lighting=shot.lighting)
    keep: list = []
    placed = []
    cursor = 0.0
    for b in blocks:              # side by side along -X (image left to right from the front)
        objs = add_block(hooks, s.sc, b, shot, keep)
        if not objs:
            raise JobError(f"{b}: nothing to show in state {shot.state!r}.")
        lo, hi = ST.bounds(objs)
        _move(objs, Vector((cursor - hi.x, -(lo.y + hi.y) / 2, -lo.z)))
        cursor -= (hi.x - lo.x) + GAP
        placed += objs
    lo, hi = ST.bounds(placed)
    _move(placed, Vector((-(lo.x + hi.x) / 2, 0, 0)))
    lo, hi = ST.bounds(placed)
    size = hi - lo
    big = max(size.x, size.y, size.z)
    scale = max(1.0, big / 2.5)
    # the light rig and floor follow the subject's size
    for o in [o for o in s.sc.collection.objects if o.type == "LIGHT"]:
        o.location = o.location * scale
        o.data.energy *= scale * scale
        o.data.size *= scale
        o.rotation_euler = (Vector((0, 0, scale)) - o.location).to_track_quat("-Z", "Y").to_euler()
    m = big * 0.75 + 1.0
    s.grid(lo.x - m, hi.x + m, lo.y - m, hi.y + m, z=0.0, step=0.25 if big < 6 else 1.0)
    fit_camera(s, placed, shot)
    path = s.render()
    for subj in keep:
        subj.free()
    return path


def main(argv: list[str]) -> int:
    if not argv:
        print("ERROR: no job file (se2mod preview writes one and runs this).", file=sys.stderr)
        return 2
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    hooks = hooks_of(job)
    out = Path(job["out"])
    try:
        PB.check_output(hooks.paths.source_model, out)
    except PB.PreviewError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    PM.configure(legacy_prefix=hooks.prefix)
    PB.configure(out, fast=job.get("fast", False), only=())
    PM.get("Caption")
    written = []
    try:
        for d in job["shots"]:
            shot = shot_of(d)
            single = shot.together or len(shot.blocks) == 1
            groups = [shot.blocks] if single else [(b,) for b in shot.blocks]
            for g in groups:
                written.append(render_one(hooks, shot, g, shot.name if single else f"{shot.name}_{g[0]}"))
    except (JobError, PB.PreviewError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        manifest = inside(out, out / "shots.json", "the shot list")
    except JobError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    manifest.write_text(json.dumps([p.name for p in written], indent=1) + "\n", encoding="utf-8")
    print(f"PREVIEW DONE {len(written)}")
    return 0


if __name__ == "__main__":
    rc = main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    if rc:
        sys.exit(rc)
