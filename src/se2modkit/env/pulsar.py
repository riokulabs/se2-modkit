"""Pulsar, the launcher that runs native Space Engineers 2 (its `Modern` build)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def pulsar_default(*, platform: str, environ: Mapping[str, str], home: Path | None = None) -> Path | None:
    """The default-rule location if it exists. Linux: `$XDG_CONFIG_HOME/Pulsar/Modern.bin` (`~/.config` if unset).

    Windows has no default rule until the install location is confirmed: set `SE2_PULSAR` or config `pulsar`.
    """
    if not platform.startswith("linux"):
        return None
    xdg = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (home if home is not None else Path.home()) / ".config"
    cand = base / "Pulsar" / "Modern.bin"
    return cand if cand.is_file() else None


def find_pulsar(*, platform: str, environ: Mapping[str, str], config: Mapping[str, object],
                home: Path | None = None) -> Path | None:
    """`SE2_PULSAR`, then config `pulsar`, then the platform default rule."""
    if environ.get("SE2_PULSAR"):
        return Path(environ["SE2_PULSAR"])
    if config.get("pulsar"):
        return Path(str(config["pulsar"]))
    return pulsar_default(platform=platform, environ=environ, home=home)
