"""Release builds: the clean, full model build a publish ships, and the stamp that proves it.

`se2mod build-models MOD --release` refuses a work tree with uncommitted changes (and files git ignores under the
models folder or `Assets/`, which would ship), a build script outside the mod's git repository and any symlink under
the models folder or `Assets/`. It then rebuilds and exports every block whatever the saved build state says
(`--force --export`). When the tree is still clean afterwards and every model file under `Assets/<mod>/` (what a
publish stages from) is a byte-identical copy of the export at the same place in its block, it writes `release.json` next to the mod's build state
(`buildstate`, never in the mod):

    {"version", "mod", "head", "modkit", "script", "keys", "blocks", "time",
     "files": {mod-relative path: sha256}, "shipped": {mod-relative path: sha256}}

`keys` is one hash over every built block's input key, `files` every file under the mod's models folder, `shipped`
every model file under `Assets/<mod>/`, each accounted for at its place in the block: geometry by the export, a
`.model` by the staged package. The publish preflight (`check`) accepts a mod only when
that stamp is the mod's, its commit is the mod repo's HEAD, the tree is clean, the build script it names is there,
nothing is a symlink, and the model files and their shipped copies are still exactly the stamped ones. Incremental
builds stay the default everywhere else; only what is published must come from a release build.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import buildstate

__all__ = ["STAMP", "ReleaseError", "check", "dirty", "few", "head", "keys_digest", "model_files", "publish_check",
           "read_stamp", "rel", "release_command", "script_problem", "shipped_copies", "stamp_path", "symlinks",
           "toplevel", "write_stamp"]

STAMP = "release.json"
STAMP_VERSION = 2


class ReleaseError(Exception):
    """A release build or check that cannot go on; the message is one sentence."""


def release_command(mod: str) -> str:
    return f"se2mod build-models {mod} --release"


def stamp_path(report: Path, mod_name: str, environ: Mapping[str, str] | None = None) -> Path:
    """Where the mod's release stamp lives: in its build state folder, next to the block fragments."""
    return buildstate.mod_state_dir(Path(report), mod_name, environ) / STAMP


def _git(root: Path, *args: str) -> str:
    import subprocess
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReleaseError(f"git could not be run in {root} ({e}); a release build needs the mod's git repository.") \
            from None
    if p.returncode != 0:
        raise ReleaseError(f"{root} is not in a git repository, so a release build cannot tell what it was built from; "
                           "commit the mod to git first.")
    return p.stdout


def head(root: Path) -> str:
    """The commit the mod repo's HEAD points at."""
    return _git(root, "rev-parse", "HEAD").strip()


def dirty(root: Path, ship: Iterable[Path] = ()) -> list[str]:
    """The uncommitted changes under the mod folder (`git status --porcelain`, untracked files included), and the
    files git ignores under the `ship` folders (the models folder, `Assets/`): those would ship all the same."""
    out = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", ".")
    found = [line[3:] for line in out.splitlines() if line.strip()]
    dirs = [Path(d) for d in ship if Path(d).is_dir()]
    if dirs:
        out = _git(root, "status", "--porcelain=v1", "--ignored=matching", "--untracked-files=all", "--",
                   *(str(d) for d in dirs))
        found += [line[3:] for line in out.splitlines() if line.startswith("!! ")]
    return found


def toplevel(root: Path) -> Path:
    """The top folder of the mod's git repository."""
    return Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()


def script_problem(script: Path, root: Path) -> str | None:
    """Why `script` cannot build a release (outside the mod's git repository, or not committed as it is), or None."""
    script = Path(script).resolve()
    top = toplevel(root)
    if not (script == top or top in script.parents):
        return (f"the build script {script} is outside the mod's git repository ({top}), so a release build cannot "
                "tell what built it; keep the build script in the repository.")
    if not script.is_file():
        return f"the build script {script} does not exist."
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", str(script)).strip():
        return f"the build script {rel(script, root)} has uncommitted changes; commit it first."
    return None


def rel(p: Path, root: Path) -> str:
    """`p` relative to the mod folder ("/"-separated), else as it is."""
    return Path(os.path.relpath(Path(p).resolve(), Path(root).resolve())).as_posix()


def symlinks(*dirs: Path) -> list[str]:
    """Every symlink (to a file or a folder) under `dirs`, which are walked without following links."""
    out = []
    for d in dirs:
        d = Path(d)
        if d.is_symlink():
            out.append(str(d))
            continue
        if not d.is_dir():
            continue
        for dp, ds, fs in os.walk(d, followlinks=False):
            out += [os.path.join(dp, n) for n in (*ds, *fs) if os.path.islink(os.path.join(dp, n))]
    return sorted(out)


def _files(d: Path, root: Path) -> dict[str, str]:
    out = {}
    if not Path(d).is_dir():
        return out
    for dp, ds, fs in os.walk(d, followlinks=False):
        ds[:] = sorted(x for x in ds if x != "__pycache__" and not os.path.islink(os.path.join(dp, x)))
        for n in sorted(fs):
            p = Path(dp, n)
            if not p.is_symlink():
                out[rel(p, root)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def model_files(models: Path, root: Path) -> dict[str, str]:
    """{path relative to the mod folder: sha256} of every file under the models folder (what the export wrote;
    symlinks are never followed, see `symlinks`)."""
    return _files(models, root)


# The model files of the shipped package: the export's geometry, copied byte for byte, and the .model assets the
# staging writes next to them.
MODEL_SUFFIXES = (".fbx", ".gltf", ".bin", ".model")


def shipped_copies(models: Path, assets: Path, root: Path, mod: str, staged: Path | None = None,
                   require_staged: bool = False) -> tuple[dict[str, str], list[str]]:
    """({path: sha256} of every model file under `assets`/<mod>, [the ones not accounted for]).

    The shipped package holds a block's exported files at `Assets/<mod>/<block>/Models/<path inside the block's
    export folder>` (`se2modkit.stage.stage_models`): each such copy must exist in the export at that very place and
    be byte-identical to it. A `.model` asset (written by the staging, not the export) must sit in the Models folder
    of an exported block and be byte-identical to the staged package's file at the same place (`staged`, the
    mod's `paths.staged`), when that package is there; with `require_staged` a missing staged copy does not pass
    either. Anything else (an older copy, the files of a renamed or removed block, a copy swapped between two blocks'
    same-named files) is not accounted for."""
    models = Path(models)
    export = _files(models, models)
    blocks = {k.split("/", 1)[0] for k in export}
    base = Path(assets) / mod
    stage = Path(staged) / mod if staged is not None else None
    if stage is not None and not stage.is_dir() and not require_staged:
        stage = None
    shipped, bad = {}, []
    for path, digest in _files(base, base).items():
        if not path.lower().endswith(MODEL_SUFFIXES):
            continue
        shown = rel(base / path, root)
        shipped[shown] = digest
        parts = path.split("/")
        if len(parts) < 3 or parts[1] != "Models" or parts[0] not in blocks:
            bad.append(shown)
        elif path.lower().endswith(".model"):
            if stage is not None:
                src = stage / path
                if not src.is_file() or src.is_symlink() or hashlib.sha256(src.read_bytes()).hexdigest() != digest:
                    bad.append(shown)
        elif export.get(f"{parts[0]}/{'/'.join(parts[2:])}") != digest:
            bad.append(shown)
    return shipped, sorted(bad)


def keys_digest(keys: Mapping[str, str]) -> str:
    """One hash over every block's input key."""
    return hashlib.sha256("".join(f"{b}\0{k}\n" for b, k in sorted(keys.items())).encode("utf-8")).hexdigest()


def write_stamp(path: Path, *, mod: str, head_sha: str, blocks: Iterable[str], keys: Mapping[str, str],
                files: Mapping[str, str], shipped: Mapping[str, str], script: str, now: str | None = None) -> dict:
    from . import __version__
    data = {"version": STAMP_VERSION, "mod": mod, "head": head_sha, "modkit": __version__, "script": script,
            "keys": keys_digest(keys), "blocks": sorted(blocks),
            "time": now or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "files": dict(files),
            "shipped": dict(shipped)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return data


def read_stamp(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == STAMP_VERSION else None


def few(items: Iterable[str]) -> str:
    """The first of `items` (sorted) and how many more: "a.txt and 3 more"."""
    items = sorted(items)
    if not items:
        return "none"
    return items[0] + (f" and {len(items) - 1} more" if len(items) > 1 else "")


def check(*, mod: str, root: Path, report: Path, models: Path, assets: Path | None = None,
          staged: Path | None = None, environ: Mapping[str, str] | None = None) -> str | None:
    """Why the mod's models are not a release build of its current commit (one sentence naming the command to run),
    or None when they are: the mod's own stamp exists, its commit is HEAD, the tree is clean (ignored files that
    would ship included), its build script is there, nothing under the models folder or `Assets/` is a symlink, and
    the model files and their shipped copies are the stamped ones."""
    cmd = release_command(mod)
    assets = Path(root) / "Assets" if assets is None else Path(assets)
    try:
        stamp = read_stamp(stamp_path(report, mod, environ))
        if stamp is None:
            return f"{mod} has no release build; run `{cmd}` on a clean, committed tree before publishing."
        now = head(root)
        changes = dirty(root, (models, assets))
    except ReleaseError as e:
        return f"{str(e).rstrip('.')}; then run `{cmd}`."
    links = symlinks(models, assets)
    if links:
        return (f"{mod} has a symlink where models ship from ({few(links)}); replace it with the real files, commit, "
                f"and run `{cmd}` again.")
    if stamp.get("mod") != mod:
        return f"The release stamp found for {mod} was made for {stamp.get('mod')!r}; run `{cmd}`."
    if stamp.get("head") != now:
        return (f"{mod}'s release build is from commit {str(stamp.get('head'))[:12]}, not HEAD {now[:12]}; run "
                f"`{cmd}` again.")
    if changes:
        return (f"{mod} has uncommitted changes ({few(changes)}), so its release build does not describe what "
                f"would ship; commit them and run `{cmd}` again.")
    script = stamp.get("script")
    if not isinstance(script, str) or not script or not (Path(root) / script).is_file():
        return (f"{mod}'s release build names no build script that is still there ({script or 'none'}); run "
                f"`{cmd}` again.")
    files = model_files(models, root)
    stamped = stamp.get("files") if isinstance(stamp.get("files"), dict) else {}
    differ = {p for p in set(files) | set(stamped) if files.get(p) != stamped.get(p)}
    if differ:
        return f"{mod}'s model files changed since its release build ({few(differ)}); run `{cmd}` again."
    shipped, bad = shipped_copies(models, assets, root, mod, staged)
    stamped = stamp.get("shipped") if isinstance(stamp.get("shipped"), dict) else {}
    differ = set(bad) | {p for p in set(shipped) | set(stamped) if shipped.get(p) != stamped.get(p)}
    if differ:
        return (f"{mod} ships model files in Assets/ that are not its release build's export at that place "
                f"({few(differ)}); "
                f"install the export into Assets/ and remove what it no longer has, commit, and run `{cmd}` again.")
    return None


def publish_check(folder: Path, environ: Mapping[str, str] | None = None) -> tuple[str | None, str | None]:
    """(why a publish from `folder` must be refused, a warning to show) for the release-build check. Only a mod that
    builds through se2modkit (a `[tool.se2modkit]` project with a model build script or model files, whatever
    its build script is named) needs a release build; a mod
    with a build script of its own (no project) does not use the incremental build state, so it only gets a warning
    that its models are not verified. A mod with no model build at all gets neither."""
    from .project import ProjectError, load_project
    folder = Path(folder).resolve()
    try:
        cfg = load_project(folder).containing(folder)
    except ProjectError as e:
        if not str(e).startswith("No pyproject.toml"):
            return (f"{folder.name}'s [tool.se2modkit] project cannot be read ({str(e).rstrip('.')}), so it has no "
                    "release build; fix it and run `se2mod build-models <mod> --release`."), None
        cfg = None
    if cfg is None:
        if sorted((folder / "Source" / "Model").glob("build_*.py")):
            return None, ("models not verified by a release build; this mod doesn't build through modkit (no "
                          "[tool.se2modkit] project).")
        return None, None
    has_models = Path(cfg.paths.models).is_dir() and any(p.is_file() or p.is_symlink()
                                                         for p in Path(cfg.paths.models).rglob("*"))
    if not has_models and not sorted(Path(cfg.paths.source_model).glob("build_*.py")):
        return None, None          # a project with no model build and no models: nothing to verify
    return check(mod=cfg.name, root=cfg.root, report=cfg.paths.report, models=cfg.paths.models,
                 assets=cfg.paths.assets, staged=cfg.paths.staged, environ=environ), None
