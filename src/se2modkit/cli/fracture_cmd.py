"""`se2mod fracture check MOD BLOCK ...`: a block's fracture checked before any export (zone health shares, seams,
loose LOD0 geometry, cell groups), in a headless Blender through the job queue. The shares are the ones the export would
write (the same plan, cut and colliders); most of the time is the block's own build. Exit 0 pass (WARN lines allowed),
1 a FAIL line, 2 the run itself failed."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .. import jobqueue as Q
from ..pipeline import fracture_check as FC
from . import modarg, register

TIMEOUT = 1800   # s


def _args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="fracture_cmd", metavar="SUBCOMMAND", required=True)
    c = sub.add_parser("check", help="check blocks' fracture before an export (queued, headless)")
    c.add_argument("mod", help="mod name (project here or a workspace_mods folder) or path to a mod directory")
    c.add_argument("blocks", nargs="+", metavar="BLOCK", help="block names (or PREFIX*)")
    c.add_argument("--max-key-share", type=float, metavar="F",
                   help="fail a critical zone over this share of the health (the mod kit's bar); without it a critical "
                        f"zone over {FC.MAX_KEY_SHARE:g} is only a warning")
    c.add_argument("--script", type=Path, metavar="FILE",
                   help="the mod's build script (default: the one Source/Model/build_*.py)")
    c.add_argument("--json", action="store_true", help="print the reports as JSON, each with its problems")
    c.add_argument("--timeout", type=float, default=TIMEOUT, metavar="S", help=f"seconds (default {TIMEOUT})")


@register("fracture", "Check blocks' fracture before an export: fracture check MOD BLOCK ...", add_args=_args,
          check_game_version=False)
def cmd_fracture(args, env) -> int:
    from .. import modelbuild as MB
    from .blender_cmd import blender_argv
    try:
        cfg = modarg.resolve(env, args.mod, need="config").config
        if cfg is None:
            raise MB.BuildError(f"{args.mod} is not a mod of a [tool.se2modkit] project; the check needs its paths.")
        script = MB.find_script(cfg.paths.source_model, args.script)
    except MB.BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = Path(env.cache_dir) / "fracture-check"
    out.mkdir(parents=True, exist_ok=True)
    status = out / f"{cfg.name}-{os.getpid()}-{int(time.time())}.json"
    argv = blender_argv(env, script, [*args.blocks, "--check-fracture", "--no-render", f"--status={status}"],
                        mod_source=cfg.paths.source_model, windowed=False)
    store = Q.Store(env.cache_dir / "queue")
    name = f"fracture-check:{cfg.name}:{','.join(args.blocks)}"
    job = store.submit(argv, name=name, load=Q.LOADS["medium"], timeout=args.timeout,
                       env={"SE2MODKIT_OWNS_BLENDER": "1"})
    Q.ensure_runner(store)
    print(f"queued job {job.id}: {name}", file=sys.stderr)
    code = Q.wait(store, job.id, args.timeout + 120)
    try:
        reports = json.loads(status.read_text(encoding="utf-8"))["fracture"] if code == 0 else None
    except (OSError, ValueError, KeyError):
        reports = None
    finally:
        status.unlink(missing_ok=True)
    if reports is None:
        print(f"error: the fracture check job {job.id} failed (exit {code}); see `se2mod queue log {job.id}`.",
              file=sys.stderr)
        return 2
    failed = False
    if args.json:
        for r in reports:
            r["problems"] = FC.problems(r, args.max_key_share)
            r["warnings"] = FC.warnings(r, args.max_key_share)
            failed |= bool(r["problems"])
        print(json.dumps(reports, indent=1))
    else:
        for r in reports:
            lines = FC.summary(r, args.max_key_share)
            failed |= any(ln.startswith("FAIL") for ln in lines)
            print("\n".join(lines))
    return 1 if failed else 0
