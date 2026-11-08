"""`se2mod mcp`: serve the modkit's most used commands as MCP tools over stdio (see `se2modkit.mcp`)."""

from __future__ import annotations

import argparse
import sys

from . import register

HELP = ("Serve look, describe, ship check, geocheck, queue, defs, chain and deps check as MCP tools over stdio "
        "(needs the mcp extra)")


def _args(p: argparse.ArgumentParser) -> None:
    p.description = (HELP + ". The tools return JSON and file paths instead of terminal text; long work goes "
                     "through the job queue as the commands do. --list prints the tools and their hints instead.")
    p.add_argument("--list", action="store_true", help="print each tool, whether it only reads, and its title; "
                                                       "serve nothing")


@register("mcp", HELP, add_args=_args, check_game_version=False)
def cmd_mcp(args, env) -> int:
    from ..mcp import server
    from ..mcp.tools import Toolbox
    toolbox = Toolbox(env)
    if args.list:
        for spec in toolbox.specs():
            kind = "read-only" if spec.read_only else "destructive" if spec.destructive else "changes"
            print(f"{spec.name:14} {kind:11} {spec.title}")
        return 0
    try:
        server.run_stdio(toolbox)
    except server.McpMissing as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
