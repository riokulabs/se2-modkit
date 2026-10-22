"""`se2mod` command line: a small subcommand registry plus the flags every command shares."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from ..compat import UntestedGameError, check_game, load_compat
from ..env.config import ConfigError
from ..env.core import EnvError, SE2Env, discover_env
from ..project import ProjectError

__all__ = ["commands", "main", "register"]

Handler = Callable[[argparse.Namespace, SE2Env], int]
_COMMANDS: dict[str, tuple[str, Handler, Callable[[argparse.ArgumentParser], None] | None, bool]] = {}

_FLAGS = {
    "--steam-root": ("steam_root", "Steam install root (else SE2_STEAM_ROOT, config steam_root, discovery)"),
    "--sdk": ("sdk", "Mod SDK directory (else SE2_SDK_ASSETS, config sdk, discovery)"),
    "--blender": ("blender", "Blender executable (else SE2_BLENDER, config blender, PATH)"),
    "--proton": ("proton", "Proton build directory or name (Linux; else SE2_PROTON, config proton)"),
    "--pulsar": ("pulsar", "Pulsar directory or executable (else SE2_PULSAR, config pulsar)"),
}


def register(name: str, help: str, *, add_args: Callable[[argparse.ArgumentParser], None] | None = None,
             check_game_version: bool = True):
    """Decorator registering `fn(args, env) -> int` as `se2mod <name>`.

    `check_game_version=False` skips the installed-game compatibility warning (for commands that never touch the game).
    """
    def deco(fn: Handler) -> Handler:
        _COMMANDS[name] = (help, fn, add_args, check_game_version)
        return fn
    return deco


def _load_commands() -> None:
    from . import (  # noqa: F401  (importing registers them)
        blender_cmd,
        build_cmd,
        defs_cmd,
        deps_cmd,
        doctor,
        env_cmd,
        facts_cmd,
        kit_cmd,
        models_cmd,
        mods_cmd,
        mount_cmd,
        outline_cmd,
        play_cmd,
        preview_cmd,
        publish_cmd,
        report_cmd,
        review_cmd,
        sdk_cmd,
        snapshot_cmd,
        style_cmd,
        testrun_cmd,
        wait_cmd,
    )


def commands() -> list[str]:
    """Names of every registered subcommand."""
    _load_commands()
    return sorted(_COMMANDS)


def _parser() -> argparse.ArgumentParser:
    _load_commands()
    common = argparse.ArgumentParser(add_help=False)
    for flag, (dest, text) in _FLAGS.items():
        common.add_argument(flag, dest=dest, metavar="PATH", default=None, help=text)
    common.add_argument("--strict-game", action="store_true",
                        help="fail (exit 2) when the installed game is not a tested build")
    p = argparse.ArgumentParser(prog="se2mod", description="Space Engineers 2 mod tooling")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    for name, (help_, _fn, add, _chk) in sorted(_COMMANDS.items()):
        sp = sub.add_parser(name, help=help_, description=help_, parents=[common])
        if add:
            add(sp)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["publish"] and len(argv) > 1:  # its own argument parser and safety gates (`se2publish`)
        from .publish_cmd import run
        return run(argv[1:])
    parser = _parser()
    args, extra = parser.parse_known_args(argv)
    if extra and extra[0] == "--" and isinstance(getattr(args, "script_args", None), list):
        # Python 3.11's argparse leaves what follows "--" unparsed once an option came before it
        args.script_args = [*args.script_args, *extra[1:]]
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if not args.command:
        parser.print_help()
        return 0
    _help, fn, _add, check = _COMMANDS[args.command]
    try:
        env = discover_env(flags={k: getattr(args, k) for k in ("steam_root", "sdk", "blender", "proton", "pulsar")})
        if check:
            try:
                for warning in check_game(env.game_version, load_compat(), strict=args.strict_game):
                    print(f"warning: {warning}", file=sys.stderr)
            except UntestedGameError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        return fn(args, env)
    except (EnvError, ProjectError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
