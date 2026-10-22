"""`se2mod sdk standin [--force] [--dest DIR]`: build the SDK standin the Mod SDK builder needs (user cache)."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..env import standin
from . import register


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="sdk_cmd", metavar="SUBCOMMAND", required=True)
    s = sub.add_parser("standin", help="link the game assemblies where the SDK projects look for them")
    s.add_argument("--force", action="store_true", help="rebuild even if it is up to date")
    s.add_argument("--dest", type=Path, default=None, help="standin folder (default: <cache>/standin)")


@register("sdk", "Mod SDK helpers: sdk standin", add_args=_add_args)
def cmd_sdk(args, env) -> int:
    dest = args.dest or standin.default_dest(env)
    if not args.force and standin.current(env, dest):
        print(f"SDK standin up to date: {dest}")
        return 0
    standin.build(env, dest)
    print(f"SDK standin built for game {env.game_version} (SDK build {env.sdk_buildid}): {dest}")
    return 0
