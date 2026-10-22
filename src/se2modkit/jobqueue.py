"""A job queue for GPU-heavy work (Blender renders and exports), run within a power budget.

Two full-load GPU jobs at once can draw more power than a workstation's UPS supplies. Tools submit jobs here instead of
starting Blender themselves; one runner process starts them in order while their combined load fits the budget.

Each job has a load: heavy 1.0, medium 0.5, light 0.25, or a number. The total budget (`[queue] budget`, default 1.0)
and the per-GPU budget (`[queue] gpu_budget`, default 1.0) cap what runs at once, so under the defaults a heavy job
runs alone and up to four light jobs share. A Blender the queue did not start, and the running game, each count as a
heavy load on an unknown GPU.

Storage is a folder (normally `<cache>/queue`): `jobs/<id>.json` and `jobs/<id>.log` per job, `queue.lock` taken by
everyone who changes a job file, `runner.lock` held by the one runner for its lifetime (it holds the runner's PID),
`runner.log` for the runner's progress lines.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import gpu as gpumod
from . import procgroup, progress
from .env import procs

LOADS = {"heavy": 1.0, "medium": 0.5, "light": 0.25}
HEAVY = LOADS["heavy"]
KILL_GRACE = 10.0             # seconds between SIGTERM and SIGKILL when a job is cancelled or times out
IDLE_EXIT = 600.0             # the runner exits after this long with nothing queued or running
TICK = 0.5                    # runner poll interval, seconds
SAMPLE_WINDOW = 1.0           # seconds of GPU load sampling before a placement
EXIT_TIMEOUT = 124
EXIT_CANCELLED = 125
FINAL = ("done", "failed", "cancelled", "timeout", "lost")
# Captured from the submitting shell, so that a detached runner can start windowed programs (Blender exports run
# without -b) on the user's display, and find the same programs on PATH.
CAPTURED_ENV = ("PATH", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
_EPS = 1e-9


class QueueError(Exception):
    """A queue request that cannot be carried out, reported as one sentence."""


# --- settings -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    budget: float = 1.0           # total load that may run at once
    gpu_budget: float = 1.0       # load that may run at once on one GPU
    idle_exit: float = IDLE_EXIT  # runner idle timeout, seconds
    count_outside: bool = True    # count Blender processes the queue did not start, and the game, as heavy loads

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> Settings:
        """From the `[queue]` table of the user config."""
        raw = config.get("queue", {})
        if not isinstance(raw, Mapping):
            raise QueueError("The user config key `queue` must be a table ([queue]).")
        kw: dict[str, object] = {}
        for key in ("budget", "gpu_budget", "idle_exit"):
            if key in raw:
                val = raw[key]
                if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
                    raise QueueError(f"The user config value queue.{key} must be a positive number.")
                kw[key] = float(val)
        if "count_outside" in raw:
            if not isinstance(raw["count_outside"], bool):
                raise QueueError("The user config value queue.count_outside must be true or false.")
            kw["count_outside"] = raw["count_outside"]
        return cls(**kw)


def parse_load(text: str | float) -> float:
    """`heavy`, `medium`, `light` or a positive number."""
    if isinstance(text, str) and text.lower() in LOADS:
        return LOADS[text.lower()]
    try:
        val = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{text!r} is not heavy, medium, light or a number") from None
    if not val > 0 or val == float("inf"):
        raise ValueError(f"load {text!r} must be a positive number")
    return val


def load_label(load: float) -> str:
    for name, val in LOADS.items():
        if abs(load - val) < _EPS:
            return name
    return f"{load:g}"


def parse_gpu(text: str) -> str | int:
    """`auto`, `none` or a Vulkan device index."""
    if text in ("auto", "none"):
        return text
    try:
        val = int(text)
    except ValueError:
        raise ValueError(f"{text!r} is not auto, none or a device index") from None
    if val < 0:
        raise ValueError("a device index must be 0 or more")
    return val


# --- jobs -----------------------------------------------------------------------------------------------------------

@dataclass
class JobRecord:
    id: int
    argv: list[str]
    name: str = ""
    load: float = HEAVY
    gpu_req: str | int = "auto"          # what was asked for
    cwd: str = ""
    timeout: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    state: str = "queued"                # queued, running, then one of FINAL
    gpu: int | None = None               # the device it was given (None: no GPU choice)
    pid: int | None = None
    submitted: float = 0.0
    started: float | None = None
    ended: float | None = None
    exit: int | None = None
    cancel: bool = False                 # cancel requested
    stopping: str = ""                   # "cancel" or "timeout" once the runner signalled the job

    @property
    def final(self) -> bool:
        return self.state in FINAL

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping) -> JobRecord:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def elapsed(self, now: float) -> float | None:
        if self.started is None:
            return None
        return (self.ended if self.ended is not None else now) - self.started


def exit_code(job: JobRecord) -> int:
    """What `se2mod queue wait` exits with for a finished job."""
    if job.state == "timeout":
        return EXIT_TIMEOUT
    if job.state == "cancelled":
        return EXIT_CANCELLED
    if job.state == "lost":
        return 1
    return job.exit if job.exit is not None else 1


# --- file locks -----------------------------------------------------------------------------------------------------

class FileLock:
    """An exclusive lock on a file (flock on POSIX, msvcrt on Windows), released on close or process exit."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self, blocking: bool = True, timeout: float = 60.0) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        deadline = time.monotonic() + timeout
        while True:
            if _try_lock(fd):
                self._fd = fd
                return True
            if not blocking or time.monotonic() > deadline:
                os.close(fd)
                if blocking:
                    raise QueueError(f"Timed out waiting for the queue lock {self.path}.")
                return False
            time.sleep(0.02)

    def release(self) -> None:
        if self._fd is not None:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None

    def write(self, text: str) -> None:
        """Replace the lock file's content (the runner writes its PID there)."""
        if self._fd is None:
            raise QueueError("lock not held")
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.ftruncate(self._fd, 0)
        os.write(self._fd, text.encode())

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


# --- the store ------------------------------------------------------------------------------------------------------

class Store:
    """The queue folder. Every change to a job file happens under `lock()`."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.jobs_dir = self.root / "jobs"
        self.runner_log = self.root / "runner.log"
        self._lock = FileLock(self.root / "queue.lock")

    @contextmanager
    def lock(self) -> Iterator[None]:
        with self._lock:
            yield

    def job_file(self, job_id: int) -> Path:
        return self.jobs_dir / f"{job_id:06d}.json"

    def log_path(self, job_id: int) -> Path:
        return self.jobs_dir / f"{job_id:06d}.log"

    def load(self, job_id: int) -> JobRecord | None:
        try:
            return JobRecord.from_dict(json.loads(self.job_file(job_id).read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            return None

    def all(self) -> list[JobRecord]:
        out = []
        if not self.jobs_dir.is_dir():
            return out
        for p in sorted(self.jobs_dir.glob("*.json")):
            if p.stem.isdigit() and (job := self.load(int(p.stem))) is not None:
                out.append(job)
        return sorted(out, key=lambda j: j.id)

    def save(self, job: JobRecord) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = self.job_file(job.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), indent=1) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)

    def submit(self, argv: list[str], *, name: str = "", load: float = HEAVY, gpu: str | int = "auto",
               cwd: Path | str | None = None, timeout: float | None = None, env: Mapping[str, str] | None = None,
               environ: Mapping[str, str] = os.environ) -> JobRecord:
        """Add a job; returns it with its new id. `environ` is the submitter's environment (see CAPTURED_ENV)."""
        if not argv:
            raise QueueError("Nothing to run: put the command after --.")
        job_env = {k: environ[k] for k in CAPTURED_ENV if environ.get(k)}
        job_env.update(env or {})
        with self.lock():
            counter = self.root / "next-id"
            try:
                job_id = int(counter.read_text(encoding="utf-8").strip() or 1)
            except (OSError, ValueError):
                job_id = 1
            job_id = max([job_id, *(j.id + 1 for j in self.all())])
            counter.write_text(f"{job_id + 1}\n", encoding="utf-8", newline="\n")
            job = JobRecord(id=job_id, argv=list(argv), name=name or Path(argv[0]).name, load=load, gpu_req=gpu,
                            cwd=str(Path(cwd if cwd is not None else os.getcwd()).resolve()), timeout=timeout,
                            env=job_env, submitted=time.time())
            self.save(job)
        return job

    def cancel(self, job_ids: list[int] | None) -> list[JobRecord]:
        """Cancel queued jobs now and flag running ones for the runner; None cancels every unfinished job."""
        changed = []
        with self.lock():
            jobs = self.all() if job_ids is None else [j for i in job_ids if (j := self.load(i)) is not None]
            for job in jobs:
                if job.final:
                    continue
                if job.state == "queued":
                    job.state, job.ended, job.cancel = "cancelled", time.time(), True
                else:
                    job.cancel = True
                self.save(job)
                changed.append(job)
        return changed

    def prune(self, older_than: float, now: float | None = None) -> None:
        """Delete finished jobs (and their logs) that ended more than `older_than` seconds ago."""
        now = time.time() if now is None else now
        with self.lock():
            for job in self.all():
                if job.final and job.ended is not None and now - job.ended > older_than:
                    for p in (self.job_file(job.id), self.log_path(job.id)):
                        p.unlink(missing_ok=True)

    # the runner lock ---------------------------------------------------------------------------------------------

    def runner_lock(self) -> FileLock:
        return FileLock(self.root / "runner.lock")

    def runner_alive(self) -> bool:
        lock = self.runner_lock()
        if lock.acquire(blocking=False):
            lock.release()
            return False
        return True

    def runner_pid(self) -> int | None:
        try:
            text = (self.root / "runner.lock").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None


def runner_command(root: Path) -> list[str]:
    return [sys.executable, "-c", "import sys; from se2modkit.cli import main; sys.exit(main(sys.argv[1:]))",
            "queue", "--queue-dir", str(root), "run"]


def spawn_runner(root: Path) -> subprocess.Popen:
    """Start a detached runner (it outlives the caller); its output goes to runner.log."""
    root.mkdir(parents=True, exist_ok=True)
    log = root / "runner.log"
    try:
        if log.stat().st_size > 1 << 20:
            os.replace(log, root / "runner.log.1")
    except OSError:
        pass
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    with open(log, "a", encoding="utf-8", newline="\n") as sink:
        return subprocess.Popen(runner_command(root), stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
                                close_fds=True, **kw)


def ensure_runner(store: Store, spawn: Callable[[Path], object] | None = None) -> bool:
    """Start a runner unless one is running; True if one was started. A second runner started by a racing
    submitter finds the runner lock taken and exits at once."""
    if store.runner_alive():
        return False
    (spawn or spawn_runner)(store.root)
    return True


# --- scheduling -----------------------------------------------------------------------------------------------------

def plan(waiting: list[JobRecord], running: list[JobRecord], *, outside: float, gpus: list[gpumod.Gpu],
         settings: Settings) -> list[tuple[JobRecord, int | None]]:
    """Which waiting jobs to start now, and on which GPU (None: no GPU choice; the command gets device 0).

    Jobs start in FIFO order while their load fits the total budget and their GPU's budget. A job that does not fit
    keeps its load reserved against the total, so a later job may start ahead of it only where that cannot delay it:
    a waiting heavy job therefore lets nothing pass while it waits for the running jobs to finish. A job larger than a
    budget is counted as exactly that budget, so it runs alone instead of never.
    """
    budget, gpu_budget = settings.budget, settings.gpu_budget
    used = outside + sum(min(j.load, budget) for j in running)
    per = {g.index: 0.0 for g in gpus}
    for j in running:
        if j.gpu is not None and j.gpu_req != "none":
            per[j.gpu] = per.get(j.gpu, 0.0) + min(j.load, gpu_budget)
    busy = {g.index: g for g in gpus}
    reserved = 0.0
    out: list[tuple[JobRecord, int | None]] = []
    for job in waiting:
        load, gload = min(job.load, budget), min(job.load, gpu_budget)
        if used + reserved + load > budget + _EPS:
            reserved += load
            continue
        choice: int | None
        if job.gpu_req == "none" or (job.gpu_req == "auto" and not gpus):
            choice = None
        elif isinstance(job.gpu_req, int):
            choice = job.gpu_req if per.get(job.gpu_req, 0.0) + gload <= gpu_budget + _EPS else -1
        else:
            room = [i for i in per if per[i] + gload <= gpu_budget + _EPS]
            # Least busy card first; load the queue already put on a card counts as busy (1.0 = 100 %), because a
            # job started a moment ago does not show in the sampled load yet.
            choice = min(room, key=lambda i: (busy[i].busy + 100 * per[i], busy[i].vram, i)) if room else -1
        if choice == -1:
            reserved += load
            continue
        used += load
        if choice is not None:
            per[choice] = per.get(choice, 0.0) + gload
        out.append((job, choice))
    return out


def _session_id(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[3])   # fields after "(comm)": state ppid pgrp session
    except (OSError, ValueError, IndexError):
        return None


def outside_load(own_pids: set[int], settings: Settings) -> float:
    """Heavy loads the queue did not start: each other Blender, and the running game."""
    if not settings.count_outside:
        return 0.0
    load = HEAVY if procs.game_running() else 0.0
    blenders = gpumod.blender_processes()
    if sys.platform.startswith("linux"):
        # A job's Blender runs in the job's session (each job starts a new session led by its own PID).
        load += HEAVY * sum(1 for pid, _ in blenders if pid not in own_pids and _session_id(pid) not in own_pids)
    else:
        load += HEAVY * max(0, len(blenders) - len(own_pids))
    return load


# --- the runner -----------------------------------------------------------------------------------------------------

class Runner:
    """Starts queued jobs within the budget, enforces timeouts and cancels, and records how each job ended."""

    def __init__(self, store: Store, settings: Settings, *,
                 sampler: Callable[[], list[gpumod.Gpu]] | None = None,
                 outside: Callable[[set[int]], float] | None = None,
                 clock: Callable[[], float] = time.time, log: Callable[[str], None] = progress.log):
        self.store, self.settings, self.clock, self.log = store, settings, clock, log
        self.sampler = sampler if sampler is not None else self._sample
        self.outside = outside if outside is not None else (lambda own: outside_load(own, settings))
        self.procs: dict[int, subprocess.Popen] = {}
        self.kill_at: dict[int, float] = {}
        self._devices: list[tuple[int, str]] | None = None

    def _sample(self) -> list[gpumod.Gpu]:
        if not gpumod.supported():
            return []
        if self._devices is None:
            self._devices = gpumod.vulkan_devices()
        return gpumod.sample(self._devices, SAMPLE_WINDOW) if self._devices else []

    # job lifecycle ----------------------------------------------------------------------------------------------

    def recover(self) -> None:
        """Jobs marked running by a runner that is gone cannot be followed any more (they stopped with it)."""
        with self.store.lock():
            for job in self.store.all():
                if job.state == "running" and job.id not in self.procs:
                    job.state, job.ended = "lost", self.clock()
                    self.store.save(job)
                    self.log(f"lost: job {job.id} {job.name} (its runner stopped)")

    def _start(self, job: JobRecord, device: int | None) -> None:
        dev = 0 if device is None else device
        argv = [a.replace("{gpu}", str(dev)) for a in job.argv]
        env = dict(os.environ)
        env.update(job.env)
        env["SE2_GPU_DEVICE"] = str(dev)
        log = self.store.log_path(job.id)
        job.gpu, job.started = device, self.clock()
        try:
            proc = procgroup.start(procgroup.Job(argv, log, env=env, cwd=Path(job.cwd) if job.cwd else None))
        except OSError as e:
            with open(log, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"cannot start {argv[0]}: {e}\n")
            job.state, job.exit, job.ended = "failed", 127, self.clock()
            self.store.save(job)
            self.log(f"FAILED: job {job.id} {job.name}: cannot start {argv[0]}: {e}")
            return
        self.procs[job.id] = proc
        job.state, job.pid = "running", proc.pid
        self.store.save(job)
        where = "no GPU choice" if device is None else f"GPU {device}"
        self.log(f"> job {job.id} {job.name} ({load_label(job.load)}, {where})")

    def _finish(self, job: JobRecord, proc: subprocess.Popen) -> None:
        code = procgroup.reap(proc)
        del self.procs[job.id]
        self.kill_at.pop(job.id, None)
        job.exit = 128 - code if code < 0 else code
        job.ended = self.clock()
        if job.stopping == "timeout":
            job.state = "timeout"
        elif job.stopping == "cancel":
            job.state = "cancelled"
        else:
            job.state = "done" if job.exit == 0 else "failed"
        self.store.save(job)
        took = f"{job.elapsed(job.ended):.0f}s"
        word = {"done": "done", "failed": f"FAILED (exit {job.exit})", "timeout": "timed out",
                "cancelled": "cancelled"}[job.state]
        self.log(f"{word}: job {job.id} {job.name} ({took})")

    def _stop(self, job: JobRecord, proc: subprocess.Popen, why: str, now: float) -> None:
        job.stopping = why
        self.kill_at[job.id] = now + KILL_GRACE
        procgroup.signal_group(proc, "terminate")
        self.store.save(job)

    def tick(self) -> bool:
        """One pass: reap, enforce cancels and timeouts, start what fits. True while anything is queued or running."""
        now = self.clock()
        with self.store.lock():
            for job in self.store.all():
                proc = self.procs.get(job.id)
                if job.state != "running" or proc is None:
                    continue
                if procgroup.exited(proc):
                    self._finish(job, proc)
                elif job.id in self.kill_at:
                    if now >= self.kill_at[job.id]:
                        procgroup.signal_group(proc, "kill")
                        self.kill_at[job.id] = float("inf")
                elif job.cancel:
                    self._stop(job, proc, "cancel", now)
                elif job.timeout is not None and job.started is not None and now - job.started > job.timeout:
                    self._stop(job, proc, "timeout", now)
                    self.log(f"  ... job {job.id} {job.name} passed its {job.timeout:g}s timeout; stopping it")
            waiting = [j for j in self.store.all() if j.state == "queued"]
        if not waiting:
            return bool(self.procs)
        own = {p.pid for p in self.procs.values()}
        outside = self.outside(own)
        gpus = self.sampler() if any(j.gpu_req == "auto" for j in waiting) else []
        with self.store.lock():
            jobs = self.store.all()
            waiting = [j for j in jobs if j.state == "queued" and not j.cancel]
            running = [j for j in jobs if j.state == "running"]
            for job, device in plan(waiting, running, outside=outside, gpus=gpus, settings=self.settings):
                self._start(job, device)
        return True

    def stop_all(self) -> None:
        """Stop every job this runner started (the runner itself is being stopped)."""
        with self.store.lock():
            for job_id, proc in list(self.procs.items()):
                procgroup.stop(proc, KILL_GRACE)
                job = self.store.load(job_id)
                if job is not None and not job.final:
                    job.stopping = job.stopping or "cancel"
                    self._finish(job, proc)
            self.procs.clear()
            self.kill_at.clear()

    def run(self, idle_exit: float | None = None, sleep: Callable[[float], None] = time.sleep) -> int:
        """Run until idle for `idle_exit` seconds. Returns 0, also when another runner already holds the lock."""
        idle_exit = self.settings.idle_exit if idle_exit is None else idle_exit
        lock = self.store.runner_lock()
        if not lock.acquire(blocking=False):
            self.log("another queue runner is already running; exiting")
            return 0
        lock.write(str(os.getpid()))
        previous = None
        if os.name != "nt":
            def _term(_sig, _frame):
                raise KeyboardInterrupt
            previous = signal.signal(signal.SIGTERM, _term)
        try:
            self.log(f"queue runner {os.getpid()} started (budget {self.settings.budget:g}, "
                     f"per GPU {self.settings.gpu_budget:g})")
            self.store.prune(7 * 86400)
            self.recover()
            idle_since = time.monotonic()
            while True:
                if self.tick():
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since >= idle_exit:
                    with self.store.lock():
                        if any(j.state == "queued" for j in self.store.all()):
                            continue
                        lock.release()   # a submitter that writes after this starts a new runner
                    self.log(f"queue runner {os.getpid()} idle for {idle_exit:g}s; exiting")
                    return 0
                sleep(TICK)
        except KeyboardInterrupt:
            self.log("queue runner stopped; stopping its jobs")
            self.stop_all()
            return 130
        finally:
            lock.release()
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)


def wait(store: Store, job_id: int, timeout: float, *, poll: float = 0.5,
         spawn: Callable[[Path], object] | None = None) -> int:
    """Block until the job ends; its exit code (124 when `timeout` passes first, 125 when cancelled)."""
    deadline = time.monotonic() + timeout
    checked_runner = 0.0
    while True:
        job = store.load(job_id)
        if job is None:
            raise QueueError(f"There is no job {job_id} in {store.root}.")
        if job.final:
            return exit_code(job)
        if time.monotonic() - checked_runner > 5.0:   # a runner that died is replaced (its running jobs are lost)
            ensure_runner(store, spawn)
            checked_runner = time.monotonic()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return EXIT_TIMEOUT
        time.sleep(min(poll, remaining))


def format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


def status_lines(store: Store, jobs: list[JobRecord], now: float | None = None) -> list[str]:
    """One line per job: id, name, load, state, GPU, elapsed, exit (and the last log line of a running job)."""
    now = time.time() if now is None else now
    rows = [("ID", "NAME", "LOAD", "STATE", "GPU", "ELAPSED", "EXIT")]
    tails = [""]
    for j in jobs:
        dev = "-" if j.state == "queued" else ("none" if j.gpu is None else str(j.gpu))
        state = j.state + ("*" if j.cancel and not j.final else "")
        rows.append((str(j.id), j.name, load_label(j.load), state, dev, format_elapsed(j.elapsed(now)),
                     "-" if j.exit is None else str(j.exit)))
        tails.append(f"  {progress.last_line(store.log_path(j.id), 80)}" if j.state == "running" else "")
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [("  ".join(c.ljust(w) for c, w in zip(r, widths)) + t).rstrip() for r, t in zip(rows, tails)]
