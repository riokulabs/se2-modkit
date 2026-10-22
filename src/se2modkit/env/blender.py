"""Locate a Blender executable."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from .config import Resolved, resolve

_VER_RE = re.compile(r"(\d+)")


def _version_key(d: Path) -> list[int]:
    return [int(n) for n in _VER_RE.findall(d.name)]


def _candidates(*, platform: str, environ: Mapping[str, str], home: Path, sysroot: Path,
                steam_libraries: Iterable[Path], glob: Callable[[Path, str], Iterable[Path]]) -> list[Path]:
    if platform.startswith("win"):
        out: list[Path] = []
        pf = environ.get("ProgramFiles")
        if pf:
            found = [p for p in glob(Path(pf) / "Blender Foundation", "Blender */blender.exe") if p.is_file()]
            out += sorted(found, key=lambda p: _version_key(p.parent), reverse=True)
        out += [lib / "steamapps" / "common" / "Blender" / "blender.exe" for lib in steam_libraries]
        return out
    if platform == "darwin":
        return [sysroot / "Applications" / "Blender.app" / "Contents" / "MacOS" / "Blender",
                *(lib / "steamapps" / "common" / "Blender" / "Blender.app" / "Contents" / "MacOS" / "Blender"
                  for lib in steam_libraries)]
    return [
        sysroot / "snap" / "bin" / "blender",
        sysroot / "usr" / "bin" / "blender",
        sysroot / "var" / "lib" / "flatpak" / "exports" / "bin" / "org.blender.Blender",
        home / ".local" / "share" / "flatpak" / "exports" / "bin" / "org.blender.Blender",
        *(lib / "steamapps" / "common" / "Blender" / "blender" for lib in steam_libraries),
    ]


def find_blender(*, flag: object | None, platform: str, environ: Mapping[str, str], config: Mapping[str, object],
                 which: Callable[..., str | None] = shutil.which, glob: Callable[..., Iterable[Path]] = Path.glob,
                 home: Path | None = None, sysroot: Path = Path("/"),
                 steam_libraries: Iterable[Path] = ()) -> Resolved:
    """`--blender`, `SE2_BLENDER`/`BLENDER`, config `blender`, PATH, then the OS default locations."""
    libs = list(steam_libraries)

    def discover() -> Path | None:
        path = environ.get("PATH", "")
        for name in ("blender.exe", "blender") if platform.startswith("win") else ("blender",):
            hit = which(name, path=path)
            if hit:
                return Path(hit)
        for cand in _candidates(platform=platform, environ=environ, home=home if home is not None else Path.home(),
                                sysroot=sysroot, steam_libraries=libs, glob=glob):
            if cand.is_file():
                return cand
        return None

    return resolve(flag=flag, env_names=("SE2_BLENDER", "BLENDER"), config=dict(config), config_key="blender",
                   discover=discover, environ=environ)
