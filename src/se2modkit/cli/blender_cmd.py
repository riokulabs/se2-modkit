"""`se2mod blender run SCRIPT [--mod MOD] [--windowed] [-- ARGS...]`: run a Blender script with se2modkit importable.

Blender runs its own Python, so `sys.path` gets exactly two entries prepended: the folder holding the `se2modkit`
package and the mod's model source. A venv's `site-packages` is never added (its numpy or other packages would shadow
Blender's own); an installed, non-editable se2modkit is reached through a cache folder holding only a link to it.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from ..env.core import SE2Env, require
from ..env.links import is_link, make_link, remove_link, same_target
from ..project import ProjectError, load_project
from . import modarg, register

_SITE = ("site-packages", "dist-packages")


def _package_dir() -> Path:
    import se2modkit
    return Path(se2modkit.__file__).resolve().parent


def _shim(env: SE2Env, pkg: Path) -> Path:
    """`<cache>/blender-path/se2modkit` -> the package, so only se2modkit (not the whole site-packages) is visible."""
    shim = env.cache_dir / "blender-path"
    link = shim / "se2modkit"
    if is_link(link) and same_target(link, pkg):
        return shim
    if is_link(link):
        remove_link(link)
    elif link.exists():
        shutil.rmtree(link)
    try:
        make_link(link, pkg, platform=env.platform)
    except OSError:  # no symlink rights: a copy does the same job
        shutil.copytree(pkg, link, ignore=shutil.ignore_patterns("__pycache__"))
    return shim


def package_root(env: SE2Env) -> Path:
    """The folder to put on Blender's sys.path so `import se2modkit` works (never a site-packages folder)."""
    pkg = _package_dir()
    return _shim(env, pkg) if pkg.parent.name in _SITE else pkg.parent


def blender_argv(env: SE2Env, script: Path, args: list[str], *, mod_source: Path, windowed: bool) -> list[str]:
    require(env, "blender")
    paths = [str(package_root(env)), str(mod_source)]
    # se2modkit.pipeline first: it installs the read hook (pipeline.incremental), so even the files a build script's
    # modules read at import time count as inputs of its blocks
    expr = f"import sys; sys.path[:0] = {paths!r}; import se2modkit.pipeline"
    # --python-exit-code 1: an exception in the script fails the shell (Blender exits 0 by default)
    return [str(env.blender), *([] if windowed else ["-b"]), "--python-exit-code", "1", "--python-expr", expr,
            "--python", str(script), "--", *args]


def _mod_source(env: SE2Env, selector: str | None, script: Path) -> Path:
    """The mod's model source: MOD (a name or a path) through the shared resolver (`paths.source_model` of a project
    mod, else `<mod>/Source/Model`); without MOD the project's mod here (a multi-mod project outside a mod directory
    is the "which mod?" error), else the script's folder."""
    if selector is not None:
        ref = modarg.resolve(env, selector)
        return ref.config.paths.source_model if ref.config is not None else ref.root / "Source" / "Model"
    try:
        project = load_project(Path.cwd())
    except ProjectError as e:
        if not str(e).startswith("No pyproject.toml"):  # a project is here but broken: say so
            raise
        return script.resolve().parent
    return project.mod(None, cwd=Path.cwd()).paths.source_model


def build_env(env: SE2Env, base: dict | None = None) -> dict[str, str]:
    """The environment for a Blender build: the installed game version and Mod SDK build id ride along as
    SE2MODKIT_GAME_VERSION / SE2MODKIT_SDK_BUILDID, so they are inputs of every block's key (pipeline.incremental)."""
    child = dict(os.environ if base is None else base)
    child["SE2MODKIT_GAME_VERSION"] = str(env.game_version or "none")
    child["SE2MODKIT_SDK_BUILDID"] = str(env.sdk_buildid or "none")
    return child


def _run(argv: list[str], env: SE2Env | None = None) -> int:
    return subprocess.run(argv, check=False, env=None if env is None else build_env(env)).returncode


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="blender_cmd", metavar="SUBCOMMAND", required=True)
    r = sub.add_parser("run", help="run SCRIPT in Blender with se2modkit and the mod source importable")
    r.add_argument("script", type=Path)
    r.add_argument("--mod", help="mod whose model source goes on sys.path (default: the project's mod, else the "
                                 "script's folder)")
    r.add_argument("--windowed", action="store_true", help="open the Blender window (default: background, -b)")
    r.add_argument("--print-only", action="store_true", help="print the Blender command; run nothing")
    r.add_argument("script_args", nargs="*", metavar="ARG", help="arguments for the script (put them after --)")


@register("blender", "Run a script in Blender with se2modkit importable: blender run", add_args=_add_args,
          check_game_version=False)
def cmd_blender(args, env) -> int:
    extra = list(args.script_args)
    if extra and extra[0] == "--":
        extra = extra[1:]
    argv = blender_argv(env, args.script, extra, mod_source=_mod_source(env, args.mod, args.script),
                        windowed=args.windowed)
    if args.print_only:
        print(subprocess.list2cmdline(argv) if env.platform.startswith("win") else shlex.join(argv))
        return 0
    return _run(argv, env)
