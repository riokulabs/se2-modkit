"""The tools `se2mod mcp` serves: plain Python over the same functions the `se2mod` commands use, each returning a
JSON-ready dict (paths as strings) instead of terminal text. Nothing here imports the `mcp` package, so the tools can
be tested, and called, without it.

Each tool says whether it only reads (`read_only`) or changes something: queues a job, writes a look set or a report
(`read_only=False`), or may delete or overwrite things (`destructive=True`, only `queue_submit`, which runs any
command). Long work (renders, describe, ship check) goes through the job queue exactly as the commands do; nothing
here starts Blender itself.

A tool that fails in an expected way (no such mod, a bad argument, a job that failed) returns `ok: false` and an
`error` line rather than raising, so the caller always gets the same shape back.
"""

import argparse
import contextlib
import functools
import io
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .. import jobqueue as Q

LOG_TAIL = 40          # lines of a job's log a wait returns
DEFAULT_LIMIT = 200    # rows a lookup returns before it says it was cut


@dataclass(frozen=True)
class ToolSpec:
    """One served tool: its name, function and the hints a client shows (MCP tool annotations)."""
    name: str
    fn: Callable[..., dict]
    title: str
    description: str
    read_only: bool
    destructive: bool = False
    idempotent: bool = False


class ToolFailure(Exception):
    """An expected failure, returned to the caller as `ok: false` with this message."""


def _expected_errors() -> tuple[type[BaseException], ...]:
    from ..env.config import ConfigError
    from ..env.errors import EnvError
    from ..modelbuild import BuildError
    from ..preview.compose import PreviewError
    from ..preview.plan import PlanError
    from ..project import ProjectError
    return (ToolFailure, ProjectError, EnvError, ConfigError, Q.QueueError, PreviewError, PlanError, BuildError,
            OSError, ValueError)


def _guarded(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Turn expected errors into `{"ok": false, "error": ...}` and add `ok` from `code` to every result."""
    @functools.wraps(fn)
    def run(*args, **kwargs) -> dict[str, Any]:
        try:
            res = fn(*args, **kwargs)
        except _expected_errors() as e:
            return {"ok": False, "error": str(e)}
        res.setdefault("ok", res.get("code", 0) == 0 and "error" not in res)
        return res
    return run


def _jsonable(value: Any) -> Any:
    """Paths (and anything else json can't take) as strings."""
    return json.loads(json.dumps(value, default=str))


class Toolbox:
    """The served tools, bound to one environment (discovered on first use unless given) and one job queue."""

    def __init__(self, env=None, *, env_factory: Callable[[], Any] | None = None):
        self._env = env
        self._env_factory = env_factory
        self._indexes: dict[Path, Any] = {}     # SDK Assets root -> chain Index (slow to build, kept)
        self._stdout_lock = threading.Lock()

    # -- shared -------------------------------------------------------------------------------------------------

    @property
    def env(self):
        if self._env is None:
            if self._env_factory is not None:
                self._env = self._env_factory()
            else:
                from ..env.core import discover_env
                self._env = discover_env()
        return self._env

    def store(self) -> Q.Store:
        return Q.Store(Path(self.env.cache_dir) / "queue")

    @staticmethod
    def parse(argv: list[str]) -> argparse.Namespace:
        """`argv` through the `se2mod` parser, so a tool gets exactly the command's defaults and checks."""
        from ..cli import _parser
        try:
            args, extra = _parser().parse_known_args(argv)
        except SystemExit as e:   # argparse has printed the reason on stderr
            raise ToolFailure(f"bad arguments for `se2mod {' '.join(argv[:2])}`: see the server's stderr") from e
        if extra:
            raise ToolFailure(f"unrecognized arguments: {' '.join(extra)}")
        return args

    def _captured(self, fn: Callable[[], Any]) -> tuple[Any, str]:
        """Run `fn` with its printed output collected (one at a time: stdout is shared by the whole process)."""
        buf = io.StringIO()
        with self._stdout_lock, contextlib.redirect_stdout(buf):
            value = fn()
        return value, buf.getvalue()

    def _index(self, root: str | None):
        from ..defs import block_chain as bc
        path = Path(root) if root else bc.default_root(self.env)
        if path not in self._indexes:
            self._indexes[path] = bc.Index(path)
        return self._indexes[path]

    def _write_roots(self, mod: str | None) -> list[Path]:
        """Where a tool may write: the modkit cache, and the preview folder of the mod named (the folders the
        commands write to by default)."""
        from ..cli import modarg
        roots = [Path(self.env.cache_dir).resolve()]
        cfg = modarg.resolve(self.env, mod, need="config").config if mod else None
        if cfg is not None:
            roots.append((Path(cfg.paths.source_model) / "preview").resolve())
        return roots

    def _confined(self, path: str, mod: str | None, what: str) -> Path:
        """`path`, resolved (symbolic links followed), if it lies in a folder a tool may write to; else a refusal."""
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else Path.cwd() / p).resolve()
        roots = self._write_roots(mod)
        if any(p == r or p.is_relative_to(r) for r in roots):
            return p
        raise ToolFailure(f"{what} {path} is outside the folders this server writes to "
                          f"({', '.join(str(r) for r in roots)}); give a path under one of them"
                          + ("" if mod else ", or name the mod to allow its preview folder") + ".")

    @staticmethod
    def _plain_name(value: str, what: str) -> str:
        """A name that becomes an argument or a folder: no leading '-', no path separators, not '.' or '..'."""
        if not value or value.startswith("-") or "/" in value or "\\" in value or value in (".", ".."):
            raise ToolFailure(f"{what} {value!r} is not a plain name (no leading '-', no '/' or '\\', not '.' "
                              "or '..').")
        return value

    # -- look -----------------------------------------------------------------------------------------------------

    def look(self, source: str, block: str, mod: str | None = None, out: str | None = None,
             set_name: str = "neighbors", views: str = "look", interior: bool = False,
             stage: Literal["look", "concept", "fix"] = "look", round: int | None = None, collection: str = "",
             mount_face: str = "-Z", grid: Literal["large", "small"] = "large", before: str | None = None,
             close: list[str] | None = None, material: dict[str, str] | None = None,
             tris_budget: int | None = None, no_check: bool = False, full: bool = False,
             wait: bool = True) -> dict[str, Any]:
        self._plain_name(block, "block")
        if out:
            out = str(self._confined(out, mod, "out"))
        argv = ["look", f"--block={block}", f"--set={set_name}", f"--views={views}", f"--stage={stage}",
                f"--collection={collection}", f"--mount-face={mount_face}", f"--grid={grid}"]
        argv += [f"--mod={mod}"] * bool(mod) + [f"--out={out}"] * bool(out) + [f"--before={before}"] * bool(before)
        argv += [f"--round={round}"] * (round is not None)
        argv += [f"--tris-budget={tris_budget}"] * (tris_budget is not None)
        argv += [f"--close={c}" for c in close or ()] + [f"--material={k}={v}" for k, v in (material or {}).items()]
        argv += ["--interior"] * interior + ["--no-check"] * no_check + ["--full"] * full + ["--no-wait"] * (not wait)
        argv += ["--", source]
        from ..cli.look_cmd import run_look
        log: list[str] = []
        res = run_look(self.parse(argv), self.env, say=log.append)
        if not wait and "job_id" in res:
            res["next"] = (f"queue_wait job {res['job_id']}, then look_finish on {res['set_dir']} to check the set "
                           "and make its sheet")
        return _jsonable({**res, "log": log})

    def look_finish(self, set_dir: str, stage: Literal["look", "concept", "fix"] = "look",
                    interior: bool = False, mod: str | None = None) -> dict[str, Any]:
        from ..cli.look_cmd import finish_look
        out = self._confined(set_dir, mod, "set_dir")
        if not out.is_dir() or not out.name.startswith("r") or not out.name[1:].isdigit():
            raise ToolFailure(f"{set_dir} is not a look set folder (<look>/<block>/r<N>).")
        return _jsonable({"set_dir": str(out), **finish_look(out, block=out.parent.name, stage=stage,
                                                             rnd=int(out.name[1:]), interior=interior)})

    def geocheck(self, set_dir: str) -> dict[str, Any]:
        out = Path(set_dir)
        geo, report = out / "geocheck.json", out / "report.json"
        if not out.is_dir():
            raise ToolFailure(f"There is no look set at {set_dir}.")
        res: dict = {"set_dir": str(out), "geocheck_path": str(geo) if geo.is_file() else None}
        if geo.is_file():
            g = json.loads(geo.read_text(encoding="utf-8"))
            res.update(passed=bool(g.get("ok", True)), failures=g.get("failures", []), warnings=g.get("warnings", []),
                       geocheck=g)
        else:
            res["error"] = "no geocheck.json: the set ran with no_check, or its render has not finished"
        if report.is_file():
            res["not_ready"] = json.loads(report.read_text(encoding="utf-8")).get("problems")
            res["report_path"] = str(report)
        return res

    # -- describe and ship check ----------------------------------------------------------------------------------

    def describe(self, blend: str, mod: str | None = None, collection: list[str] | None = None,
                 targets: str | None = None, envelope: list[float] | None = None, json_out: str | None = None,
                 timeout: float = 900) -> dict[str, Any]:
        if json_out:
            json_out = str(self._confined(json_out, mod, "json_out"))
        argv = ["describe", f"--timeout={timeout:g}"] + [f"--collection={c}" for c in collection or ()]
        argv += [f"--mod={mod}"] * bool(mod) + [f"--targets={targets}"] * bool(targets)
        argv += [f"--json={json_out}"] * bool(json_out)
        if envelope is not None:
            argv.append("--envelope=" + ",".join(f"{v:g}" for v in envelope))
        argv += ["--", blend]
        from ..cli.describe_cmd import run_describe
        log: list[str] = []
        res = run_describe(self.parse(argv), self.env, say=log.append)
        res.pop("text", None)   # the report holds the same, as data
        return _jsonable({**res, "log": log})

    def ship_check(self, mod: str, blocks: list[str], max_key_share: float | None = None,
                   blend: list[str] | None = None, timeout: float = 1800) -> dict[str, Any]:
        if not blocks:
            raise ToolFailure("name at least one block (or PREFIX*).")
        if mod.startswith("-"):
            raise ToolFailure(f"mod {mod!r} starts with '-'; give a mod name or folder.")
        for b in blocks:
            self._plain_name(b, "block")
        argv = ["ship", "check", f"--timeout={timeout:g}"] + [f"--blend={b}" for b in blend or ()]
        argv += [f"--max-key-share={max_key_share}"] * (max_key_share is not None)
        argv += ["--", mod, *blocks]   # the mod's own build script only: no script parameter here
        from ..cli.ship_cmd import run_ship
        log: list[str] = []
        res = run_ship(self.parse(argv), self.env, say=log.append)
        return _jsonable({**res, "log": log})

    # -- queue ------------------------------------------------------------------------------------------------------

    @staticmethod
    def _job(store: Q.Store, j: Q.JobRecord, now: float) -> dict[str, Any]:
        return {"id": j.id, "name": j.name, "state": j.state, "load": Q.load_label(j.load), "gpu": j.gpu,
                "priority": j.priority, "elapsed_s": j.elapsed(now), "exit": j.exit, "cancel_requested": j.cancel,
                "log": str(store.log_path(j.id))}

    def queue_status(self, all: bool = False) -> dict[str, Any]:
        import time
        store, now = self.store(), time.time()
        jobs = store.all()
        if not all:
            done = [j for j in jobs if j.final]
            jobs = [j for j in jobs if not j.final or j in done[-10:]]
        waiting = sorted((j for j in jobs if j.state == "queued"), key=Q.queue_key)
        jobs = [j for j in jobs if j.state != "queued"] + waiting
        return {"runner_running": store.runner_alive(), "runner_pid": store.runner_pid(), "paused": store.paused,
                "runner_log": str(store.runner_log), "jobs": [self._job(store, j, now) for j in jobs]}

    def queue_submit(self, command: list[str], name: str = "", load: str = "heavy", gpu: str = "auto",
                     cwd: str | None = None, timeout: float | None = None, env: dict[str, str] | None = None,
                     priority: bool = False) -> dict[str, Any]:
        if not command:
            raise ToolFailure("give the command to queue, as a list of arguments.")
        store = self.store()
        job = store.submit(list(command), name=name, load=Q.parse_load(load), gpu=Q.parse_gpu(gpu),
                           cwd=Path(cwd) if cwd else None, timeout=timeout, env=dict(env or {}), priority=priority)
        Q.ensure_runner(store)
        return {"job_id": job.id, "name": job.name, "log": str(store.log_path(job.id)), "paused": store.paused}

    def queue_wait(self, job_id: int, timeout: float = 3600) -> dict[str, Any]:
        store = self.store()
        code = Q.wait(store, job_id, timeout)
        job = store.load(job_id)
        log = store.log_path(job_id)
        try:
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL:]
        except OSError:
            tail = []
        res = {"job_id": job_id, "code": code, "state": job.state if job else None, "log": str(log), "log_tail": tail}
        if code == Q.EXIT_TIMEOUT and (job is None or not job.final):
            res["error"] = f"timed out after {timeout:g} s; the job is still {job.state if job else 'unknown'}"
        return res

    # -- definitions --------------------------------------------------------------------------------------------

    def defs(self, action: Literal["types", "type", "guid", "show", "grep"], query: str = "",
             path: str | None = None, root: str | None = None, max_lines: int = 400,
             limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        from collections import Counter

        from ..defs import query as DQ
        from ..defs import se2defs as S

        def content_root() -> Path:
            return Path(root) if root else S.default_root(self.env)

        if action == "show":
            if not query:
                raise ToolFailure("show needs a definition file path or GUID in `query`.")
            f, d = DQ.find_definition(content_root, query)
            value = DQ.select(d, path) if path else d
            text = json.dumps(value, indent=2)
            if text.count("\n") + 1 <= max_lines:
                return {"file": str(f), "value": value}
            return {"file": str(f), "truncated": True, "text": DQ.bounded(text, max_lines),
                    "hint": "pass `path` (dotted keys and list indexes) for a sub-tree, or a larger max_lines"}
        if action != "types" and not query:
            raise ToolFailure(f"{action} needs `query`.")
        base = content_root()
        rows: list = []
        if action == "types":
            counts: Counter = Counter()
            for p in S.iter_defs(base):
                if (d := S.load(p)):
                    counts[S.short_type(d.get("$Type"))] += 1
            rows = [{"type": t, "count": n} for t, n in counts.most_common()
                    if not query or query.lower() in t.lower()]
        elif action == "type":
            for p in S.iter_defs(base):
                d = S.load(p)
                if d and query.lower() in (d.get("$Type") or "").lower():
                    rows.append({"guid": (d.get("$Value") or {}).get("Guid", ""), "path": S._rel(p, base)})
        elif action == "guid":
            g = query.lower()
            for p in S.iter_defs(base):
                if g not in S._read(p).lower():
                    continue
                d = S.load(p)
                own = bool(d) and str((d.get("$Value") or {}).get("Guid", "")).lower() == g
                rows.append({"relation": "defines" if own else "references", "path": S._rel(p, base),
                             "type": S.short_type(d.get("$Type")) if d else None})
        else:   # grep
            import re
            try:
                rx = re.compile(query, re.I)
            except re.error as e:
                raise ToolFailure(f"bad regular expression {query!r}: {e}") from e
            rows = [S._rel(p, base) for p in S.iter_defs(base) if rx.search(S._read(p))]
        return {"root": str(base), "count": len(rows), "truncated": len(rows) > limit, "results": rows[:limit]}

    def chain(self, action: Literal["show", "resolve", "lineage", "find"], target: str,
              fields: list[str] | None = None, root: str | None = None, limit: int = 20) -> dict[str, Any]:
        from ..defs import block_chain as bc
        from ..defs import query as DQ
        idx = self._index(root)
        if action == "show":
            bl, hits = bc.show_hits(idx, target)
            return {"root": str(idx.root), "count": len(hits), "truncated": len(hits) > limit,
                    "blocks": [bl.describe(g) for g in hits[:limit]]}
        if action == "find":
            import re
            try:
                rx = re.compile(target, re.I)
            except re.error as e:
                raise ToolFailure(f"bad regular expression {target!r}: {e}") from e
            rows = [{"guid": g, "path": idx.rel(e.path)}
                    for g, e in sorted(bc.Blocks(idx).bindings(), key=lambda x: str(x[1].path))
                    if rx.search(idx.rel(e.path))]
            return {"root": str(idx.root), "count": len(rows), "truncated": len(rows) > DEFAULT_LIMIT,
                    "results": rows[:DEFAULT_LIMIT]}
        if action == "lineage":
            return {"root": str(idx.root),
                    "lineage": [{"guid": e.guid, "kind": e.kind, "type": e.type, "path": idx.rel(e.path)}
                                for e in idx.lineage(target)],
                    "overrides": [idx.rel(o.path) for o in idx.overrides.get(target.lower(), [])]}
        value = idx.resolve(target)
        if value is None:
            raise bc.DefsError(f"{target} is not found in {idx.root}.")
        entry = idx.by_guid[target.lower()]
        if fields:
            names = DQ.parse_fields(",".join(fields))
            return {"fields": {f: DQ.FIELDS[f](value, entry, idx) for f in names}}
        return {"file": idx.rel(entry.path), "type": entry.type, "value": _jsonable(value)}

    # -- game update checks -------------------------------------------------------------------------------------

    def deps_check(self, deps_file: str | None = None, workspace: str | None = None,
                   min_risk: Literal["low", "med", "high"] = "low", refs: bool = False,
                   no_guids: bool = False) -> dict[str, Any]:
        import os
        import time

        from ..deps import check
        out = Path(self.env.cache_dir) / "deps" / f"mcp-report-{os.getpid()}-{int(time.time() * 1000)}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        code, text = self._captured(lambda: check.run(
            self.env, deps_file=Path(deps_file) if deps_file else None,
            workspace=Path(workspace) if workspace else None, refs=refs, no_guids=no_guids, min_risk=min_risk,
            report_json=out, quiet=True))
        try:
            findings = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
        finally:
            out.unlink(missing_ok=True)
        meaning = {0: "nothing at risk changed", 1: "a high-risk dependency changed",
                   2: "a medium- or high-risk dependency is missing", 3: "no dependencies were checked"}
        return {"code": code, "verdict": meaning.get(code, "unknown"),
                "summary": [line for line in text.splitlines() if line.strip() and "report written" not in line],
                "findings": findings}

    # -- registry -------------------------------------------------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        """Every served tool, bound to this toolbox, with its description and hints."""
        g = _guarded
        return [
            ToolSpec("look", g(self.look), "Render a look set", (
                "Render a fast look set of one block from its working file (.blend, .glb, .gltf, .fbx; read, never "
                "written) in the look-dev scene, through the job queue, like `se2mod look`. With wait (default) it "
                "waits for the render, checks the set and returns set_dir, sheet, report_path, not_ready (what keeps "
                "the set back; empty means ready) and geocheck (the geometry checks' file). With wait=false it "
                "returns job_id and set_dir: call queue_wait, then look_finish. material maps working-file "
                "materials to Keen slots ({\"rb_Bolt\": \"MetalDull\"}); close names parts to close up on. out, when "
                "given, must be in the modkit cache or the named mod's preview folder."),
                read_only=False),
            ToolSpec("look_finish", g(self.look_finish), "Check a rendered look set", (
                "Check a look set whose render job has ended (after look with wait=false and queue_wait): makes the "
                "contact sheet and report.json and returns sheet, not_ready and geocheck. set_dir is "
                "<look folder>/<block>/r<N>, in the modkit cache or the named mod's preview folder."),
                read_only=False, idempotent=True),
            ToolSpec("geocheck", g(self.geocheck), "Read a look set's geometry checks", (
                "Read the geometry check results of a look set (geocheck.json in set_dir): passed, failures, "
                "warnings, plus not_ready from its report.json. Reads only."), read_only=True, idempotent=True),
            ToolSpec("describe", g(self.describe), "Describe a block model as numbers", (
                "A block model as numbers, like `se2mod describe`: one headless Blender job through the queue (it "
                "never saves the .blend) returns every shipping part with role, size and center in mm, grid cell, "
                "nearest part and gap, contacts and mirror partner, then floating parts, overlaps, mirror errors and "
                "the proportion targets of a targets markdown file. envelope is the block size in meters [x, y, z]. "
                "json_out, when given, must be in the modkit cache or the named mod's preview folder. code 0 pass, "
                "1 a target failed, 2 the run failed."), read_only=False),
            ToolSpec("ship_check", g(self.ship_check), "Check blocks before an export", (
                "Check blocks before an export, like `se2mod ship check`: the queue order first (nothing is queued "
                "when a save of the working .blend waits behind or runs beside a build or export), then one headless "
                "Blender job, which runs the mod's own model build script, reporting what ships, the coplanar-face "
                "audit, the fracture check and an exploded zone "
                "picture. Returns each block's report with problems and warnings, and where its ship.json and "
                "picture are. code 0 pass, 1 a FAIL, 2 the run failed."), read_only=False),
            ToolSpec("queue_status", g(self.queue_status), "Show the job queue", (
                "The GPU job queue: whether the runner runs or is paused, and each job (unfinished plus the last 10 "
                "finished, or every kept job with all=true) with id, name, state, load, GPU, elapsed seconds, exit "
                "code and log path, waiting jobs in start order. Reads only."), read_only=True, idempotent=True),
            ToolSpec("queue_submit", g(self.queue_submit), "Queue a command", (
                "Queue a command (argument list) on the GPU job queue, like `se2mod queue submit`; the only way to "
                "run Blender or another GPU-heavy program. {gpu} in the command becomes the chosen device index. "
                "load: heavy (renders, exports), medium, light, or a number. gpu: auto, none or an index. Returns "
                "job_id and its log path. The command runs as given, so it can change or delete files."),
                read_only=False, destructive=True),
            ToolSpec("queue_wait", g(self.queue_wait), "Wait for a queued job", (
                "Wait for a queued job to end (at most timeout seconds) and return its exit code (124 timed out, "
                "125 cancelled), state, log path and the last lines of its log. Reads only."),
                read_only=True, idempotent=True),
            ToolSpec("defs", g(self.defs), "Look up game definitions", (
                "Look up compiled game definitions (.def files; root defaults to the game's vanilla content), like "
                "`se2mod defs`. action: types (counts per $Type, query filters), type (files whose $Type contains "
                "query), guid (which file defines query and which reference it), show (one definition by file path "
                "or GUID; path selects a sub-tree such as Components.0.Name), grep (files matching the regex query). "
                "Reads only."), read_only=True, idempotent=True),
            ToolSpec("chain", g(self.chain), "Resolve vanilla block definition chains", (
                "Resolve vanilla block definition chains from the Mod SDK sources (root defaults to its "
                "Vanilla/Assets), like `se2mod chain`. action: show (a block's binding, prefabs, composites, "
                "components, menu and unlock; target is a GUID or binding path fragment), resolve (one definition "
                "fully merged; fields picks guid, name, type, kind, file, base), lineage (its Copy chain), find "
                "(bindings whose path matches the regex target). The index is kept between calls. Reads only."),
                read_only=True, idempotent=True),
            ToolSpec("deps_check", g(self.deps_check), "Check recorded game dependencies", (
                "Re-check every recorded game dependency (hashes, GUIDs, anchors, build ids) after a game update, "
                "like `se2mod deps check` (deps_file defaults to the project's our-game-dependencies.json). Returns "
                "code (0 fine, 1 a high-risk change, 2 something missing, 3 nothing checked), the summary lines and "
                "each finding. Reads only; never writes a rebaselined file."), read_only=True, idempotent=True),
        ]


def tool_names() -> list[str]:
    return [s.name for s in Toolbox(env=object()).specs()]
