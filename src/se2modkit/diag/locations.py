"""Where diag looks for things. Every location comes from `SE2Env` (discovered once per command) or from a
diag-specific environment variable; there is no hard-coded home path here.

Feature gating: Proton log/prefix features need Linux and a discovered prefix; Pulsar-log features need a discovered
Pulsar (any OS); host sampling reads `/proc` and needs Linux. When a feature cannot work, `NotSupported` carries one
plain sentence starting "not supported here:".
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ..env.core import SE2Env, discover_env, override_hint

PROBES_DIR = Path(__file__).resolve().parent / "probes"
DEFAULT_MCP_URL = "http://localhost:6789/"

_ENV: SE2Env | None = None


class NotSupported(RuntimeError):
    """A feature that cannot work on this machine; the message is one line starting "not supported here:"."""

    def __init__(self, reason: str):
        super().__init__(f"not supported here: {reason}")


def _discover() -> SE2Env:
    return discover_env()


def _environ() -> Mapping[str, str]:
    return os.environ


def env() -> SE2Env:
    """The environment for this command, discovered on first use."""
    global _ENV
    if _ENV is None:
        _ENV = _discover()
    return _ENV


def set_env(e: SE2Env | None) -> None:
    """Use `e` from now on (`None`: discover again on next use)."""
    global _ENV
    _ENV = e


def _override(name: str) -> Path | None:
    v = _environ().get(name)
    return Path(v).expanduser() if v else None


def is_linux() -> bool:
    return env().platform.startswith("linux")


def process_name() -> str:
    return _environ().get("SE2_PROCESS_NAME") or "Modern.bin"


def require_linux(what: str) -> None:
    if not is_linux():
        raise NotSupported(f"{what} reads /proc, which only Linux has")


def log_dir() -> Path | None:
    """The game's log folder (`SE2_LOG_DIR`, config `logs_dir`, or discovered); None if not found."""
    return env().logs_dir


def proton_log_dir() -> Path:
    """Game logs inside the Proton prefix (Linux only)."""
    over = _override("SE2_PROTON_LOG_DIR")
    if over:
        return over
    e = env()
    if not e.platform.startswith("linux"):
        raise NotSupported("Proton game logs exist only on Linux; on this OS use the game's own log folder")
    if e.prefix is None:
        raise NotSupported(f"no Proton prefix found for the game; set {override_hint('prefix')}")
    return e.prefix / "drive_c" / "users" / "steamuser" / "AppData" / "Roaming" / "SpaceEngineers2" / "Temp" / "Logs"


def pulsar_info_log() -> Path:
    """Pulsar's launch log, next to its `Modern` build (`SE2_PULSAR_LOG` overrides)."""
    over = _override("SE2_PULSAR_LOG")
    if over:
        return over
    pulsar = env().pulsar
    if pulsar is None:
        raise NotSupported(f"Pulsar not found; set {override_hint('pulsar')} (or SE2_PULSAR_LOG)")
    base = pulsar.parent if pulsar.suffix or pulsar.is_file() else pulsar
    return base / "Modern" / "info.log"


def mcp_url() -> str:
    return _environ().get("SE2_MCP_URL") or DEFAULT_MCP_URL


def previews_dir() -> Path:
    """Screenshots: `SE2_DIAG_PREVIEWS`, else `$SE2_WORKSPACE/resources/previews/diag`, else the user cache."""
    over = _override("SE2_DIAG_PREVIEWS")
    if over:
        return over
    ws = _environ().get("SE2_WORKSPACE")
    if ws:
        return Path(ws) / "resources" / "previews" / "diag"
    return env().cache_dir / "diag" / "previews"


def output_dir() -> Path:
    """Host captures (traces, counters, gcdumps): `SE2_DIAG_OUT`, else the user cache (outside every git repo)."""
    return _override("SE2_DIAG_OUT") or env().cache_dir / "diag"


def dotnet_tools_dir() -> Path | None:
    """Where `dotnet tool install --global` puts tools: `$DOTNET_CLI_HOME` or the user's home, then `.dotnet/tools`."""
    ev = _environ()
    base = ev.get("DOTNET_CLI_HOME") or ev.get("HOME") or ev.get("USERPROFILE")
    return Path(base) / ".dotnet" / "tools" if base else None
