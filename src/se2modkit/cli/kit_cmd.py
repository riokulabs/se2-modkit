"""`se2mod kit build | fetch | path NAME | library`: the kit assets (Rioku atlas, maker mark) in the user cache, and
Keen's Blender material library from the Mod SDK."""

from __future__ import annotations

import argparse
import json
import shlex
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
    lib.add_argument("--register", action="store_true", help="add the folder to your Blender preferences as an "
                                                             "asset library (queued; writes your Blender config)")
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
    if not (args.check or args.register):
        print(f"register this folder as a Blender asset library: {lib}  (or run `se2mod kit library --register`)")
        return 0
    require(env, "blender")
    script = Path(L.__file__).with_name("library_blend.py")
    rc = 0
    if args.check:
        blend = L.planned_blend(zpath, lib)
        out = lib / "probe.json"
        argv = [str(env.blender), "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script),
                "--", "probe", str(blend), str(out)]
        rc = _queue(env, argv, name="kit library check", load="medium", print_only=args.print_only)
        if rc == 0 and not args.print_only:
            s = L.summarize(json.loads(out.read_text(encoding="utf-8")), getattr(env, "game_dir", None),
                            getattr(env, "sdk_assets", None))
            print(f"{s['materials']} materials, {s['images']} images: {s['packed']} packed, "
                  f"{len(s['missing'])} pointing at files that are not there")
            for i in s["missing"]:
                here = s["found_locally"].get(i["filepath"])
                print(f"  missing: {i['filepath']}" + (f"\n    here: {here}" if here else ""))
            if s["missing"]:
                print(f"{len(s['found_locally'])} of the missing images exist in this game or SDK install "
                      "(as DDS or PNG); the library's materials show them once relinked.")
    if args.register and rc == 0:
        argv = [str(env.blender), "-b", "--python-exit-code", "1", "--python", str(script), "--", "register",
                str(lib), args.name]
        rc = _queue(env, argv, name="kit library register", load="light", print_only=args.print_only)
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
