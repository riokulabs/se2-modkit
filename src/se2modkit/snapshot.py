"""Determinism snapshots: hash a mod's generated outputs so a refactor can prove nothing changed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import report
from .project import ModConfig

_SECTIONS = ("models", "staged")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file(path: Path) -> str | None:
    try:
        return _sha(path.read_bytes())
    except OSError:
        return None


def _report(path: Path, ignore: tuple[str, ...]) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return _sha(raw)
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in ignore}
    return _sha(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def _tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[p.relative_to(root).as_posix()] = _sha(p.read_bytes())
    return out


def _report_stats(path: Path) -> dict:
    """Per-block per-LOD numbers, so `se2mod report --vs SNAPSHOT` can show deltas (hashes alone cannot)."""
    try:
        return report.stats_of(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        return {}


def take(mod: ModConfig, *, report_ignore: tuple[str, ...] = ("se2modkit",)) -> dict:
    return {
        "report": _report(mod.paths.report, report_ignore),
        "report_stats": _report_stats(mod.paths.report),
        "models": _tree(mod.paths.models),
        "staged": _tree(mod.paths.staged),
        "golden_states": _file(mod.paths.golden_states),
    }


def compare(before: dict, after: dict) -> list[str]:
    """One line per added, removed or changed path (`models/x`, `staged/y`, `report`, `golden_states`)."""
    lines: list[str] = []
    for key in ("report", "golden_states"):
        b, a = before.get(key), after.get(key)
        if b == a:
            continue
        lines.append(f"added: {key}" if b is None else f"removed: {key}" if a is None else f"changed: {key}")
    for section in _SECTIONS:
        b, a = before.get(section) or {}, after.get(section) or {}
        for rel in sorted(set(b) | set(a)):
            if rel not in b:
                lines.append(f"added: {section}/{rel}")
            elif rel not in a:
                lines.append(f"removed: {section}/{rel}")
            elif b[rel] != a[rel]:
                lines.append(f"changed: {section}/{rel}")
    return lines
