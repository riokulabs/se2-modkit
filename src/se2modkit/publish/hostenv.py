"""What the publisher needs from `se2modkit.env`: the builder's Wine paths, the HUB's bundle stamps, the game's VRage
version and a Steamworks redistributable for the real upload. Discovery itself lives in `se2modkit.env`."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..env.core import SE2Env
from ..env.procs import GAME_PROCS, HUB_PROCS, TEXCONV, find_processes
from ..env.steam import GAME_APP_ID, SDK_APP_ID
from ..env.versions import SYSTEM_RUNTIME_BUNDLE, bundle_versions_from_dlls
from .project import Refusal

__all__ = ["GAME_APP_ID", "GAME_PROCS", "HUB_PROCS", "SDK_APP_ID", "TEXCONV", "BuildEnv", "build_env",
           "find_libsteam_api", "find_processes", "hub_bundles"]


@dataclass(frozen=True)
class BuildEnv:
    """The install paths the SDK builder run needs (Linux/Proton)."""
    steamapps: Path             # the library holding the game's Proton prefix (the sandbox overlays it)
    game_dir: Path
    sdk_dir: Path
    prefix: Path                # Proton prefix shared by the game and the SDK
    proton: Path                # the Proton build directory

    @property
    def sdk_editor(self) -> Path:
        return self.sdk_dir / "Editor"

    @property
    def builder_exe(self) -> Path:
        return self.sdk_editor / "VRage.ContentPipeline.Builder.exe"

    @property
    def wine(self) -> Path:
        return self.proton / "files/bin/wine"

    @property
    def wineserver(self) -> Path:
        return self.proton / "files/bin/wineserver"


def _missing(env: SE2Env, *fields: str) -> list[str]:
    return [f for f in fields if getattr(env, f) is None]


def build_env(env: SE2Env) -> BuildEnv:
    """The builder's paths, or a Refusal naming what is missing and how to set it."""
    from ..env.core import override_hint
    missing = _missing(env, "game_dir", "sdk_dir", "prefix", "proton")
    if missing:
        raise Refusal("cannot run the SDK builder: " + "; ".join(f"{m} not found (set {override_hint(m)})"
                                                                  for m in missing))
    steamapps = env.prefix.parents[2]       # <library>/steamapps/compatdata/<app>/pfx
    return BuildEnv(steamapps, env.game_dir, env.sdk_dir, env.prefix, env.proton)


def hub_bundles(env: SE2Env) -> dict[str, str]:
    """Bundles the HUB would stamp: the SDK Editor's VRage (+ Game2) assemblies, else the game's, plus
    System.Runtime."""
    found: dict[str, str] = {}
    if env.sdk_editor is not None:
        found = bundle_versions_from_dlls(env.sdk_editor / "VRage.Core.dll", env.sdk_editor / "Game2.Simulation.dll")
    if "VRage" not in found and env.game_dir is not None:
        found = bundle_versions_from_dlls(env.game_dir / "Game2/VRage.Core.dll",
                                          env.game_dir / "Game2/Game2.Simulation.dll")
    found["System.Runtime"] = SYSTEM_RUNTIME_BUNDLE
    return found


def find_libsteam_api(env: SE2Env, environ=os.environ) -> Path | None:
    """A native Linux libsteam_api.so (the Steamworks redistributable) already on this machine.

    Order: $SE2_LIBSTEAM_API, then the copies next to Pulsar (the native SE2 launcher)."""
    cands: list[Path] = []
    if environ.get("SE2_LIBSTEAM_API"):
        cands.append(Path(environ["SE2_LIBSTEAM_API"]))
    if env.pulsar is not None:
        base = env.pulsar.parent if env.pulsar.is_file() else env.pulsar
        cands += [base / "Libraries/Modern/libsteam_api.so", base / "Libraries/Interim/libsteam_api.so"]
    return next((c for c in cands if c.is_file()), None)
