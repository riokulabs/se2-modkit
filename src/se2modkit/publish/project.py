"""A mod project folder: its .vrgproj, its publish config, the canonical-folder rule and the publish ledger."""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import gitutil
from .changelog import DEFAULT_SKIP_SECTIONS
from .metadata import MOD_TAGS, VISIBILITIES, guid_to_item_id

CONFIG_REL = "docs/workshop/publish.toml"
LEDGER_REL = "docs/workshop/publish-ledger.jsonl"
DEFAULTS = {
    "description": "docs/workshop/description.bbcode",
    "changelog": "CHANGELOG.md",
    "thumbnail": "docs/workshop/thumb.jpg",
    "first_visibility": "Unlisted",
    "summary": "",
    "tags": [],
}


class Refusal(Exception):
    """A rule says no. The message says why and what to do instead."""


@dataclass
class PublishConfig:
    title: str
    tags: list[str]
    summary: str
    description: Path
    changelog: Path
    thumbnail: Path
    first_visibility: str
    source: Path | None             # the publish.toml it came from (None = defaults only)
    problems: list[str] = field(default_factory=list)
    dependencies: list[int] = field(default_factory=list)   # Workshop item ids this mod requires
    change_note_skip_sections: list[str] = field(default_factory=lambda: list(DEFAULT_SKIP_SECTIONS))


@dataclass
class ModProject:
    folder: Path
    vrgproj: Path
    data: dict
    repo: Path | None = None        # the git work tree holding the mod; None = the mod folder itself (single-mod)
    key: str | None = None          # the mod's name in a multi-mod repo's [tool.se2modkit.mods] table

    def __post_init__(self) -> None:
        if self.repo is None:
            self.repo = self.folder

    @property
    def multi(self) -> bool:
        """True for a mod inside a multi-mod repo (its folder is not the top of the git work tree)."""
        return self.folder != self.repo

    def release_tag(self, version: str) -> str:
        """The git tag for a release: `<mod>-vX.Y.Z` in a multi-mod repo, `vX.Y.Z` in a single-mod repo."""
        v = "v" + version.lstrip("vV")
        return f"{self.name}-{v}" if self.multi else v

    @property
    def name(self) -> str:
        return self.data["$Value"].get("Name") or self.vrgproj.stem

    @property
    def guid(self) -> str:
        return self.data["$Value"]["ProjectID"]["Guid"]

    @property
    def version(self) -> str | None:
        return self.data["$Value"].get("Version")

    @property
    def mod_item_id(self) -> int | None:
        """The Workshop item id recorded in ProjectID.ModId (the HUB writes it on its first publish)."""
        mod = self.data["$Value"]["ProjectID"].get("ModId")
        if not mod or not mod.get("Id"):
            return None
        item = guid_to_item_id(mod["Id"])
        return item or None


def load_project(folder: Path, repo: Path | None = None, key: str | None = None) -> ModProject:
    folder = folder.resolve()
    own = folder / f"{folder.name}.vrgproj"
    projs = sorted(folder.glob("*.vrgproj"))
    if own.exists():
        proj = own
    elif len(projs) == 1:
        proj = projs[0]
    else:
        raise Refusal(f"{folder}: expected exactly one .vrgproj (found {len(projs)})")
    data = json.loads(proj.read_text(encoding="utf-8-sig"))
    if data.get("$Type", "").split(":")[-1] != "Keen.VRage.Core.Project.VRageProjectData":
        raise Refusal(f"{proj} is not a VRage project file")
    if data["$Value"].get("Type") != "Mod":
        raise Refusal(f"{proj} is a {data['$Value'].get('Type')} project, not a Mod")
    return ModProject(folder, proj, data, repo.resolve() if repo is not None else None, key)


def check_canonical(p: ModProject, workspace_mods: Path | None = None) -> list[str]:
    """Refuse anything but the canonical mods/<Name> folder (several folders share a project GUID).

    Returns informational notes (e.g. sibling folders that share the GUID)."""
    top = gitutil.toplevel(p.folder)
    if top is None:
        raise Refusal(f"{p.folder} is not a git repository; publish only from the mod's own repo")
    if top != p.repo:
        raise Refusal(f"{p.repo if p.multi else p.folder} is not the top of its git repo ({top})")
    if gitutil.is_linked_worktree(p.repo):
        raise Refusal(f"{p.repo} is a linked git worktree, refused on purpose: it shares the project GUID with the "
                      "canonical repo, and a publish from it would record the Workshop item id and ledger on that "
                      "branch only, so merge and publish from the canonical mods/<Mod> folder.")
    if p.folder.name != p.vrgproj.stem or p.vrgproj.stem != p.name:
        raise Refusal(f"folder '{p.folder.name}', project file '{p.vrgproj.name}' and project Name '{p.name}' "
                      "must all match for the canonical folder")
    if p.key is not None and p.key != p.name:
        raise Refusal(f"pyproject.toml names this mod '{p.key}' but its project Name is '{p.name}'; they must match")
    notes = []
    mods = workspace_mods or p.repo.parent
    for other in sorted(mods.glob("*/*.vrgproj")):
        if other.parent.resolve() == p.folder:
            continue
        try:
            g = json.loads(other.read_text(encoding="utf-8-sig"))["$Value"]["ProjectID"]["Guid"]
        except (OSError, ValueError, KeyError):
            continue
        if g.lower() == p.guid.lower():
            notes.append(f"{other.parent.name}/ shares this project GUID (never publish from it; don't load it "
                         "in the game while testing the Workshop copy)")
    return notes


# --- multi-mod repos ----------------------------------------------------------------------------------------------

_TAG = re.compile(r"^(?P<mod>.+)-[vV](?P<ver>\d[\w.+-]*)$")


def split_release_tag(version: str | None) -> tuple[str | None, str | None]:
    """`Core-v1.2.0` -> ("Core", "1.2.0"); `v1.2.0` / `1.2.0` -> (None, the same); None -> (None, None)."""
    if not version:
        return None, version
    m = _TAG.match(version)
    return (m.group("mod"), m.group("ver")) if m else (None, version)


def repo_mods(top: Path) -> dict[str, str] | None:
    """The `[tool.se2modkit.mods.<name>] path = ...` table of the repo's pyproject.toml at git HEAD, or None for a
    single-mod repo. Read from HEAD like every other publish input."""
    try:
        rows = [r for r in gitutil.ls_tree(top, "HEAD", "pyproject.toml") if r[3] == "pyproject.toml"]
    except gitutil.GitError:      # no commit yet: the canonical-folder check reports it
        return None
    if not rows or rows[0][1] != "blob" or rows[0][0] == "120000":
        return None
    try:
        data = tomllib.loads(gitutil.cat_blob(top, rows[0][2]).decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise Refusal(f"{top}/pyproject.toml (git HEAD) cannot be parsed: {e}") from e
    table = data.get("tool", {}).get("se2modkit", {}).get("mods")
    if not isinstance(table, dict) or not table:
        return None
    out: dict[str, str] = {}
    for name, mt in table.items():
        rel = mt.get("path") if isinstance(mt, dict) else None
        if not isinstance(rel, str) or not rel.strip():
            raise Refusal(f"pyproject.toml: [tool.se2modkit.mods.{name}] needs a path")
        out[name] = rel
    return out


def _mod_folder(top: Path, name: str, rel: str) -> Path:
    pure = PurePosixPath(rel.replace("\\", "/"))
    if pure.is_absolute() or rel.startswith("~") or re.match(r"^[A-Za-z]:", rel) or ".." in pure.parts:
        raise Refusal(f"pyproject.toml: mod '{name}' path {rel!r} must be a relative path inside the repo")
    folder = (top / pure).resolve()
    if not folder.is_relative_to(top) or folder == top or folder.is_relative_to(top / ".git"):
        raise Refusal(f"pyproject.toml: mod '{name}' path {rel!r} resolves outside the repo's mod folders")
    if not folder.is_dir():
        raise Refusal(f"pyproject.toml: mod '{name}' folder {rel} does not exist")
    return folder


def select_mod(path: Path, mod: str | None = None, version: str | None = None) -> ModProject:
    """The mod to publish from `path` (a mod repo, or a mod folder inside a multi-mod repo).

    In a multi-mod repo the mod comes from `--mod`, a `<mod>-vX.Y.Z` release tag in `version`, or the mod folder
    `path` points into; with none of them the answer is a "which mod?" refusal listing the names, never the first
    mod. In a single-mod repo `mod` and a tag's mod part must name that mod."""
    path = path.resolve()
    tag_mod, _ = split_release_tag(version)
    if mod and tag_mod and mod != tag_mod:
        raise Refusal(f"--mod {mod} and release tag {version} name different mods")
    want = mod or tag_mod
    top = gitutil.toplevel(path)
    table = repo_mods(top) if top is not None else None
    if table is None:
        p = load_project(path)
        if want and want != p.name:
            raise Refusal(f"{path} holds the single mod '{p.name}', not '{want}'"
                          + ("; single-mod repos tag releases vX.Y.Z" if tag_mod else ""))
        return p
    folders = {name: _mod_folder(top, name, rel) for name, rel in table.items()}
    names = ", ".join(sorted(folders))
    here = next((n for n, f in folders.items() if path == f or f in path.parents), None)
    if want is not None:
        if want not in folders:
            raise Refusal(f"no mod '{want}' in {top}; mods in this repo: {names}")
        if here is not None and here != want:
            raise Refusal(f"{path} is inside mod '{here}', but '{want}' was asked for")
        here = want
    if here is None:
        raise Refusal(f"which mod? {top} holds several mods: {names}. Name one with --mod NAME "
                      "(or a NAME-vX.Y.Z release tag as --version)")
    return load_project(folders[here], repo=top, key=here)


def _tag_name(t: str) -> str | None:
    if t in MOD_TAGS:
        return t
    cand = "ModTag" + t[:1].upper() + t[1:].lower()
    return cand if cand in MOD_TAGS else None


# --- config-supplied paths (security: they decide what gets uploaded publicly) --------------------------------

def safe_repo_path(p: ModProject, raw: object, key: str) -> Path:
    """A config path must be a relative path inside the mod repo: no absolute paths, no '..', no '~', and its
    resolved location (following any symlink) must stay inside the repo. Raises Refusal otherwise."""
    if not isinstance(raw, str) or not raw.strip():
        raise Refusal(f"{CONFIG_REL}: '{key}' must be a non-empty relative path")
    text = raw.replace("\\", "/")
    pure = PurePosixPath(text)
    if pure.is_absolute() or text.startswith("~") or re.match(r"^[A-Za-z]:", text):
        raise Refusal(f"{CONFIG_REL}: '{key}' = {raw!r} is absolute; only paths inside the mod repo are allowed")
    if any(part == ".." for part in pure.parts):
        raise Refusal(f"{CONFIG_REL}: '{key}' = {raw!r} uses '..'; only paths inside the mod repo are allowed")
    root = p.folder.resolve()
    full = p.folder / pure
    resolved = full.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise Refusal(f"{CONFIG_REL}: '{key}' = {raw!r} resolves to {resolved}, outside the mod repo "
                      "(symlink?); refusing")
    if resolved.is_relative_to(root / ".git"):
        raise Refusal(f"{CONFIG_REL}: '{key}' points into .git")
    return full


def repo_rel(p: ModProject, path: Path) -> str:
    return path.relative_to(p.folder).as_posix()


def read_tracked(p: ModProject, path: Path) -> bytes | None:
    """The file's content at git HEAD (None if it is not tracked). Refuses symlinks and git-lfs pointers."""
    rel = repo_rel(p, path)
    rows = [r for r in gitutil.ls_tree(p.folder, "HEAD", rel) if r[3] == rel]
    if not rows:
        return None
    mode, typ, sha, _ = rows[0]
    if typ != "blob":
        raise Refusal(f"{rel} is not a file in git")
    if mode == "120000":
        raise Refusal(f"{rel} is a symlink in git; refusing to follow it")
    data = gitutil.cat_blob(p.folder, sha)
    if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise Refusal(f"{rel} is a git-lfs pointer")
    return data


def differs_from_head(p: ModProject, path: Path) -> bool:
    return bool(gitutil.status_porcelain(p.folder, [repo_rel(p, path)]))


def load_config(p: ModProject) -> PublishConfig:
    """publish.toml is read from git HEAD when it is committed (what a real upload uses), else from the working
    tree with a problem noted."""
    src = p.folder / CONFIG_REL
    raw: dict = {}
    problems: list[str] = []
    head = read_tracked(p, src) if src.exists() or gitutil.ls_tree(p.folder, "HEAD", CONFIG_REL) else None
    if head is not None:
        raw = tomllib.loads(head.decode("utf-8"))
        if src.exists() and differs_from_head(p, src):
            problems.append(f"{CONFIG_REL} has uncommitted changes; using the committed version")
    elif src.exists():
        raw = tomllib.loads(src.read_text(encoding="utf-8"))
        problems.append(f"{CONFIG_REL} is not committed (a real upload needs it committed)")
    else:
        problems.append(f"no {CONFIG_REL} (using defaults: title = project Name, no tags)")
    known = {"title", "tags", "summary", "description", "changelog", "thumbnail", "first_visibility", "dependencies",
             "change_note_skip_sections"}
    for k in raw:
        if k not in known:
            problems.append(f"{CONFIG_REL}: unknown key '{k}'")
    tags = []
    for t in raw.get("tags", DEFAULTS["tags"]):
        n = _tag_name(str(t))
        if n is None:
            problems.append(f"tag '{t}' is not one of SE2's tags: "
                            + ", ".join(x.removeprefix("ModTag") for x in MOD_TAGS))
        elif n not in tags:
            tags.append(n)
    deps: list[int] = []
    raw_deps = raw.get("dependencies", [])
    if not isinstance(raw_deps, list):
        raise Refusal(f"{CONFIG_REL}: dependencies must be a list of Workshop item ids")
    for d in raw_deps:
        if isinstance(d, bool) or not isinstance(d, int) or not 0 < d < 2 ** 64:
            raise Refusal(f"{CONFIG_REL}: dependency {d!r} is not a Workshop item id (a positive integer)")
        if d not in deps:
            deps.append(d)
    vis = str(raw.get("first_visibility", DEFAULTS["first_visibility"]))
    if vis not in VISIBILITIES:
        problems.append(f"first_visibility '{vis}' is not one of {', '.join(VISIBILITIES)}")
        vis = "Unlisted"
    skip = raw.get("change_note_skip_sections", list(DEFAULT_SKIP_SECTIONS))
    if not isinstance(skip, list) or not all(isinstance(x, str) for x in skip):
        problems.append("change_note_skip_sections must be a list of section names")
        skip = list(DEFAULT_SKIP_SECTIONS)
    return PublishConfig(
        title=str(raw.get("title", p.name)),
        tags=tags,
        summary=str(raw.get("summary", DEFAULTS["summary"])),
        description=safe_repo_path(p, raw.get("description", DEFAULTS["description"]), "description"),
        changelog=safe_repo_path(p, raw.get("changelog", DEFAULTS["changelog"]), "changelog"),
        thumbnail=safe_repo_path(p, raw.get("thumbnail", DEFAULTS["thumbnail"]), "thumbnail"),
        first_visibility=vis,
        source=src if src.exists() else None,
        problems=problems,
        dependencies=deps,
        change_note_skip_sections=list(skip),
    )


# --- ledger ------------------------------------------------------------------------------------------------------

def ledger_path(p: ModProject) -> Path:
    return p.folder / LEDGER_REL


def read_ledger(p: ModProject) -> list[dict]:
    lp = ledger_path(p)
    if not lp.exists():
        return []
    rows = []
    for ln in lp.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            rows.append(json.loads(ln))
    return rows


def append_ledger(p: ModProject, record: dict) -> None:
    lp = ledger_path(p)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def ledger_item_ids(rows: list[dict]) -> set[int]:
    return {int(r["item_id"]) for r in rows if r.get("item_id")}


def last_published_commit(rows: list[dict]) -> str | None:
    for r in reversed(rows):
        if r.get("event") == "published" and r.get("commit"):
            return r["commit"]
    return None


def write_mod_id(p: ModProject, item_id: int) -> None:
    """Record the item id in the .vrgproj the way the HUB's UpdateModId does (ModId + ServiceId = "Steam"),
    keeping the file's existing newline style and 2-space indentation."""
    from .metadata import item_id_to_guid
    text = p.vrgproj.read_text(encoding="utf-8-sig")
    nl = "\r\n" if "\r\n" in text else "\n"
    data = json.loads(text)
    data["$Value"]["ProjectID"] = {"Guid": p.guid,
                                  "ModId": {"Id": item_id_to_guid(item_id), "ServiceName": "Steam"},
                                  "ServiceId": "Steam"}
    out = json.dumps(data, indent=2, ensure_ascii=False)
    if nl != "\n":
        out = out.replace("\n", nl)
    trailing = nl if text.endswith(("\n", "\r\n")) else ""
    p.vrgproj.write_text(out + trailing, encoding="utf-8", newline="")
    p.data = data


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
