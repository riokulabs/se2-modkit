"""`style-waivers.toml`: a mod's reasoned exceptions to its style profile.

    [[waiver]]
    block = "MyMod_Cabinet"
    key = "paint.colorable_min_share"
    reason = "dark equipment cabinet: its vanilla analogue is mostly Darker"
    date = 2026-10-21

Every waiver needs a block, a key, a reason and a date (a TOML date or `YYYY-MM-DD`); one waiver per block + key.
A waived finding is still printed (with its reason), and a waiver that matches no finding is listed as unused.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path

FILE_NAME = "style-waivers.toml"


class WaiverError(Exception):
    """A waiver file problem, reported to the user as one plain sentence."""


@dataclass(frozen=True)
class Waiver:
    block: str
    key: str
    reason: str
    date: dt.date

    @property
    def label(self) -> str:
        return f"{self.reason} ({self.date.isoformat()})"


@dataclass(frozen=True)
class Waivers:
    items: tuple[Waiver, ...] = ()

    def find(self, block: str, key: str) -> Waiver | None:
        for w in self.items:
            if w.block == block and w.key == key:
                return w
        return None


def _date(v, where: str) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return dt.date.fromisoformat(v.strip())
        except ValueError:
            pass
    raise WaiverError(f"{where}: date must be a date like 2026-10-21 (got {v!r}).")


def load(path: Path) -> Waivers:
    """The waivers in `path`; a missing file means no waivers."""
    path = Path(path)
    if not path.is_file():
        return Waivers(())
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise WaiverError(f"{path} is not valid TOML ({e}).") from None
    rows = data.get("waiver", [])
    if not isinstance(rows, list):
        raise WaiverError(f"{path}: waivers must be [[waiver]] tables.")
    items: list[Waiver] = []
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(rows, 1):
        where = f"{path.name} waiver {i}"
        if not isinstance(row, dict):
            raise WaiverError(f"{where} is not a table.")
        for field in ("block", "key", "reason", "date"):
            v = row.get(field)
            if v is None or (isinstance(v, str) and not v.strip()):
                raise WaiverError(f"{where} needs a {field}.")
        for field in ("block", "key", "reason"):
            if not isinstance(row[field], str):
                raise WaiverError(f"{where}: {field} must be text.")
        w = Waiver(row["block"].strip(), row["key"].strip(), row["reason"].strip(), _date(row["date"], where))
        if (w.block, w.key) in seen:
            raise WaiverError(f"{path.name}: more than one waiver for {w.block} {w.key} (one per block and key).")
        seen.add((w.block, w.key))
        items.append(w)
    return Waivers(tuple(items))
