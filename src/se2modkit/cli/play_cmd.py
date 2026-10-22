"""`se2mod play [MOD ...] [--native] [--scripts|--no-scripts] [--print-only]`: launch the game with mods mounted.

Through Steam (`steam -applaunch 1133870`; on Linux the game runs in Proton, so mod paths are Wine paths) or, with
`--native`, through Pulsar (native paths, cwd the Pulsar folder). The game reads mods only at startup.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from ..env import procs
from ..env.core import EnvError, SE2Env, require
from ..env.links import is_link
from ..env.steam import GAME_APP_ID, parse_vdf
from . import register
from .mods_cmd import ModError, _resolve, project_file

__all__ = ["launch_options", "pulsar_argv", "steam_argv", "want_scripts"]

_PULSAR_EXES = ("Modern.bin", "Modern.exe")
_FLATPAK_STEAM = "com.valvesoftware.Steam"


def want_scripts(mods: list[Path], override: bool | None) -> bool:
    """Mod C# only compiles and loads with `-loadScripts`: `override` forces it on or off, otherwise it is on
    whenever a mounted mod ships `Scripts/`."""
    if override is not None:
        return override
    return any((Path(m) / "Scripts").is_dir() for m in mods)


def _project(mod: Path) -> Path:
    p = project_file(mod)
    if p is None:
        raise ModError(f"No mod project in {mod} (want {Path(mod).name}.vrgproj or exactly one .vrgproj).")
    return p


def _launch_args(projects: list[str], global_dirs: list[str], scripts: bool) -> list[str]:
    out = ["-startupTag:HUB"]
    if projects:
        out.append("-projectPaths:" + ";".join(projects))
        out.append("-globalProjectDirs:" + ";".join(global_dirs))
    if scripts:
        out.append("-loadScripts")
    return out


def _mounted_rel(env: SE2Env, mod: Path) -> Path | None:
    """`mod` relative to the folder the game's Mods link points at, or None when it lives elsewhere."""
    try:
        target = env.game_mods_dir.resolve()
        return Path(mod).resolve().relative_to(target)
    except (OSError, RuntimeError, ValueError):
        return None


def _steam_exe(env: SE2Env) -> list[str]:
    if env.platform.startswith("win"):
        require(env, "steam_root")
        return [str(env.steam_root / "steam.exe")]
    if shutil.which("steam") is None and env.steam_root is not None and _FLATPAK_STEAM in env.steam_root.parts:
        return ["flatpak", "run", _FLATPAK_STEAM]
    return ["steam"]


def steam_argv(env: SE2Env, mods: list[Path], *, scripts: bool) -> list[str]:
    """The Steam launch argv. A mod inside the mounted mods folder is addressed through the game's Mods folder (as
    the legacy script did); on Linux every path is the Wine path the game sees inside the Proton prefix."""
    mods = [Path(m) for m in mods]
    projects: list[str] = []
    global_dirs: list[str] = []
    if mods:
        require(env, "game_dir", "game_mods_dir")
        if not is_link(env.game_mods_dir):
            raise ModError("Workspace mods are detached from the game (run `se2mod on` first).")
        files = [_project(m) for m in mods]
        if env.platform.startswith("linux"):
            from ..env.proton import to_wine_path, wine_drive_map
            require(env, "prefix")
            drives = wine_drive_map(env.prefix)
            mods_wine = to_wine_path(env.game_mods_dir.parent, drives) + "\\" + env.game_mods_dir.name
            for m, f in zip(mods, files):
                rel = _mounted_rel(env, m)
                projects.append("\\".join([mods_wine, *rel.parts, f.name]) if rel is not None
                                else to_wine_path(f, drives))
            global_dirs = [to_wine_path(env.game_dir / "GameData", drives),
                           to_wine_path(env.game_dir / "VRage" / "GameData", drives)]
        else:
            for m, f in zip(mods, files):
                rel = _mounted_rel(env, m)
                projects.append(str(env.game_mods_dir.joinpath(*rel.parts, f.name)) if rel is not None else str(f))
            global_dirs = [str(env.game_dir / "GameData"), str(env.game_dir / "VRage" / "GameData")]
    return [*_steam_exe(env), "-applaunch", str(GAME_APP_ID), *_launch_args(projects, global_dirs, scripts)]


def _pulsar_exe(env: SE2Env) -> Path:
    require(env, "pulsar")
    p = Path(env.pulsar)
    if p.is_dir():
        hit = next((p / n for n in _PULSAR_EXES if (p / n).is_file()), None)
        if hit is None:
            raise EnvError(f"Pulsar not found in {p} (no {' or '.join(_PULSAR_EXES)}); set --pulsar, SE2_PULSAR or "
                           "config pulsar to the Pulsar launcher.")
        return hit
    if not p.is_file():
        raise EnvError(f"Pulsar not found at {p}; set --pulsar, SE2_PULSAR or config pulsar.")
    return p


def pulsar_argv(env: SE2Env, mods: list[Path], *, scripts: bool) -> tuple[list[str], Path]:
    """The Pulsar (native game) argv and its cwd, the Pulsar folder. Pulsar forwards unknown args to the game."""
    exe = _pulsar_exe(env)
    projects = [str(_project(Path(m))) for m in mods]
    global_dirs: list[str] = []
    if projects:
        require(env, "game_dir")
        global_dirs = [str(env.game_dir / "GameData"), str(env.game_dir / "VRage" / "GameData")]
    return [str(exe), *_launch_args(projects, global_dirs, scripts)], exe.parent


def _ci(d: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = next((v for k, v in d.items() if k.lower() == key.lower()), None)
    return d


def launch_options(env: SE2Env) -> list[str]:
    """The game's Steam launch options, one per Steam user whose `localconfig.vdf` lists the game (best effort)."""
    if env.steam_root is None:
        return []
    found = []
    for cfg in sorted((env.steam_root / "userdata").glob("*/config/localconfig.vdf")):
        try:
            data = parse_vdf(cfg.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        app = _ci(data, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", str(GAME_APP_ID))
        if isinstance(app, dict):
            lo = _ci(app, "LaunchOptions")
            found.append(lo if isinstance(lo, str) else "")
    return found


def _quote(argv: list[str], platform: str) -> str:
    return subprocess.list2cmdline(argv) if platform.startswith("win") else shlex.join(argv)


def _launch(argv: list[str], cwd: Path | None = None) -> None:
    """Start detached: the launcher outlives `se2mod`, its output is not ours."""
    kw: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(argv, cwd=cwd, **kw)


def _add_args(p) -> None:
    p.add_argument("mods", nargs="*", metavar="MOD", help="mods to mount (name or mod directory)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--scripts", dest="scripts", action="store_true", default=None,
                   help="force -loadScripts on (default: on when a mounted mod has Scripts/)")
    g.add_argument("--no-scripts", dest="scripts", action="store_false",
                   help="force -loadScripts off, even if a mod has Scripts/ (test the data-only fallback)")
    p.add_argument("--native", action="store_true", help="launch the native game through Pulsar instead of Steam")
    p.add_argument("--print-only", action="store_true", help="print the launch command; start nothing")


@register("play", "Launch Space Engineers 2 (Steam, or Pulsar with --native) with the mods mounted", add_args=_add_args)
def cmd_play(args, env) -> int:
    mods = [_resolve(env, m) for m in args.mods]
    scripts = want_scripts(mods, args.scripts)
    if args.native:
        argv, cwd = pulsar_argv(env, mods, scripts=scripts)
        busy = "native SE2 (Pulsar) is already running; quit it first, mods are only read at startup"
    else:
        argv, cwd = steam_argv(env, mods, scripts=scripts), None
        busy = "SE2 is already running; quit it first, mods are only read at startup"
    if not args.print_only and procs.game_running():  # before printing a command that will not run
        print(f"error: {busy}", file=sys.stderr)
        return 4
    print(_quote(argv, env.platform))
    if args.print_only:
        return 0
    if not args.native and env.platform.startswith("linux") and procs.find_processes(procs.HUB_PROCS):
        print("warning: the Mod HUB or Editor is running; Proton starts the game only after every process in the "
              "prefix has exited, so close them first", file=sys.stderr)
    _launch(argv, cwd)
    return 0
