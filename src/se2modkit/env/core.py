"""`SE2Env`: every install path and version modkit needs, discovered once (flag > env > config > discovery)."""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import versions
from .blender import find_blender
from .config import Resolved, load_user_config, resolve, user_cache_dir, user_config_dir
from .errors import EnvError
from .pulsar import pulsar_default
from .steam import GAME_APP_ID, SDK_APP_ID, app_dir, app_manifest, find_steam_root, libraries

__all__ = ["EnvError", "SE2Env", "discover_env", "override_hint", "require"]

_OVERRIDES = {"discovered", "missing"}  # sources that are not user overrides


@dataclass(frozen=True)
class SE2Env:
    platform: str
    steam_root: Path | None
    libraries: tuple[Path, ...] | None
    game_dir: Path | None
    sdk_dir: Path | None
    sdk_assets: Path | None
    sdk_editor: Path | None
    game_mods_dir: Path | None
    prefix: Path | None
    proton: Path | None
    pulsar: Path | None
    blender: Path | None
    logs_dir: Path | None
    decompiled_dir: Path | None
    cache_dir: Path
    config_dir: Path
    game_version: str | None
    game_buildid: str | None
    sdk_buildid: str | None
    sources: dict[str, str] = field(default_factory=dict, hash=False, compare=False)


# field -> (what it is, how to set it)
_STEAM = "--steam-root, SE2_STEAM_ROOT or config steam_root"
_SDK = "--sdk, SE2_SDK_ASSETS or config sdk"
_HINTS: dict[str, tuple[str, str]] = {
    "steam_root": ("Steam", _STEAM),
    "libraries": ("No Steam library", _STEAM),
    "game_dir": ("Space Engineers 2", "--steam-root, SE2_STEAM_ROOT or config game_dir"),
    "sdk_dir": ("Mod SDK", _SDK),
    "sdk_assets": ("Mod SDK Vanilla assets", _SDK),
    "sdk_editor": ("Mod SDK Editor", _SDK),
    "game_mods_dir": ("Game mods folder", "config game_mods_dir"),
    "prefix": ("Proton prefix", "SE2_STEAMAPPS or config prefix"),
    "proton": ("Proton build", "--proton, SE2_PROTON or config proton"),
    "pulsar": ("Pulsar", "--pulsar, SE2_PULSAR or config pulsar"),
    "blender": ("Blender", "--blender, SE2_BLENDER, BLENDER or config blender"),
    "logs_dir": ("Game logs folder", "SE2_LOG_DIR or config logs_dir"),
    "decompiled_dir": ("Decompiled game tree", "SE2_DECOMPILED or config decompiled_dir"),
    "game_version": ("Game version", "--steam-root, SE2_STEAM_ROOT or config game_dir"),
    "game_buildid": ("Game build id", "--steam-root, SE2_STEAM_ROOT or config game_dir"),
    "sdk_buildid": ("Mod SDK build id", _SDK),
}
_LINUX_ONLY = {"prefix", "proton"}


def override_hint(name: str) -> str:
    """How to set a field, e.g. `"--sdk, SE2_SDK_ASSETS or config sdk"`."""
    return _HINTS[name][1]


def require(env: SE2Env, *fields: str) -> None:
    """Raise `EnvError` with one plain sentence per missing field, naming how to set it."""
    known = {f.name for f in dataclasses.fields(SE2Env)}
    bad = [f for f in fields if f not in known or f not in _HINTS]
    if bad:
        raise ValueError(f"unknown SE2Env field(s): {', '.join(bad)}")
    sentences = []
    for name in fields:
        if getattr(env, name) not in (None, ()):
            continue
        what, hint = _HINTS[name]
        if name in _LINUX_ONLY and not env.platform.startswith("linux"):
            sentences.append(f"The {what} is only used on Linux; on this OS the SDK tools run natively.")
        else:
            sentences.append(f"{what} not found ({name}); set {hint}.")
    if sentences:
        raise EnvError(" ".join(sentences))


def _path(v: object | None) -> Path | None:
    if v is None:
        return None
    return v if isinstance(v, Path) else Path(str(v)).expanduser()


def _sdk_root(p: Path) -> tuple[Path, Path | None]:
    """Accept the SDK dir or its `GameData/Vanilla/Assets` (what `SE2_SDK_ASSETS` historically named)."""
    if len(p.parts) > 3 and [s.lower() for s in p.parts[-3:]] == ["gamedata", "vanilla", "assets"]:
        return p.parents[2], p
    return p, None


def _buildid(libs: tuple[Path, ...] | None, app_id: int, d: Path | None) -> str | None:
    if d is None:
        return None
    for lib in libs or ():
        m = app_manifest(lib, app_id)
        if m and lib / "steamapps" / "common" / m["installdir"] == d and m.get("buildid"):
            return str(m["buildid"])
    return None


def discover_env(
    *,
    flags: Mapping[str, str | None] | None = None,
    environ: Mapping[str, str] = os.environ,
    platform: str = sys.platform,
    home: Path | None = None,
    config: Mapping[str, object] | None = None,
    sysroot: Path = Path("/"),
) -> SE2Env:
    """Discover the environment. Missing pieces are `None` with `sources[field] == "missing"`; never raises for them.

    `flags` keys: `steam_root`, `sdk`, `game`, `proton`, `pulsar`, `blender`. `config` defaults to the user config
    file; `sysroot` roots the system-wide default locations (tests).
    """
    fl = {k: v for k, v in (flags or {}).items() if v not in (None, "")}
    home = Path.home() if home is None else home
    if config is None:
        config = load_user_config(user_config_dir(platform=platform, environ=environ, home=home) / "config.toml",
                                  environ=environ)
    cfg = dict(config)
    linux = platform.startswith("linux")
    src: dict[str, str] = {"platform": "discovered"}

    def take(name: str, r: Resolved) -> Path | None:
        src[name] = r.source
        return _path(r.value)

    def derived(name: str, parent: str, value: Path | None) -> Path | None:
        """A path under another field: inherits its source; a discovered one must exist."""
        if value is None or (src[parent] == "discovered" and not value.exists()):
            src[name] = "missing"
            return None
        src[name] = src[parent]
        return value

    def found(name: str, value: object | None) -> object | None:
        src[name] = "missing" if value is None else "discovered"
        return value

    # Steam root; the legacy SE2_STEAMAPPS names the root's steamapps.
    env_view = dict(environ)
    if not env_view.get("SE2_STEAM_ROOT") and env_view.get("SE2_STEAMAPPS"):
        env_view["SE2_STEAM_ROOT"] = str(Path(env_view["SE2_STEAMAPPS"]).parent)

    def steam() -> Path | None:
        root = find_steam_root(home=home, platform=platform, environ=environ)
        return root.resolve() if root else None

    steam_root = take("steam_root", resolve(flag=fl.get("steam_root"), env_names=("SE2_STEAM_ROOT",), config=cfg,
                                            config_key="steam_root", discover=steam, environ=env_view))
    libs = found("libraries", tuple(libraries(steam_root)) or None if steam_root else None)

    game_dir = take("game_dir", resolve(flag=fl.get("game"), env_names=(), config=cfg, config_key="game_dir",
                                        discover=lambda: app_dir(list(libs or ()), GAME_APP_ID), environ=environ))
    sdk_given = take("sdk_dir", resolve(flag=fl.get("sdk"), env_names=("SE2_SDK_ASSETS",), config=cfg,
                                        config_key="sdk", discover=lambda: app_dir(list(libs or ()), SDK_APP_ID),
                                        environ=environ))
    sdk_dir, assets_given = _sdk_root(sdk_given) if sdk_given else (None, None)
    sdk_assets = derived("sdk_assets", "sdk_dir",
                         assets_given or (sdk_dir / "GameData" / "Vanilla" / "Assets" if sdk_dir else None))
    sdk_editor = derived("sdk_editor", "sdk_dir", sdk_dir / "Editor" if sdk_dir else None)

    prefix = proton_dir = None
    if linux:
        from . import proton  # Linux only: never imported elsewhere

        def find_prefix() -> Path | None:
            steamapps = [Path(environ["SE2_STEAMAPPS"])] if environ.get("SE2_STEAMAPPS") else []
            if game_dir is not None and game_dir.parent.name == "common":
                steamapps.append(game_dir.parent.parent)
            steamapps += [lib / "steamapps" for lib in libs or ()]
            return next((p for p in map(proton.prefix_for, steamapps) if p.is_dir()), None)

        prefix = take("prefix", resolve(flag=None, env_names=(), config=cfg, config_key="prefix",
                                        discover=find_prefix, environ=environ))
        r = resolve(flag=fl.get("proton"), env_names=("SE2_PROTON",), config=cfg, config_key="proton",
                    discover=lambda: proton.proton_build(steam_root, configured=None, libraries=libs or ())
                    if steam_root else None,
                    environ=environ)
        if r.source not in _OVERRIDES and steam_root is not None:
            r = Resolved(proton.proton_build(steam_root, configured=str(r.value),
                                                    libraries=libs or ()), r.source)
        proton_dir = take("proton", r)

        def mods_default() -> Path | None:
            return proton.game_mods_dir_proton(prefix) if prefix else None
    else:
        src["prefix"] = src["proton"] = "missing"

        def mods_default() -> Path | None:
            profile = environ.get("USERPROFILE")
            return Path(profile) / "Documents" / "SpaceEngineers2" / "Mods" if profile else None

    game_mods_dir = take("game_mods_dir", resolve(flag=None, env_names=(), config=cfg, config_key="game_mods_dir",
                                                  discover=mods_default, environ=environ))
    pulsar = take("pulsar", resolve(flag=fl.get("pulsar"), env_names=("SE2_PULSAR",), config=cfg,
                                    config_key="pulsar", environ=environ,
                                    discover=lambda: pulsar_default(platform=platform, environ=environ, home=home)))
    blender = take("blender", find_blender(flag=fl.get("blender"), platform=platform, environ=environ, config=cfg,
                                           home=home, sysroot=sysroot, steam_libraries=libs or ()))

    def find_logs() -> Path | None:
        if platform.startswith("win"):
            appdata = environ.get("APPDATA")
            cands = [Path(appdata) / "SpaceEngineers2" / "Temp" / "Logs"] if appdata else []
        else:
            xdg = environ.get("XDG_CONFIG_HOME")
            cands = [(Path(xdg) if xdg else home / ".config") / "SpaceEngineers2" / "Temp" / "Logs"]  # native
            if prefix is not None:
                cands.append(prefix / "drive_c" / "users" / "steamuser" / "AppData" / "Roaming" / "SpaceEngineers2"
                             / "Temp" / "Logs")
        return next((c for c in cands if c.is_dir()), None)

    logs_dir = take("logs_dir", resolve(flag=None, env_names=("SE2_LOG_DIR",), config=cfg, config_key="logs_dir",
                                        discover=find_logs, environ=environ))
    decompiled_dir = take("decompiled_dir", resolve(flag=None, env_names=("SE2_DECOMPILED",), config=cfg,
                                                    config_key="decompiled_dir", discover=lambda: None,
                                                    environ=environ))
    src["cache_dir"] = src["config_dir"] = "discovered"

    return SE2Env(
        platform=platform,
        steam_root=steam_root,
        libraries=libs,
        game_dir=game_dir,
        sdk_dir=sdk_dir,
        sdk_assets=sdk_assets,
        sdk_editor=sdk_editor,
        game_mods_dir=game_mods_dir,
        prefix=prefix,
        proton=proton_dir,
        pulsar=pulsar,
        blender=blender,
        logs_dir=logs_dir,
        decompiled_dir=decompiled_dir,
        cache_dir=user_cache_dir(platform=platform, environ=environ, home=home),
        config_dir=user_config_dir(platform=platform, environ=environ, home=home),
        game_version=found("game_version", versions.game_version(game_dir)),
        game_buildid=found("game_buildid", _buildid(libs, GAME_APP_ID, game_dir)),
        sdk_buildid=found("sdk_buildid", _buildid(libs, SDK_APP_ID, sdk_dir)),
        sources=src,
    )
