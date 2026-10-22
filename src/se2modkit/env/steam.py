"""Locate Steam, its libraries and installed apps (read-only)."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

GAME_APP_ID = 1133870  # Space Engineers 2
SDK_APP_ID = 3578990  # Space Engineers 2 - Mod SDK

_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*|\s+')
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), s)


def parse_vdf(text: str) -> dict:
    """Parse Valve KeyValues text (quoted keys, nested braces, // comments); keys keep their case."""
    tokens: list[tuple[str, str]] = []
    for m in _TOKEN_RE.finditer(text):
        if m.group(1) is not None:
            tokens.append(("str", _unescape(m.group(1))))
        elif m.group(2):
            tokens.append(("brace", m.group(2)))
    pos = 0

    def block(top: bool) -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            kind, val = tokens[pos]
            if kind == "brace":
                pos += 1
                if val == "}":
                    return out
                continue  # stray "{": ignore
            pos += 1
            if pos >= len(tokens):
                break
            nkind, nval = tokens[pos]
            if nkind == "brace" and nval == "{":
                pos += 1
                out[val] = block(False)
            elif nkind == "str":
                pos += 1
                out[val] = nval
        return out

    return block(True)


def steam_root_candidates(*, home: Path, platform: str, environ: Mapping[str, str],
                          read_registry: Callable[[], str | None] | None = None) -> list[Path]:
    """Places Steam may live, most likely first. Existence is not checked here."""
    if platform.startswith("win"):
        out: list[Path] = []
        reg = (read_registry or _read_registry)()
        if reg:
            out.append(Path(reg))
        pf = environ.get("ProgramFiles(x86)")
        if pf:
            out.append(Path(pf) / "Steam")
        return out
    return [
        home / ".steam" / "steam",
        home / ".steam" / "debian-installation",
        home / ".local" / "share" / "Steam",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]


def _read_registry() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
        return str(value)
    except OSError:
        return None


def find_steam_root(*, home: Path, platform: str, environ: Mapping[str, str],
                    read_registry: Callable[[], str | None] | None = None) -> Path | None:
    for cand in steam_root_candidates(home=home, platform=platform, environ=environ, read_registry=read_registry):
        if (cand / "steamapps").is_dir():
            return cand
    return None


def _read_vdf(path: Path) -> dict | None:
    try:
        return parse_vdf(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def libraries(steam_root: Path) -> list[Path]:
    """The Steam root plus every library listed in its libraryfolders.vdf, without duplicates."""
    if not (steam_root / "steamapps").is_dir():
        return []
    found = [steam_root]
    data = _read_vdf(steam_root / "steamapps" / "libraryfolders.vdf") or {}
    folders = next((v for k, v in data.items() if k.lower() == "libraryfolders"), {})
    for entry in folders.values() if isinstance(folders, dict) else []:
        raw = entry.get("path") if isinstance(entry, dict) else entry  # older files list paths directly
        if isinstance(raw, str) and raw:
            p = Path(raw)
            if p not in found:
                found.append(p)
    return found


def app_manifest(library: Path, app_id: int) -> dict | None:
    """`installdir` and `buildid` (strings) from the library's appmanifest, or None."""
    data = _read_vdf(library / "steamapps" / f"appmanifest_{app_id}.acf")
    if not data:
        return None
    state = next((v for k, v in data.items() if k.lower() == "appstate"), None)
    if not isinstance(state, dict) or not isinstance(state.get("installdir"), str):
        return None
    return {"installdir": state["installdir"], "buildid": state.get("buildid")}


def app_dir(libs: list[Path], app_id: int) -> Path | None:
    """Install directory of an app, searched across all libraries."""
    for lib in libs:
        manifest = app_manifest(lib, app_id)
        if manifest:
            d = lib / "steamapps" / "common" / manifest["installdir"]
            if d.is_dir():
                return d
    return None
