"""`se2mod env`: every discovered path and version, with where it came from."""

from __future__ import annotations

import dataclasses
import json
from importlib import metadata
from pathlib import Path

from .. import __version__
from ..env.core import SE2Env, override_hint
from . import register

_OVERRIDE_SOURCES = {"flag", "env", "config"}
_LINUX_ONLY = {"prefix", "proton"}


def install_kind() -> str:
    """`editable` (a development checkout), `installed`, or `source` (running from a tree, not installed)."""
    try:
        dist = metadata.distribution("se2-modkit")
    except metadata.PackageNotFoundError:
        return "source"
    try:
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        direct = {}
    return "editable" if (direct.get("dir_info") or {}).get("editable") else "installed"


def _show(value: object) -> str:
    if isinstance(value, tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def _line(env: SE2Env, name: str) -> str:
    value, source = getattr(env, name), env.sources.get(name, "discovered")
    if value in (None, ()):
        if name in _LINUX_ONLY and not env.platform.startswith("linux"):
            return f"{name}: not used on this OS"
        return f"{name}: missing (set {override_hint(name)})"
    text = f"{name}: {_show(value)} [{source}]"
    if source in _OVERRIDE_SOURCES and isinstance(value, Path) and not value.exists():
        text += " (does not exist)"
    return text


def _unresolved_proton(env: SE2Env) -> str | None:
    if not env.platform.startswith("linux") or env.steam_root is None or env.sources.get("proton") in _OVERRIDE_SOURCES:
        return None
    from ..env import proton

    name = proton.unresolved_mapping(env.steam_root, env.libraries or ())
    if name is None:
        return None
    using = env.proton if env.proton else "no Proton build"
    return f"note: Steam maps Proton '{name}' to the game but it could not be resolved; using {using}"


def _add_args(parser) -> None:
    pass


@register("env", "Show the discovered environment and where each value came from", add_args=_add_args)
def run(args, env: SE2Env) -> int:
    print(f"modkit: {__version__} ({install_kind()})")
    for f in dataclasses.fields(SE2Env):
        if f.name != "sources":
            print(_line(env, f.name))
    note = _unresolved_proton(env)
    if note:
        print(note)
    return 0
