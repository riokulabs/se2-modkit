"""Child processes that never outlive their run: each in its own process group, stopped as a group.

`run_all(jobs, timeout)` starts every job at once (stdout and stderr into the job's log), waits, and on the first
failure, on the timeout or on Ctrl-C stops every job still running: the group gets SIGTERM (Windows: CTRL_BREAK_EVENT),
then after GRACE_SECONDS SIGKILL (Windows: kill()). On Linux a child also gets SIGTERM if this process dies first
(PR_SET_PDEATHSIG), so a killed `se2mod` leaves no Blender behind. Every child is waited for (no zombie). A group is
signalled only while its leader is not yet reaped (os.waitid with WNOWAIT), so a recycled group id is never hit; where
waitid is missing a stray grandchild of a job that already finished is the accepted risk.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["GRACE_SECONDS", "Job", "Result", "exited", "reap", "run_all", "signal_group", "start", "stop"]

GRACE_SECONDS = 5.0
_POLL = 0.1


@dataclass
class Job:
    argv: list[str]
    log: Path
    env: Mapping[str, str] | None = None
    cwd: Path | None = None
    tag: str = ""                     # what the caller calls it (e.g. "worker 2")
    data: dict = field(default_factory=dict)


@dataclass
class Result:
    job: Job
    code: int | None                  # None: stopped before it finished
    stopped: bool = False             # stopped by us (another job failed, the timeout, Ctrl-C)
    pid: int = 0


def _die_with_parent() -> None:   # runs in the child (Linux), after setsid
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001 (best effort: the group kill still applies)
        pass


def _start(job: Job) -> subprocess.Popen:
    job.log.parent.mkdir(parents=True, exist_ok=True)
    sink = open(job.log, "w", encoding="utf-8", newline="\n")
    kw: dict = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    if sys.platform.startswith("linux"):
        kw["preexec_fn"] = _die_with_parent
    try:
        return subprocess.Popen(job.argv, cwd=job.cwd, env=None if job.env is None else dict(job.env),
                                stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT, **kw)
    finally:
        sink.close()   # the child holds its own handle


def _signal(proc: subprocess.Popen, sig) -> None:
    try:
        if os.name == "nt":
            proc.send_signal(sig) if sig is not None else proc.kill()
        else:
            os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _exited(proc: subprocess.Popen) -> bool:
    """Whether the leader exited, without reaping it where the OS allows (its unreaped zombie keeps the group id
    reserved, so signalling the group can never reach an unrelated process)."""
    if proc.returncode is not None:
        return True
    waitid = getattr(os, "waitid", None)
    if os.name == "nt" or waitid is None:
        return proc.poll() is not None
    try:
        return waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return proc.poll() is not None


def _reap(proc: subprocess.Popen) -> int:
    """Kill what is left of the group while the leader is still unreaped (POSIX), then reap the leader. Without
    waitid (the leader may already be reaped) the group is not signalled: a stray grandchild is the accepted risk,
    never a signal to a recycled group id."""
    if os.name != "nt" and proc.returncode is None and getattr(os, "waitid", None) is not None:
        _signal(proc, signal.SIGKILL)
    return proc.wait()


def stop(proc: subprocess.Popen, grace: float = GRACE_SECONDS) -> None:
    """Stop the process and its group: a polite signal, then after `grace` seconds a kill; always waited for."""
    if not _exited(proc):
        _signal(proc, signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        deadline = time.monotonic() + grace
        while not _exited(proc) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not _exited(proc):
            _signal(proc, None if os.name == "nt" else signal.SIGKILL)
    _reap(proc)


def start(job: Job) -> subprocess.Popen:
    """Start one job in its own process group, output into its log (for callers that schedule jobs themselves)."""
    return _start(job)


def exited(proc: subprocess.Popen) -> bool:
    """Whether the job's leader exited; it stays unreaped until `reap`, so its group id is not reused before then."""
    return _exited(proc)


def reap(proc: subprocess.Popen) -> int:
    """Kill what is left of the job's group and reap the leader; returns its exit code."""
    return _reap(proc)


def signal_group(proc: subprocess.Popen, sig) -> None:
    """Signal the job's whole group while the leader is unreaped. `terminate` maps to SIGTERM (Windows:
    CTRL_BREAK_EVENT), `kill` to SIGKILL (Windows: kill())."""
    if not _exited(proc):
        if sig == "terminate":
            _signal(proc, signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        elif sig == "kill":
            _signal(proc, None if os.name == "nt" else signal.SIGKILL)
        else:
            raise ValueError(f"unknown signal {sig!r}")


def run_all(jobs: list[Job], timeout: float | None, *, grace: float = GRACE_SECONDS) -> list[Result]:
    """Run `jobs` in parallel; returns one Result per job, in order. The first job to fail (non-zero exit) stops the
    others; so does the timeout (every running job is then `stopped`). Ctrl-C stops them all and re-raises."""
    procs: list[subprocess.Popen] = []
    results = [Result(j, None) for j in jobs]
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        for j, r in zip(jobs, results):
            p = _start(j)
            procs.append(p)
            r.pid = p.pid
        while True:
            failed = False
            for p, r in zip(procs, results):
                if r.code is None and not r.stopped and _exited(p):
                    r.code = _reap(p)
                    failed |= r.code != 0
            if all(r.code is not None for r in results):
                return results
            if failed or (deadline is not None and time.monotonic() > deadline):
                for p, r in zip(procs, results):
                    if r.code is None:
                        stop(p, grace)
                        r.stopped = True
                return results
            time.sleep(_POLL)
    except BaseException:
        for p, r in zip(procs, results):
            if p.returncode is None:
                stop(p, grace)
                r.stopped = True
        raise
    finally:
        for p in procs:     # every child reaped, its group cleared first, whatever happened
            if p.returncode is None:
                stop(p, grace)
