"""A synthetic `SE2Env` over a tmp tree: a Steam library, the game, the SDK, a Proton prefix and a mods folder."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from se2modkit.env.core import SE2Env


def make_mod(mods_root: Path, name: str, *, scripts: bool = False) -> Path:
    d = mods_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.vrgproj").write_text(json.dumps({"$Value": {"Name": name}}), encoding="utf-8")
    if scripts:
        (d / "Scripts").mkdir(exist_ok=True)
    return d


def make_env(tmp_path: Path, platform: str = "linux", **changes) -> SE2Env:
    """Library `Steam Library` (Wine drive S: on Linux), prefix with C: and Z:, mods under `mods root`."""
    lib = tmp_path / "Steam Library"
    game = lib / "steamapps" / "common" / "SpaceEngineers2"
    sdk = lib / "steamapps" / "common" / "Space Engineers 2 - Mod SDK"
    for d in (game / "Game2", game / "GameData", game / "VRage" / "GameData",
              sdk / "GameData" / "Vanilla" / "Assets", sdk / "GameData" / "Engine", sdk / "Editor"):
        d.mkdir(parents=True, exist_ok=True)
    linux = platform.startswith("linux")
    prefix = None
    if linux:
        prefix = tmp_path / "compatdata" / "1133870" / "pfx"
        (prefix / "drive_c" / "users" / "steamuser" / "Documents" / "SpaceEngineers2").mkdir(parents=True)
        dd = prefix / "dosdevices"
        dd.mkdir()
        if os.name != "nt":   # drive links named "c:" cannot exist on Windows: the prefix there maps no Wine drives
            os.symlink("../drive_c", dd / "c:")
            os.symlink(str(lib), dd / "s:")
            os.symlink("/", dd / "z:")
        mods_dir = prefix / "drive_c" / "users" / "steamuser" / "Documents" / "SpaceEngineers2" / "Mods"
    else:
        mods_dir = tmp_path / "Users" / "Jörg" / "Documents" / "SpaceEngineers2" / "Mods"
        mods_dir.parent.mkdir(parents=True)
    env = SE2Env(
        platform=platform, steam_root=lib, libraries=(lib,), game_dir=game, sdk_dir=sdk,
        sdk_assets=sdk / "GameData" / "Vanilla" / "Assets", sdk_editor=sdk / "Editor", game_mods_dir=mods_dir,
        prefix=prefix, proton=None, pulsar=None, blender=None, logs_dir=None, decompiled_dir=None,
        cache_dir=tmp_path / "cache dir", config_dir=tmp_path / "config",
        game_version="4.0.0.3155", game_buildid="100", sdk_buildid="200",
    )
    return dataclasses.replace(env, **changes) if changes else env
