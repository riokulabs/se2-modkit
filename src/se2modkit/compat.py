"""Game version compatibility: which builds this release was tested against."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources


class UntestedGameError(RuntimeError):
    """Raised in strict mode when the installed game is untested or older than the minimum."""


@dataclass(frozen=True)
class Compat:
    game_versions_tested: tuple[str, ...]
    game_min: str
    sdk_buildid_tested: int


def load_compat() -> Compat:
    raw = tomllib.loads(resources.files("se2modkit").joinpath("compat.toml").read_text(encoding="utf-8"))
    return Compat(
        game_versions_tested=tuple(raw["game_versions_tested"]),
        game_min=raw["game_min"],
        sdk_buildid_tested=int(raw["sdk_buildid_tested"]),
    )


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(part) for part in v.strip().split("."))


def check_game(installed: str | None, compat: Compat, *, strict: bool = False) -> list[str]:
    """Return warnings about the installed game build; raise UntestedGameError when strict and it is not usable."""
    hint = "run `se2mod deps check`"
    tested = ", ".join(compat.game_versions_tested)
    if installed is None:
        msg = f"Installed game version is unknown (tested: {tested}); {hint}."
        problem = False
    else:
        try:
            below_min = version_tuple(installed) < version_tuple(compat.game_min)
        except ValueError:
            below_min = False
        if installed in compat.game_versions_tested:
            return []
        if below_min:
            msg = f"Game {installed} is older than the minimum {compat.game_min} (tested: {tested}); {hint}."
        else:
            msg = f"Game {installed} is not one of the tested builds ({tested}); {hint}."
        problem = True
    if strict and (problem or installed is None):
        raise UntestedGameError(msg)
    return [msg]
