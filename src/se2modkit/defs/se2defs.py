"""se2defs: explore Space Engineers 2 definition (.def) files; `se2mod defs ...` is its command line.

Vanilla content and mods are JSON files shaped like
`{"$Bundles": {...}, "$Type": "Assembly:Namespace.TypeObjectBuilder", "$Value": {"Guid": "...", ...}}`.
Definitions reference each other by GUID, and mod overrides (.partialdef) target a vanilla definition by its GUID
(`BaseDefinition`).

  types [FILTER]   count definition $Types (optionally filtered by substring)
  type SUBSTR      list files whose root $Type contains SUBSTR
  guid GUID        which file defines GUID, and which files reference it
  show GUID        print the definition that declares GUID
  grep REGEX       list files whose text matches REGEX (case-insensitive)
  mods             summarise content mods in the Steam Workshop folder

The content root defaults to the game's `GameData/Vanilla/Content` (or `$SE2_CONTENT`); `--root` overrides it.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from pathlib import Path

from ..env.core import EnvError, SE2Env, require
from ..env.steam import GAME_APP_ID
from .block_chain import DefsError

DEF_EXTS = (".def", ".partialdef")


def default_root(env: SE2Env, environ: Mapping[str, str] = os.environ) -> Path:
    if environ.get("SE2_CONTENT"):
        return Path(environ["SE2_CONTENT"])
    require(env, "game_dir")
    return env.game_dir / "GameData" / "Vanilla" / "Content"


def workshop_dir(env: SE2Env) -> Path:
    """The game's Workshop content folder in the library the game is installed in."""
    require(env, "game_dir")
    return env.game_dir.parent.parent / "workshop" / "content" / str(GAME_APP_ID)


def iter_defs(root: Path) -> Iterator[Path]:
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(DEF_EXTS):
                yield Path(dirpath) / name


def load(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def short_type(t: str | None) -> str:
    return t.split(":", 1)[-1] if t else "?"


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def cmd_types(root: Path, flt: str | None = None) -> None:
    counts: Counter = Counter()
    for p in iter_defs(root):
        d = load(p)
        if d:
            counts[short_type(d.get("$Type"))] += 1
    for t, n in counts.most_common():
        if not flt or flt.lower() in t.lower():
            print(f"{n:6d}  {t}")


def cmd_type(root: Path, substr: str) -> None:
    needle = substr.lower()
    for p in iter_defs(root):
        d = load(p)
        if d and needle in (d.get("$Type") or "").lower():
            guid = (d.get("$Value") or {}).get("Guid", "")
            print(f"{guid:36}  {_rel(p, root)}")


def cmd_guid(root: Path, guid: str) -> None:
    g = guid.lower()
    for p in iter_defs(root):
        if g not in _read(p).lower():
            continue
        d = load(p)
        own = d and str((d.get("$Value") or {}).get("Guid", "")).lower() == g
        tag = "DEFINES " if own else "refs    "
        print(f"{tag}{_rel(p, root)}  [{short_type(d.get('$Type')) if d else '?'}]")


def cmd_show(root: Path, guid: str) -> None:
    g = guid.lower()
    for p in iter_defs(root):
        d = load(p)
        if d and str((d.get("$Value") or {}).get("Guid", "")).lower() == g:
            print(f"# {p}")
            print(json.dumps(d, indent=2))
            return
    raise DefsError(f"GUID {guid} is not declared under {root}.")


def cmd_grep(root: Path, regex: str) -> None:
    try:
        rx = re.compile(regex, re.I)
    except re.error as e:
        raise DefsError(f"Bad regular expression {regex!r}: {e}.") from e
    for p in iter_defs(root):
        if rx.search(_read(p)):
            print(_rel(p, root))


def cmd_mods(workshop: Path) -> None:
    if not workshop.is_dir():
        raise EnvError(f"No Workshop folder at {workshop}; subscribe to a mod in Steam first.")
    for proj in sorted(workshop.glob("*/*.vrgproj")):
        d = load(proj) or {}
        name = (d.get("$Value") or {}).get("Name", proj.stem)
        print(f"\n[{proj.parent.name}] {name}")
        seen: Counter = Counter()
        for pd in sorted(proj.parent.rglob("*.partialdef")):
            v = (load(pd) or {}).get("$Value") or {}
            seen[(v.get("PartialDefinitionKind", "?"), short_type(v.get("DefinitionType")),
                  v.get("BaseDefinition"))] += 1
        for (kind, dtype, base), n in seen.items():
            print(f"   {kind:9} {dtype}  base={base}" + (f"  (x{n})" if n > 1 else ""))
        n_defs = sum(1 for _ in proj.parent.rglob("*.def"))
        print(f"   ({n_defs} .def files)")
