"""`se2mod look`: a fast look set of one block from its working file, in the look-dev scene, through the job queue."""

from __future__ import annotations

import argparse
import inspect
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .. import jobqueue as Q
from ..env.core import SE2Env
from . import register

LOOK_TIMEOUT = 900   # s


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", type=Path, help="the working file: .blend, .glb, .gltf or .fbx (read, never written)")
    p.add_argument("--block", required=True, help="block name for the file names")
    p.add_argument("--mod", help="mod name or folder: its previews.toml [lookdev] and preview folder (default: here)")
    p.add_argument("--out", type=Path, help="look folder (default: the mod's Source/Model/preview/look); the set "
                                            "goes in <out>/<block>/r<N>/")
    p.add_argument("--set", dest="set_name", default="neighbors", help="hull, neighbors (default), interior, space")
    p.add_argument("--views", default="look", help="look (default), concept, or a comma list of views (a set is "
                                                  "ready only when it has every view its --stage requires)")
    p.add_argument("--interior", action="store_true", help="the block has an inside: add the inside view")
    p.add_argument("--stage", default="look", choices=("look", "concept", "fix"))
    p.add_argument("--round", type=int, dest="rnd", metavar="N", help="round number (default: the next free one)")
    p.add_argument("--collection", default="", help="only this collection of a .blend (default: every object)")
    p.add_argument("--mount-face", default="-Z", help="the face the block mounts by: -Z (default), +Z, -Y, +Y, -X, +X")
    p.add_argument("--grid", default="large", choices=("large", "small"))
    p.add_argument("--before", type=Path, metavar="FOLDER", help="an earlier set: reuse its cameras, write pairs")
    p.add_argument("--close", action="append", default=[], metavar="PART", help="a close-up of this part (repeatable)")
    p.add_argument("--material", action="append", default=[], metavar="NAME=SLOT",
                   help="map a working-file material to a Keen slot, on top of [lookdev.materials] (repeatable)")
    p.add_argument("--full", action="store_true", help="full resolution and samples (default: fast)")
    p.add_argument("--no-wait", action="store_true", help="queue it and return (the set is checked only when waited for)")
    p.add_argument("--print-only", action="store_true", help="write the job and print the queue command; queue nothing")


def _has_priority_lane() -> bool:
    return "priority" in inspect.signature(Q.Store.submit).parameters


def _lookdev_config(env: SE2Env, mod: str | None):
    """(look folder default, LookdevConfig) from the mod's project, or (None, empty) outside one."""
    from ..preview import plan as PL
    from . import modarg
    from ..project import ProjectError
    try:
        ref = modarg.resolve(env, mod, need="config")
    except ProjectError as e:   # only "no project here" means outside a mod; a broken one is reported
        if mod or not modarg._no_project(e):
            raise
        return None, PL.LookdevConfig()
    cfg = ref.config
    if cfg is None:
        return None, PL.LookdevConfig()
    path = PL.find_plan(ref.root, cfg.paths.source_model)
    if path is None:
        return cfg.paths.source_model / "preview" / "look", PL.LookdevConfig()
    import tomllib
    return cfg.paths.source_model / "preview" / "look", PL.parse_lookdev(tomllib.loads(path.read_text("utf-8")),
                                                                          str(path))


@register("look", "fast look set of a block from its working file, in the look-dev scene (queued)", add_args=_args,
          check_game_version=False)
def cmd_look(args: argparse.Namespace, env: SE2Env) -> int:
    from ..preview import PreviewError, look
    from ..preview import plan as PL
    from ..project import ProjectError
    from .blender_cmd import blender_argv, package_root
    try:
        default_out, ld = _lookdev_config(env, args.mod)
        out_root = args.out or default_out
        if out_root is None:
            raise PreviewError("not in a mod project: give --out (the look folder) or --mod.")
        block_dir = Path(out_root) / args.block
        rnd = args.rnd or look.next_round(block_dir)
        materials = dict(ld.materials)
        for pair in args.material:
            name, sep, slot = pair.partition("=")
            if not sep or not name or not slot:
                raise PreviewError(f"--material {pair!r}: give NAME=SLOT, such as rb_Bolt=MetalDull.")
            materials[name] = slot
        armor = Path(env.sdk_assets) / "Armors" if getattr(env, "sdk_assets", None) else None
        job = look.build_job(source=args.source, block=args.block, out_dir=block_dir / f"r{rnd}", stage=args.stage,
                             rnd=rnd, views=look.views_for(args.views, args.interior), set_name=args.set_name,
                             collection=args.collection, mount_face=args.mount_face, grid=args.grid,
                             neighbors=ld.neighbors, materials=materials,
                             armor_dir=armor if armor and armor.is_dir() else None, before=args.before,
                             close=args.close, fast=not args.full)
    except (PreviewError, PL.PlanError, ProjectError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    job_file = out / "job.json"      # beside its set, so two mods' jobs never share a name
    job_file.write_text(json.dumps(job, indent=1) + "\n", encoding="utf-8", newline="\n")
    script = Path(look.__file__).with_name("look_run.py")
    argv = blender_argv(env, script, [str(job_file)], mod_source=package_root(env), windowed=False)
    argv[1:1] = ["--gpu-backend", "vulkan", "--gpu-device", "{gpu}"]
    name = f"look {args.block} {args.stage} r{rnd} ({args.set_name})"
    priority = _has_priority_lane()
    if args.print_only:
        qargv = look.queue_argv(job_file, argv, name=name, priority=priority, timeout=LOOK_TIMEOUT)
        print("se2mod " + (subprocess.list2cmdline(qargv) if env.platform.startswith("win") else shlex.join(qargv)))
        return 0
    store = Q.Store(env.cache_dir / "queue")
    extra = {"priority": True} if priority else {}
    qjob = store.submit(argv, name=name, load=Q.LOADS[look.LOAD], timeout=LOOK_TIMEOUT, **extra)
    Q.ensure_runner(store)
    print(f"queued job {qjob.id}: {out}")
    if args.no_wait:
        return 0
    code = Q.wait(store, qjob.id, LOOK_TIMEOUT + 120)
    if code:
        print(f"error: the look job {qjob.id} failed (exit {code}); see `se2mod queue log {qjob.id}`.",
              file=sys.stderr)
        return 1
    report = look.finish(out, block=args.block, stage=args.stage, rnd=rnd, interior=args.interior)
    print(f"{out / 'sheet.png'}  ({report.get('seconds', '?')} s in Blender, {report.get('tris', '?')} triangles)")
    for line in report["problems"]:
        print(f"  not ready: {line}")
    return 1 if report["problems"] else 0
