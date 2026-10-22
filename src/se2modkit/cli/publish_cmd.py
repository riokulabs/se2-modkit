"""`se2mod publish ...`: the Workshop publisher (`se2publish`), same arguments, same safety gates."""

from __future__ import annotations

from . import register

HELP = "Steam Workshop publisher (dry run by default; same as `se2publish`, see `se2mod publish --help`)"


def run(argv: list[str]) -> int:
    from ..publish.cli import main
    return main(argv)


@register("publish", HELP, check_game_version=False)
def cmd_publish(args, env) -> int:  # reached only without arguments; `main` hands real calls over untouched
    try:
        return run(["--help"])
    except SystemExit as e:  # argparse exits after printing help
        return e.code if isinstance(e.code, int) else 0
