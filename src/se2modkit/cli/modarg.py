"""The MOD argument every mod command takes: a mod name or a path to a mod directory.

Every command resolves it through `resolve` so names and paths behave the same everywhere: a name in the project here
first, then an existing directory, then a name in a `workspace_mods` folder. A multi-mod repo root without a name is
the "which mod?" error listing the mods, never "first mod wins".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..env.config import ConfigError, load_user_config
from ..project import ModConfig, Project, ProjectError, load_project

__all__ = ["ModNotFound", "ModRef", "project_file", "resolve", "workspace_mods_dirs"]


class ModNotFound(ProjectError):
    """The MOD argument names no mod; reported as one plain sentence."""


@dataclass(frozen=True)
class ModRef:
    name: str
    root: Path
    config: ModConfig | None  # None for a mod directory outside any [tool.se2modkit] project


def project_file(d: Path) -> Path | None:
    """The mod folder's .vrgproj: `<folder name>.vrgproj`, else its only .vrgproj (a git worktree such as
    `MyMod-room` holds `MyMod.vrgproj`), else None."""
    d = Path(d)
    own = d / f"{d.name}.vrgproj"
    if own.is_file():
        return own
    found = sorted(d.glob("*.vrgproj")) if d.is_dir() else []
    return found[0] if len(found) == 1 else None


def workspace_mods_dirs(env) -> list[Path]:
    """Folders holding mods outside a project: user config `workspace_mods` (a path or a list), then
    `$SE2_WORKSPACE/mods`."""
    try:
        cfg = load_user_config(env.config_dir / "config.toml")
    except (ConfigError, OSError, UnicodeDecodeError):  # `se2mod` reports an unreadable config before this runs
        cfg = {}
    raw = cfg.get("workspace_mods") or []
    out = [Path(p).expanduser() for p in ([raw] if isinstance(raw, str) else raw) if isinstance(p, str) and p]
    ws = os.environ.get("SE2_WORKSPACE")
    if ws:
        out.append(Path(ws) / "mods")
    seen: list[Path] = []
    for p in out:
        if p not in seen:
            seen.append(p)
    return seen


def _no_project(e: ProjectError) -> bool:
    return str(e).startswith("No pyproject.toml")


def _project_at(path: Path) -> Project | None:
    try:
        return load_project(path)
    except ProjectError as e:
        if _no_project(e):
            return None
        raise


def _ref(cfg: ModConfig) -> ModRef:
    return ModRef(cfg.name, cfg.root, cfg)


def _from_dir(p: Path, need: str) -> ModRef | None:
    project = _project_at(p)
    cfg = project.containing(p) if project is not None else None
    if cfg is None and project is not None and p.resolve() == project.root.resolve() and len(project.mods) > 1:
        project.mod(None)  # a multi-mod repo root: the "which mod?" error with the names
    if cfg is not None:
        return _ref(cfg)
    if need == "config":
        raise ModNotFound(f"{p} is not a mod of a [tool.se2modkit] project (no pyproject.toml here or above "
                          "declares it).")
    if need == "dir" or project_file(p) is not None:
        return ModRef(p.name, p, None)
    return None


def resolve(env, selector: str | None, *, need: str = "vrgproj", cwd: Path | None = None) -> ModRef:
    """The mod `selector` names (a name or a path); `None` means the mod here.

    `need`: "vrgproj" (a directory with a .vrgproj; its project config when one covers it), "config" (a mod of a
    [tool.se2modkit] project) or "dir" (any existing directory, e.g. a test-only mod)."""
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    if selector is None:
        return _ref(load_project(cwd).mod(None, cwd=cwd))
    why = None
    try:
        project = load_project(cwd)
    except ProjectError as e:
        project, why = None, str(e)
    if project is not None and selector in project.mods:
        return _ref(project.mods[selector])
    as_path = Path(selector).expanduser()
    if as_path.is_dir():
        found = _from_dir(as_path, need)
        if found is not None:
            return found
    dirs = workspace_mods_dirs(env) if env is not None else []
    for base in dirs:
        d = base / selector
        if project_file(d) is not None:
            found = _from_dir(d, need)
            if found is not None:
                return found
    where = []
    if project is not None:
        where.append(f"not a mod of the project at {project.root} ({', '.join(sorted(project.mods)) or 'none'})")
    elif why and not why.startswith("No pyproject.toml"):
        where.append(why.rstrip("."))
    where.append("not a mod directory")
    where.append(f"not in {', '.join(map(str, dirs))}" if dirs
                 else "no workspace_mods folder is configured (set config workspace_mods or SE2_WORKSPACE)")
    raise ModNotFound(f"Mod '{selector}' not found: {'; '.join(where)}.")
