"""`se2mod tex new | check | view`: author a mod's own textures the way the Mod SDK packs them.

  new NAME --like MATERIAL [--mod MOD]   flat placeholder PNG maps plus .comptex files shaped like a vanilla material
                                         (its packing preset GUIDs only; no vanilla pixels)
  check [MOD]                            validate the mod's texture sets and refuse copied game textures (exit 1)
  view TARGET [--set DIR]                a local three.js page for a .glb or a texture set, served on 127.0.0.1
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from ..validate import vanilla_textures
from . import register
from .mods_cmd import _resolve


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="tex_action", metavar="ACTION", required=True)
    n = sub.add_parser("new", help="start a texture set: flat placeholder PNG maps and .comptex files shaped like a "
                                   "vanilla material (reuses its packing preset GUIDs; copies no vanilla image)")
    n.add_argument("name", help="set name (letters, digits, _); files are <name>_<map>.png and <name>_<pack>.comptex")
    n.add_argument("--like", required=True, metavar="MATERIAL",
                   help="vanilla block material to take the layout from, e.g. PaintedMetalColorable")
    n.add_argument("--mod", default=".", help="mod name or folder (default: the current folder)")
    n.add_argument("--size", type=int, default=1024, help="map size in pixels, a power of two (default 1024)")
    n.add_argument("--folder", default="Textures", help="folder under the mod's Assets (default Textures)")
    c = sub.add_parser("check", help="validate the mod's texture sets: power-of-two sizes, one size per set, map "
                                     "channels, .comptex references; fails on any copy of a game texture")
    c.add_argument("mod", nargs="?", default=".", help="mod name or folder (default: the current folder)")
    c.add_argument("--json", action="store_true", help="print the report as JSON")
    v = sub.add_parser("view", help="look at a .glb model or a texture set in a local three.js page (127.0.0.1 "
                                    "only) with adjustable light; refuses game textures")
    v.add_argument("target", help="a .glb model, or a texture set folder (<set>_<map>.png files)")
    v.add_argument("--set", dest="tex_set", metavar="DIR", help="texture set folder to put on the model's meshes")
    v.add_argument("--out", metavar="DIR", help="folder to write the page into (default: a new temporary folder)")
    v.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    v.add_argument("--no-serve", action="store_true", help="write the page and print its folder; serve nothing")


def _sdk_index(env):
    """The SDK block-material index, or None without the Mod SDK."""
    assets = getattr(env, "sdk_assets", None)
    if assets is None or not (Path(assets) / "BlockMaterials").is_dir():
        return None
    from ..preview import mats, sdkmat
    bm = Path(assets) / "BlockMaterials"
    return sdkmat.index(bm, mats._presets_dir(str(bm)))


def _new(args, env) -> int:
    from ..tex import scaffold
    mod = _resolve(env, args.mod)
    ix = _sdk_index(env)
    if ix is None:
        print("error: Mod SDK not found; `tex new` reads the vanilla material's layout from it (set --sdk).",
              file=sys.stderr)
        return 1
    try:
        dest, slots, written = scaffold.new_set(mod, args.name, args.like, ix, size=args.size, folder=args.folder)
    except scaffold.SetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"texture set {args.name} ({args.size}x{args.size}, laid out like {args.like}): "
          f"{dest.relative_to(mod).as_posix()}/")
    for f in written:
        if f.suffix != ".meta":
            print(f"  {f.relative_to(mod).as_posix()}")
    print("Point your material definition's texture slots at these .comptex GUIDs:")
    for slot, guid in slots.items():
        print(f'  "{slot}": "{{G}}{guid}"')
    print("Paint over the placeholder PNGs (normal maps OpenGL style, green up); the Mod SDK packs them at build.")
    return 0


def _check(args, env) -> int:
    from ..tex import check
    mod = _resolve(env, args.mod)
    roots = vanilla_textures.vanilla_roots(env)
    ix = _sdk_index(env)
    rep = check.check_mod(mod, ix, roots, env.cache_dir / vanilla_textures.CACHE_NAME)
    if args.json:
        print(check.to_json(rep))
        return 1 if rep.errors else 0
    if not roots:
        print(vanilla_textures.NO_ROOTS, file=sys.stderr)
    if ix is None:
        print("warning: Mod SDK not found; packing presets and SDK references were not checked", file=sys.stderr)
    if not rep.pixels_checked:
        print("warning: Pillow not installed; normal-map pixels were not checked "
              "(pip install 'se2-modkit[preview]')", file=sys.stderr)
    for p in rep.problems:
        print(p)
    n_err, n_warn = len(rep.errors), len(rep.problems) - len(rep.errors)
    print(f"{len(rep.sets)} texture set(s): {n_err} error(s), {n_warn} warning(s)")
    return 1 if n_err else 0


def _view(args, env) -> int:
    from ..tex import view
    target = Path(args.target).expanduser()
    model, maps = None, {}
    if target.is_dir():
        maps = view.maps_in(target)
    elif target.suffix.lower() == ".glb":
        model = target
    else:
        print(f"error: {target} is neither a .glb file nor a texture set folder.", file=sys.stderr)
        return 1
    if args.tex_set:
        maps = view.maps_in(Path(args.tex_set).expanduser())
    out = Path(args.out).expanduser() if args.out else Path(tempfile.mkdtemp(prefix="se2tex-view-"))
    forbidden = [d for d in (getattr(env, "game_dir", None), getattr(env, "sdk_dir", None)) if d is not None]
    try:
        page = view.prepare(out, model, maps, vanilla_roots=vanilla_textures.vanilla_roots(env), forbidden=forbidden)
    except view.ViewRefused as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(view.LOCAL_ONLY)
    print(f"page: {page}")
    if args.no_serve:
        return 0
    view.serve(out, args.port)
    return 0


@register("tex", "Author a mod's own textures: `new` set from a vanilla layout, `check` sets, `view` locally",
          add_args=_add_args, check_game_version=False)
def cmd_tex(args, env) -> int:
    return {"new": _new, "check": _check, "view": _view}[args.tex_action](args, env)
