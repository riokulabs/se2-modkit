"""Compact queries over a mod's `build_report.json`: LOD table, budget % of Keen's model limits, deltas."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from .model import limits
from .project import ProjectError

FIELDS = ("lods", "tris", "materials", "pct")
DEFAULT_MAX = 60
_HEADERS = {"lods": "LOD", "tris": "tris", "materials": "materials", "pct": "%limit"}

Stats = dict[str, list[list]]  # block -> per LOD [tris, materials, max_tris]


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError:
        raise ProjectError(f"No {what} at {path}; build the mod first (se2mod build).") from None
    except ValueError as e:
        raise ProjectError(f"{what} {path} is not valid JSON ({e}).") from None


def _max_tris(block: dict, i: int, lod: dict) -> int | None:
    if isinstance(lod.get("max_tris"), int | float):
        return int(lod["max_tris"])
    size = block.get("model_size")
    if isinstance(size, list) and len(size) == 3:
        try:
            lods = limits.model_limits(size)["lods"]
            return lods[min(i, len(lods) - 1)]["max_tris"]
        except (TypeError, ValueError, KeyError, IndexError):
            return None
    return None


def stats_of(report) -> Stats:
    """`{block: [[tris, materials, max_tris], ...]}` per LOD, for the table and for snapshots."""
    out: Stats = {}
    blocks = report.get("blocks") if isinstance(report, dict) else None
    if not isinstance(blocks, dict):
        return out
    for name, b in blocks.items():
        lods = b.get("lods") if isinstance(b, dict) else None
        if not isinstance(lods, list):
            continue
        out[name] = [[int(lod.get("tris", 0)), int(lod.get("materials", 0)), _max_tris(b, i, lod)]
                     for i, lod in enumerate(lods) if isinstance(lod, dict)]
    return out


def load_stats(path: Path) -> Stats:
    return stats_of(_read_json(path, "build_report.json"))


def load_baseline(path: Path) -> Stats:
    """A baseline is a build report or a snapshot (`se2mod snapshot --out`, which records per-LOD stats)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError:
        raise ProjectError(f"Cannot read baseline {path}.") from None
    except ValueError as e:
        raise ProjectError(f"Baseline {path} is not valid JSON ({e}).") from None
    if isinstance(data, dict) and "blocks" in data:
        return stats_of(data)
    if isinstance(data, dict) and isinstance(data.get("report_stats"), dict):
        return data["report_stats"]
    raise ProjectError(f"Baseline {path} holds no per-LOD numbers (an older snapshot?); "
                       "give a build_report.json or re-record the snapshot.")


def _delta(now: int, was: int | None) -> str:
    if was is None or now == was:
        return str(now) if was is not None else f"{now}"
    return f"{now} ({now - was:+d})"


def parse_fields(text: str | None) -> tuple[str, ...] | None:
    if text is None:
        return None
    names = tuple(f.strip().lower() for f in text.split(",") if f.strip())
    bad = [f for f in names if f not in FIELDS]
    if bad or not names:
        raise ProjectError(f"Unknown field {', '.join(repr(b) for b in bad) or repr(text)}; "
                           f"fields: {', '.join(FIELDS)}.")
    return names


def table(stats: Stats, *, block: str | None, fields: tuple[str, ...] | None, base: Stats | None) -> list[str]:
    if block is not None and block not in stats:
        close = difflib.get_close_matches(block, stats, n=5, cutoff=0.5)
        close += [n for n in stats if block.lower() in n.lower() and n not in close][:5]
        hint = f"close matches: {', '.join(close[:5])}." if close else f"the report has {len(stats)} blocks."
        raise ProjectError(f"No block '{block}' in the report; {hint}")
    cols = list(fields) if fields else ["lods", "tris", "materials", "pct"]
    all_lods = block is not None or bool(fields and "lods" in fields)
    names = [block] if block else sorted(stats)
    rows: list[list[str]] = []
    for name in names:
        lods = stats[name] if all_lods else stats[name][:1]
        prior = base.get(name) if base is not None else None
        for i, (tris, mats, mx) in enumerate(lods):
            was = prior[i] if prior is not None and i < len(prior) else None
            new = base is not None and (prior is None or was is None)
            cell = {
                "lods": str(i),
                "tris": f"{tris} (new)" if new else _delta(tris, was[0] if was else None),
                "materials": _delta(mats, was[1] if was else None) if not new else f"{mats} (new)",
                "pct": f"{tris / mx * 100:.1f}%" if mx else "-",
            }
            rows.append([name, *(cell[c] for c in cols)])
    header = ["block", *(_HEADERS[c] for c in cols)]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in [header, *rows]]
    if base is not None and block is None:
        gone = sorted(set(base) - set(stats))
        if gone:
            lines.append(f"removed: {', '.join(gone)}")
    return lines


def bounded(lines: list[str], max_lines: int) -> list[str]:
    if max_lines < 1:
        raise ProjectError(f"--max must be at least 1 (got {max_lines}).")
    if len(lines) <= max_lines + 1:  # +1: the header is free
        return lines
    keep = max_lines + 1
    return [*lines[:keep], f"… {len(lines) - keep} more lines (use --max)"]
