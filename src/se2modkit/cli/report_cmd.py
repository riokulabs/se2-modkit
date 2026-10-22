"""`se2mod report MOD [--block B] [--fields ...] [--vs BASELINE]`: LOD budget table from build_report.json."""

from __future__ import annotations

from pathlib import Path

from .. import report
from . import register
from .snapshot_cmd import _resolve


def _add_args(p) -> None:
    p.add_argument("mod", help="mod name (project in the current directory) or path to a mod directory")
    p.add_argument("--block", help="only this block, with every LOD (default: LOD0 of every block)")
    p.add_argument("--fields", metavar="LIST",
                   help=f"columns to show ({','.join(report.FIELDS)}); 'lods' lists every LOD of every block")
    p.add_argument("--vs", metavar="SNAPSHOT_OR_REPORT",
                   help="a snapshot (se2mod snapshot --out) or a build_report.json to show deltas against")
    p.add_argument("--max", type=int, default=report.DEFAULT_MAX, metavar="N",
                   help=f"print at most N table rows (default {report.DEFAULT_MAX})")


@register("report", "Compact per-block LOD table from build_report.json: tris, materials, %% of Keen's limit",
          add_args=_add_args, check_game_version=False)
def cmd_report(args, env) -> int:
    fields = report.parse_fields(args.fields)
    mod = _resolve(args.mod, env)
    stats = report.load_stats(mod.paths.report)
    base = report.load_baseline(Path(args.vs)) if args.vs else None
    lines = report.table(stats, block=args.block, fields=fields, base=base)
    print("\n".join(report.bounded(lines, args.max)))
    return 0
