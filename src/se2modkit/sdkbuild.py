"""Headless Mod SDK build: `Assets/*.def|*.partialdef` -> `Content/` through `VRage.ContentPipeline.Builder.exe`.

Linux runs the builder under the Proton build's Wine in the game's prefix; Windows runs it natively. Every guarantee of
the legacy `se2mod.py build` holds here: the `.vrgproj` is patched only for the build and its original bytes are put
back, `Scripts/` is hidden from the builder and un-hidden, stale definition outputs are moved aside to
`Content.pruned/` and put back if the build fails or is interrupted, the caches pass (`--generateCaches`) runs after a
successful build, and the outputs are verified afterwards (exit 3 if any is still stale).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from .cli.mods_cmd import ModError, own_guid, project_file
from .compat import load_compat
from .defs.envelope import envelope, write_json
from .env import procs
from .env.core import EnvError, SE2Env, override_hint, require
from .env.versions import SYSTEM_RUNTIME_BUNDLE, bundle_stamps

__all__ = [
    "BUILDER_EXE", "EnvError", "build", "builder_invocation", "definition_outputs", "ensure_metas", "hidden_scripts",
    "patched_project", "sdk_project_dirs", "stale_outputs",
]

BUILDER_EXE = "VRage.ContentPipeline.Builder.exe"
EXIT_STALE = 3
EXIT_GAME_RUNNING = 4
_META = "VRage:Keen.VRage.ContentPipeline.Metafiles.MetaData"
_HIDDEN = ".Scripts.hidden-for-build"
_SEARCH_KEYS = ("GameExecutableSearchPaths", "ReferencedAssembliesSearchPaths")

Runner = Callable[..., object]  # subprocess.run-like: run(argv, *, cwd, env) -> object with .returncode


def _linux(env: SE2Env) -> bool:
    return env.platform.startswith("linux")


def _tool_path(env: SE2Env, p: Path) -> str:
    """A host path as the builder sees it: a Wine path in the prefix on Linux, the native path elsewhere."""
    if not _linux(env):
        return str(p)
    from .env.proton import to_wine_path, wine_drive_map  # Linux only

    return to_wine_path(p, wine_drive_map(env.prefix))


# --- definition outputs -----------------------------------------------------------------------------------------


def _short(t: str | None) -> str:
    t = (t or "").split(":", 1)[-1]
    return t[:-len("ObjectBuilder")] if t.endswith("ObjectBuilder") else t


def definition_outputs(mod: Path) -> list[tuple[Path, Path, str, dict]]:
    """`[(asset, Content output, declared class, own members)]` for every .def / Copy .partialdef under `mod/Assets`.

    The SDK builder writes each to `Content/<same path>.def` with only the asset's own members (no BaseDefinition)."""
    out: list[tuple[Path, Path, str, dict]] = []
    assets, content = Path(mod) / "Assets", Path(mod) / "Content"
    for src in sorted(assets.rglob("*")):
        if src.suffix not in (".def", ".partialdef") or not src.is_file():
            continue
        try:
            data = json.loads(src.read_text(encoding="utf-8-sig"))
        except ValueError:
            continue
        v = data.get("$Value") or {}
        kind = v.get("PartialDefinitionKind")
        if kind == "Override":
            continue
        cls, own = ((_short(v.get("DefinitionType")), v.get("Manipulator") or {}) if kind == "Copy"
                    else (_short(data.get("$Type")), v))
        out.append((src, (content / src.relative_to(assets)).with_suffix(".def"), cls, own))
    return out


def stale_outputs(mod: Path, *, by_mtime: bool = False) -> list[tuple[Path, Path, str]]:
    """`[(asset, output, why)]`: an output missing, of another class than its asset declares, lacking one of the
    asset's own non-null members, or (`by_mtime`) older than its asset.

    Such an output loads in game against the definition index the builder DID regenerate (`Content/definitionsets.vrb`:
    base and class from the asset) as a different definition, and world load fails validation."""
    bad: list[tuple[Path, Path, str]] = []
    for src, dst, cls, own in definition_outputs(mod):
        if not dst.exists():
            bad.append((src, dst, "missing"))
            continue
        if by_mtime and dst.stat().st_mtime < src.stat().st_mtime:
            bad.append((src, dst, "older than its asset"))
            continue
        try:
            b = json.loads(dst.read_text(encoding="utf-8-sig"))
        except ValueError:
            bad.append((src, dst, "unparseable"))
            continue
        if _short(b.get("$Type")) != cls:
            bad.append((src, dst, f"class {_short(b.get('$Type')).rsplit('.', 1)[-1]} != {cls.rsplit('.', 1)[-1]}"))
            continue
        bv = b.get("$Value") or {}
        lost = sorted(k for k, x in own.items() if x is not None and k not in bv)
        if lost:
            bad.append((src, dst, f"lacks {lost}"))
    return bad


def ensure_metas(mod: Path, bundles: dict[str, str] | None = None) -> None:
    """Give every asset (file or folder) without a `.meta` sidecar a minimal one, as the Editor would; the builder
    fills in hashes and processor info. `bundles` defaults to the newest tested VRage stamp (pass the SDK's)."""
    if bundles is None:
        bundles = {"System.Runtime": SYSTEM_RUNTIME_BUNDLE, "VRage": load_compat().game_versions_tested[-1]}
    mod = Path(mod)
    todo: list[tuple[Path, str | None]] = []
    for asset in sorted((mod / "Assets").rglob("*")):
        meta = asset.with_name(asset.name + ".meta")
        # Files and sub-folders both need a sidecar ("File with unreadable parent" otherwise).
        if asset.suffix == ".meta" or meta.exists():
            continue
        # A definition asset's AssetID must equal the GUID it defines; otherwise the pipeline rewrites the
        # definition's GUID to the AssetID and every reference to it dangles. So an unreadable definition stops the
        # build before any sidecar is written (a random AssetID would persist and mismatch once it is fixed).
        asset_id = None
        if asset.suffix in (".def", ".partialdef") and asset.is_file():
            try:
                asset_id = own_guid(json.loads(asset.read_text(encoding="utf-8-sig")))
            except (ValueError, UnicodeDecodeError) as e:
                raise ModError(f"Cannot read {asset.relative_to(mod).as_posix()} as JSON ({e}); fix it, then build "
                               "again.") from e
        todo.append((meta, asset_id))
    for meta, asset_id in todo:
        write_json(meta, envelope(_META, {"AssetID": asset_id or str(uuid.uuid4()), "InternalItems": []}, bundles))
        print(f"  created {meta.relative_to(mod).as_posix()}")


# --- invocation and temporary project changes ------------------------------------------------------------------


def builder_invocation(env: SE2Env, project: Path, *, dry_run: bool,
                       verbose: bool) -> tuple[list[str], Path, dict[str, str]]:
    """`(argv, cwd, environment)` that runs the SDK's content builder on `project`."""
    require(env, "sdk_editor")
    run_env = dict(os.environ)
    if _linux(env):
        require(env, "proton", "prefix")
        wine = env.proton / "files" / "bin" / "wine"
        if not wine.is_file():
            raise EnvError(f"Proton build has no Wine at {wine}; set {override_hint('proton')}.")
        argv = [str(wine), BUILDER_EXE, "-p", _tool_path(env, Path(project))]
        run_env.update(WINEPREFIX=str(env.prefix), WINEDEBUG="-all")
    else:
        argv = [str(env.sdk_editor / BUILDER_EXE), "-p", str(project)]
    if dry_run:
        argv.append("--dryRun")
    if verbose:
        argv.append("--verbose")
    return argv, env.sdk_editor, run_env


def sdk_project_dirs(env: SE2Env, environ: Mapping[str, str] = os.environ) -> list[Path]:
    """Parent folder of the SDK's Vanilla/Engine projects, as the Editor gets it via `--globalProjectDirs`.

    The standin tree (`se2mod sdk standin`, user cache) lays it out so the projects' relative `..\\Game2` assembly
    search paths reach the game binaries; a workspace `tools/standin` (`SE2_WORKSPACE`) is accepted as before; else
    the SDK's own `GameData`, which works only where the game sits beside it."""
    cands = [env.cache_dir / "standin" / "SDK" / "GameData"]
    if environ.get("SE2_WORKSPACE"):
        cands.append(Path(environ["SE2_WORKSPACE"]) / "tools" / "standin" / "SDK" / "GameData")
    for c in cands:
        if c.is_dir():
            return [c]
    require(env, "sdk_dir")
    return [env.sdk_dir / "GameData"]


@contextmanager
def patched_project(project: Path, env: SE2Env) -> Iterator[Path]:
    """Point the project at the SDK projects and the game binaries for the build; restore its exact bytes on exit
    (including on `KeyboardInterrupt`). The standalone builder finds Vanilla only through `ProjectDirectories`."""
    require(env, "game_dir")
    project = Path(project)
    original = project.read_bytes()
    data = json.loads(original.decode("utf-8-sig"))
    value = data.setdefault("$Value", {})
    sdk_dirs = sdk_project_dirs(env)
    if env.sdk_dir is not None and sdk_dirs == [env.sdk_dir / "GameData"]:
        print("warning: no SDK standin found; using the SDK's own GameData (run `se2mod sdk standin` if the build "
              "cannot find the game assemblies)", file=sys.stderr)
    add_dirs = [_tool_path(env, p) for p in sdk_dirs]
    game_bin = _tool_path(env, env.game_dir / "Game2")
    dirs = value.setdefault("ProjectDirectories", [])
    if any(x in dirs for x in add_dirs) or any(game_bin in (value.get(k) or []) for k in _SEARCH_KEYS):
        print(f"warning: {project.name} already holds the build-time SDK/game paths (a previous build was stopped "
              "before restoring it); keeping it as it is now", file=sys.stderr)
    dirs.extend([x for x in add_dirs if x not in dirs])
    for key in _SEARCH_KEYS:
        paths = value.setdefault(key, [])
        if game_bin not in paths:
            paths.append(game_bin)
    try:
        _atomic_write(project, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
        yield project
    finally:
        _atomic_write(project, original)


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace `path` with `data` through a temporary sibling, so an interruption never leaves a half-written file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def _sigterm_as_interrupt() -> Iterator[None]:
    """Turn SIGTERM into KeyboardInterrupt for the duration, so a killed build still runs every restore."""
    sig = getattr(signal, "SIGTERM", None)
    if sig is None or threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    previous = signal.signal(sig, handler)
    try:
        yield
    finally:
        signal.signal(sig, previous)


@contextmanager
def hidden_scripts(mod: Path) -> Iterator[None]:
    """Hide `Scripts/` from the builder (it compiles them against VRage only, so any Game2 reference fails the whole
    build; the game compiles scripts itself at load time) and un-hide it on exit, including on interrupt."""
    scripts, hidden = Path(mod) / "Scripts", Path(mod) / _HIDDEN
    if hidden.exists() and scripts.exists():
        raise ModError(f"Both Scripts/ and {_HIDDEN}/ exist in {mod}; keep one (an interrupted build left the "
                       "second), then build again.")
    if hidden.exists():
        hidden.rename(scripts)  # left over from an interrupted build
    if scripts.exists():
        scripts.rename(hidden)
    try:
        yield
    finally:
        if hidden.exists() and not scripts.exists():
            hidden.rename(scripts)


def _restore_pruned(mod: Path, held: Path) -> int:
    """Put moved-aside outputs back where the build did not write a new one; returns how many."""
    n = 0
    for f in sorted(p for p in held.rglob("*") if p.is_file()):
        dst = mod / "Content" / f.relative_to(held)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            f.rename(dst)
            n += 1
    shutil.rmtree(held)
    return n


# --- build ------------------------------------------------------------------------------------------------------


def build(env: SE2Env, mod: Path, *, dry_run: bool = False, verbose: bool = False,
          run: Runner = subprocess.run, assume_game_closed: bool = False) -> int:
    """Build `mod` with the SDK builder; returns the exit code (0 ok, builder's code on failure, 3 stale outputs,
    4 refused because the game is running or the process list is unavailable)."""
    if not assume_game_closed and not procs.listing_available():
        print("Cannot tell whether Space Engineers 2 is running (the process list is unavailable); close it and "
              "pass --assume-game-closed to build anyway.", file=sys.stderr)
        return EXIT_GAME_RUNNING
    if procs.game_running():
        print("Space Engineers 2 is running; close it before building (the build and the game share the mod files).",
              file=sys.stderr)
        return EXIT_GAME_RUNNING
    mod = Path(mod)
    require(env, "sdk_editor", "game_dir", *(("proton", "prefix") if _linux(env) else ()))
    project = project_file(mod)
    if project is None:
        raise ModError(f"No mod project in {mod} (want {mod.name}.vrgproj or exactly one .vrgproj).")
    argv, cwd, run_env = builder_invocation(env, project, dry_run=dry_run, verbose=verbose)
    # An interrupted or failed build can leave Content.tmp/ with folders Wine can't enter, and the next build then
    # fails with access denied. It's the builder's own scratch dir, so clear it before building.
    scratch = mod / "Content.tmp"
    if scratch.exists():
        shutil.rmtree(scratch)
        print(f"removed stale {scratch.name}/")
    ensure_metas(mod, bundle_stamps(env)[0])
    # The builder can leave a definition's Content output stale while it regenerates the definition index, and the
    # game then loads the old members on the new base. So every output older than its asset (or otherwise stale) is
    # MOVED aside to Content.pruned/ first (a missing output is always rewritten) and put back if the build fails.
    held = mod / "Content.pruned"
    if held.exists():
        _restore_pruned(mod, held)  # left over from an interrupted build
    with _sigterm_as_interrupt():  # a killed build still restores the project, Scripts/ and Content.pruned/
        try:
            if not dry_run:
                pruned = [x for x in stale_outputs(mod, by_mtime=True) if x[1].exists()]
                for _, dst, _ in pruned:
                    to = held / dst.relative_to(mod / "Content")
                    to.parent.mkdir(parents=True, exist_ok=True)
                    dst.rename(to)
                if pruned:
                    print(f"moved {len(pruned)} stale definition outputs aside to {held.name}/ (rebuilt now)")
            with patched_project(project, env), hidden_scripts(mod):
                print("building", mod.name, "...", flush=True)
                rc = run(argv, cwd=cwd, env=run_env).returncode
                if rc == 0 and not dry_run:
                    # The game only registers a mod's definitions through Content/definitionsets.vrb and
                    # Content/contentcache.vrb; a plain build doesn't write them (the Editor does).
                    print("generating content caches ...", flush=True)
                    rc = run(argv + ["--generateCaches"], cwd=cwd, env=run_env).returncode
        except BaseException:
            if held.exists():
                n = _restore_pruned(mod, held)
                print(f"build interrupted: put {n} moved-aside definition outputs back", file=sys.stderr)
            raise
    for f in sorted((mod / "Content").glob("*.def")):
        try:
            v = json.loads(f.read_text(encoding="utf-8-sig")).get("$Value")
        except (OSError, ValueError, AttributeError):
            continue
        print(f"  {f.name}: {json.dumps(v)[:120]}")
    if held.exists():
        if rc == 0:
            shutil.rmtree(held)
        else:
            n = _restore_pruned(mod, held)
            print(f"build failed: put {n} moved-aside definition outputs back", file=sys.stderr)
    if rc == 0 and not dry_run:
        stale = stale_outputs(mod)
        if stale:
            print(f"ERROR: {len(stale)} definition outputs are stale after the build (the game would load them on "
                  "the regenerated index and may fail validation):", file=sys.stderr)
            for src, _, why in stale[:30]:
                print(f"  {src.relative_to(mod).as_posix()}: {why}", file=sys.stderr)
            rc = EXIT_STALE
        else:
            print(f"verified {len(definition_outputs(mod))} definition outputs current")
    return rc
