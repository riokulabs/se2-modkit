"""`se2mod sheet` and `se2mod pair`: contact sheets and before/after images (needs Pillow); `se2mod preview`: the
mod's declarative preview shots (previews.toml), rendered by Blender in the background."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .. import preview
from ..env.core import SE2Env
from . import register


def _fail(e: preview.PreviewError) -> int:
    print(f"error: {e}", file=sys.stderr)
    return 1


def _sheet_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("inputs", nargs="+", metavar="DIR|GLOB", help="directories (their images) and/or file globs")
    p.add_argument("--out", type=Path, required=True, metavar="FILE", help="output .png or .jpg")
    p.add_argument("--cols", type=int, default=4, help="columns (default 4)")
    p.add_argument("--labels", action="store_true", help="print each file name above its cell")
    p.add_argument("--title", default=None, help="heading text")
    p.add_argument("--thumb", type=int, default=320, metavar="PX", help="cell size in pixels (default 320)")


def _pair_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--out", type=Path, required=True, metavar="FILE", help="output .png or .jpg")
    p.add_argument("--labels", nargs="?", const="before,after", default=None, metavar="A,B",
                   help='panel labels (default "before,after")')
    p.add_argument("--diff", action="store_true", help="add a third panel highlighting changed pixels")


@register("sheet", "grid of images (contact sheet) from directories or globs", add_args=_sheet_args,
          check_game_version=False)
def sheet(args: argparse.Namespace, env: SE2Env) -> int:
    try:
        w, h = preview.make_sheet(args.inputs, args.out, cols=args.cols, labels=args.labels, title=args.title,
                                  thumb=args.thumb)
    except preview.PreviewError as e:
        return _fail(e)
    print(f"{args.out} {w}x{h}")
    return 0


@register("pair", "before/after images side by side at one height, optional diff panel", add_args=_pair_args,
          check_game_version=False)
def pair(args: argparse.Namespace, env: SE2Env) -> int:
    try:
        w, h = preview.make_pair(args.before, args.after, args.out, labels=args.labels, diff=args.diff)
    except preview.PreviewError as e:
        return _fail(e)
    print(f"{args.out} {w}x{h}")
    return 0


# ------------------------------------------------------------------------------------------------ se2mod preview
PREVIEW_TIMEOUT = 1800   # s, the whole Blender run


def _preview_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("mod", nargs="?", help="mod name or directory (default: the mod here)")
    p.add_argument("--set", dest="set_name", metavar="NAME", help="preview set of previews.toml (needed if it has "
                                                                    "several)")
    p.add_argument("--block", action="append", default=[], metavar="B", help="only shots of this block (repeatable)")
    p.add_argument("--shot", metavar="NAME", help="only this shot")
    p.add_argument("--still", action="store_true", help="one fast still: the first selected shot of the first block")
    p.add_argument("--fast", action="store_true", help="half resolution, fewer samples")
    p.add_argument("--plan", type=Path, metavar="FILE", help="the shot list (default: the mod's previews.toml)")
    p.add_argument("--list", action="store_true", help="list the plan's sets and shots; render nothing")
    p.add_argument("--print-only", action="store_true", help="print the Blender command; render nothing")
    p.add_argument("--timeout", type=int, default=PREVIEW_TIMEOUT, metavar="S",
                   help=f"stop Blender after S seconds (default {PREVIEW_TIMEOUT})")


def _missing_exports(cfg, shots) -> list[str]:
    out = []
    for s in shots:
        for b in s.blocks:
            d = cfg.paths.models / b
            need = (d / "NonFractured" / (f"{b}.fbx" if s.lod == 0 else f"{b}_LOD{s.lod}.fbx")
                    if s.state == "intact" else d / "export.json")
            if not need.is_file() and str(need) not in out:
                out.append(str(need))
    return out


def _run_blender(argv: list[str], timeout: int) -> tuple[int, list[str], list[str]]:
    """Run Blender, echoing the images it writes; (exit code, written files, error lines). Killed after `timeout` s."""
    import threading
    written, errors, timed_out = [], [], []

    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace") as pr:
        def stop():
            timed_out.append(True)
            pr.kill()
        timer = threading.Timer(timeout, stop)
        timer.start()
        try:
            for line in pr.stdout:
                line = line.rstrip("\n")
                if line.startswith("WROTE "):
                    written.append(line[6:])
                    print(line[6:])
                elif line.startswith("ERROR: "):
                    errors.append(line[7:])
        finally:
            timer.cancel()
        code = pr.wait()
    if timed_out:
        errors.append(f"Blender did not finish within {timeout} s and was stopped (raise --timeout).")
    elif code and not errors:
        errors.append(f"Blender failed (exit {code}); run with --print-only and start that command to see its log.")
    return code, written, errors


@register("preview", "render the declarative preview shots of a mod (previews.toml) in Blender, headless",
          add_args=_preview_args, check_game_version=False)
def preview_shots(args: argparse.Namespace, env: SE2Env) -> int:
    from ..env import procs
    from ..preview import base as PB
    from ..preview import plan as PL
    from . import modarg
    from .blender_cmd import blender_argv

    ref = modarg.resolve(env, args.mod, need="config")
    cfg = ref.config
    if cfg is None:
        print(f"error: {ref.name} is not a mod of a [tool.se2modkit] project; previews need its paths.",
              file=sys.stderr)
        return 1
    path = args.plan or PL.find_plan(ref.root, cfg.paths.source_model)
    if path is None:
        print(f"error: {cfg.name} has no previews.toml (in {ref.root} or {cfg.paths.source_model}); see "
              "`se2modkit.preview.plan` for the format.", file=sys.stderr)
        return 1
    try:
        plan = PL.load_plan(path)
        if args.list:
            for ps in plan.values():
                print(f"{ps.name}: " + ", ".join(f"{s.name} ({s.state}; {' '.join(s.blocks)})" for s in ps.shots))
            return 0
        ps, shots = PL.select(plan, set_name=args.set_name, blocks=args.block, shot=args.shot, still=args.still)
        out = PB.output_dir(cfg, ps.name)
    except (PL.PlanError, preview.PreviewError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    missing = _missing_exports(cfg, shots)
    if missing:
        print(f"error: exported models missing (export the blocks first): {', '.join(missing)}", file=sys.stderr)
        return 1
    p = cfg.paths
    job = PL.job(mod_name=cfg.name, prefix=cfg.prefix, out=out, shots=shots, fast=args.fast or args.still,
                 paths={"models": p.models, "report": p.report, "staged": p.staged, "golden_states": p.golden_states,
                        "source_model": p.source_model, "asset_guids": p.asset_guids, "root": p.root})
    jobs = env.cache_dir / "preview-jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job_file = jobs / f"{cfg.name}-{ps.name}.json"
    job_file.write_text(json.dumps(job, indent=1) + "\n", encoding="utf-8", newline="\n")
    script = Path(PB.__file__).with_name("run.py")
    argv = blender_argv(env, script, [str(job_file)], mod_source=p.source_model, windowed=False)
    if args.print_only:
        print(subprocess.list2cmdline(argv) if env.platform.startswith("win") else shlex.join(argv))
        return 0
    if not procs.listing_available() or procs.game_running():
        print("error: Space Engineers 2 is running (or the process list cannot be read); previews render only while "
              "it is closed.", file=sys.stderr)
        return 4
    code, written, errors = _run_blender(argv, args.timeout)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if code or errors:
        return 1
    print(f"{len(written)} image(s) in {out}")
    return 0
