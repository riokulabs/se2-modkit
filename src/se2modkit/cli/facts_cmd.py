"""`se2mod facts build [--decompiled DIR] [--game-version V]`: the game's type facts, into the user cache only.

The validators (`validate.clientserver`, `validate.schema`) and the `clientserver` / `schema` gates classify the game's
component and definition types from facts read once from a decompiled game: type and member NAMES and flags, never
code. They describe the game, so they are written only to `<user cache>/facts/<game version>.json` and never next to a
mod (a cache dir inside a git work tree is refused). DIR comes from the flag, SE2_DECOMPILED or the user config's
decompiled_dir; it is never discovered.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from ..validate.facts import FactsError, build_facts, write_facts
from . import register


def _add_args(p) -> None:
    sub = p.add_subparsers(dest="facts_cmd", metavar="SUBCOMMAND", required=True)
    b = sub.add_parser("build", help="read the type facts from a decompiled game into the user cache")
    b.add_argument("--decompiled", type=Path, default=None, metavar="DIR",
                   help="the decompiled game (<Assembly>/<Namespace>/<Type>.cs; default: SE2_DECOMPILED or config "
                        "decompiled_dir)")
    b.add_argument("--game-version", default=None, metavar="V",
                   help="the game version the facts are for (default: the installed game's)")


@register("facts", "Type facts for the validators: facts build (from a decompiled game, into the user cache)",
          add_args=_add_args, check_game_version=False)
def cmd_facts(args, env) -> int:
    dec = args.decompiled or env.decompiled_dir
    if dec is None:
        print("error: No decompiled game: pass --decompiled DIR, set SE2_DECOMPILED, or set decompiled_dir in the "
              "se2modkit user config.", file=sys.stderr)
        return 1
    version = args.game_version or env.game_version
    if not version:
        print("error: The installed game's version is unknown: pass --game-version (e.g. 4.0.0.3155).", file=sys.stderr)
        return 1
    started = time.monotonic()
    try:
        facts = build_facts(Path(dec), version)
        path = write_facts(facts, cache_dir=env.cache_dir)
    except (FactsError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(facts['types'])} types from {facts['source']} for game {version} -> {path} "
          f"({time.monotonic() - started:.1f} s)")
    return 0
