"""User config location and the flag > env > config > discovery precedence."""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

class ConfigError(Exception):
    """The user config file cannot be read; reported as one plain sentence."""


Source = Literal["flag", "env", "config", "discovered", "missing"]


@dataclass(frozen=True)
class Resolved:
    value: Path | str | None
    source: Source


def user_config_dir(*, platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
                    home: Path | None = None) -> Path:
    home = Path.home() if home is None else home
    if platform.startswith("win"):
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "se2modkit"
    xdg = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else home / ".config"
    return base / "se2modkit"


def user_cache_dir(*, platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
                   home: Path | None = None) -> Path:
    home = Path.home() if home is None else home
    if platform.startswith("win"):
        local = environ.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return base / "se2modkit" / "Cache"
    xdg = environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else home / ".cache"
    return base / "se2modkit"


def load_user_config(path: Path | None = None, *, environ: Mapping[str, str] = os.environ) -> dict:
    """Load the user config TOML; `SE2MODKIT_CONFIG` overrides the path; a missing file gives {}."""
    override = environ.get("SE2MODKIT_CONFIG")
    if override:
        path = Path(override)
    elif path is None:
        path = user_config_dir(environ=environ) / "config.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Cannot parse user config {path}: {e}.") from e


def resolve(
    *,
    flag: object | None,
    env_names: tuple[str, ...],
    config: dict,
    config_key: str,
    discover: Callable[[], object | None],
    environ: Mapping[str, str] | None = None,
) -> Resolved:
    """First of: CLI flag, env var (first alias set wins), user config, discovery; else missing."""
    if flag is not None:
        return Resolved(_coerce(flag), "flag")
    env = os.environ if environ is None else environ
    for name in env_names:
        val = env.get(name)
        if val:
            return Resolved(val, "env")
    if config.get(config_key) not in (None, ""):
        return Resolved(_coerce(config[config_key]), "config")
    found = discover()
    if found is not None:
        return Resolved(_coerce(found), "discovered")
    return Resolved(None, "missing")


def _coerce(v: object) -> Path | str:
    return v if isinstance(v, (Path, str)) else str(v)
