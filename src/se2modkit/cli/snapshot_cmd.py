"""`se2mod snapshot MOD --out FILE | --compare FILE`."""

from __future__ import annotations

import json
from pathlib import Path

from .. import snapshot
from ..project import ModConfig, ProjectError
from . import modarg, register


def _add_args(p) -> None:
    p.add_argument("mod", help="mod name (project in the current directory) or path to a mod directory")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", metavar="FILE", help="write a snapshot of the mod's outputs")
    g.add_argument("--compare", metavar="FILE", help="compare the mod's outputs with a snapshot (exit 1 on any difference)")


def _resolve(selector: str | None, env=None) -> ModConfig:
    """The project mod MOD names (a name or a path); `None` is the mod here."""
    return modarg.resolve(env, selector, need="config").config


@register("snapshot", "Record or compare hashes of a mod's generated outputs", add_args=_add_args,
          check_game_version=False)
def run(args, env) -> int:
    mod = _resolve(args.mod, env)
    now = snapshot.take(mod)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"snapshot of {mod.name}: {len(now['models'])} models, {len(now['staged'])} staged files -> {out}")
        return 0
    try:
        before = json.loads(Path(args.compare).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProjectError(f"Cannot read snapshot {args.compare}: {e}.") from e
    diff = snapshot.compare(before, now)
    for line in diff:
        print(line)
    if diff:
        return 1
    print(f"{mod.name}: identical to snapshot")
    return 0
