"""`se2mod style check [MOD] [--warn-only]`: the mod's build_report.json against its style profile and waivers."""

from __future__ import annotations

import sys

from ..project import ModConfig
from ..style import check
from ..style.profile import ProfileError
from ..style.waivers import WaiverError
from . import modarg, register


def _add_args(p) -> None:
    sub = p.add_subparsers(dest="style_cmd", metavar="SUBCOMMAND", required=True)
    c = sub.add_parser("check", help="check build_report.json against the mod's style profile")
    c.add_argument("mod", nargs="?", default=None,
                   help="mod name or path to a mod directory (default: the mod here)")
    c.add_argument("--warn-only", action="store_true", help="report findings but exit 0")


def _resolve(selector: str | None, env=None) -> ModConfig:
    return modarg.resolve(env, selector, need="config").config


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def print_result(mod: ModConfig, res: check.Result, profile_name: str) -> None:
    print(f"{mod.name}: style profile {profile_name}")
    for f in res.findings:
        cmp = "<" if f.value < f.limit else ">"
        where = f" [{f.where}]" if f.where else ""
        tail = f"  waived: {f.waived_by}" if f.waived_by else ""
        print(f"  {f.level.upper():4} {f.block}{where} {f.key} {_fmt(f.value)} {cmp} {_fmt(f.limit)}{tail}")
    for key, blocks in sorted(res.not_measured.items()):
        shown = ", ".join(blocks[:3]) + (f" (+{len(blocks) - 3} more)" if len(blocks) > 3 else "")
        print(f"  not measured: {key} on {len(blocks)} block(s): {shown}")
    for w in res.unused_waivers:
        print(f"  unused waiver: {w.block} {w.key} ({w.reason})")
    fails = res.unwaived_failures
    warns = sum(1 for f in res.findings if f.level == "warn" and f.waived_by is None)
    waived = sum(1 for f in res.findings if f.waived_by)
    print(f"{len(fails)} fail, {warns} warn, {waived} waived, {len(res.not_measured)} check(s) not measured")


@register("style", "House-style checks: style check [MOD] [--warn-only]", add_args=_add_args,
          check_game_version=False)
def cmd_style(args, env) -> int:
    mod = _resolve(args.mod, env)
    try:
        report, profile, waivers = check.mod_inputs(mod)
        res = check.run(report, profile, waivers)
    except (ProfileError, WaiverError, check.StyleError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print_result(mod, res, profile.name)
    return 1 if res.unwaived_failures and not args.warn_only else 0
