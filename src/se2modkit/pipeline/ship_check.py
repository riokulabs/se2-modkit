"""The ship check of a block (plain Python, no bpy): the queue-order rule run before anything is queued, and the fail
lines and summary of the report the Blender half (`ship_probe`, through `Runner.check_ship`) writes.

A report: {"block", "family", "status": "checked" | "error", "error",
  "shipping": [{"model", "collection", "objects": [{"name", "tris"}]}] (what the export takes: every block and subpart
      LOD as built) and "collision": [object names],
  "sources": [{"blend", "collection", "objects": [{"name", "path", "type", "ships", "why", "viewport_hidden"}]}]
      (the working-file collections the block's builder loaded, each object as saved: whether it ships and why not),
  "zfight": {key: [rows]} (the export's own z-fight audit, `zfight.audit_built`, on the built models: only the keys
      with faults; "coplanar" is same-facing coplanar faces across everything that ships),
  "fracture": the fracture check report (`fracture_check`), "zone_bounds": {zone: [lo, hi]},
  "preview": the tinted exploded fracture PNG (or None), "preview_error",
  "queue": {"fail": [...], "warn": [...]} (added by the command: `queue_order`)}.

Fail lines: an error, any z-fight fault, the fracture check's fail lines and a queue-order FAIL. Warnings: the
fracture check's, a source object that ships while hidden in the viewport (the trap of checking only what was on
screen), one whose name looks like a layout stand-in, a missing preview, and the queue-order WARN lines.

Queue order (`queue_order`), on the jobs waiting or running, for one block and its working .blend files:
  reader  a job that builds or exports the mod's block: it runs the mod's build script, `build-models MOD`, or is a
          `fracture-check` / `ship-check` / `export` job of the mod, and names the block, a PREFIX* of it, "all" or no
          block at all;
  writer  a job that saves or holds the .blend: a live Blender session (`se2mod queue blender FILE`) or any job whose
          command names the file (a reader is never a writer).
  FAIL    a writer that starts after a reader, or runs alongside one (both running, or the reader already running):
          the export reads the old file;
  WARN    a writer queued ahead of every reader (it must finish and release before the export starts), a live Blender
          on another or no file (it may hold the .blend), and an active `se2mod lock` lease on the .blend.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from . import fracture_check as FC

__all__ = ["READER_JOBS", "STANDIN", "job_blocks", "names_file", "problems", "queue_order", "summary", "warnings"]

READER_JOBS = ("fracture-check", "ship-check", "export")       # job name prefixes `<kind>:<mod>:<blocks>`
STANDIN = re.compile(r"stand.?in|placeholder|proxy|blockout|layout", re.IGNORECASE)
MAX_ROWS = 8                                                     # fault rows printed per z-fight key
_VALUE_FLAGS = ("--script", "--timeout", "-j", "--jobs")


# ------------------------------------------------------------------------------------------------ queue order
def _norm(path, cwd: str = "") -> str:
    return os.path.normcase(os.path.abspath(os.path.join(cwd or os.getcwd(), os.fsdecode(path))))


def _words(args: list[str]) -> list[str]:
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in _VALUE_FLAGS:
            skip = True
        elif not a.startswith("-"):
            out.append(a)
    return out


def job_blocks(job, mod: str, script: Path | None) -> list[str] | None | bool:
    """The block words of a job that reads the mod's blocks ([] or ["all"]: every block), else False."""
    argv = [str(a) for a in job.argv]
    if script is not None:
        target = _norm(script)
        for i, a in enumerate(argv):
            if a.endswith(".py") and _norm(a, job.cwd) == target:
                rest = argv[i + 1:]
                return _words(rest[rest.index("--") + 1:] if "--" in rest else rest)
    if "build-models" in argv:
        i = argv.index("build-models")
        if i + 1 < len(argv) and (argv[i + 1] == mod or Path(argv[i + 1]).name == mod):
            return _words(argv[i + 2:])
    kind, _, rest = (job.name or "").partition(":")
    owner, _, blocks = rest.partition(":")
    if kind in READER_JOBS and owner == mod:
        return [b for b in blocks.split(",") if b]
    return False


def _match(a: str, b: str) -> bool:
    """Block words a and b name a common block (either may be a PREFIX*)."""
    pa, pb = a.rstrip("*"), b.rstrip("*")
    return (a.endswith("*") and pb.startswith(pa)) or (b.endswith("*") and pa.startswith(pb)) or a == b


def _touches(words: list[str], block: str) -> bool:
    return not words or "all" in words or any(_match(w, block) for w in words)


def names_file(job, blend: str) -> bool:
    """Whether the job's command names the file (resolved against the job's folder)."""
    return any(str(a).lower().endswith(".blend") and _norm(a, job.cwd) == blend for a in job.argv)


def _label(job) -> str:
    return f"job {job.id} ({job.name})" if job.name else f"job {job.id}"


def queue_order(jobs: Iterable, *, mod: str, block: str, script: Path | None, blends: Iterable,
                leases: Iterable = ()) -> tuple[list[str], list[str]]:
    """(FAIL lines, WARN lines) of the queue for one block and its working .blend files (see the module doc).
    jobs: `jobqueue.JobRecord`s (finished ones are skipped); leases: `lease.Lease`s still active."""
    from ..jobqueue import queue_key
    live = [j for j in jobs if not j.final and not j.cancel and j.state in ("queued", "running")]
    queued = sorted((j for j in live if j.state == "queued"), key=queue_key)
    pos = {j.id: (-1 if j.state == "running" else queued.index(j)) for j in live}
    readers = [j for j in live if (w := job_blocks(j, mod, script)) is not False and _touches(w, block)]
    reader_ids = {j.id for j in readers}
    files = {_norm(b): Path(b) for b in blends}
    fails, warns, flagged = [], [], set()
    for key, path in sorted(files.items()):
        writers = [j for j in live if j.id not in reader_ids and names_file(j, key)]
        for w in writers:
            verb = "holds" if w.kind == "blender" else "saves"
            ahead = True
            for r in readers:
                when = ("alongside" if pos[w.id] == pos[r.id] == -1 else "after" if pos[w.id] > pos[r.id] else None)
                if when:
                    ahead = False
                    fails.append(f"{block}: {_label(w)} {verb} {path.name} but runs {when} {_label(r)}, which reads "
                                 f"it: that run reads the old file; finish the save first (se2mod queue move {w.id} "
                                 f"0), or cancel {r.id} and queue it again after the save")
            if ahead:
                warns.append(f"{block}: {_label(w)} {verb} {path.name}: save and release before the export")
            flagged.add(w.id)
        for lease in leases:
            if _norm(lease.path) == key:
                warns.append(f"{block}: {path.name} is leased to {lease.owner} (se2mod lock): save and release "
                             "before the export")
    for j in live:
        if j.kind == "blender" and j.id not in flagged:
            opened = next((Path(a).name for a in j.argv if str(a).lower().endswith(".blend")), None)
            warns.append(f"{block}: a live Blender is open ({_label(j)}, {opened or 'no file named'}): if it holds "
                         f"{', '.join(p.name for p in files.values()) or 'the working file'}, save and release before "
                         "the export")
    return fails, warns


# ------------------------------------------------------------------------------------------------ report
def _row(key: str, row) -> str:
    if isinstance(row, dict) and "area_mm2" in row:
        a, b = row.get("a", row.get("subpart")), row.get("b", row.get("block"))
        at = row.get("at")
        return (f"{row.get('model', '')}: {a} and {b} share {row['area_mm2']:g} mm2 of faces in one plane, facing "
                f"the same way" + (f" (at {', '.join(f'{c:g}' for c in at)})" if at else ""))
    if isinstance(row, dict):
        return f"{key}: " + ", ".join(f"{k} {v}" for k, v in row.items())
    if isinstance(row, (list, tuple)):
        return f"{key}: " + " / ".join(str(x) for x in row)
    return f"{key}: {row}"


def _shipped(rep: dict) -> list[dict]:
    return [o for s in rep.get("sources") or () for o in s.get("objects") or () if o.get("ships")]


def warnings(rep: dict, max_key_share: float | None = None) -> list[str]:
    name = rep["block"]
    out = FC.warnings(rep["fracture"], max_key_share) if rep.get("fracture") else []
    for o in _shipped(rep):
        if o.get("viewport_hidden"):
            out.append(f"{name}: {o['path']} is hidden in the viewport but ships (the export takes render "
                       "visibility): check it is meant to")
        if STANDIN.search(o["path"]):
            out.append(f"{name}: {o['path']} looks like a layout stand-in and ships: hide it from renders or move it "
                       "out of the block's collection")
    if rep.get("status") == "checked" and not rep.get("preview"):
        out.append(f"{name}: no fracture preview ({rep.get('preview_error') or 'not rendered'})")
    out += (rep.get("queue") or {}).get("warn", [])
    return out


def problems(rep: dict, max_key_share: float | None = None) -> list[str]:
    name = rep["block"]
    out = []
    if rep.get("status") == "error":
        out.append(f"{name}: {rep.get('error') or 'the check failed'}")
    for key, rows in sorted((rep.get("zfight") or {}).items()):
        out += [f"{name}: {_row(key, r)}" for r in rows[:MAX_ROWS]]
        if len(rows) > MAX_ROWS:
            out.append(f"{name}: {key}: and {len(rows) - MAX_ROWS} more (see --json)")
    if rep.get("fracture"):
        out += FC.problems(rep["fracture"], max_key_share)
    out += (rep.get("queue") or {}).get("fail", [])
    return out


def summary(rep: dict, max_key_share: float | None = None) -> list[str]:
    """Readable lines: one summary line, what ships and the fracture zones, a WARN line per warning, a FAIL line per
    problem, then PASS or FAIL with the block's name."""
    name = rep["block"]
    models = rep.get("shipping") or []
    sources = rep.get("sources") or []
    fr = rep.get("fracture") or {}
    cop = sum(len(v) for v in (rep.get("zfight") or {}).values())
    src = ", ".join(s["collection"] for s in sources) or "built in code"
    lines = [f"{name} ({rep.get('family')}): ships {src}; {len(models)} models, "
             f"{sum(len(m['objects']) for m in models)} objects; {cop} z-fight faults; "
             f"{len(fr.get('zones') or ())} zones; preview {rep.get('preview') or 'none'}"]
    for s in sources:
        objs = s.get("objects") or []
        out = [f"{o['name']} ({o['why']})" for o in objs if not o.get("ships")]
        lines.append(f"  source {Path(s['blend']).name}:{s['collection']}: {sum(1 for o in objs if o.get('ships'))} "
                     f"objects ship" + (f"; out: {', '.join(out)}" if out else ""))
    for m in models:
        lines.append(f"  ships {m['model']} ({m['collection']}): {len(m['objects'])} objects, "
                     f"{sum(o.get('tris', 0) for o in m['objects'])} tris")
    if rep.get("collision"):
        lines.append(f"  ships collision: {len(rep['collision'])} objects")
    if fr:
        lines += ["  " + ln for ln in FC.summary(fr, max_key_share) if not ln.startswith(("WARN", "FAIL"))]
    lines += [f"WARN {w}" for w in warnings(rep, max_key_share)]
    bad = problems(rep, max_key_share)
    lines += [f"FAIL {p}" for p in bad]
    lines.append(f"{'FAIL' if bad else 'PASS'} {name}")
    return lines
