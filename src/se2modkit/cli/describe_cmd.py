"""`se2mod describe FILE.blend`: a block model as numbers, for a reader that judges geometry better from figures than
from pictures. One read-only, headless Blender job through the queue (it renders nothing and never saves the file)
lists every part that ships with its size, center, grid cell, nearest neighbor and gap, contacts and mirror partner,
then the floating parts, overlaps and mirror errors, and checks the proportion targets of `--targets FILE.md`.
Exit 0 pass, 1 a target failed, 2 the run itself failed. The work is `se2modkit.describe`."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .. import describe as D
from .. import jobqueue as Q
from . import modarg, register

TIMEOUT = 900   # s


def _envelope(text: str) -> list[float]:
    try:
        vals = [float(x) for x in text.replace("x", ",").split(",")]
    except ValueError:
        vals = []
    if len(vals) != 3 or min(vals) <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} is not X,Y,Z in meters (three numbers over 0)")
    return vals


def _args(p: argparse.ArgumentParser) -> None:
    p.description = ("A block model as numbers, read-only, in one headless Blender job through the queue: each part "
                     "that ships (#N, sorted by collection then name) with its role, size and center in mm, grid "
                     "cell (0.25 m), nearest part and gap (measured on the meshes when close; negative = overlap), "
                     "contacts and mirror partner; then FLOATING, OVERLAP and MIRROR-ERROR lists and the targets. "
                     "Exit 0 pass, 1 a target failed, 2 the run failed.")
    p.add_argument("blend", type=Path, help="the .blend file (read, never saved); relative to the mod's model "
                                            "source when --mod is given and it is not found here")
    p.add_argument("--collection", action="append", default=[], metavar="C",
                   help="describe only this collection, taken as the block (repeatable; default: everything that "
                        "renders)")
    p.add_argument("--mod", help="mod name or folder, to find the .blend under its model source")
    p.add_argument("--targets", type=Path, metavar="FILE.md",
                   help="markdown file whose ```targets blocks (TOML [[target]] a, b, value, tol) are checked")
    p.add_argument("--envelope", type=_envelope, metavar="X,Y,Z",
                   help="the block's size in meters: adds the fill, and mirrors across the block origin instead "
                        "of the model's box center")
    p.add_argument("--json", type=Path, metavar="OUT", help="also write the full report as JSON to OUT")
    p.add_argument("--timeout", type=float, default=TIMEOUT, metavar="S", help=f"seconds (default {TIMEOUT:g})")
    p.add_argument("--print-only", action="store_true", help="print the queue command; queue nothing")


def _find_blend(env, args) -> Path:
    blend = args.blend
    if not blend.is_file() and args.mod and not blend.is_absolute():
        ref = modarg.resolve(env, args.mod)
        src = ref.config.paths.source_model if ref.config is not None else ref.root / "Source" / "Model"
        blend = Path(src) / blend
    if not blend.is_file():
        raise FileNotFoundError(f"no file {blend}")
    return blend.resolve()


def job_argv(env, blend: Path, spec: Path, status: Path) -> list[str]:
    from ..env.core import require
    from .blender_cmd import package_root
    require(env, "blender")
    expr = f"import sys; sys.path[:0] = {[str(package_root(env))]!r}"
    script = Path(D.__file__).with_name("describe_job.py")
    return [str(env.blender), "-b", str(blend), "--python-exit-code", "1", "--python-expr", expr,
            "--python", str(script), "--", str(spec), str(status)]


@register("describe", "A block model as numbers (parts, gaps, contacts, mirrors, targets): describe FILE.blend",
          add_args=_args, check_game_version=False)
def cmd_describe(args, env) -> int:
    try:
        blend = _find_blend(env, args)
        targets = D.parse_targets(args.targets.read_text(encoding="utf-8"), str(args.targets)) if args.targets else []
    except (OSError, D.TargetError, modarg.ModNotFound) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    cache = Path(env.cache_dir) / "describe"
    cache.mkdir(parents=True, exist_ok=True)
    stem = cache / f"{blend.stem}-{os.getpid()}-{int(time.time())}"
    spec, status = stem.with_suffix(".spec.json"), stem.with_suffix(".json")
    spec.write_text(json.dumps({"source": blend.name, "collections": args.collection, "envelope": args.envelope,
                                "targets": targets}), encoding="utf-8")
    argv = job_argv(env, blend, spec, status)
    name = f"describe:{blend.name}" + (f":{','.join(args.collection)}" if args.collection else "")
    if args.print_only:
        qargv = ["queue", "submit", "--name", name, "--load", "light", "--timeout", f"{args.timeout:g}", "--", *argv]
        print("se2mod " + (subprocess.list2cmdline(qargv) if env.platform.startswith("win") else shlex.join(qargv)))
        return 0
    store = Q.Store(env.cache_dir / "queue")
    job = store.submit(argv, name=name, load=Q.LOADS["light"], timeout=args.timeout)
    Q.ensure_runner(store)
    print(f"queued job {job.id}: {name}", file=sys.stderr)
    code = Q.wait(store, job.id, args.timeout + 120)
    try:
        rep = json.loads(status.read_text(encoding="utf-8"))["describe"] if code == 0 else None
    except (OSError, ValueError, KeyError):
        rep = None
    finally:
        status.unlink(missing_ok=True)
        spec.unlink(missing_ok=True)
    if rep is None:
        print(f"error: the describe job {job.id} failed (exit {code}); see `se2mod queue log {job.id}`.",
              file=sys.stderr)
        return 2
    if rep.get("error"):
        print(f"error: {rep['error']}", file=sys.stderr)
        return 2
    rep["blend"] = str(blend)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(D.text(rep)))
    return 1 if any(not t["ok"] for t in rep.get("targets") or ()) else 0
