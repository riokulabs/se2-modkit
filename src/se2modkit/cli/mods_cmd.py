"""`se2mod new | override | show | copy | list`: scaffold mods and work with vanilla definitions.

Bundle stamps written into new files come from the installed Mod SDK (`bundle_stamps`), never from a constant.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
import uuid
from pathlib import Path

from ..compat import load_compat
from .. import __version__, scaffold
from ..defs.assetio import own_guid
from ..defs.envelope import envelope, write_json
from ..env.core import EnvError, SE2Env, require
from ..env.versions import SYSTEM_RUNTIME_BUNDLE, bundle_stamps
from ..project import Project, ProjectError, load_project
from ..scaffold.templates import GITATTRIBUTES, GITIGNORE
from ..style import profile as style_profile
from . import modarg, register
from .modarg import project_file, workspace_mods_dirs

__all__ = [
    "GITATTRIBUTES", "GITIGNORE", "GUID_RE", "ModError", "copy_defs", "find_vanilla", "own_guid", "project_file",
    "resolve_mod", "vanilla_content", "workspace_mods_dirs",
]

GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_NAME_RE = re.compile(r"[A-Za-z0-9 _.-]+")

_DIFF = "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff"


class ModError(ProjectError):
    """A mod command cannot proceed; reported as one plain sentence."""


# --- mod lookup -------------------------------------------------------------------------------------------------


def _project_here() -> tuple[Project | None, str | None]:
    try:
        return load_project(Path.cwd()), None
    except ProjectError as e:
        return None, str(e)


def resolve_mod(env: SE2Env, project: Project | None, selector: str) -> Path:
    """A mod directory: by name in `project`, else `selector` as a mod directory path, else by name in a
    `workspace_mods` folder."""
    if project is not None and selector in project.mods:
        return project.mods[selector].root
    as_path = Path(selector).expanduser()
    if as_path.is_dir() and project_file(as_path) is not None:
        return as_path
    dirs = workspace_mods_dirs(env)
    for base in dirs:
        d = base / selector
        if project_file(d) is not None:
            return d
    where = []
    if project is not None:
        where.append(f"not a mod of the project at {project.root} ({', '.join(sorted(project.mods)) or 'none'})")
    where.append("not a mod directory")
    where.append(f"not in {', '.join(map(str, dirs))}" if dirs
                 else "no workspace_mods folder is configured (set config workspace_mods or SE2_WORKSPACE)")
    raise ModError(f"Mod '{selector}' not found: {'; '.join(where)}.")


def _resolve(env: SE2Env, selector: str) -> Path:
    """The mod directory MOD names (a name or a path), through the one resolver every command shares."""
    return modarg.resolve(env, selector).root


# --- vanilla definitions ----------------------------------------------------------------------------------------


def vanilla_content(env: SE2Env) -> Path:
    require(env, "game_dir")
    content = env.game_dir / "GameData" / "Vanilla" / "Content"
    if not content.is_dir():
        raise EnvError(f"No vanilla Content folder at {content}; verify the game files in Steam.")
    return content


def find_vanilla(guid: str, env: SE2Env) -> tuple[Path, dict]:
    """`(path, data)` of the compiled vanilla .def that declares `guid` (pure-Python scan of `Content/**/*.def`)."""
    guid = guid.lower()
    content = vanilla_content(env)
    needle = guid.encode()
    for p in sorted(content.rglob("*.def")):
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if needle not in raw.lower():
            continue
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except ValueError:
            continue
        if isinstance(data, dict) and str((data.get("$Value") or {}).get("Guid", "")).lower() == guid:
            return p, data
    raise ModError(f"GUID {guid} is not declared by any vanilla .def under {content}.")


# --- copy -------------------------------------------------------------------------------------------------------


def copy_defs(mod: Path, sdk_assets: Path, args) -> None:
    """Clone the SDK source defs in `sdk_assets/args.src` into `mod/Assets/<dest or to>` under new GUIDs, rewiring
    references between them; the GUID map lives in `mod/.clonemap.json` (flat, or keyed per clone with map_key).

    `args` needs `src`, `from_`, `to`, `dest`, `recursive`, `force`; `map_key` and `also` are optional (callers pass
    a bare `argparse.Namespace`)."""
    mod, sdk_assets = Path(mod), Path(sdk_assets)
    src = (sdk_assets / args.src).resolve()
    if not src.is_dir():
        raise ModError(f"Not a folder: {src}.")
    pattern = "**/*" if args.recursive else "*"
    files = sorted(p for p in src.glob(pattern) if p.suffix in (".def", ".partialdef") and p.is_file())
    if not files:
        raise ModError(f"No .def/.partialdef files in {src}.")
    map_file = mod / ".clonemap.json"
    raw = json.loads(map_file.read_text(encoding="utf-8")) if map_file.exists() else {}
    # Keyed format ({"$keyed": true, "maps": {KEY: {"copies": [...], "guids": {...}}}}): one GUID map per clone,
    # so the same vanilla folder can be cloned several times into one mod (each clone gets its own GUIDs and
    # copies under the same key still rewire each other). The flat format (vanilla GUID -> new GUID) is the default.
    keyed = bool(raw.get("$keyed"))
    entry = None
    map_key, also = getattr(args, "map_key", None), getattr(args, "also", None)
    if map_key:
        if raw and not keyed:
            raise ModError(f"{map_file} is a flat map; --map-key needs a keyed map "
                           "(start one in a mod without a flat map).")
        raw["$keyed"] = True
        entry = raw.setdefault("maps", {}).setdefault(map_key, {"copies": [], "guids": {}})
        guid_map = entry["guids"]
    else:
        if keyed:
            raise ModError(f"{map_file} is keyed per clone; pass --map-key KEY.")
        guid_map = raw
    renames = [(args.from_, args.to)]
    for pair in also or []:
        a, sep, b = pair.partition("=")
        if not sep or not a:
            raise ModError(f"--also expects FROM=TO, got {pair!r}.")
        renames.append((a, b))
    root = sdk_assets.resolve()
    loaded = []
    for f in files:
        text = f.read_text(encoding="utf-8-sig")
        g = own_guid(json.loads(text))
        if not g:
            print(f"  skip (Override, nothing to clone): {f.relative_to(root)}")
            continue
        guid_map.setdefault(g.lower(), str(uuid.uuid4()))
        loaded.append((f, text))
    dest_root = mod / "Assets" / (args.dest or args.to)
    for f, text in loaded:
        # Rewire every GUID we have cloned (in this or an earlier `copy` into the same mod).
        new_text = GUID_RE.sub(lambda m: guid_map.get(m.group(0).lower(), m.group(0)), text)
        new_name = f.name
        for a, b in renames:
            new_name = new_name.replace(a, b)
        out = dest_root / f.relative_to(src).with_name(new_name)
        if out.exists() and not args.force:
            raise ModError(f"{out} exists (use --force to overwrite).")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(new_text, encoding="utf-8", newline="\n")
        print(f"  {f.relative_to(root)}  ->  {out.relative_to(mod)}")
    if entry is not None:
        rec = {"src": args.src, "from": args.from_, "to": args.to, "also": list(also or []),
               "dest": dest_root.relative_to(mod / "Assets").as_posix(), "recursive": bool(args.recursive),
               "definitions": len(loaded)}
        key = (rec["src"], rec["from"], rec["to"], rec["dest"])
        # A re-run (e.g. --force) replaces its earlier record instead of appending a duplicate.
        entry["copies"] = [c for c in entry["copies"]
                           if (c.get("src"), c.get("from"), c.get("to"), c.get("dest")) != key]
        entry["copies"].append(rec)
    map_file.write_text(json.dumps(raw if entry is not None else guid_map, indent=2) + "\n", encoding="utf-8",
                        newline="\n")
    print(f"{len(loaded)} definitions cloned; GUID map ({len(guid_map)} entries) in {map_file.relative_to(mod)}")


# --- commands ---------------------------------------------------------------------------------------------------


def _new_args(p) -> None:
    p.add_argument("name", help="mod name (letters, digits, space, _ . -)")
    p.add_argument("--prefix", help="block/file prefix, e.g. MM_ (default: the name's initials); also names the "
                                    "model package")
    p.add_argument("--profile", default="vanilla", help="style profile (default vanilla)")
    p.add_argument("--library", action="store_true",
                   help="a library mod: data-only placeholder plus Scripts/, no model skeleton")
    p.add_argument("--into", metavar="REPO", type=Path,
                   help="add the mod as mods/NAME to this multi-mod repo instead of creating a repo")
    p.add_argument("--convert", action="store_true",
                   help="with --into a single-mod repo: move its [tool.se2modkit] table under "
                        "[tool.se2modkit.mods.<its mod>] first")
    p.add_argument("--org-sync", metavar="PATH", type=Path,
                   help="org template repo: render its managed files (CI, CONTRIBUTING, issue forms) into the new "
                        "repo with its tools/sync-github-structure.py (dry run, then apply)")
    p.add_argument("--org-name", metavar="NAME",
                   help="the new repo's repos/<NAME>.toml in the org template repo (default: the name, hyphenated)")


def _vrage_stamps(env: SE2Env) -> dict[str, str]:
    try:
        return bundle_stamps(env)[0]
    except EnvError as e:
        newest = load_compat().game_versions_tested[-1]
        print(f"warning: {e} Stamping VRage {newest} (the newest tested build).", file=sys.stderr)
        return {"System.Runtime": SYSTEM_RUNTIME_BUNDLE, "VRage": newest}


@register("new", "Scaffold a mod repo (Mod HUB files, pyproject, model skeleton, tests), or add a mod with --into",
          add_args=_new_args, check_game_version=False)
def cmd_new(args, env: SE2Env) -> int:
    name = args.name
    if not _NAME_RE.fullmatch(name) or not name.strip(" ."):
        raise ModError("Use letters, digits, space, _ . - only in a mod name.")
    if name != name.strip(" ") or name.endswith("."):
        raise ModError("A mod name cannot start or end with a space or end with a dot (Windows folder names).")
    # (the same rule as project.mod_name_error, which load_project applies to every configured mod)
    prefix = scaffold.normalize_prefix(args.prefix) if args.prefix else scaffold.default_prefix(name)
    known = style_profile.available()
    if args.profile not in known:
        raise ModError(f"Unknown style profile '{args.profile}'; known profiles: {', '.join(known)}.")
    spec = scaffold.ModSpec(name=name, prefix=prefix, profile=args.profile, library=args.library)
    if args.into is not None:
        if args.org_sync is not None:
            raise ModError("--org-sync applies to a new repo; the repo given to --into already has its org files.")
        d = scaffold.add_into(args.into, spec, _vrage_stamps(env), convert=args.convert)
        print(f"added {d}\n  [tool.se2modkit.mods.{name}] path = \"mods/{name}\", prefix {prefix}\n"
              f"  next: se2mod test {shlex.quote(str(d))}")
        return 0
    if args.convert:
        raise ModError("--convert only applies with --into REPO.")
    dirs = workspace_mods_dirs(env)
    d = (dirs[0] if dirs else Path.cwd()) / name
    if d.exists():
        raise ModError(f"{d} already exists.")
    git = shutil.which("git")
    if git is None:
        raise ModError("git not found on PATH; install git (every mod repo is a git repository).")
    slug = scaffold.check_org_name(args.org_name) if args.org_name else scaffold.dist_name(name)
    plan = scaffold.org_check(args.org_sync, slug, d, spec) if args.org_sync is not None else None
    project_id = scaffold.new_repo(d, spec, _vrage_stamps(env), git=git,
                                   gitattributes=plan is None or plan.gitattributes)
    print(f"created {d}\n  project id {project_id}, prefix {prefix}")
    if not scaffold.is_release(__version__):
        print(f"warning: se2-modkit {__version__} is not a release, so the pin's tag v{__version__} does not exist "
              "yet; bump the pin in pyproject.toml to a release tag before CI can install it.", file=sys.stderr)
    if plan is not None:
        scaffold.org_sync(args.org_sync, slug, plan.script)
    else:
        print(f"  org files (CI, CONTRIBUTING, issue forms): not rendered; rerun with --org-sync PATH, or run the org "
              f"template repo's tools/sync-github-structure.py {slug} --apply")
    print(f"  next: se2mod override {shlex.quote(name)} <vanilla-guid>")
    return 0


def _show_args(p) -> None:
    p.add_argument("guid", help="vanilla definition GUID")


@register("show", "Print the compiled vanilla definition that declares a GUID", add_args=_show_args)
def cmd_show(args, env: SE2Env) -> int:
    path, data = find_vanilla(args.guid, env)
    print(f"# {path.relative_to(vanilla_content(env))}")
    print(json.dumps(data, indent=2))
    return 0


def _override_args(p) -> None:
    p.add_argument("mod", help="mod name or mod directory")
    p.add_argument("guid", help="vanilla definition GUID to override")
    p.add_argument("--name", help="partialdef file name (default: 'Override of <vanilla file>')")


@register("override", "Add an Override partialdef for a vanilla definition GUID", add_args=_override_args)
def cmd_override(args, env: SE2Env) -> int:
    d = _resolve(env, args.mod)
    game_bundles = bundle_stamps(env)[1]  # before the vanilla scan: a missing SDK fails fast
    path, data = find_vanilla(args.guid, env)
    obj_type = data["$Type"]  # e.g. Game2:Keen....InventoryDefinitionObjectBuilder
    def_type = re.sub(r"ObjectBuilder$", "", obj_type)
    name = args.name or f"Override of {path.stem}"
    out = d / "Assets" / f"{name}.partialdef"
    if out.exists():
        raise ModError(f"{out} already exists.")
    write_json(out, envelope(_DIFF, {
        "BaseDefinition": data["$Value"]["Guid"],
        "PartialDefinitionKind": "Override",
        "Manipulator": {},
        "DefinitionType": def_type,
        "PriorityOverride": False,
    }, game_bundles))
    print(f"wrote {out.relative_to(d.parent)}")
    print("vanilla values (copy the fields you want to change into \"Manipulator\"):")
    print(json.dumps({k: v for k, v in data["$Value"].items() if k != "Guid"}, indent=2))
    return 0


def _copy_args(p) -> None:
    p.add_argument("mod", help="mod name or mod directory")
    p.add_argument("src", help="folder under the SDK's Vanilla/Assets")
    p.add_argument("--from", dest="from_", required=True, help="text to replace in file names, e.g. Welder400")
    p.add_argument("--to", required=True, help="replacement, e.g. MyWelder")
    p.add_argument("--dest", help="folder under the mod's Assets (default: --to)")
    p.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    p.add_argument("--also", action="append", metavar="FROM=TO",
                   help="extra file-name replacement applied after --from/--to (repeatable), for files in the "
                        "same folder with another prefix (e.g. door subparts)")
    p.add_argument("--map-key", help="keep this clone's GUID map under KEY in a keyed .clonemap.json, so the same "
                                     "vanilla folder can be cloned more than once into one mod")
    p.add_argument("--force", action="store_true", help="overwrite files that already exist")


@register("copy", "Clone vanilla source defs (SDK Assets/SRC) under new GUIDs, rewiring references between them",
          add_args=_copy_args, check_game_version=False)
def cmd_copy(args, env: SE2Env) -> int:
    mod = _resolve(env, args.mod)
    require(env, "sdk_assets")
    copy_defs(mod, env.sdk_assets, args)
    return 0


def _counts(d: Path) -> str:
    n_src = len(list((d / "Assets").rglob("*.partialdef")))
    n_out = len(list((d / "Content").rglob("*.def")))
    return f"{n_src} partialdef(s), {n_out} compiled def(s)"


@register("list", "List the mods of the project here and of the workspace_mods folders", check_game_version=False)
def cmd_list(args, env: SE2Env) -> int:
    project, why = _project_here()
    rows: list[tuple[str, Path]] = []
    if project is not None:
        rows += [(name, m.root) for name, m in sorted(project.mods.items())]
    seen = {r.resolve() for _, r in rows}
    for base in workspace_mods_dirs(env):
        for d in sorted({p.parent for p in base.glob("*/*.vrgproj")}):
            if d.resolve() not in seen:
                seen.add(d.resolve())
                rows.append((d.name, d))
    if not rows:
        reason = (why or "the project has no mods").rstrip(".")
        print(f"no mods found ({reason}; set config workspace_mods or SE2_WORKSPACE "
              "to list a mods folder)", file=sys.stderr)
        return 0
    for name, d in rows:
        print(f"{name:30} {_counts(d)}")
    return 0
