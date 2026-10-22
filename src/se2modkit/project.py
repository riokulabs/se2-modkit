"""Mod project model: single-mod and multi-mod `[tool.se2modkit]` layouts."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ProjectError(Exception):
    """A project/config problem, reported to the user as one plain sentence."""


@dataclass(frozen=True)
class ModPaths:
    models: Path
    report: Path
    staged: Path
    golden_states: Path
    source_model: Path
    asset_guids: Path | None = None  # the stable asset GUID store (`defs.guids.Guids`)
    root: Path | None = None  # the mod directory (the one holding the .vrgproj)

    @property
    def assets(self) -> Path:
        """The mod's `Assets/` (where clones land and the staged package installs)."""
        return self._root() / "Assets"

    @property
    def clone_map(self) -> Path:
        """The mod's `.clonemap.json` (written by `se2mod copy` and the clone step)."""
        return self._root() / ".clonemap.json"

    def _root(self) -> Path:
        if self.root is None:
            raise ProjectError("These mod paths have no root (the mod directory); build them with the mod's root.")
        return self.root


@dataclass(frozen=True)
class ModConfig:
    name: str
    root: Path
    profile: str
    prefix: str
    gates: tuple[str, ...]
    style: dict
    paths: ModPaths
    gate_options: dict = field(default_factory=dict)   # [tool.se2modkit.gate_options.<gate>] settings


@dataclass(frozen=True)
class Project:
    root: Path
    mods: dict[str, ModConfig] = field(default_factory=dict)

    def mod(self, selector: str | None, *, cwd: Path | None = None) -> ModConfig:
        names = ", ".join(sorted(self.mods))
        if selector is not None:
            if selector not in self.mods:
                raise ProjectError(f"Unknown mod '{selector}'; mods in this project: {names}.")
            return self.mods[selector]
        if len(self.mods) == 1:
            return next(iter(self.mods.values()))
        if cwd is not None:
            found = self.containing(cwd)
            if found is not None:
                return found
        raise ProjectError(f"Which mod? Name one of: {names} (or run from inside a mod directory).")


    def containing(self, path: Path) -> ModConfig | None:
        """The mod whose directory holds `path`; the deepest one when mods nest (a repo-root mod around `mods/*`)."""
        here = Path(path).resolve()
        best: ModConfig | None = None
        for m in self.mods.values():
            root = m.root.resolve()
            inside = here == root or root in here.parents
            if inside and (best is None or len(root.parts) > len(best.root.resolve().parts)):
                best = m
        return best


MOD_NAME_RE = re.compile(r"[A-Za-z0-9 _.-]+")


def mod_name_error(name) -> str | None:
    """Why `name` cannot be a mod name (it builds folder, cache and staging paths), else None: letters, digits,
    space, _ . - only, not only dots and spaces, no leading or trailing space, no trailing dot (`se2mod new`'s rule)."""
    if not isinstance(name, str) or not MOD_NAME_RE.fullmatch(name) or not name.strip(" ."):
        return f"Mod name {name!r}: use letters, digits, space, _ . - only."
    if name != name.strip(" ") or name.endswith("."):
        return f"Mod name {name!r} cannot start or end with a space or end with a dot (Windows folder names)."
    return None


_DEFAULT_PATHS = {
    "models": "Source/Model/Models",
    "report": "Source/Model/build_report.json",
    "staged": "Source/Model/integration/Assets",
    "golden_states": "Tests/golden/states_baseline.json",
    "source_model": "Source/Model",
    "asset_guids": "Source/Model/asset_guids.json",
}
_WIN_DRIVE = re.compile(r"^[A-Za-z]:")


def _non_portable(s: str) -> bool:
    return s.startswith(("/", "\\", "~")) or bool(_WIN_DRIVE.match(s))


def portable_errors(table: dict, where: str) -> list[str]:
    """Errors for string values that are absolute, drive-lettered or home-relative paths."""
    errors: list[str] = []
    for key, val in table.items():
        label = f"{where}.{key}"
        if isinstance(val, str):
            if _non_portable(val):
                errors.append(f"{label} = {val!r} is not portable (use a path relative to the repo)")
        elif isinstance(val, dict):
            errors.extend(portable_errors(val, label))
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, str) and _non_portable(item):
                    errors.append(f"{label}[{i}] = {item!r} is not portable (use a path relative to the repo)")
    return errors


def load_project(start: Path) -> Project:
    start = Path(start).resolve()
    for d in (start, *start.parents):
        pyproject = d / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ProjectError(f"Cannot parse {pyproject}: {e}.") from e
        table = data.get("tool", {}).get("se2modkit")
        if table is not None:
            return _build(d, table)
    raise ProjectError(f"No pyproject.toml with a [tool.se2modkit] table found at or above {start}.")


def _build(root: Path, table: dict) -> Project:
    errors = portable_errors(table, "tool.se2modkit")
    if errors:
        raise ProjectError("; ".join(errors))
    sub = table.get("mods")
    defaults = {k: v for k, v in table.items() if k != "mods"}
    mods: dict[str, ModConfig] = {}
    if sub is None:
        name = defaults.get("mod_name")
        if not name:
            raise ProjectError("[tool.se2modkit] needs mod_name (or [tool.se2modkit.mods.<name>] tables).")
        mods[name] = _mod(name, root, defaults)
    else:
        for name, mt in sub.items():
            merged = {**defaults, **mt}
            if isinstance(defaults.get("style"), dict) and isinstance(mt.get("style"), dict):
                merged["style"] = {**defaults["style"], **mt["style"]}
            if isinstance(defaults.get("gate_options"), dict) and isinstance(mt.get("gate_options"), dict):
                merged["gate_options"] = {**defaults["gate_options"], **mt["gate_options"]}
            if isinstance(defaults.get("paths"), dict) and isinstance(mt.get("paths"), dict):
                merged["paths"] = {**defaults["paths"], **mt["paths"]}
            rel = mt.get("path")
            if not rel:
                raise ProjectError(f"Mod '{name}' in [tool.se2modkit.mods.{name}] needs a path.")
            mods[name] = _mod(name, root / rel, merged)
    return Project(root=root, mods=mods)


def _mod(name: str, mod_root: Path, t: dict) -> ModConfig:
    why = mod_name_error(name)
    if why:
        raise ProjectError(why)
    found = sorted(mod_root.glob("*.vrgproj")) if mod_root.is_dir() else []
    if len(found) != 1:
        raise ProjectError(
            f"Mod '{name}' at {mod_root} must contain exactly one .vrgproj (found {len(found)})."
        )
    rel = {**_DEFAULT_PATHS, **{k: v for k, v in (t.get("paths") or {}).items() if k != "root"}}
    return ModConfig(
        name=name,
        root=mod_root,
        profile=t.get("profile", "vanilla"),
        prefix=t.get("prefix", ""),
        gates=tuple(t.get("gates", ())),
        style=dict(t.get("style") or {}),
        gate_options=dict(t.get("gate_options") or {}),
        paths=ModPaths(**{k: mod_root / v for k, v in rel.items()}, root=mod_root),
    )
