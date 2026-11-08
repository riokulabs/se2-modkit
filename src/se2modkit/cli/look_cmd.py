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
from .describe_cmd import _envelope

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
    p.add_argument("--tris-budget", type=int, metavar="N", help="fail the set over N triangles (the kit's budget)")
    p.add_argument("--no-check", action="store_true", help="skip the geometry checks (a quick warp test)")
    p.add_argument("--no-diag", action="store_true",
                   help="skip the measured views: diag.png (clay, silhouette, wireframe, numbered parts, section "
                        "cuts, gameplay-distance sizes) and ortho.png (front, side, top with sizes in mm)")
    p.add_argument("--envelope", type=_envelope, metavar="X,Y,Z",
                   help="the block's size in meters along the file's X, Y, Z, centered on its origin: drawn on "
                        "ortho.png (default: the smallest whole-grid-cell box around the model)")
    p.add_argument("--reference", type=Path, metavar="IMG",
                   help="a front or side picture to compare silhouettes with: writes refdiff.png and its overlap "
                        "(IoU) in report.json")
    p.add_argument("--reference-view", default="front", choices=("front", "side"),
                   help="which of our views --reference shows: front (default) or side")
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


def run_look(args: argparse.Namespace, env: SE2Env, *, say=None) -> dict:
    """Write the look job, queue it, wait for it and check the set (as `se2mod look` does), returning what happened.

    The result always has `code` (the command's exit code) and `warnings`; then `error`, or `queue_command`
    (`--print-only`), or `job_id` and `set_dir`, and once waited for `sheet`, `report_path`, `report`, `not_ready`
    and `geocheck` (the geometry checks' file, when they ran). `say` gets progress lines (default: stderr)."""
    from ..preview import PreviewError, look
    from ..preview import plan as PL
    from ..project import ProjectError
    from .blender_cmd import blender_argv, package_root
    say = say or (lambda line: print(line, file=sys.stderr))
    res: dict = {"code": 0, "warnings": []}
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
                             close=args.close, fast=not args.full, geocheck=not args.no_check,
                             tris_budget=args.tris_budget, diag=not args.no_diag, envelope=args.envelope,
                             reference=args.reference, reference_view=args.reference_view)
    except (PreviewError, PL.PlanError, ProjectError) as e:
        return {**res, "code": 1, "error": str(e)}
    if args.no_check and args.tris_budget is not None:
        res["warnings"].append("--tris-budget is not checked with --no-check (the geometry checks are skipped).")
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    job_file = out / "job.json"      # beside its set, so two mods' jobs never share a name
    job_file.write_text(json.dumps(job, indent=1) + "\n", encoding="utf-8", newline="\n")
    res.update(set_dir=str(out), job_file=str(job_file), round=rnd)
    script = Path(look.__file__).with_name("look_run.py")
    argv = blender_argv(env, script, [str(job_file)], mod_source=package_root(env), windowed=False)
    # never run scripts saved in the working file (drivers, registered text blocks)
    argv[1:1] = ["--disable-autoexec", "--gpu-backend", "vulkan", "--gpu-device", "{gpu}"]
    name = f"look {args.block} {args.stage} r{rnd} ({args.set_name})"
    priority = _has_priority_lane()
    if args.print_only:
        qargv = look.queue_argv(job_file, argv, name=name, priority=priority, timeout=LOOK_TIMEOUT)
        res["queue_command"] = "se2mod " + (subprocess.list2cmdline(qargv) if env.platform.startswith("win")
                                            else shlex.join(qargv))
        return res
    store = Q.Store(env.cache_dir / "queue")
    extra = {"priority": True} if priority else {}
    qjob = store.submit(argv, name=name, load=Q.LOADS[look.LOAD], timeout=LOOK_TIMEOUT, **extra)
    Q.ensure_runner(store)
    res["job_id"] = qjob.id
    say(f"queued job {qjob.id}: {out}")
    if args.no_wait:
        return res
    code = Q.wait(store, qjob.id, LOOK_TIMEOUT + 120)
    if code:
        return {**res, "code": 1, "error": f"the look job {qjob.id} failed (exit {code}); see "
                                          f"`se2mod queue log {qjob.id}`."}
    return {**res, **finish_look(out, block=args.block, stage=args.stage, rnd=rnd, interior=args.interior,
                                 reference=args.reference, reference_view=args.reference_view)}


def finish_look(out: Path, *, block: str, stage: str, rnd: int, interior: bool = False,
                reference: Path | None = None, reference_view: str = "front") -> dict:
    """Check a rendered set (contact sheet, measured views, `report.json`) and say where everything is; `code` 1
    when it is not ready. `diag_sheet`, `ortho` and `refdiff` are None when that picture was not made."""
    from ..preview import look
    out = Path(out)
    report = look.finish(out, block=block, stage=stage, rnd=rnd, interior=interior, reference=reference,
                         reference_view=reference_view)
    geo = out / "geocheck.json"
    diag = report.get("diag") or {}
    ref = report.get("reference") or {}
    return {"code": 1 if report["problems"] else 0, "sheet": str(out / "sheet.png"),
            "report_path": str(out / "report.json"), "report": report, "not_ready": list(report["problems"]),
            "geocheck": str(geo) if geo.is_file() else None,
            "diag_sheet": str(out / diag["sheet"]) if diag.get("sheet") else None,
            "ortho": str(out / diag["ortho"]) if diag.get("ortho") else None,
            "refdiff": str(out / ref["diff"]) if ref.get("diff") else None, "iou": ref.get("iou")}


@register("look", "fast look set of a block from its working file, in the look-dev scene (queued)", add_args=_args,
          check_game_version=False)
def cmd_look(args: argparse.Namespace, env: SE2Env) -> int:
    res = run_look(args, env, say=print)
    for line in res["warnings"]:
        print(f"warning: {line}", file=sys.stderr)
    if "error" in res:
        print(f"error: {res['error']}", file=sys.stderr)
    elif "queue_command" in res:
        print(res["queue_command"])
    elif "report" in res:
        report = res["report"]
        out = Path(res["set_dir"])
        print(f"{res['sheet']}  ({report.get('seconds', '?')} s in Blender, {report.get('tris', '?')} triangles)")
        diag = report.get("diag") or {}
        for key in ("sheet", "ortho"):
            if diag.get(key):
                print(f"{out / diag[key]}" + (f"  ({diag['seconds']} s)" if key == "sheet" and "seconds" in diag
                                              else ""))
        ref = report.get("reference") or {}
        if ref.get("diff"):
            print(f"{out / ref['diff']}  (silhouette overlap with the reference: IoU {ref['iou']:.3f})")
        for line in report.get("warnings", []):
            print(f"  warning: {line}")
        for line in res["not_ready"]:
            print(f"  not ready: {line}")
    return res["code"]
