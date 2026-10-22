"""Proton prefix, Proton build and Wine path translation (Linux only; never imported on other OSes)."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from .errors import EnvError
from .steam import GAME_APP_ID, parse_vdf

_DRIVE_RE = re.compile(r"^[a-z]:$", re.I)
_NUM_RE = re.compile(r"(\d+)")


def prefix_for(steamapps: Path) -> Path:
    """The Proton prefix the game (and the Mod HUB/Editor run in its prefix) uses inside a library."""
    return steamapps / "compatdata" / str(GAME_APP_ID) / "pfx"


def game_mods_dir_proton(prefix: Path) -> Path:
    """The game's Documents/SpaceEngineers2/Mods folder inside a Proton prefix (Proton's user is `steamuser`)."""
    return prefix / "drive_c" / "users" / "steamuser" / "Documents" / "SpaceEngineers2" / "Mods"


def _ci_get(d: object, *keys: str) -> object:
    """Walk nested VDF dicts by key, ignoring case (Steam writes both `Valve` and `valve`)."""
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = next((v for k, v in d.items() if k.lower() == key.lower()), None)
    return d


def _is_tool(d: Path) -> bool:
    return d.is_dir() and ((d / "proton").is_file() or (d / "compatibilitytool.vdf").is_file())


def _internal_names(tool: Path) -> set[str]:
    try:
        data = parse_vdf((tool / "compatibilitytool.vdf").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return set()
    tools = _ci_get(data, "compatibilitytools", "compat_tools")
    return set(tools) if isinstance(tools, dict) else set()


def _natural(name: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in _NUM_RE.split(name)]


def _custom_tools(steam_root: Path) -> list[Path]:
    base = steam_root / "compatibilitytools.d"
    try:
        return [d for d in base.iterdir() if _is_tool(d)]
    except OSError:
        return []


def _mapped_tool_name(steam_root: Path) -> str | None:
    try:
        data = parse_vdf((steam_root / "config" / "config.vdf").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    name = _ci_get(data, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping",
                   str(GAME_APP_ID), "name")
    return name if isinstance(name, str) and name else None


def _official_dirnames(name: str) -> list[str]:
    """Directory names Steam installs its own Proton builds under, from the internal tool name.

    `proton_experimental` -> `Proton - Experimental`, `proton_hotfix` -> `Proton Hotfix`, `proton_9` -> `Proton 9.0`,
    `proton_63` -> `Proton 6.3` (Steam's toolmanifest.vdf carries no name, so the naming convention is used).
    """
    low = name.lower()
    if not low.startswith("proton_"):
        return []
    rest = low[len("proton_"):]
    if rest == "experimental":
        return ["Proton - Experimental"]
    if rest.isdigit():
        out = [f"Proton {rest}.0"]
        if len(rest) > 1:
            out.append(f"Proton {rest[0]}.{rest[1:]}")
        return out
    return [f"Proton {rest.replace('_', ' ').title()}"]


def _official_tools(libraries: list[Path]) -> list[Path]:
    out = []
    for lib in libraries:
        try:
            out += [d for d in (lib / "steamapps" / "common").iterdir() if d.name.startswith("Proton") and _is_tool(d)]
        except OSError:
            continue
    return out


def _official_rank(d: Path) -> tuple:
    """Numbered builds newest first, then Experimental, then anything else (Hotfix)."""
    nums = [int(n) for n in _NUM_RE.findall(d.name)]
    if nums:
        return (2, nums)
    return (1 if "experimental" in d.name.lower() else 0, [])


def _resolve_name(name: str, custom: list[Path], libraries: list[Path]) -> Path | None:
    for t in custom:
        if t.name == name or name in _internal_names(t):
            return t
    for dirname in _official_dirnames(name):
        for lib in libraries:
            d = lib / "steamapps" / "common" / dirname
            if _is_tool(d):
                return d
    return None


def proton_build(steam_root: Path, *, configured: str | None, libraries: Iterable[Path] = ()) -> Path | None:
    """The Proton build directory to run the SDK tools with.

    Names resolve against `compatibilitytools.d` (directory or internal `compatibilitytool.vdf` name) and Steam's
    own builds (`steamapps/common/Proton ...` in any of `libraries`; the Steam root is always searched).
    `configured` (a name or a path) wins and is returned even if absent, so the caller can name it in an error.
    Otherwise the tool Steam maps to the game (`config/config.vdf` `CompatToolMapping`), then the newest build in
    `compatibilitytools.d`, then the newest of Steam's own Proton builds.
    """
    libs = [steam_root, *(lib for lib in libraries if lib != steam_root)]
    custom = _custom_tools(steam_root)
    if configured:
        p = Path(configured)
        if p.is_absolute():
            return p
        return _resolve_name(configured, custom, libs) or steam_root / "compatibilitytools.d" / configured
    mapped = _mapped_tool_name(steam_root)
    if mapped:
        hit = _resolve_name(mapped, custom, libs)
        if hit:
            return hit
    if custom:
        return max(custom, key=lambda t: _natural(t.name))
    official = _official_tools(libs)
    return max(official, key=_official_rank) if official else None


def wine_drive_map(prefix: Path) -> dict[str, Path]:
    """Drive letters (`"C:"`, `"Z:"`, ...) to the host directories the prefix's `dosdevices/` links point at."""
    dd = prefix / "dosdevices"
    try:
        entries = list(dd.iterdir())
    except OSError:
        return {}
    out: dict[str, Path] = {}
    for e in entries:
        if not _DRIVE_RE.match(e.name) or not e.is_symlink():
            continue
        target = Path(os.readlink(e))
        if not target.is_absolute():
            target = dd / target
        try:
            out[e.name.upper()] = target.resolve(strict=True)
        except (OSError, RuntimeError):  # dangling link or symlink loop: not a usable drive
            continue
    return out


def to_wine_path(p: Path, drives: dict[str, Path]) -> str:
    """Translate a host path to the Wine path of the drive with the longest matching root (backslashes)."""
    try:
        host = Path(p).resolve()
    except (OSError, RuntimeError) as e:
        raise EnvError(f"Cannot translate {p} for Wine: the path cannot be resolved ({e}).") from e
    best: tuple[int, str, Path] | None = None
    for letter, root in drives.items():
        if host == root or root in host.parents:
            depth = len(root.parts)
            if best is None or depth > best[0]:
                best = (depth, letter, root)
    if best is None:
        raise EnvError(f"Cannot translate {p} for Wine: no Wine drive in the Proton prefix covers it.")
    _, letter, root = best
    return letter + "\\" + "\\".join(host.relative_to(root).parts)


def unresolved_mapping(steam_root: Path, libraries: Iterable[Path] = ()) -> str | None:
    """The Proton tool name Steam maps to the game when no installed build matches it, else None."""
    mapped = _mapped_tool_name(steam_root)
    if not mapped:
        return None
    libs = [steam_root, *(lib for lib in libraries if lib != steam_root)]
    return None if _resolve_name(mapped, _custom_tools(steam_root), libs) else mapped
