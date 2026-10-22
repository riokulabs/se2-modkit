"""Incremental, parallel model builds driven from outside Blender (`se2mod build-models`, `se2mod check`).

The mod's build script (a `pipeline.runner.Runner`) decides what is stale; this module only runs Blender:

* serial (`jobs` 1, `--render`, or `--export`): one Blender run of the build script; it builds the stale blocks and
  writes the report. `--export` needs Keen's VRAGE Tools add-on, so that run is windowed: one window, every export.
* parallel: a headless `--plan` run lists the stale blocks (and, when none is stale, writes the report itself); the
  stale blocks are split over N headless workers (`--worker=DIR`, each its own temp dir and log, balanced by the
  blocks' last build times); a last headless run takes the workers' fragments in (`--adopt=DIR`) and writes the
  report. A worker that fails stops the others and names the block it was building. The SDK build and anything under
  Proton are not part of this; they stay serial in `se2mod build`.

Every Blender process runs in its own process group (`procgroup`): on a failure, the timeout or Ctrl-C the groups are
stopped and waited for.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import procgroup
from .env.core import SE2Env
from .pipeline import incremental as IN

__all__ = ["BuildError", "BuildResult", "build_models", "default_jobs", "find_script", "split"]

KEEP_RUNS = 10
_TAIL = 20


class BuildError(Exception):
    """A build that failed; the message is one sentence (with the log to read)."""

    def __init__(self, message: str, log: Path | None = None, tail: list[str] | None = None):
        super().__init__(message)
        self.log, self.tail = log, list(tail or ())


@dataclass
class BuildResult:
    selected: list[str]
    built: list[str]
    seconds: float
    workers: int = 1
    status: dict = field(default_factory=dict)


def default_jobs() -> int:
    return max(1, min(os.cpu_count() or 1, 4))


def split(blocks: list[str], n: int, seconds: dict | None = None) -> list[list[str]]:
    """`blocks` over at most `n` workers, longest first onto the least loaded (by their last build time; a block never
    built counts as the median of the known ones, else 1 s); each worker's blocks keep their given order. Empty
    workers are dropped."""
    seconds = seconds or {}
    known = sorted(float(v) for v in seconds.values() if isinstance(v, (int, float)) and v > 0)
    default = known[len(known) // 2] if known else 1.0
    cost = {b: float(seconds[b]) if isinstance(seconds.get(b), (int, float)) and seconds[b] > 0 else default
            for b in blocks}
    n = max(1, min(n, len(blocks)))
    loads = [0.0] * n
    groups: list[list[str]] = [[] for _ in range(n)]
    order = {b: i for i, b in enumerate(blocks)}
    for b in sorted(blocks, key=lambda b: (-cost[b], order[b])):
        i = min(range(n), key=lambda k: (loads[k], k))
        groups[i].append(b)
        loads[i] += cost[b]
    return [sorted(g, key=order.__getitem__) for g in groups if g]


def find_script(source_model: Path, script: Path | None = None) -> Path:
    """The mod's build script: `script`, else the one `build_*.py` in its model source."""
    if script is not None:
        if not Path(script).is_file():
            raise BuildError(f"Build script {script} does not exist.")
        return Path(script)
    found = sorted(Path(source_model).glob("build_*.py"))
    if len(found) != 1:
        names = ", ".join(p.name for p in found) or "none"
        raise BuildError(f"Pass --script: {source_model} needs exactly one build_*.py build script (found {names}).")
    return found[0]


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "mod"


def _run_dir(env: SE2Env, mod_name: str) -> Path:
    base = env.cache_dir / "build-runs"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d, i = base / f"{_safe(mod_name)}-{stamp}", 1
    while d.exists():
        d, i = base / f"{_safe(mod_name)}-{stamp}-{i}", i + 1
    d.mkdir(parents=True)
    return d


def _prune(run_dir: Path, mod_name: str, keep: int = KEEP_RUNS) -> None:
    """Keep the newest `keep` failed-run folders of this mod (a successful run removes its own)."""
    try:
        mine = sorted((p for p in run_dir.parent.iterdir() if p.is_dir() and p.name.startswith(_safe(mod_name) + "-")
                       and p != run_dir), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in mine[keep - 1:]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


def _drop_temp(run_dir: Path) -> None:
    for d in sorted(run_dir.rglob("*"), reverse=True):
        if d.is_dir() and (d.name == "tmp" or d.name.endswith("-tmp")):
            shutil.rmtree(d, ignore_errors=True)


def _tail(log: Path, n: int = _TAIL) -> list[str]:
    try:
        return log.read_text(encoding="utf-8", errors="replace").rstrip().splitlines()[-n:]
    except OSError:
        return []


def _why(log: Path) -> str:
    """The build's own one-sentence error: the last non-empty line of its log that is not a Blender banner."""
    for line in reversed(_tail(log, 60)):
        s = line.strip()
        if s and not s.startswith(("Blender quit", "Read prefs", "Error: Python: Traceback", "=== BUILD")):
            return s.rstrip(".")
    return "no output"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _run_all(jobs: list[procgroup.Job], timeout: float) -> list[procgroup.Result]:
    try:
        return procgroup.run_all(jobs, timeout)
    except OSError as e:
        raise BuildError(f"Cannot start Blender {jobs[0].argv[0]} ({e.strerror or e}); set --blender, SE2_BLENDER "
                         "or config blender.") from None


class _Blender:
    def __init__(self, env: SE2Env, script: Path, source_model: Path, run_dir: Path, timeout: float):
        self.env, self.script, self.source_model = env, script, source_model
        self.run_dir, self.timeout = run_dir, timeout
        self.lock: Path | None = None

    def job(self, args: list[str], name: str, *, windowed: bool = False, tmp: Path | None = None) -> procgroup.Job:
        from .cli.blender_cmd import blender_argv, build_env
        argv = blender_argv(self.env, self.script, args, mod_source=self.source_model, windowed=windowed)
        child = build_env(self.env)
        if self.lock is not None:
            child[IN.LOCK_ENV] = str(self.lock)   # the Blenders run under this build's lock
        if tmp is not None:
            tmp.mkdir(parents=True, exist_ok=True)
            for k in ("TMPDIR", "TEMP", "TMP"):
                child[k] = str(tmp)
        child["PYTHONUNBUFFERED"] = "1"
        return procgroup.Job(argv, self.run_dir / f"{name}.log", env=child, tag=name)

    def one(self, args: list[str], name: str, *, windowed: bool = False) -> dict:
        """Run one Blender; its --status JSON. Raises BuildError when it fails."""
        status = self.run_dir / f"{name}.json"
        job = self.job([*args, f"--status={status}"], name, windowed=windowed, tmp=self.run_dir / f"{name}-tmp")
        (r,) = _run_all([job], self.timeout)
        text = "\n".join(_tail(job.log, 200))
        failed = r.code != 0 or (windowed and "=== BUILD DONE ===" not in text)
        data = _read(status)
        if r.stopped:
            raise BuildError(f"Blender did not finish within {self.timeout:g} s and was stopped; log: {job.log}",
                             job.log, _tail(job.log))
        if failed or data is None:
            raise BuildError(f"The {name} run failed ({_why(job.log)}); log: {job.log}", job.log, _tail(job.log))
        return data


def build_models(env: SE2Env, *, mod_name: str, source_model: Path, script: Path, words: list[str], jobs: int,
                 report: Path, force: bool = False, export: bool = False, render: bool = False,
                 blocks_only: bool = False, timeout: float = 3600) -> BuildResult:
    """Build the mod's stale blocks (all selected with `force`); see the module docstring. Holds the mod's build lock
    (its report path) throughout. Raises BuildError."""
    try:
        with IN.build_lock(report, mod_name) as lock:
            return _build_models(env, mod_name, source_model, script, words, jobs, force, export, render,
                                 blocks_only, timeout, lock)
    except IN.LockHeld as e:
        raise BuildError(str(e)) from None


def _build_models(env, mod_name, source_model, script, words, jobs, force, export, render, blocks_only, timeout,
                  lock) -> BuildResult:
    started = time.monotonic()
    run_dir = _run_dir(env, mod_name)
    bl = _Blender(env, script, source_model, run_dir, timeout)
    bl.lock = lock
    common = [*words, *([] if render else ["--no-render"]), *(["--blocks-only"] if blocks_only else [])]
    try:
        if export or render or jobs <= 1:
            st = bl.one([*common, *(["--force"] if force else []), *(["--export"] if export else [])], "build",
                        windowed=export)
            result = BuildResult(st.get("selected", []), st.get("built", []), 0.0, 1, st)
        else:
            plan = bl.one([*common, *(["--force"] if force else []), "--plan"], "plan")
            stale = list(plan.get("stale", []))
            if not stale:
                result = BuildResult(plan.get("selected", []), [], 0.0, 0, plan)
            else:
                result = _parallel(bl, common, plan, stale, jobs)
    except BaseException:
        _drop_temp(run_dir)       # a failed run keeps its logs, not its workers' temp files
        _prune(run_dir, mod_name)
        raise
    shutil.rmtree(run_dir, ignore_errors=True)
    result.seconds = time.monotonic() - started
    return result


def _parallel(bl: _Blender, common: list[str], plan: dict, stale: list[str], jobs: int) -> BuildResult:
    groups = split(stale, jobs, plan.get("seconds"))
    work = []
    for i, blocks in enumerate(groups, 1):
        d = bl.run_dir / f"worker-{i}"
        frags = d / "fragments"
        job = bl.job([*blocks, "--no-render", f"--worker={frags}"], f"worker-{i}", tmp=d / "tmp")
        job.data = {"blocks": blocks, "fragments": frags}
        work.append(job)
    results = _run_all(work, bl.timeout)
    bad = next((r for r in results if r.code not in (0, None)), None) or next(
        (r for r in results if r.code is None), None)
    if bad is not None:
        from .pipeline.incremental import Store
        store = Store(bad.job.data["fragments"])
        block = next((b for b in bad.job.data["blocks"] if store.load(b) is None), bad.job.data["blocks"][-1])
        where = f"{bad.job.tag} of {len(results)}"
        if bad.code is None:
            raise BuildError(f"{block}: {where} did not finish within {bl.timeout:g} s and was stopped; "
                             f"log: {bad.job.log}", bad.job.log, _tail(bad.job.log))
        raise BuildError(f"{block} failed in {where} ({_why(bad.job.log)}); log: {bad.job.log}", bad.job.log,
                         _tail(bad.job.log))
    adopt = [f"--adopt={j.data['fragments']}" for j in work]
    merged = bl.one([*common, "--plan", *adopt], "merge")
    if merged.get("stale"):
        raise BuildError("The inputs of " + ", ".join(merged["stale"]) + " changed during the build; run it again.")
    return BuildResult(plan.get("selected", []), stale, 0.0, len(work), merged)
