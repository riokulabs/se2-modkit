"""`se2mod test [MOD] [--fast] [--changed [--base REF]] [--module M] [--block B] [--full]`: the test tiers.

Runs `python -m pytest` (this interpreter) in the mod root on the mod's `Tests/` dir, in parallel when pytest-xdist is
installed, captures the output into a log under the user cache and prints one summary line plus the failure sections
only. The summary is read from pytest's own final line; if that cannot be found, the last lines are printed raw.

`--changed`, `--module` and `--block` pick test modules through the static import graph (`se2modkit.testsel`); they are
the inner loop. `--full` (whole suite, parallel) is the gate before a commit or a review.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from .. import testsel
from ..project import ProjectError, load_project
from . import modarg, register

__all__ = ["pytest_argv", "resolve_mod"]

_FLAGS = ["-q", "-rfE", "--no-header", "-p", "no:cacheprovider", "--tb=short"]
_TAIL = 20
KEEP_LOGS = 20
GRACE_SECONDS = 10.0
GATE_NOTE = "static selection — `se2mod test --full` is the gate"


class RunError(Exception):
    """A problem the user can fix; reported as one sentence."""



def _tests_dir(root: Path) -> Path | None:
    for name in ("Tests", "tests"):
        if (root / name).is_dir():
            return root / name
    return None


def resolve_mod(selector: str | None, cwd: Path, env=None) -> tuple[str, Path, Path]:
    """(name, mod root, tests dir) for a mod given by name or path (the shared MOD resolver), or by `cwd` itself.

    A `[tool.se2modkit]` project is used when there is one; otherwise a directory with a `Tests/` dir is the mod."""
    if selector is not None:
        try:
            ref = modarg.resolve(env, selector, need="dir", cwd=cwd)
        except ProjectError as e:
            raise RunError(str(e)) from None
        root, name = ref.root.resolve(), ref.name
    else:
        try:
            mod = load_project(cwd).mod(None, cwd=cwd)
            root, name = mod.root.resolve(), mod.name
        except ProjectError as e:
            if not str(e).startswith("No pyproject.toml"):  # e.g. a multi-mod repo root: "which mod?" with the names
                raise RunError(str(e)) from None
            here = cwd.resolve()
            root = next((d for d in (here, *here.parents) if _tests_dir(d) and d != Path(d.anchor)), None)
            if root is None:
                raise RunError("No mod here: pass a mod name or directory (one with a Tests/ dir).") from None
            name = root.name
    tests = _tests_dir(root)
    if tests is None:
        raise RunError(f"{root} has no Tests/ directory to run.")
    return name, root, tests


def _xdist() -> bool:
    try:
        return importlib.util.find_spec("xdist") is not None
    except (ImportError, ValueError):
        return False


def pytest_argv(targets: list[str], *, marker: str | None, workers: int | str | None,
                gates: list[str] = ()) -> list[str]:
    argv = [sys.executable, "-m", "pytest", *_FLAGS]
    for g in gates:                     # the se2modkit plugin's own selection (testing.plugin)
        argv += ["--se2-gate", g]
    if workers is not None:
        argv += ["-n", str(workers), "--dist", "loadfile"]
    if marker:
        argv += ["-m", marker]
    return argv + targets


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST_") and k not in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION")}
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env["PYTHONUNBUFFERED"] = "1"  # a killed run still leaves its last lines in the log
    return env


def _log_path(cache_dir: Path, name: str) -> Path | None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "mod"
    d = Path(cache_dir) / "test-logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    p, i = d / f"{safe}-{stamp}.log", 1
    while p.exists():
        p, i = d / f"{safe}-{stamp}-{i}.log", i + 1
    return p


def _prune_logs(log: Path, name: str, keep: int = KEEP_LOGS) -> None:
    """Keep the newest `keep` logs of this mod (`<mod>-YYYYmmdd-HHMMSS[-N].log`); other mods' logs are untouched."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "mod"
    own = re.compile(re.escape(safe) + r"-\d{8}-\d{6}(?:-\d+)?\.log")
    try:
        mine = [p for p in log.parent.iterdir() if own.fullmatch(p.name)]
        mine.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
        for old in mine[keep:]:
            if old != log:
                old.unlink()
    except OSError:
        pass


def _run(argv: list[str], cwd: Path, log: Path | None, timeout: float | None) -> tuple[int, str, bool]:
    """Run pytest into `log` (or a pipe); returns (exit code, output, timed out). pytest runs in its own process group:
    on a timeout or Ctrl-C the group first gets SIGINT (Windows: CTRL_BREAK_EVENT), which pytest turns into a
    KeyboardInterrupt so fixtures tear down (a mod's project restore, say); stock pytest has no SIGTERM handler. After
    GRACE_SECONDS it gets SIGTERM, after another GRACE_SECONDS SIGKILL, so xdist workers never linger."""
    header = f"$ (cd {shlex.quote(str(cwd))} && {shlex.join(argv)})\n"
    sink = open(log, "w", encoding="utf-8", newline="\n") if log else None
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    try:
        if sink:
            sink.write(header)
            sink.flush()
        proc = subprocess.Popen(argv, cwd=cwd, env=_child_env(), stdin=subprocess.DEVNULL,
                                stdout=sink or subprocess.PIPE, stderr=subprocess.STDOUT, **group)
        timed_out = False
        try:
            piped, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            piped = _stop(proc)
        except KeyboardInterrupt:
            _stop(proc)
            raise
    finally:
        if sink:
            sink.close()
    if log:
        text = log.read_text(encoding="utf-8", errors="replace")[len(header):]
    else:
        text = (piped or b"").decode("utf-8", "replace")
    return proc.returncode, text, timed_out


def _signal_group(proc: subprocess.Popen, sig) -> None:
    try:
        if os.name == "nt":
            proc.send_signal(sig) if sig is not None else proc.terminate()
        else:
            os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _stop(proc: subprocess.Popen) -> bytes:
    """Stop the group in three steps, each followed by up to GRACE_SECONDS of waiting: SIGINT (pytest tears its
    fixtures down), SIGTERM, SIGKILL; on Windows CTRL_BREAK_EVENT, terminate(), kill(). Timeouts and Ctrl-C take
    the same path. Returns whatever was piped meanwhile."""
    if os.name == "nt":
        steps = [lambda: _signal_group(proc, signal.CTRL_BREAK_EVENT), proc.terminate, proc.kill]
    else:
        steps = [lambda sig=sig: _signal_group(proc, sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL)]
    out = b""
    for i, step in enumerate(steps):
        try:
            step()
        except OSError:
            pass
        try:
            out, _ = proc.communicate(timeout=None if i == len(steps) - 1 else GRACE_SECONDS)
            break
        except subprocess.TimeoutExpired:
            continue
    if os.name != "nt":
        _signal_group(proc, signal.SIGKILL)  # stragglers (xdist workers) left in the group
    return out or b""


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)


def _add_args(p) -> None:
    p.add_argument("mod", nargs="?", help="mod name (project in the current directory) or path to a mod directory "
                   "(default: the mod containing the current directory)")
    p.add_argument("--fast", action="store_true",
                   help="leave out tests marked build, sdkbuild or slow (only markers the mod registers)")
    p.add_argument("--changed", action="store_true",
                   help="only tests reached from files changed versus HEAD (working tree, index, untracked)")
    p.add_argument("--base", metavar="REF", help="with --changed: also include changes versus REF (e.g. main)")
    p.add_argument("--fallback-full", action="store_true",
                   help="with --changed: run the whole suite when no test maps to the changes")
    p.add_argument("--module", action="append", default=[], metavar="M",
                   help="only tests importing module M (dotted name or .py path), transitively; repeatable")
    p.add_argument("--block", action="append", default=[], metavar="B",
                   help="only tests whose file, or a module they import, is named after B; repeatable")
    p.add_argument("--gate", action="append", default=[], metavar="NAME",
                   help="only the shared gate NAME of [tool.se2modkit] gates (repeatable); every other test is "
                        "deselected")
    p.add_argument("--full", action="store_true", help="the pre-commit gate: whole suite, parallel")
    p.add_argument("--serial", action="store_true", help="do not use pytest-xdist")
    p.add_argument("--list", action="store_true",
                   help="print the selected test files and the pytest command line, run nothing")
    p.add_argument("--max-lines", type=int, default=200, metavar="N",
                   help="cap on printed failure lines (default 200; the full log is always written)")
    p.add_argument("--timeout", type=float, default=3600, metavar="SECONDS",
                   help="kill the run after this long (default 3600)")


@register("test", "Run a mod's tests: one summary line plus failures (fast, changed, module, block, full tiers)",
          add_args=_add_args, check_game_version=False)
def cmd_test(args, env) -> int:
    try:
        return _cmd(args, env)
    except (RunError, testsel.SelectionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _cmd(args, env) -> int:
    selecting = args.changed or args.module or args.block
    if args.full and selecting:
        print("error: --full runs the whole suite; drop --changed/--module/--block (or drop --full).", file=sys.stderr)
        return 2
    if args.gate:
        from ..testing.gates import GATES
        unknown = [g for g in args.gate if g not in GATES]
        if unknown:
            print(f"error: Unknown gate {unknown[0]!r}; known: {', '.join(GATES)}.", file=sys.stderr)
            return 2
        if selecting or args.full:
            print("error: --gate picks shared gates; drop --changed/--module/--block/--full (or drop --gate).",
                  file=sys.stderr)
            return 2
    if (args.base or args.fallback_full) and not args.changed:
        print("error: --base and --fallback-full only apply with --changed.", file=sys.stderr)
        return 2
    name, root, tests_dir = resolve_mod(args.mod, Path.cwd(), env)

    targets: list[str] = [_rel(tests_dir, root)]
    picked: list[Path] | None = None
    if selecting:
        graph = testsel.ImportGraph.build(root, tests_dir)
        chosen: set[Path] = set()
        for m in args.module:
            chosen.update(graph.tests_for_module(m))
        for b in args.block:
            chosen.update(graph.tests_for_block(b))
        if args.changed:
            changed = testsel.changed_files(root, args.base)
            hits = graph.tests_for(changed)
            if not changed:
                print(f"{name}: no changed files versus {args.base or 'HEAD'}; nothing to run.")
            elif not hits:
                shown = ", ".join(_rel(c, root) + ("" if c.exists() else " (deleted)") for c in changed[:5])
                shown += " ..." if len(changed) > 5 else ""
                print(f"{name}: no test maps to the {len(changed)} changed file(s) ({shown}); nothing to run.")
            chosen.update(hits)
            if not chosen and args.fallback_full:
                print(f"{name}: --fallback-full: running the whole suite.")
                chosen = set()
                selecting = False
        if selecting:
            print(GATE_NOTE)
            if not chosen:
                if not args.changed:
                    print(f"{name}: no test matches the selection; nothing to run.")
                return 0
            picked = sorted(chosen)
            targets = [_rel(t, root) for t in picked]

    marker = testsel.fast_expression(testsel.registered_markers(root, tests_dir)) if args.fast else None
    workers: int | str | None = None
    note = ""
    if not args.serial:
        if _xdist():
            n = min(os.cpu_count() or 1, len(picked)) if picked is not None else "auto"
            workers = n if n != 1 else None
        else:
            note = "pytest-xdist is not installed, running serially"
    argv = pytest_argv(targets, marker=marker, workers=workers, gates=args.gate)
    if args.list:
        for t in targets:
            print(t)
        print(f"(cd {shlex.quote(str(root))} && {shlex.join(argv)})")
        return 0
    log = _log_path(env.cache_dir, name)

    started = time.monotonic()
    code, output, timed_out = _run(argv, root, log, args.timeout)
    where = f"full log: {log}" if log else "no log written (cache dir not writable)"
    if log:
        _prune_logs(log, name)
    if timed_out:
        print(f"error: {name}: pytest did not finish within {args.timeout:g} s and was stopped; {where}",
              file=sys.stderr)
        for line in output.rstrip().splitlines()[-_TAIL:]:
            print(line)
        return 1
    extras = [f"{len(picked)} test files" if picked is not None else _rel(tests_dir, root)]
    extras.append(f"-n {workers} --dist loadfile" if workers is not None else "serial")
    if marker:
        extras.append(f'-m "{marker}"')
    elif args.fast:
        extras.append("--fast: the mod registers none of build/sdkbuild/slow")
    if note:
        extras.append(note)

    summary = testsel.parse_summary(output)
    if summary is None:
        tail = output.rstrip().splitlines()[-_TAIL:]
        print(f"{name}: could not read pytest's summary (exit {code}, {time.monotonic() - started:.1f} s); "
              f"last {len(tail)} lines follow; {where}")
        for line in tail:
            print(line)
        return code or 1
    head = summary.line()
    if code == 5:
        head = "no tests ran" + (f" ({summary.get('deselected')} deselected)" if summary.get("deselected") else "")
        head += f" in {summary.seconds:g}s"
    elif code != 0:
        head += f" FAILED (pytest exit {code})"  # never reads as success, whatever the counts say
    print(f"{head} | {name}: {', '.join(extras)}")
    if code not in (0, 5) or summary.get("failed") or summary.get("errors"):
        sections = testsel.failure_sections(output)
        cap = max(args.max_lines, 1)
        for line in sections[:cap]:
            print(line)
        if code not in (0, 1) or not sections:
            # an internal error, an interrupted session or a crashing hook: what pytest printed last explains it
            tail = output.rstrip().splitlines()[-_TAIL:]
            print(f"--- last {len(tail)} lines of output ---")
            for line in tail:
                print(line)
        more = len(sections) - cap
        print(f"... {more} more lines; {where}" if more > 0 else where)
    return code
