"""`se2mod kit build | fetch | path NAME | library [--check] [--relink] [--register]`: the kit assets (Rioku atlas,
maker mark) in the user cache, and Keen's Blender material library from the Mod SDK."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .. import kit
from . import register

LIBRARY_NAME = "SE2 Keen Materials"
PROBE_TIMEOUT = 900   # s: opening the 330 MB library takes a few seconds; the rest is the queue


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="kit_action", metavar="ACTION", required=True)
    b = sub.add_parser("build", help="render the Rioku atlas from its SVG sources into the user cache")
    b.add_argument("--no-dds", action="store_true", help="write the PNG only, never run texconv")
    b.add_argument("--timeout", type=float, default=60.0, metavar="S", help="texconv time limit (default 60 s)")
    sub.add_parser("fetch", help="download this version's kit asset zip and check its sha256")
    q = sub.add_parser("path", help="print the cached path of a kit asset")
    q.add_argument("name", help="asset file name, e.g. RiokuAtlas01.png")
    lib = sub.add_parser("library", help="unpack Keen's Blender material library from the Mod SDK into the user "
                                         "cache and print the folder to register as a Blender asset library")
    lib.add_argument("--check", action="store_true", help="open the library in Blender (queued, read only) and "
                                                          "report the image paths that do not exist here")
    lib.add_argument("--relink", action="store_true", help="write a copy of the library in the cache whose images "
                                                           "point at the same files in this game or SDK install "
                                                           "(queued; the install is never changed), and report what "
                                                           "stays missing")
    lib.add_argument("--register", action="store_true", help="add the folder (the relinked copy when there is "
                                                             "one) to your Blender preferences as an asset library "
                                                             "(queued; writes your Blender config)")
    lib.add_argument("--name", default=LIBRARY_NAME, help=f"asset library name (default {LIBRARY_NAME!r})")
    lib.add_argument("--print-only", action="store_true", help="print what would be unpacked and the queue "
                                                               "commands; change nothing")


def _queue(env, argv: list[str], *, name: str, load: str, print_only: bool) -> int:
    from .. import jobqueue as Q
    if print_only:
        q = ["queue", "submit", "--name", name, "--load", load, "--timeout", str(PROBE_TIMEOUT), "--", *argv]
        print("se2mod " + (subprocess.list2cmdline(q) if env.platform.startswith("win") else shlex.join(q)))
        return 0
    store = Q.Store(env.cache_dir / "queue")
    job = store.submit(argv, name=name, load=Q.LOADS[load], timeout=PROBE_TIMEOUT)
    Q.ensure_runner(store)
    print(f"queued job {job.id}: {name}")
    code = Q.wait(store, job.id, PROBE_TIMEOUT + 120)
    if code:
        print(f"error: job {job.id} failed (exit {code}); see `se2mod queue log {job.id}`.", file=sys.stderr)
    return code


def _blender_job(env, script: Path, *job: str, factory: bool = True) -> list[str]:
    return [str(env.blender), "-b", *(["--factory-startup"] if factory else []), "--python-exit-code", "1",
            "--python", str(script), "--", *job]


def _probe(args, env, L, script, lib, blend, *, force: bool) -> tuple[int, dict | None]:
    """Run the read-only probe (or reuse one newer than the .blend unless `force`): (exit code, summary or None)."""
    out = lib / "probe.json"
    fresh = out.is_file() and blend.is_file() and out.stat().st_mtime >= blend.stat().st_mtime
    if force or not fresh:
        rc = _queue(env, _blender_job(env, script, "probe", str(blend), str(out)), name="kit library check",
                    load="medium", print_only=args.print_only)
        if rc or args.print_only:
            return rc, None
    return 0, L.summarize(json.loads(out.read_text(encoding="utf-8")), getattr(env, "game_dir", None),
                          getattr(env, "sdk_assets", None))


def _relink(args, env, L, script, lib, blend, s) -> int:
    """Write the relinked copy beside `lib` (queued Blender job), then say what stayed missing."""
    if args.print_only:
        final = L.relinked_dir(lib)
        _queue(env, _blender_job(env, script, "relink", str(blend), str(final / "relink.json"),
                                 str(final / blend.relative_to(lib)), str(final / "relink-result.json")),
               name="kit library relink", load="medium", print_only=True)
        return 0
    stage, mapping, out_blend = L.prepare_relink(lib, blend, s["found_locally"])
    try:
        result = stage / "relink-result.json"
        rc = _queue(env, _blender_job(env, script, "relink", str(blend), str(mapping), str(out_blend), str(result)),
                    name="kit library relink", load="medium", print_only=False)
        if rc:
            return rc
        r = json.loads(result.read_text(encoding="utf-8"))
        final = L.finish_relink(lib, stage)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    print(f"relinked: {final}")
    print(f"{len(r['relinked'])} of {r['images']} images now point at this game or SDK install; "
          f"{len(r['missing'])} still missing:")
    for path in r["missing"]:
        print(f"  missing: {path}")
    return 0


def _library(args, env) -> int:
    from ..env.core import require
    from ..kit import library as L
    zpath = L.find_zip(getattr(env, "sdk_dir", None))
    if args.print_only:    # say what would happen; unpack nothing
        lib = L.library_dir(zpath, env.cache_dir)
        print(f"{'library' if (lib / L.DONE).is_file() else 'would unpack ' + str(zpath) + ' into'}: {lib}")
    else:
        lib, new = L.unpack(zpath, env.cache_dir)
        print(f"{'unpacked' if new else 'library'}: {lib}")
    if not (args.check or args.relink or args.register):
        print(f"register this folder as a Blender asset library: {L.register_dir(lib)}  "
              "(or run `se2mod kit library --register`)")
        return 0
    require(env, "blender")
    script = Path(L.__file__).with_name("library_blend.py")
    blend = L.planned_blend(zpath, lib)
    rc = 0
    if args.check or args.relink:
        rc, s = _probe(args, env, L, script, lib, blend, force=args.check)
        if s is not None and args.check:
            print(f"{s['materials']} materials, {s['images']} images: {s['packed']} packed, "
                  f"{len(s['missing'])} pointing at files that are not there")
            for i in s["missing"]:
                here = s["found_locally"].get(i["filepath"])
                print(f"  missing: {i['filepath']}" + (f"\n    here: {here}" if here else ""))
            if s["missing"]:
                print(f"{len(s['found_locally'])} of the missing images exist in this game or SDK install "
                      "(as DDS or PNG); `se2mod kit library --relink` points a copy of the library at them.")
        if rc == 0 and args.relink:
            rc = _relink(args, env, L, script, lib, blend, s)
    if args.register and rc == 0:
        folder = L.relinked_dir(lib) if args.relink and args.print_only else L.register_dir(lib)
        rc = _queue(env, _blender_job(env, script, "register", str(folder), args.name, factory=False),
                    name="kit library register", load="light", print_only=args.print_only)
    return rc


@register("kit", "Kit assets: build the Rioku atlas, fetch the release zip, print an asset path; Keen's material "
                 "library", add_args=_add_args, check_game_version=False)
def run(args, env) -> int:
    try:
        if args.kit_action == "build":
            from ..kit import build
            res = build.build(env.cache_dir, texconv=None if args.no_dds else "auto", timeout=args.timeout)
            for w in res.warnings:
                print(f"warning: {w}", file=sys.stderr)
            for f in res.files:
                print(f)
            return 0
        if args.kit_action == "fetch":
            from ..kit import fetch
            print(fetch.fetch(env.cache_dir))
            return 0
        if args.kit_action == "library":
            return _library(args, env)
        print(kit.asset_path(args.name, env.cache_dir))
        return 0
    except kit.KitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
