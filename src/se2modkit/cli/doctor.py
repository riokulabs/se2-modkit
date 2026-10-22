"""`se2mod doctor`: check each external piece the tooling needs and say how to fix what is missing."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..env.core import SE2Env, override_hint
from . import register


def _addon_roots(env: SE2Env) -> list[Path]:
    home = Path.home()
    if env.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / "Blender Foundation" / "Blender" if appdata else home / "AppData/Roaming/Blender Foundation/Blender"
    elif env.platform == "darwin":
        base = home / "Library/Application Support/Blender"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(xdg) if xdg else home / ".config") / "blender"
    out: list[Path] = []
    try:
        for ver in base.iterdir():
            out += [ver / "scripts" / "addons", ver / "extensions"]
    except OSError:
        pass
    return out


def find_vrage_addon(env: SE2Env) -> Path | None:
    """A user-installed Blender add-on whose name mentions VRAGE (folder, module file or extension)."""
    for root in _addon_roots(env):
        try:
            for p in root.rglob("*"):
                if "vrage" in p.name.lower() and (p.is_dir() or p.suffix == ".py"):
                    return p
        except OSError:
            continue
    return None


def _checks(env: SE2Env) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    out.append(("Blender", f"ok ({env.blender})" if env.blender else f"missing (set {override_hint('blender')})"))
    addon = find_vrage_addon(env)
    out.append(("VRAGE Tools add-on", f"ok ({addon})" if addon else "not found in the Blender user add-ons "
                "(install it from the Mod SDK's Blender tools; needed to export models)"))
    if env.sdk_dir is None:
        out.append(("Mod SDK", f"missing (set {override_hint('sdk_dir')})"))
    else:
        ver = f", build {env.sdk_buildid}" if env.sdk_buildid else ""
        out.append(("Mod SDK", f"ok ({env.sdk_dir}{ver})"))
    standin = env.cache_dir / "standin" / "standin.json"
    out.append(("Standin", f"ok ({standin.parent})" if standin.is_file()
                else "not built yet (only needed to build mods; created by `se2mod sdk standin`)"))
    v = sys.version_info
    out.append(("Python", f"ok ({v.major}.{v.minor}.{v.micro})" if v >= (3, 11)
                else f"too old ({v.major}.{v.minor}); modkit needs 3.11 or newer"))
    return out


@register("doctor", "Check Blender, the VRAGE Tools add-on, the Mod SDK, the standin tree and Python")
def run(args, env: SE2Env) -> int:
    for label, status in _checks(env):
        print(f"{label}: {status}")
    return 0
