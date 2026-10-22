"""The SDK standin: a small tree of links that lets the Mod SDK builder find the game's assemblies.

The SDK's Vanilla and Engine projects search `..\\Game2` (relative to their `GameData`) for assemblies the SDK does
not ship. The standin lays out

    <dest>/SDK/GameData/Vanilla -> <SDK>/GameData/Vanilla
    <dest>/SDK/GameData/Engine  -> <SDK>/GameData/Engine
    <dest>/SDK/Game2            -> <game>/Game2
    <dest>/Game2                -> <game>/Game2

and `se2mod build` passes `<dest>/SDK/GameData` as the project directory. Links are symlinks, or directory
junctions on Windows (no admin rights needed). `standin.json` records the game and SDK versions it was built for, so
a game or SDK update rebuilds it. Only links are ever removed, never what they point at.
"""

from __future__ import annotations

import json
from pathlib import Path

from .core import EnvError, SE2Env, require
from .links import is_link, make_link, remove_link, same_target

__all__ = ["build", "current", "default_dest", "ensure"]

META = "standin.json"


def default_dest(env: SE2Env) -> Path:
    return env.cache_dir / "standin"


def _links(env: SE2Env) -> dict[str, Path]:
    game2 = env.game_dir / "Game2"
    return {
        "Game2": game2,
        "SDK/Game2": game2,
        "SDK/GameData/Vanilla": env.sdk_dir / "GameData" / "Vanilla",
        "SDK/GameData/Engine": env.sdk_dir / "GameData" / "Engine",
    }


def _meta(env: SE2Env) -> dict:
    return {"game_version": env.game_version, "game_buildid": env.game_buildid, "sdk_buildid": env.sdk_buildid,
            "game_dir": str(env.game_dir), "sdk_dir": str(env.sdk_dir)}


def current(env: SE2Env, dest: Path) -> bool:
    """True if `dest` is a standin for this game and SDK: same versions and dirs, every link pointing right."""
    dest = Path(dest)
    try:
        meta = json.loads((dest / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if env.game_dir is None or env.sdk_dir is None or meta != _meta(env):
        return False
    return all(is_link(dest / rel) and same_target(dest / rel, target) for rel, target in _links(env).items())


def build(env: SE2Env, dest: Path) -> Path:
    """(Re)build the standin at `dest` and return it. Refuses to replace anything that is not a link."""
    require(env, "game_dir", "sdk_dir")
    dest = Path(dest)
    links = _links(env)
    for rel, target in links.items():
        if not target.is_dir():
            raise EnvError(f"Cannot build the SDK standin: {target} is missing; verify the game and Mod SDK files in "
                           "Steam.")
        p = dest / rel
        if p.exists() and not is_link(p):
            raise EnvError(f"Cannot build the SDK standin: {p} exists and is not a link; remove it or pick another "
                           "folder.")
    (dest / META).unlink(missing_ok=True)
    for rel, target in links.items():
        p = dest / rel
        if is_link(p):
            remove_link(p)
        make_link(p, target.resolve(), platform=env.platform)
    (dest / META).write_text(json.dumps(_meta(env), indent=1) + "\n", encoding="utf-8", newline="\n")
    return dest


def ensure(env: SE2Env, dest: Path | None = None) -> tuple[Path, bool]:
    """The standin at `dest` (default: the user cache), rebuilt if it is stale; `(dest, rebuilt)`."""
    dest = Path(dest) if dest is not None else default_dest(env)
    if current(env, dest):
        return dest, False
    return build(env, dest), True
