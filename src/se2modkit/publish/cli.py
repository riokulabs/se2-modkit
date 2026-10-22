"""Command line: se2publish {publish,stage,thumb,ledger} MOD [--mod NAME] ...

`publish` is a DRY RUN unless --i-have-user-approval is given AND the interactive confirmation is answered.
In a repo holding several mods, name the mod with --mod NAME (or a NAME-vX.Y.Z release tag as --version).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import progress
from ..env.config import ConfigError
from ..env.core import EnvError, SE2Env, discover_env
from ..release import publish_check
from . import __version__, gitutil, limits, release
from .hostenv import GAME_APP_ID, build_env, find_libsteam_api, hub_bundles
from .project import Refusal, check_canonical, load_config, read_ledger, read_tracked, select_mod
from .publish import assemble, execute, preflight_live, render_plan
from .stage import Limits, StageResult, check_tree, collect_head_scripts, stage


def discover() -> SE2Env:
    """The install paths (`se2modkit.env`: flag > env var > user config > discovery)."""
    return discover_env()


def workspace_mods(env: SE2Env | None) -> list[Path]:
    """Where a bare mod name is looked up: user config `workspace_mods`, then `$SE2_WORKSPACE/mods`."""
    if env is None:
        ws = os.environ.get("SE2_WORKSPACE")
        return [Path(ws) / "mods"] if ws else []
    from ..cli.mods_cmd import workspace_mods_dirs
    return workspace_mods_dirs(env)


def resolve_mod(arg: str, env: SE2Env | None = None) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p.resolve()
    dirs = workspace_mods(env)
    for d in dirs:
        if (d / arg).is_dir():
            return (d / arg).resolve()
    where = ", ".join(str(d) for d in dirs) or "no workspace_mods (set config workspace_mods or SE2_WORKSPACE)"
    raise Refusal(f"no mod folder '{arg}' (looked in . and {where})")


def project_for(args, env: SE2Env | None = None):
    return select_mod(resolve_mod(args.mod, env), getattr(args, "only_mod", None), getattr(args, "version", None))


def work_dir(args, p) -> Path:
    base = Path(args.work_dir) if args.work_dir else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "se2publish"
    return (base / (f"{p.repo.name}--{p.name}" if p.multi else p.folder.name)).resolve()


def do_stage(args, p, cfg, env) -> StageResult:
    wd = work_dir(args, p)
    info = wd / "stage-info.json"
    if getattr(args, "reuse_stage", False):
        if not info.exists():
            raise Refusal(f"--reuse-stage: nothing staged in {wd} (run `se2publish stage {args.mod}"
                          f"{f' --mod {p.name}' if p.multi else ''}`)")
        meta = json.loads(info.read_text())
        head = gitutil.head_sha(p.folder)
        if meta.get("head") != head:
            raise Refusal(f"--reuse-stage: the stage is from commit {meta.get('head', '?')[:8]}, HEAD is {head[:8]}")
        st_dir = wd / "stage"
        scripts, warns = collect_head_scripts(p.folder, None)
        man, errors = check_tree(st_dir, p, Limits(), {s: "git:HEAD" for s in scripts})
        if errors:
            raise Refusal("staged content failed the safety checks:\n  " + "\n  ".join(errors))
        return StageResult(st_dir, man, scripts, head, warns + [f"reused stage from {meta.get('time')}"],
                           scratch=wd / "src" / p.folder.name)
    st = stage(p, build_env(env), wd, read_tracked(p, cfg.thumbnail), sandbox=not args.no_sandbox,
               prebuild=not args.no_prebuild, timeout=args.timeout)
    import datetime as dt
    info.write_text(json.dumps({"head": st.head, "time": dt.datetime.now().isoformat(timespec="seconds"),
                                "sandbox": not args.no_sandbox}) + "\n")
    return st


def cmd_stage(args) -> int:
    env = discover()
    p = project_for(args, env)
    for n in check_canonical(p):
        print("note:", n)
    cfg = load_config(p)
    st = do_stage(args, p, cfg, env)
    m = st.manifest
    print(f"staged {p.name} at {st.stage_dir} (commit {st.head[:12]}, builder {st.build.seconds:.0f}s)" if st.build
          else f"staged {p.name} at {st.stage_dir}")
    print(f"{len(m.entries)} files, {m.total_bytes:,} bytes, manifest sha256 {m.digest()}")
    for top, (n, b) in m.by_top().items():
        print(f"  {top:<28} {n:>6} files {b:>14,} bytes")
    if args.list:
        for e in sorted(m.entries, key=lambda e: e.path):
            print(f"  {e.size:>12,}  {e.origin:<9} {e.path}")
    for w in st.warnings:
        print("WARNING:", w)
    return 0


def cmd_publish(args) -> int:
    real = args.i_have_user_approval
    progress.log(f"{'publish' if real else 'dry run'}: {args.mod} {args.version or ''}".rstrip())
    with progress.step("reading the mod and finding the game and SDK"):
        env = discover()
        p = project_for(args, env)
        notes = check_canonical(p)
        cfg = load_config(p)
    # what ships must come from a release build of this very commit (`se2mod build-models MOD --release`)
    release_issue, release_note = publish_check(p.folder)
    if release_issue and real:
        raise Refusal(release_issue)
    st, stage_error = None, None
    if not args.no_stage:
        try:
            with progress.step("staging: SDK build and package checks (several minutes)"):
                st = do_stage(args, p, cfg, env)
        except Refusal as e:
            if real:
                raise
            stage_error = str(e)          # a dry run still prints the rest of the plan
    progress.log("assembling the upload plan")
    plan = assemble(p, cfg, version=args.version, visibility=args.visibility, adopt_existing=args.adopt_existing,
                    change_note_file=Path(args.change_note_file) if args.change_note_file else None,
                    keep_web_description=args.keep_web_description, stage=st, bundles=hub_bundles(env),
                    strict_git=real)
    plan.warnings[:0] = notes
    if stage_error:
        plan.blockers.insert(0, "staging failed: " + stage_error)
    if release_issue:
        plan.blockers.insert(0, "release build: " + release_issue)
    if release_note:
        plan.warnings.insert(0, "release build: " + release_note)
        print(f"WARNING: {release_note}", file=sys.stderr)
    render_plan(plan, full=args.full)
    if not real:
        if release_issue:
            print(f"\nWARNING: a real upload would be refused: {release_issue}", file=sys.stderr)
        print("\nDRY RUN: nothing was sent to Steam. A real upload needs --i-have-user-approval and a typed "
              "confirmation.")
        return 0
    if not plan.ready or plan.stage is None:
        print("refusing to upload: fix the blockers above first", file=sys.stderr)
        return 2
    print("\nFiles that will be uploaded:")
    for e in sorted(plan.stage.manifest.entries, key=lambda e: e.path):
        print(f"  {e.size:>12,}  {e.path}")
    print(f"total {plan.stage.manifest.total_bytes:,} bytes in {len(plan.stage.manifest.entries)} files")
    if not sys.stdin.isatty():
        print("refusing: the confirmation must be typed at an interactive terminal", file=sys.stderr)
        return 2
    progress.log("waiting for your confirmation")
    answer = input(f"\nThis is an UNOFFICIAL upload outside Keen's Mod HUB. Type '{p.name}' to upload: ")
    if answer.strip() != p.name:
        print("not confirmed; nothing was sent")
        return 2
    with progress.step("checking the game is closed"):
        preflight_live(args.allow_running_game)
    lib = find_libsteam_api(env)
    if lib is None:
        raise Refusal("no libsteam_api.so found (set SE2_LIBSTEAM_API to a Steamworks redistributable)")
    from .steam import FlatApiBackend
    with progress.step("connecting to Steam"):
        backend = FlatApiBackend(lib, GAME_APP_ID)
    try:
        with progress.step("uploading to the Workshop"):
            item = execute(plan, backend, adopt_existing=args.adopt_existing)
    finally:
        backend.close()
    # only after the upload succeeded; problems here are warnings, never a failed publish
    release.after_upload(p, cfg, plan.release_version, plan.stage.head, item)
    return 0


def cmd_thumb(args) -> int:
    p = project_for(args, discover())
    cfg = load_config(p)
    dest = Path(args.out) if args.out else cfg.thumbnail
    q, n = limits.reencode_thumbnail(Path(args.src), dest)
    print(f"wrote {dest} ({limits.THUMB_SIZE[0]}x{limits.THUMB_SIZE[1]}, JPEG quality {q}, {n:,} bytes)")
    return 0


def cmd_ledger(args) -> int:
    p = project_for(args, discover())
    rows = read_ledger(p)
    if not rows:
        print("no publishes recorded")
    for r in rows:
        print(json.dumps(r, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="se2publish", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"se2publish {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("mod", help="mod repo folder, or a name under the workspace mods folder")
        sp.add_argument("--mod", dest="only_mod", metavar="NAME", help="the mod to use in a repo holding several mods")
        sp.add_argument("--work-dir", help="staging root (default ~/.cache/se2publish)")
        sp.add_argument("--no-sandbox", action="store_true",
                        help="run the builder directly in the shared Proton prefix (it then writes its caches into "
                             "the prefix and SDK GameData, like se2mod.py build)")
        sp.add_argument("--no-prebuild", action="store_true", help="skip the plain build before --prepareForPublish")
        sp.add_argument("--timeout", type=int, default=2700, help="builder timeout in seconds")

    sp = sub.add_parser("stage", help="build + stage + check; prints the manifest")
    common(sp)
    sp.add_argument("--list", action="store_true", help="list every staged file")
    sp.set_defaults(fn=cmd_stage)

    sp = sub.add_parser("publish", help="plan (default: dry run) or perform an upload")
    common(sp)
    sp.add_argument("--reuse-stage", action="store_true", help="use the stage from `se2publish stage` (same HEAD)")
    sp.add_argument("--no-stage", action="store_true", help="plan without staging (metadata/change note only)")
    sp.add_argument("--version", dest="version",
                    help="mod version whose CHANGELOG.md section is the change note: X.Y.Z, vX.Y.Z or, in a multi-mod "
                         "repo, the release tag NAME-vX.Y.Z (mods/<NAME>/CHANGELOG.md)")
    sp.add_argument("--visibility", choices=["Public", "FriendsOnly", "Private", "Unlisted"],
                    help="set visibility (first upload default: Unlisted; updates keep the current one)")
    sp.add_argument("--adopt-existing", action="store_true",
                    help="allow updating an item this tool did not create (e.g. one the Mod HUB published)")
    sp.add_argument("--change-note-file", help="use this file instead of CHANGELOG.md (.md converted, .bbcode as is)")
    sp.add_argument("--keep-web-description", action="store_true", help="do not send the description")
    sp.add_argument("--full", action="store_true", help="print the full description in the plan")
    sp.add_argument("--allow-running-game", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--i-have-user-approval", action="store_true",
                    help="perform the real upload (still asks for a typed confirmation)")
    sp.set_defaults(fn=cmd_publish)

    sp = sub.add_parser("thumb", help="re-encode an image to a 1280x720 JPEG under 1 MB")
    sp.add_argument("mod")
    sp.add_argument("--mod", dest="only_mod", metavar="NAME", help="the mod to use in a repo holding several mods")
    sp.add_argument("--from", dest="src", required=True)
    sp.add_argument("--out", help="destination (default: the configured thumbnail path)")
    sp.set_defaults(fn=cmd_thumb)

    sp = sub.add_parser("ledger", help="show the publish ledger")
    sp.add_argument("mod")
    sp.add_argument("--mod", dest="only_mod", metavar="NAME", help="the mod to use in a repo holding several mods")
    sp.set_defaults(fn=cmd_ledger)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    except (EnvError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
