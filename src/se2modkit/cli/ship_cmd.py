"""`se2mod ship check MOD BLOCK ...`: one read-only check before a block's export. First the queue order (plain
Python, nothing queued yet: a save of the block's working .blend must not wait behind, or run beside, a build or export
that reads it); then one headless Blender through the job queue builds the blocks as the export would and reports what
ships (every model the export takes, and each working-file collection it came from, by render visibility), the
export's z-fight audit over all of it (coplanar faces across collections included), the fracture check, and a tinted
exploded fracture picture per block. Writes only `<model source>/preview/ship/<block>/` (exploded.png, ship.json).
Exit 0 pass (WARN lines allowed), 1 a FAIL line, 2 the run itself failed."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .. import jobqueue as Q
from .. import lease as LS
from ..pipeline import ship_check as SC
from . import modarg, register

TIMEOUT = 1800   # s


def _args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="ship_cmd", metavar="SUBCOMMAND", required=True)
    c = sub.add_parser("check", help="check blocks before an export: queue order, what ships, coplanar faces across "
                                     "every shipped collection, fracture and an exploded zone picture (queued)",
                       description="Read-only check before an export. The queue order first (nothing is queued when "
                                   "a save of the working .blend waits behind or runs beside a build or export of the "
                                   "block), then one headless Blender job: what ships (by render visibility, every "
                                   "collection), the export's z-fight audit over all of it, the fracture check, and "
                                   "an exploded fracture picture in Source/Model/preview/ship/<block>/. Exit 0 pass, "
                                   "1 a FAIL line, 2 the run failed.")
    c.add_argument("mod", help="mod name (project here or a workspace_mods folder) or path to a mod directory")
    c.add_argument("blocks", nargs="+", metavar="BLOCK", help="block names (or PREFIX*)")
    c.add_argument("--max-key-share", type=float, metavar="F",
                   help="fail a critical zone over this share of the health (as `se2mod fracture check`)")
    c.add_argument("--blend", action="append", type=Path, default=[], metavar="FILE",
                   help="a working .blend the blocks are built from, for the queue-order check before the run "
                        "(default: the .blend files their last build read; the run adds the ones it loads)")
    c.add_argument("--script", type=Path, metavar="FILE",
                   help="the mod's build script (default: the one Source/Model/build_*.py)")
    c.add_argument("--json", action="store_true", help="print the reports as JSON, each with its problems")
    c.add_argument("--timeout", type=float, default=TIMEOUT, metavar="S", help=f"seconds (default {TIMEOUT})")
    c.add_argument("--print-only", action="store_true",
                   help="run the queue-order check and print the queue command; queue nothing")


def _last_blends(cfg, word: str) -> set[Path]:
    """The .blend files the last build of the block (or of every block a PREFIX* names) read, from its fragments."""
    from ..pipeline import incremental as IN
    store = IN.Store.for_report(Path(cfg.paths.report), cfg.name)
    names = [b for b in store.blocks() if b.startswith(word[:-1])] if word.endswith("*") else [word]
    out = set()
    for name in names:
        for entry in ((store.load(name) or {}).get("reads") or {}):
            if entry.lower().endswith(".blend") and not entry.startswith("dir:"):
                out.add(Path(entry))
    return out


def _queue_check(env, cfg, script, word: str, blends) -> dict:
    store = Q.Store(env.cache_dir / "queue")
    try:
        leases = LS.active(Path(env.cache_dir))
    except LS.LeaseBusy:
        leases = []
    fail, warn = SC.queue_order(store.all(), mod=cfg.name, block=word, script=script, blends=blends, leases=leases)
    return {"fail": fail, "warn": warn}


def _merge(a: dict, b: dict) -> dict:
    return {k: list(dict.fromkeys([*a.get(k, []), *b.get(k, [])])) for k in ("fail", "warn")}


def _out_dir(cfg) -> Path:
    return Path(cfg.paths.source_model) / "preview" / "ship"


@register("ship", "Check blocks before an export (queue order, what ships, coplanar, fracture, exploded zones): "
                  "ship check MOD BLOCK ...", add_args=_args, check_game_version=False)
def cmd_ship(args, env) -> int:
    from .. import modelbuild as MB
    from .blender_cmd import blender_argv
    try:
        cfg = modarg.resolve(env, args.mod, need="config").config
        if cfg is None:
            raise MB.BuildError(f"{args.mod} is not a mod of a [tool.se2modkit] project; the check needs its paths.")
        script = MB.find_script(cfg.paths.source_model, args.script).resolve()
    except MB.BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    before = {w: _queue_check(env, cfg, script, w, {*args.blend, *_last_blends(cfg, w)}) for w in args.blocks}
    failed = any(q["fail"] for q in before.values())
    for w, q in before.items():
        for line in q["fail"]:
            print(f"FAIL {line}")
        if failed or args.print_only:
            for line in q["warn"]:
                print(f"WARN {line}")
    if failed:
        print("FAIL queue order: nothing was queued; fix the order above, then run the check again.")
        return 1
    out_root = _out_dir(cfg)
    cache = Path(env.cache_dir) / "ship-check"
    cache.mkdir(parents=True, exist_ok=True)
    status = cache / f"{cfg.name}-{os.getpid()}-{int(time.time())}.json"
    argv = blender_argv(env, script, [*args.blocks, "--check-ship", f"--ship-out={out_root}", "--no-render",
                                      f"--status={status}"], mod_source=cfg.paths.source_model, windowed=False)
    name = f"ship-check:{cfg.name}:{','.join(args.blocks)}"
    if args.print_only:
        qargv = ["queue", "submit", "--name", name, "--load", "medium", "--timeout", f"{args.timeout:g}",
                 "--env", "SE2MODKIT_OWNS_BLENDER=1", "--", *argv]
        print("se2mod " + (subprocess.list2cmdline(qargv) if env.platform.startswith("win") else shlex.join(qargv)))
        return 0
    store = Q.Store(env.cache_dir / "queue")
    job = store.submit(argv, name=name, load=Q.LOADS["medium"], timeout=args.timeout,
                       env={"SE2MODKIT_OWNS_BLENDER": "1"})
    Q.ensure_runner(store)
    print(f"queued job {job.id}: {name}", file=sys.stderr)
    code = Q.wait(store, job.id, args.timeout + 120)
    try:
        reports = json.loads(status.read_text(encoding="utf-8"))["ship"] if code == 0 else None
    except (OSError, ValueError, KeyError):
        reports = None
    finally:
        status.unlink(missing_ok=True)
    if reports is None:
        print(f"error: the ship check job {job.id} failed (exit {code}); see `se2mod queue log {job.id}`.",
              file=sys.stderr)
        return 2
    failed = False
    for r in reports:
        # the queue again, now with the .blend files the build really loaded (a save queued while the check ran
        # counts too): the export this check stands in for would read them next
        word = next((w for w in args.blocks if w == r["block"]), None) or next(
            (w for w in args.blocks if w.endswith("*") and r["block"].startswith(w[:-1])), r["block"])
        loaded = {Path(s["blend"]) for s in r.get("sources") or () if s.get("blend")}
        after = _queue_check(env, cfg, script, r["block"], {*args.blend, *loaded})
        r["queue"] = _merge(before.get(word, {}), after)
        r["problems"] = SC.problems(r, args.max_key_share)
        r["warnings"] = SC.warnings(r, args.max_key_share)
        failed |= bool(r["problems"])
        d = out_root / r["block"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "ship.json").write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8", newline="\n")
    if args.json:
        print(json.dumps(reports, indent=1))
    else:
        for r in reports:
            print("\n".join(SC.summary(r, args.max_key_share)))
    return 1 if failed else 0
