"""Game and SDK versions read from installed assemblies (never hard-coded)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import EnvError

if TYPE_CHECKING:
    from .core import SE2Env

# `[assembly: Bundle(name, version)]` custom-attribute blob: prolog 01 00, length-prefixed name and version, 00 00.
_BUNDLE_RE = re.compile(rb"\x01\x00(\x05VRage|\x05Game2)([\x01-\x20])([0-9.]+)\x00\x00")

SYSTEM_RUNTIME_BUNDLE = "1.0.0.0"


def bundle_versions_from_dlls(*dlls: Path) -> dict[str, str]:
    """Read the Bundle attribute versions out of .NET assemblies without loading them; first DLL wins per name."""
    found: dict[str, str] = {}
    for dll in dlls:
        if not dll.exists():
            continue
        data = dll.read_bytes()
        for name, ln, ver in _BUNDLE_RE.findall(data):
            if len(ver) == ln[0]:
                found.setdefault(name[1:].decode(), ver.decode())
    return found


def game_version(game_dir: Path | None) -> str | None:
    """The installed game's Game2 bundle version (VRage's if Game2 has none), or None."""
    if game_dir is None:
        return None
    found = bundle_versions_from_dlls(game_dir / "Game2" / "Game2.Simulation.dll", game_dir / "Game2" / "VRage.Core.dll")
    return found.get("Game2") or found.get("VRage")


def bundle_stamps(env: SE2Env) -> tuple[dict[str, str], dict[str, str]]:
    """`(vrage, game)` bundle stamps the Mod HUB writes, read from the SDK Editor's VRage and Game2 assemblies."""
    ed = env.sdk_editor
    if ed is None or not ed.is_dir():
        raise EnvError("Mod SDK not found; set --sdk, SE2_SDK_ASSETS or config sdk.")
    found = bundle_versions_from_dlls(ed / "VRage.Core.dll", ed / "Game2.Simulation.dll")
    if "Game2" not in found and env.game_dir is not None:
        found = {**bundle_versions_from_dlls(env.game_dir / "Game2" / "Game2.Simulation.dll"), **found}
    if "VRage" not in found or "Game2" not in found:
        raise EnvError(f"Mod SDK not usable: no VRage/Game2 bundle version found in {ed}; reinstall or verify the SDK.")
    vrage = {"System.Runtime": SYSTEM_RUNTIME_BUNDLE, "VRage": found["VRage"]}
    return vrage, {"Game2": found["Game2"], **vrage}
