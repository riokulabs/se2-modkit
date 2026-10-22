"""`se2mod deps check` and `se2mod deps baseline capture|diff`: what did a game update change?"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..deps import baseline, check, diff
from . import register


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="deps_cmd", metavar="SUBCOMMAND", required=True)
    c = sub.add_parser("check", help="re-check every recorded game dependency (hashes, GUIDs, anchors, buildids)")
    c.add_argument("--json", type=Path, default=None,
                   help="recorded dependency file (default: our-game-dependencies.json of the mod/project here, else "
                        "modkit's own)")
    c.add_argument("--ws", type=Path, default=None, help="workspace root for WS: paths (default: SE2_WORKSPACE, cwd)")
    c.add_argument("--game", type=Path, default=None, help="game folder (default: discovered)")
    c.add_argument("--decompiled", type=Path, default=None,
                   help="decompiled tree to verify anchors in (default: SE2_DECOMPILED, config decompiled_dir)")
    c.add_argument("--version", help="with --rebaseline: the game version to record in the baseline block")
    c.add_argument("--refs", action="store_true", help="also check that our file:line refs still exist")
    c.add_argument("--no-guids", action="store_true", help="skip the GUID index (faster)")
    c.add_argument("--min-risk", choices=list(check.RISK), default="low",
                   help="only report dependencies at or above this risk")
    c.add_argument("--report-json", type=Path, help="write the findings as JSON here (not under the game or SDK)")
    c.add_argument("--rebaseline", nargs="?", type=Path, const=True, default=False, metavar="PATH",
                   help="also write a copy with the current hashes to PATH (e.g. over --json to refresh it in "
                        f"place; default <cache>/deps/{check.REBASELINE_NAME})")
    c.add_argument("-q", "--quiet", action="store_true", help="summary only")

    b = sub.add_parser("baseline", help="freeze or compare a full game + SDK baseline (in the user cache)")
    bsub = b.add_subparsers(dest="baseline_cmd", metavar="ACTION", required=True)
    cap = bsub.add_parser("capture", help="capture <cache>/baseline/<version>/ (new or empty)")
    cap.add_argument("--version", help="the installed game version (default: read from the game)")
    cap.add_argument("--dest", type=Path, help="baseline dir (must be under <cache>/baseline)")
    cap.add_argument("--workers", type=int, default=16, help="hashing threads")
    cap.add_argument("--api", action="store_true", help="also capture the assembly inventory + API surface")
    cap.add_argument("--asminv", type=Path, help="AsmInventory.dll for --api (runs with dotnet)")
    cap.add_argument("--decompiled", type=Path, help="also archive this decompiled tree")
    d = bsub.add_parser("diff", help="compare the install against a baseline; report under the cache")
    d.add_argument("--baseline-dir", type=Path, help="baseline to compare with (default: newest in the cache)")
    d.add_argument("--out", type=Path, help="report dir (under <cache>/baseline, not inside a baseline)")
    d.add_argument("--workers", type=int, default=16, help="hashing threads")
    d.add_argument("--fast", action="store_true", help="reuse baseline sha256 when size+mtime are unchanged")
    d.add_argument("--no-api", action="store_true", help="skip the assembly inventory / API surface diff")
    d.add_argument("--asminv", type=Path, help="AsmInventory.dll for the API diff (runs with dotnet)")
    d.add_argument("--max-diff-lines", type=int, default=3000)


@register("deps", "Game-update checks: deps check [--rebaseline], deps baseline capture|diff", add_args=_add_args)
def cmd_deps(args, env) -> int:
    if args.deps_cmd == "check":
        return check.run(env, deps_file=args.json, workspace=args.ws, game=args.game, decompiled=args.decompiled,
                         version=args.version, refs=args.refs, no_guids=args.no_guids, min_risk=args.min_risk,
                         report_json=args.report_json, rebaseline=args.rebaseline, quiet=args.quiet)
    if args.baseline_cmd == "capture":
        dest = baseline.capture(env, args.version, dest=args.dest, workers=args.workers, asminv=args.asminv,
                                api=args.api, decompiled=args.decompiled)
        print(f"baseline written to {dest}")
        return 0
    code, _ = diff.run(env, baseline_dir=args.baseline_dir, out=args.out, workers=args.workers, fast=args.fast,
                       no_api=args.no_api, asminv=args.asminv, max_diff_lines=args.max_diff_lines)
    return code
