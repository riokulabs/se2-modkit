"""`se2mod build MOD [--dry-run] [-v] [--lock-timeout S]`: compile a mod's Assets into Content with the Mod SDK builder.

A mod that carries an exact copy of a game texture is refused before the builder runs (exit 6)."""

from __future__ import annotations

import sys

from .. import sdkbuild
from ..env import standin
from ..env.core import EnvError
from ..validate import vanilla_textures
from . import register
from .mods_cmd import _resolve


def _add_args(p) -> None:
    p.add_argument("mod", help="mod name (project here or a workspace_mods folder) or path to a mod directory")
    p.add_argument("--dry-run", action="store_true", help="ask the builder what it would do; prune nothing")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose builder output")
    p.add_argument("--assume-game-closed", action="store_true",
                   help="build even when the process list is unavailable (you vouch that the game is closed)")
    p.add_argument("--lock-timeout", type=float, default=sdkbuild.SDK_LOCK_TIMEOUT, metavar="SECONDS",
                   help="how long to wait for another session's SDK build (default: %(default)g)")


@register("build", "Compile Assets/*.def|*.partialdef to Content/ with the Mod SDK builder (headless)",
          add_args=_add_args)
def cmd_build(args, env) -> int:
    mod = _resolve(env, args.mod)
    roots = vanilla_textures.vanilla_roots(env)
    if not roots:
        print(vanilla_textures.NO_ROOTS, file=sys.stderr)
    copies = vanilla_textures.find_copies(vanilla_textures.image_files(mod), roots,
                                          env.cache_dir / vanilla_textures.CACHE_NAME)
    if copies:
        print(vanilla_textures.refusal(copies, mod), file=sys.stderr)
        return sdkbuild.EXIT_VANILLA_TEXTURE
    if env.game_dir is not None and env.sdk_dir is not None:  # the standin the builder looks for first
        try:
            dest, rebuilt = standin.ensure(env)
            if rebuilt:
                print(f"SDK standin built: {dest}")
        except (EnvError, OSError) as e:
            print(f"warning: no SDK standin ({e})", file=sys.stderr)
    return sdkbuild.build(env, mod, dry_run=args.dry_run, verbose=args.verbose,
                          assume_game_closed=args.assume_game_closed, lock_timeout=args.lock_timeout)
