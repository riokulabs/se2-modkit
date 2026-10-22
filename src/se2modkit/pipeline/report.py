"""build_report.json: the per-block entries a build run writes, and the writer.

Schema: {"blocks": {name: {cells, lods, placement, dummies, provenance, ...}}, "se2modkit": {"version", "commit"}}.
The top-level "se2modkit" key records which modkit wrote the report; `se2mod snapshot` ignores it, so a modkit update
alone never shows as a changed report.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

__all__ = ["PROVENANCE_KEY", "empty_entry", "merged_blocks", "provenance", "write", "write_json"]

PROVENANCE_KEY = "se2modkit"


def empty_entry(spec) -> dict:
    """The entry of a block that has no model yet (its cells, nothing measured)."""
    return {"cells": list(spec.cells), "lods": [],
            "placement": {"occupied": 0, "total": 0, "faces": {}, "free_cells": []},
            "dummies": [], "provenance": []}


def _built(entry) -> bool:
    return bool((entry or {}).get("placement", {}).get("total"))


def merged_blocks(old: dict, catalogue: list, pipeline_blocks: list) -> dict:
    """The "blocks" table after a run, in catalogue order: each of the pipeline's blocks (its entry from `old` once
    built, else an empty one), and any other catalogue block that has a built entry in `old`. Blocks no longer in the
    catalogue drop out."""
    wanted = {b.name for b in pipeline_blocks}
    return {b.name: old[b.name] if _built(old.get(b.name)) else empty_entry(b) for b in catalogue
            if b.name in wanted or _built(old.get(b.name))}


@lru_cache(maxsize=1)
def provenance() -> dict:
    """{"version", "commit"} of this se2modkit: the package version and, from a git checkout, its HEAD (else None)."""
    from .. import __version__
    here = Path(__file__).resolve().parent
    commit = None
    try:
        r = subprocess.run(["git", "-C", str(here), "rev-parse", "--verify", "--quiet", "HEAD"], capture_output=True,
                           text=True, check=False, timeout=10)
        if r.returncode == 0 and len(r.stdout.strip()) == 40:
            top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"], capture_output=True,
                                 text=True, check=False, timeout=10).stdout.strip()
            if top and (Path(top) / "src" / "se2modkit").resolve() == here.parent:
                commit = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {"version": __version__, "commit": commit}


def write_json(path: Path, data: dict) -> None:
    """The default layout: one-space indent, sorted keys, a final newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")


def write(path: Path, data: dict, *, modkit_provenance: dict | None = None,
          writer: Callable[[Path, dict], None] | None = None) -> None:
    """Write the report with its top-level "se2modkit" provenance (`modkit_provenance`, default `provenance()`);
    `writer(path, data)` replaces the default layout (`write_json`), e.g. a mod's compact diff-friendly format. The
    writer gets a temporary path next to the report, which then replaces the report in one step (atomic)."""
    out = dict(data)
    out[PROVENANCE_KEY] = dict(provenance() if modkit_provenance is None else modkit_provenance)
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")   # atomic: a reader never sees half a report
    try:
        (writer or write_json)(tmp, out)
        if tmp.exists():        # a writer may write nothing (or elsewhere); then there is nothing to move
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
