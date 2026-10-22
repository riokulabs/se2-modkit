"""`se2mod build-models MOD [BLOCK ...]` (incremental, parallel model builds) and `se2mod check MOD BLOCK` (rebuild one
block, run its tests, render one still: one summary line)."""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

from .. import modelbuild as MB
from . import modarg, register

__all__ = ["cmd_build_models", "cmd_check"]


def _game_closed(assume: bool) -> str | None:
    """None when building is allowed, else why not (one sentence)."""
    if assume:
        return None
    from ..env import procs
    if not procs.listing_available():
        return ("The process list cannot be read, so the game may be running; close it and pass "
                "--assume-game-closed.")
    if procs.game_running():
        return "Space Engineers 2 is running; models build only while it is closed."
    return None


def _mod(env, selector):
    ref = modarg.resolve(env, selector, need="config")
    if ref.config is None:
        raise MB.BuildError(f"{ref.name} is not a mod of a [tool.se2modkit] project; the build needs its paths.")
    return ref.config


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--force", action="store_true", help="rebuild even when nothing changed")
    p.add_argument("--script", type=Path, metavar="FILE",
                   help="the mod's build script (default: the one Source/Model/build_*.py)")
    p.add_argument("--timeout", type=float, default=3600, metavar="S",
                   help="stop every Blender after S seconds per phase (default 3600)")
    p.add_argument("--assume-game-closed", action="store_true",
                   help="build even when the process list is unavailable (you vouch that the game is closed)")


def _build_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("mod", help="mod name (project here or a workspace_mods folder) or path to a mod directory")
    p.add_argument("blocks", nargs="*", metavar="BLOCK",
                   help='block names, PREFIX* or target words (default: the pipeline\'s blocks, "all")')
    p.add_argument("-j", "--jobs", type=int, default=MB.default_jobs(), metavar="N",
                   help=f"headless Blender workers (default min(CPUs, 4) = {MB.default_jobs()})")
    p.add_argument("--export", action="store_true",
                   help="also export the game models: one windowed Blender (Keen's VRAGE Tools), serial")
    p.add_argument("--render", action="store_true", help="render the mod's previews too (serial)")
    p.add_argument("--release", action="store_true",
                   help="the clean build a publish needs: refuses uncommitted changes, rebuilds and exports every "
                        "block whatever the saved build state says, and stamps the result for `se2mod publish`")
    _common_args(p)


@register("build-models", "Build a mod's stale block models in Blender (incremental; headless blocks in parallel)",
          add_args=_build_args, check_game_version=False)
def cmd_build_models(args, env) -> int:
    from .. import release as REL
    if args.jobs < 1:
        print("error: --jobs needs at least 1.", file=sys.stderr)
        return 2
    release = getattr(args, "release", False)
    if release and args.blocks:
        print("error: --release builds every block of the mod; drop the block names.", file=sys.stderr)
        return 2
    try:
        cfg = _mod(env, args.mod)
        script = MB.find_script(cfg.paths.source_model, args.script)
        why = _game_closed(args.assume_game_closed)
        if why:
            print(f"error: {why}", file=sys.stderr)
            return 4
        if release:
            stamp, head = _release_start(cfg, script)
        res = MB.build_models(env, mod_name=cfg.name, source_model=cfg.paths.source_model, script=script,
                              words=list(args.blocks), jobs=args.jobs, report=cfg.paths.report,
                              force=args.force or release, export=args.export or release,
                              render=args.render, timeout=args.timeout)
        if release:
            _release_finish(cfg, res, stamp, head, script)
    except (MB.BuildError, REL.ReleaseError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted; every Blender this build started was stopped.", file=sys.stderr)
        return 130
    for w in (res.status or {}).get("warnings") or ():
        print(f"warning: {w}", file=sys.stderr)
    n = len(res.selected)
    if res.built:
        shown = ", ".join(res.built[:8]) + (" ..." if len(res.built) > 8 else "")
        how = f"{res.workers} workers" if res.workers > 1 else "1 Blender"
        print(f"{cfg.name}: built {len(res.built)} of {n} block(s) ({shown}) with {how} in {res.seconds:.1f} s; "
              f"report {cfg.paths.report}")
    else:
        print(f"{cfg.name}: {n} block(s) up to date, nothing built ({res.seconds:.1f} s); report {cfg.paths.report}")
    return 0


def _release_start(cfg, script: Path) -> tuple[Path, str]:
    """Before a release build: the tree must be clean (what is built is exactly a commit; files git ignores under the
    models folder or Assets/ count, they would ship), the build script committed inside the mod's repository, and
    nothing under the models folder or Assets/ a symlink. The old stamp goes, so a release build that fails leaves
    none behind. Returns (stamp path, HEAD)."""
    from .. import release as REL
    changes = REL.dirty(cfg.root, (cfg.paths.models, cfg.paths.assets))
    if changes:
        raise REL.ReleaseError(f"{cfg.name} has uncommitted changes ({REL.few(changes)}); a release build is made "
                               "from a clean, committed tree, so commit or stash them first.")
    why = REL.script_problem(script, cfg.root)
    if why:
        raise REL.ReleaseError(why[0].upper() + why[1:])
    _no_symlinks(cfg)
    stamp = REL.stamp_path(cfg.paths.report, cfg.name)
    stamp.unlink(missing_ok=True)
    return stamp, REL.head(cfg.root)


def _no_symlinks(cfg) -> None:
    from .. import release as REL
    links = REL.symlinks(cfg.paths.models, cfg.paths.assets)
    if links:
        raise REL.ReleaseError(f"{cfg.name} has a symlink where models ship from ({REL.few(links)}); a release build "
                               "hashes real files only, so replace it with the files themselves.")


def _release_finish(cfg, res, stamp: Path, head: str, script: Path) -> None:
    """After a release build: stamp it, unless the build changed tracked files (those must be committed first, then
    the release build run again, so the stamp describes a commit) or a copy of an exported file in Assets/ (what a
    publish ships) is not byte-identical to the export."""
    from .. import release as REL
    from ..pipeline import incremental as IN
    changes, now = REL.dirty(cfg.root, (cfg.paths.models, cfg.paths.assets)), REL.head(cfg.root)
    if changes:
        raise REL.ReleaseError(f"The release build of {cfg.name} changed files in the mod ({REL.few(changes)}); "
                               f"commit them, then run `{REL.release_command(cfg.name)}` again.")
    if now != head:
        raise REL.ReleaseError(f"HEAD of {cfg.name} moved from {head[:12]} to {now[:12]} during the release build; "
                               f"run `{REL.release_command(cfg.name)}` again.")
    _no_symlinks(cfg)
    files = REL.model_files(cfg.paths.models, cfg.root)
    shipped, bad = REL.shipped_copies(cfg.paths.models, cfg.paths.assets, cfg.root, cfg.name, cfg.paths.staged,
                                      require_staged=True)
    if bad:
        raise REL.ReleaseError(f"{cfg.name} ships model files in Assets/ that are not the release build's export "
                               f"or staged package at that place ({REL.few(bad)}); stage and install the export into Assets/ (the mod's "
                               f"integration step) and remove what it no longer has, commit, then run "
                               f"`{REL.release_command(cfg.name)}` again.")
    store = IN.Store.for_report(cfg.paths.report, cfg.name)
    keys = {}
    for b in res.selected:
        frag = store.load(b) or {}
        keys[b] = frag.get("key") or "missing"
    REL.write_stamp(stamp, mod=cfg.name, head_sha=head, blocks=res.selected, keys=keys, files=files, shipped=shipped,
                    script=REL.rel(script, cfg.root))
    print(f"{cfg.name}: release build of commit {head[:12]} stamped in {stamp}")


# ------------------------------------------------------------------------------------------------ se2mod check
def _check_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("mod", help="mod name (project here or a workspace_mods folder) or path to a mod directory")
    p.add_argument("block", help="the block to check")
    p.add_argument("--set", dest="set_name", metavar="NAME", help="preview set of previews.toml (if it has several)")
    _common_args(p)


def _quiet(fn, *a):
    """(exit code, stdout lines, stderr lines) of fn(*a), its printing captured."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = fn(*a)
        except SystemExit as e:   # argparse-style exits from a step: its message, never the whole check
            if isinstance(e.code, str):
                print(f"error: {e.code}", file=sys.stderr)
            code = e.code if isinstance(e.code, int) else 1
        except Exception as e:  # noqa: BLE001 (any step error is one sentence in the summary)
            print(f"error: {e}", file=sys.stderr)
            code = 1
    return code, out.getvalue().splitlines(), err.getvalue().splitlines()


def _first_error(lines: list[str], default: str) -> str:
    for line in lines:
        if line.startswith("error: "):
            return line[7:].strip()
    return default


def _tests(env, mod: str, block: str, timeout: float) -> tuple[bool, str]:
    from .testrun_cmd import GATE_NOTE, cmd_test
    ns = argparse.Namespace(mod=mod, fast=False, changed=False, base=None, fallback_full=False, module=[],
                            block=[block], gate=[], full=False, serial=False, list=False, max_lines=0,
                            timeout=timeout)
    code, out, err = _quiet(cmd_test, ns, env)
    lines = [ln for ln in out if ln.strip() and ln != GATE_NOTE]
    if code not in (0, 5) and not any(" | " in ln for ln in lines):
        return False, "error (" + _first_error(err, lines[0] if lines else f"exit {code}") + ")"
    head = next((ln.split(" | ", 1)[0] for ln in lines if " | " in ln), None)
    if head is None:
        return True, "none match the block"
    return code in (0, 5), head


def _still(env, mod: str, block: str, set_name: str | None, timeout: float) -> tuple[bool, str]:
    from .preview_cmd import preview_shots
    ns = argparse.Namespace(mod=mod, set_name=set_name, block=[block], shot=None, still=True, fast=False, plan=None,
                            list=False, print_only=False, timeout=int(timeout))
    code, out, err = _quiet(preview_shots, ns, env)
    if code:
        return False, "none (" + _first_error(err, f"exit {code}") + ")"
    images = [ln for ln in out if Path(ln).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    return bool(images), images[0] if images else "none (no image written)"


@register("check", "Rebuild one block if stale, run its tests and render one still: one summary line",
          add_args=_check_args, check_game_version=False)
def cmd_check(args, env) -> int:
    block = args.block
    if not block or block == "all" or "*" in block or block.startswith("-"):
        print(f"error: {block!r} is not a block name; check takes one block, named exactly.", file=sys.stderr)
        return 2
    try:
        cfg = _mod(env, args.mod)
        script = MB.find_script(cfg.paths.source_model, args.script)
        why = _game_closed(args.assume_game_closed)
        if why:
            print(f"error: {why}", file=sys.stderr)
            return 4
        res = MB.build_models(env, mod_name=cfg.name, source_model=cfg.paths.source_model, script=script,
                              words=[block], jobs=1, report=cfg.paths.report, force=args.force, export=True,
                              blocks_only=True, timeout=args.timeout)
    except MB.BuildError as e:
        print(f"error: {block}: build failed: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted; the Blender this check started was stopped.", file=sys.stderr)
        return 130
    if block not in res.selected:
        print(f"error: {block} is not a block of {cfg.name}'s build (it built {', '.join(res.selected) or 'nothing'}).",
              file=sys.stderr)
        return 2
    rebuilt = "yes" if block in res.built else "no"
    tests_ok, tests = _tests(env, args.mod, block, args.timeout)
    still_ok, still = _still(env, args.mod, block, args.set_name, args.timeout)
    print(f"{block}: rebuilt {rebuilt} | tests {tests} | still {still}")
    return 0 if tests_ok and still_ok else 1
