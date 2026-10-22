"""`se2mod new`: a complete single-mod repo, a mod added into a multi-mod repo, or a library mod.

A single-mod repo is the 1-mod case of the multi-mod layout: the mod sits at the repo root and `[tool.se2modkit]`
names it. A multi-mod repo declares each mod in `[tool.se2modkit.mods.<name>]` with `path = "mods/<name>"`.
"""

from __future__ import annotations

import fnmatch
import keyword
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..defs.envelope import envelope, write_json
from ..project import ProjectError, load_project
from . import templates

__all__ = ["ModSpec", "OrgPlan", "ScaffoldError", "add_into", "check_org_name", "convert_single", "default_prefix",
           "dist_name", "is_release", "new_repo", "normalize_prefix", "org_check", "org_sync", "package_name",
           "write_hub_files"]

VANILLA_PROJECT = "56dacb76-e25e-468a-9432-21964a9a0569"  # the game's own Vanilla project, every mod depends on it
_META = "VRage:Keen.VRage.ContentPipeline.Metafiles.MetaData"
_PREFIX_RE = re.compile(r"[A-Z][A-Z0-9]{1,7}_")
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_HEADER = re.compile(r"^\s*\[\s*tool\s*\.\s*se2modkit\s*(?:\.\s*(?P<sub>.+?))?\s*\]\s*(?:#.*)?$")
_MOD_NAME_LINE = re.compile(r"^\s*mod_name\s*=")
_RELEASE = re.compile(r"\d+(\.\d+)*")
_ORG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ScaffoldError(ProjectError):
    """The scaffold cannot proceed; reported as one plain sentence."""


@dataclass(frozen=True)
class ModSpec:
    name: str
    prefix: str
    profile: str = "vanilla"
    library: bool = False

    @property
    def pkg(self) -> str:
        return package_name(self.prefix)


# --- names --------------------------------------------------------------------------------------------------------


def _words(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def default_prefix(name: str) -> str:
    """Initials of the name's words ("Space Tug" -> "ST_", "MyMod" -> "MM_"), else its first two letters."""
    words = [w for w in _words(name) if w[0].isalpha()] or _words(name)
    letters = "".join(w[0] for w in words).upper() if len(words) > 1 else (words[0][:2].upper() if words else "MD")
    if not letters[0].isalpha():
        letters = "M" + letters
    if len(letters) < 2:
        letters += "X"
    prefix = letters[:8] + "_"
    if keyword.iskeyword(prefix[:-1].lower()):
        prefix = letters[:7] + "X_"
    return prefix


def normalize_prefix(prefix: str) -> str:
    p = prefix.strip().upper()
    if not p.endswith("_"):
        p += "_"
    if not _PREFIX_RE.fullmatch(p) or keyword.iskeyword(p[:-1].lower()):
        raise ScaffoldError(f"Bad prefix '{prefix}': use 2-8 capital letters or digits starting with a letter, then "
                            "an underscore (e.g. ST_), and not a Python keyword.")
    return p


def package_name(prefix: str) -> str:
    """The model package for a prefix: "ST_" -> "st"."""
    return prefix.rstrip("_").lower()


def dist_name(name: str) -> str:
    """A lowercase, hyphenated name for pyproject and the org repo config: "My Mod" -> "my-mod"."""
    return "-".join(w.lower() for w in re.split(r"[^A-Za-z0-9]+", name) if w) or "mod"


def is_release(version: str) -> bool:
    """A plain release version (a `v<version>` tag can exist), not a dev or local build."""
    return bool(_RELEASE.fullmatch(version))


def check_org_name(name: str) -> str:
    if not _ORG_NAME.fullmatch(name) or ".." in name:
        raise ScaffoldError(f"Bad --org-name '{name}': use letters, digits, . _ - only (no path separators or '..').")
    return name


def _toml_key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- files --------------------------------------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def write_hub_files(d: Path, name: str, bundles: dict[str, str]) -> str:
    """The files the Mod HUB writes for a new mod (`<name>.vrgproj`, metas, ModHUB metadata); returns the project id."""
    project_id = str(uuid.uuid4())
    write_json(d / f"{name}.vrgproj", envelope("VRage:Keen.VRage.Core.Project.VRageProjectData", {
        "Name": name, "Version": None, "Type": "Mod",
        "ProjectID": {"Guid": project_id, "ModId": None, "ServiceId": ""},
        "ModSetup": None,
        "ProjectDependencies": [{"Guid": VANILLA_PROJECT, "ModId": None, "ServiceId": ""}],
        "ProjectDirectories": [], "UsedBuildStream": "", "DefinitionSetNames": ["World"],
        "GameExecutable": "SpaceEngineers2.exe", "GameExecutableSearchPaths": [],
        "ReferencedAssemblies": [], "ReferencedAssembliesSearchPaths": [],
        "EditorExtensionAssemblies": [], "EditorExtensionAssembliesSearchPaths": [],
        "SkipContentMounting": False, "EnableIgnoreProcessingErrorFlags": False,
    }, bundles))
    write_json(d / "Assets.meta", envelope(_META, {
        "AssetID": str(uuid.uuid4()),
        "InternalItems": [{"$Type": "VRage:Keen.VRage.ContentPipeline.Definitions.DefinitionSetItem",
                           "DefinitionSetName": "World"}],
    }, bundles))
    write_json(d / "Content.meta", envelope(_META, {"AssetID": str(uuid.uuid4()), "InternalItems": []}, bundles))
    write_json(d / "ModProject.modhub-metadata", envelope("VRage:Keen.VRage.ModHUB.Models.ModProject", {
        "Id": {"Id": "00000000-0000-0000-0000-000000000000", "ServiceName": ""},
        "ProjectID": {"Guid": project_id, "ModId": None, "ServiceId": ""},
        "Version": None, "BaseMetadata": None, "Name": name, "Summary": "", "Tags": [],
        "ProjectFile": f"{name}.vrgproj",
    }, bundles))
    (d / "Assets").mkdir(exist_ok=True)
    (d / "Content").mkdir(exist_ok=True)
    return project_id


def _values(spec: ModSpec) -> dict[str, str]:
    return templates.values(name=spec.name, prefix=spec.prefix, pkg=spec.pkg, profile=spec.profile,
                            dist=dist_name(spec.name), modkit_version=__version__, library=spec.library)


def _write_mod(d: Path, spec: ModSpec, bundles: dict[str, str]) -> str:
    project_id = write_hub_files(d, spec.name, bundles)
    for rel, content in templates.mod_files(_values(spec), library=spec.library).items():
        _write(d / rel, content)
    return project_id


def new_repo(d: Path, spec: ModSpec, bundles: dict[str, str], *, git: str, gitattributes: bool = True) -> str:
    """A complete single-mod repo at `d` (git init, no commit); returns the project id. `gitattributes=False` leaves
    `.gitattributes` to the org template."""
    if d.exists():
        raise ScaffoldError(f"{d} already exists.")
    d.mkdir(parents=True)
    project_id = _write_mod(d, spec, bundles)
    for rel, content in templates.repo_files(_values(spec), gitattributes=gitattributes).items():
        _write(d / rel, content)
    subprocess.run([git, "init", "-q", str(d)], check=True)
    subprocess.run([git, "-C", str(d), "lfs", "install", "--local"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return project_id


# --- multi-mod repos ------------------------------------------------------------------------------------------------


def convert_single(text: str, old: str) -> str:
    """A single-mod pyproject's `[tool.se2modkit]` (and its sub-tables) moved under `[tool.se2modkit.mods.<old>]`,
    with `path = "."` (the mod stays at the repo root)."""
    key = _toml_key(old)
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        m = _HEADER.match(line)
        if m:
            sub = m.group("sub")
            if sub is None:
                out += [f"[tool.se2modkit.mods.{key}]", 'path = "."']
                inside = True
                continue
            if not sub.startswith("mods"):
                out.append(f"[tool.se2modkit.mods.{key}.{sub}]")
                inside = False
                continue
        elif line.lstrip().startswith("["):
            inside = False
        if inside and _MOD_NAME_LINE.match(line):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def _repo_mods(table: dict) -> dict[str, str]:
    """name -> prefix of the mods a `[tool.se2modkit]` table declares."""
    if "mods" in table:
        default = table.get("prefix", "")
        return {n: (t.get("prefix", default) if isinstance(t, dict) else default) for n, t in table["mods"].items()}
    if table.get("mod_name"):
        return {table["mod_name"]: table.get("prefix", "")}
    return {}


def add_into(repo: Path, spec: ModSpec, bundles: dict[str, str], *, convert: bool) -> Path:
    """Add `mods/<name>/` and its `[tool.se2modkit.mods.<name>]` table to the repo at `repo`; returns the mod dir."""
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        raise ScaffoldError(f"{repo} has no pyproject.toml with a [tool.se2modkit] table; add one (an empty "
                            "[tool.se2modkit] table starts a multi-mod repo).")
    before = pyproject.read_text(encoding="utf-8")
    try:
        table = tomllib.loads(before).get("tool", {}).get("se2modkit")
    except tomllib.TOMLDecodeError as e:
        raise ScaffoldError(f"Cannot parse {pyproject}: {e}.") from e
    if table is None:
        raise ScaffoldError(f"{pyproject} has no [tool.se2modkit] table; add one (an empty table starts a multi-mod "
                            "repo).")
    single = "mods" not in table and bool(table.get("mod_name"))
    if single and not convert:
        raise ScaffoldError(f"{repo} is a single-mod repo ('{table['mod_name']}'); pass --convert to move its "
                            f"[tool.se2modkit] table to [tool.se2modkit.mods.{_toml_key(table['mod_name'])}] and add "
                            f"'{spec.name}' beside it.")
    existing = _repo_mods(table)
    if spec.name in existing:
        raise ScaffoldError(f"Mod '{spec.name}' is already in {repo}.")
    for other, prefix in existing.items():
        if prefix == spec.prefix:
            raise ScaffoldError(f"Prefix {spec.prefix} is already used by mod '{other}' in {repo}; pick another "
                                "with --prefix.")
    d = repo / "mods" / spec.name
    if d.exists():
        raise ScaffoldError(f"{d} already exists.")
    text = convert_single(before, table["mod_name"]) if single else before
    if not text.endswith("\n"):
        text += "\n"
    text += (f"\n[tool.se2modkit.mods.{_toml_key(spec.name)}]\npath = \"mods/{spec.name}\"\n"
             f"profile = \"{spec.profile}\"\nprefix = \"{spec.prefix}\"\n")
    if not spec.library:
        text += f"gates = {templates.DEFAULT_GATES}\n"
    d.mkdir(parents=True)
    try:
        _write_mod(d, spec, bundles)
        _write(pyproject, text)
        load_project(repo)  # the result must load; otherwise undo both
    except BaseException:  # also Ctrl-C: never leave a half-added mod behind
        shutil.rmtree(d, ignore_errors=True)
        _write(pyproject, before)
        raise
    return d


# --- org-managed files ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OrgPlan:
    script: Path
    gitattributes: bool  # write our own .gitattributes (the org repo's config keeps it in the repo)


def _org_templates(org: Path, kind: str) -> set[str]:
    out: set[str] = set()
    for layer in ("common", kind):
        base = org / "templates" / layer
        if base.is_dir():
            out |= {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
    return out


def org_check(org: Path, slug: str, repo: Path, spec: ModSpec) -> OrgPlan:
    """Check the org template repo can render files for the new repo before anything is created."""
    script = org / "tools" / "sync-github-structure.py"
    if not script.is_file():
        raise ScaffoldError(f"{org} has no tools/sync-github-structure.py; pass the org template repo to --org-sync.")
    cfg = org / "repos" / f"{slug}.toml"
    if not cfg.is_file():
        raise ScaffoldError(f"{org} has no repos/{slug}.toml; add one there (kind = \"mod\", target = the new repo's "
                            "path relative to the org repo), or name another with --org-name.")
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ScaffoldError(f"Cannot parse {cfg}: {e}.") from e
    target = data.get("target")
    if not isinstance(target, str) or (org / target).resolve() != repo.resolve():
        raise ScaffoldError(f"repos/{slug}.toml in {org} has target {target!r}, which is not {repo}; point it at the "
                            "new repo.")
    keep = [k for k in data.get("keep") or [] if isinstance(k, str)]
    kept = lambda rel: any(fnmatch.fnmatch(rel, g) for g in keep)  # noqa: E731
    managed = _org_templates(org, str(data.get("kind", "mod")))
    ours = set(templates.mod_files(_values(spec), library=spec.library)) | set(templates.repo_files(_values(spec)))
    clash = sorted(rel for rel in ours & managed if rel != ".gitattributes" and not kept(rel))
    if clash:
        raise ScaffoldError(f"The org template also manages {', '.join(clash)}, which the scaffold writes; add them to "
                            f"keep in repos/{slug}.toml so the sync leaves the scaffold's files alone.")
    return OrgPlan(script=script, gitattributes=".gitattributes" not in managed or kept(".gitattributes"))


def org_sync(org: Path, slug: str, script: Path) -> None:
    """Render the org-managed files: the sync script's dry run, then `--apply` when the dry run is clean."""
    for extra in ([], ["--apply"]):
        r = subprocess.run([sys.executable, str(script), slug, *extra], cwd=org, capture_output=True, text=True)
        if r.stdout:
            print(r.stdout, end="" if r.stdout.endswith("\n") else "\n")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["no output"]
            step = "apply" if extra else "dry run"
            raise ScaffoldError(f"The org sync {step} failed (exit {r.returncode}: {tail[0]}); fix it in {org}, then "
                                f"run `python tools/sync-github-structure.py {slug} --apply` there.")
