"""`se2mod kit build | fetch | path NAME`: the kit assets (Rioku atlas, maker mark) in the user cache."""

from __future__ import annotations

import argparse
import sys

from .. import kit
from . import register


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="kit_action", metavar="ACTION", required=True)
    b = sub.add_parser("build", help="render the Rioku atlas from its SVG sources into the user cache")
    b.add_argument("--no-dds", action="store_true", help="write the PNG only, never run texconv")
    b.add_argument("--timeout", type=float, default=60.0, metavar="S", help="texconv time limit (default 60 s)")
    sub.add_parser("fetch", help="download this version's kit asset zip and check its sha256")
    q = sub.add_parser("path", help="print the cached path of a kit asset")
    q.add_argument("name", help="asset file name, e.g. RiokuAtlas01.png")


@register("kit", "Kit assets: build the Rioku atlas, fetch the release zip, print an asset path", add_args=_add_args,
          check_game_version=False)
def run(args, env) -> int:
    try:
        if args.kit_action == "build":
            from ..kit import build
            res = build.build(env.cache_dir, texconv=None if args.no_dds else "auto", timeout=args.timeout)
            for w in res.warnings:
                print(f"warning: {w}", file=sys.stderr)
            for f in res.files:
                print(f)
            return 0
        if args.kit_action == "fetch":
            from ..kit import fetch
            print(fetch.fetch(env.cache_dir))
            return 0
        print(kit.asset_path(args.name, env.cache_dir))
        return 0
    except kit.KitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
