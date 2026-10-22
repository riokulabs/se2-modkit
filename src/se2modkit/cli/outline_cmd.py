"""`se2mod outline FILE [--symbol NAME]`: top-level functions, classes (with methods) and constants of a Python file
with line ranges, or the numbered source of one symbol, so a reader fetches only the lines it needs."""

from __future__ import annotations

import sys
from pathlib import Path

from ..outline import OutlineError, render, symbol_source
from . import register


def _add_args(p) -> None:
    p.add_argument("file", help="a Python file")
    p.add_argument("--symbol", metavar="NAME", help="print this symbol's source with line numbers (Class.method works)")
    p.add_argument("--max-lines", type=int, default=None, metavar="N",
                   help="cap on printed lines (default 200 for --symbol, 400 entries for the outline)")


@register("outline", "List a Python file's functions, classes and constants with line ranges, or one symbol's source",
          add_args=_add_args, check_game_version=False)
def cmd_outline(args, env) -> int:
    path = Path(args.file)
    try:
        if args.symbol:
            out = symbol_source(path, args.symbol, max(args.max_lines or 200, 1))
        else:
            out = render(path, max(args.max_lines or 400, 1))
    except OutlineError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("\n".join(out))
    return 0
