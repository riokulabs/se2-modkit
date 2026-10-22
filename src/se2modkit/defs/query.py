"""Bounded definition queries: `defs show` excerpts and `chain resolve --fields`.

Output is capped on purpose: a definition can run to thousands of lines, and a caller usually wants one sub-tree or
two facts. Every failure is a `DefsError` (one sentence), never a traceback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from .block_chain import DefsError
from .se2defs import DEF_EXTS, iter_defs, load

DEFAULT_MAX = 60
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def check_max(n: int) -> int:
    if n < 1:
        raise DefsError(f"--max must be at least 1 (got {n}).")
    return n


def is_guid(text: str) -> bool:
    return bool(_GUID.match(text))


def select(obj, path: str):
    """The sub-tree at the dotted `path` (dict keys, list indexes). Walks `$Value` first when the root has one and
    the first segment is not a key of the root."""
    if not path:
        return obj
    cur = obj
    if isinstance(cur, dict) and "$Value" in cur and path.split(".", 1)[0] not in cur:
        cur = cur["$Value"]
    walked = ""
    for seg in path.split("."):
        where = f"'{walked}'" if walked else "the definition root"
        if isinstance(cur, dict):
            if seg not in cur:
                keys = ", ".join(list(cur)[:8]) + (", ..." if len(cur) > 8 else "")
                raise DefsError(f"No key '{seg}' under {where} (keys: {keys}).")
            cur = cur[seg]
        elif isinstance(cur, list):
            try:
                i = int(seg)
            except ValueError:
                raise DefsError(f"'{seg}' is not a list index under {where} (a list of {len(cur)}).") from None
            if not -len(cur) <= i < len(cur):
                raise DefsError(f"Index {i} is out of range under {where} (a list of {len(cur)}).")
            cur = cur[i]
        else:
            raise DefsError(f"Cannot go into '{seg}': {where} is a value, not a container.")
        walked = f"{walked}.{seg}" if walked else seg
    return cur


def bounded(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    more = len(lines) - max_lines
    return "\n".join([*lines[:max_lines], f"… {more} more lines (use --max)"])


def find_definition(get_root: Callable[[], Path], target: str) -> tuple[Path, dict]:
    """`target` is an existing file or a GUID declared under `root`."""
    p = Path(target)
    if p.is_file():
        d = load(p)
        if d is None:
            raise DefsError(f"{p} is not a JSON definition file.")
        return p, d
    if not is_guid(target):
        hint = " (the file does not exist)" if p.suffix in DEF_EXTS or "/" in target else ""
        raise DefsError(f"'{target}' is neither a definition file nor a GUID{hint}.")
    root = get_root()
    g = target.lower()
    for q in iter_defs(root):
        d = load(q)
        if d and str((d.get("$Value") or {}).get("Guid", "")).lower() == g:
            return q, d
    raise DefsError(f"GUID {target} is not declared under {root}.")


def cmd_show(get_root: Callable[[], Path], target: str, path: str | None, max_lines: int) -> None:
    max_lines = check_max(max_lines)
    p, d = find_definition(get_root, target)
    if not path:  # the header names the file; --path output stays pure JSON
        print(f"# {p}")
    print(bounded(json.dumps(select(d, path) if path else d, indent=2), max_lines))


# --- chain resolve --fields -------------------------------------------------------------------------------------

def _name(value: dict, entry, _idx) -> str:
    for key in ("Name", "DisplayName", "BindingName"):
        if isinstance(value.get(key), str):
            return value[key]
    ui = value.get("UIData")
    if isinstance(ui, dict) and isinstance(ui.get("Name"), str):
        return ui["Name"]
    return entry.path.stem  # bindings carry no name of their own: the file name is the block name


FIELDS: dict[str, Callable] = {
    "guid": lambda v, e, i: e.guid,
    "name": _name,
    "type": lambda v, e, i: e.type,
    "kind": lambda v, e, i: e.kind,
    "file": lambda v, e, i: i.rel(e.path),
    "base": lambda v, e, i: e.base or "-",
}


def parse_fields(text: str) -> list[str]:
    names = [f.strip().lower() for f in text.split(",") if f.strip()]
    bad = [f for f in names if f not in FIELDS]
    if bad or not names:
        raise DefsError(f"Unknown field {', '.join(repr(b) for b in bad) or repr(text)}; "
                        f"fields: {', '.join(FIELDS)}.")
    return names


__all__ = ["FIELDS", "bounded", "cmd_show", "find_definition", "is_guid", "parse_fields", "select"]
