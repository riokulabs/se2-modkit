"""`se2mod off | on | status`: detach or re-attach the workspace mods folder as the game's Mods folder.

Attached, the game's `Documents/SpaceEngineers2/Mods` is a link to the workspace mods folder (a symlink on Linux, a
directory junction on Windows, which needs no admin rights), so the Mod HUB and the game see the same folders.
Detached, it is an empty real folder and a normal Steam launch sees only Workshop mods.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..env import procs
from ..env.links import is_link, make_link, remove_link, same_target
from ..env.core import SE2Env, require
from . import register
from .mods_cmd import ModError, workspace_mods_dirs

__all__ = ["attach", "detach", "mods_root", "status"]

_FLAGS = ("-loadScripts", "-projectPaths", "-startupTag:HUB")


def attach(game_mods: Path, mods_root: Path, *, platform: str) -> None:
    """Make `game_mods` a link to `mods_root`. An empty real folder is replaced; one with files in it is refused."""
    game_mods, mods_root = Path(game_mods), Path(mods_root)
    if not mods_root.is_dir():
        raise ModError(f"The mods folder {mods_root} does not exist.")
    if is_link(game_mods):
        if same_target(game_mods, mods_root):
            return
        raise ModError(f"{game_mods} links to {os.path.realpath(game_mods)}, not {mods_root}; run `se2mod off` "
                       "first.")
    if game_mods.exists():
        if any(game_mods.iterdir()):
            raise ModError(f"{game_mods} is a real folder with files in it; move them into {mods_root} first.")
        game_mods.rmdir()
    make_link(game_mods, mods_root.resolve(), platform=platform)


def detach(game_mods: Path, *, platform: str) -> None:
    """Remove the link (never its target) and leave an empty real folder; a real folder is left as it is."""
    game_mods = Path(game_mods)
    if is_link(game_mods):
        remove_link(game_mods)
    game_mods.mkdir(parents=True, exist_ok=True)


def status(game_mods: Path, mods_root: Path | None, *, platform: str) -> dict:
    """`{"attached", "link", "target", "exists"}` for the game's Mods folder."""
    game_mods = Path(game_mods)
    link = is_link(game_mods)
    target = Path(os.path.realpath(game_mods)) if link else None
    return {"attached": bool(link and mods_root is not None and same_target(game_mods, mods_root)), "link": link,
            "target": target, "exists": game_mods.exists()}


def mods_root(env: SE2Env) -> Path:
    """The folder that gets mounted: the first `workspace_mods` folder (config), else `$SE2_WORKSPACE/mods`."""
    dirs = workspace_mods_dirs(env)
    if not dirs:
        raise ModError("No workspace mods folder is configured; set config workspace_mods or SE2_WORKSPACE.")
    return dirs[0]


def _print_status(env: SE2Env) -> None:
    from .play_cmd import launch_options
    try:
        root = mods_root(env)
    except ModError:  # status still reports the link; only on/off need the workspace folder
        root = None
    st = status(env.game_mods_dir, root, platform=env.platform)
    if st["attached"]:
        state = f"ATTACHED (game sees {st['target']})"
    elif st["link"] and root is None:
        state = (f"linked to {st['target']} (no workspace mods folder is configured; set config workspace_mods or "
                 "SE2_WORKSPACE)")
    else:
        state = "detached (Workshop mods only)"
    print(f"local mods: {state}")
    if st["link"] and not st["attached"] and root is not None:
        print(f"  ! {env.game_mods_dir} links to {st['target']}, not the workspace mods folder {root}")
    for opts in launch_options(env):
        print(f"SE2 Steam launch options: {opts or '(none)'}")
        flags = [f for f in _FLAGS if f in opts]
        if flags:
            print(f"  ! contains {', '.join(flags)}: remove for a normal game")
    if not procs.listing_available():
        print("SE2 / Mod SDK running: unknown (the process list is unavailable)")
    else:
        running = bool(procs.find_processes(procs.GAME_PROCS + procs.HUB_PROCS))
        print("SE2 / Mod SDK running:", "yes" if running else "no")
    print("Note: a world you saved while playing with local mods keeps references to them; load such worlds with "
          "`se2mod play` again, or remove the mod in the world's Manage Mods first.")


def _add_assume(p) -> None:
    p.add_argument("--assume-game-closed", action="store_true",
                   help="proceed even when the process list is unavailable (you vouch that the game is closed)")


def _list_unavailable(args) -> bool:
    """Refuse (with one sentence) to change the mount when we cannot see whether the game runs."""
    if args.assume_game_closed or procs.listing_available():
        return False
    print("error: cannot tell whether Space Engineers 2 is running (the process list is unavailable); close it and "
          "pass --assume-game-closed to continue.", file=sys.stderr)
    return True


@register("off", "Detach the workspace mods from the game (a normal Steam launch sees only Workshop mods)",
          check_game_version=False, add_args=_add_assume)
def cmd_off(args, env) -> int:
    require(env, "game_mods_dir")
    if _list_unavailable(args):
        return 1
    detach(env.game_mods_dir, platform=env.platform)
    print(f"workspace mods detached: the game's Mods folder is now empty ({env.game_mods_dir})")
    _print_status(env)
    return 0


@register("on", "Re-attach the workspace mods folder as the game's Mods folder", check_game_version=False,
          add_args=_add_assume)
def cmd_on(args, env) -> int:
    require(env, "game_mods_dir")
    if _list_unavailable(args):
        return 1
    root = mods_root(env)
    if status(env.game_mods_dir, root, platform=env.platform)["attached"]:
        print("already attached")
    else:
        attach(env.game_mods_dir, root, platform=env.platform)
        print(f"workspace mods attached: {env.game_mods_dir} -> {root}")
    _print_status(env)
    return 0


@register("status", "Show whether the workspace mods are attached, the game's launch options and what runs",
          check_game_version=False)
def cmd_status(args, env) -> int:
    require(env, "game_mods_dir")
    _print_status(env)
    return 0
