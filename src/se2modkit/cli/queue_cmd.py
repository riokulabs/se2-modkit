"""`se2mod queue`: run GPU-heavy commands (Blender renders and exports) one after another, or side by side within a
power budget. See docs/render-queue.md."""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

from .. import jobqueue as Q
from ..env.config import load_user_config
from . import register

_follow_sleep = time.sleep   # replaced in tests


def _seconds(text: str) -> float:
    try:
        val = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not math.isfinite(val) or val <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} must be a positive number of seconds")
    return val


def _load(text: str) -> float:
    try:
        return Q.parse_load(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _gpu(text: str) -> str | int:
    try:
        return Q.parse_gpu(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _kv(text: str) -> tuple[str, str]:
    key, sep, val = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"{text!r} is not KEY=VALUE")
    return key, val


def _job_id(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"{text!r} is not a job id")
    return int(text)


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--queue-dir", type=Path, default=None, metavar="DIR",
                   help="queue folder (default: <cache>/queue)")
    sub = p.add_subparsers(dest="queue_cmd", metavar="SUBCOMMAND", required=True)

    s = sub.add_parser("submit", help="queue a command; prints its job id and starts the runner if needed",
                       description="Queue CMD (after --). {gpu} in CMD becomes the chosen Vulkan device index, which "
                                   "is also exported as SE2_GPU_DEVICE.")
    s.add_argument("--name", default="", help="label shown by status (default: the program name)")
    s.add_argument("--load", type=_load, default=Q.HEAVY, metavar="heavy|medium|light|N",
                   help="power load: heavy 1.0 (default), medium 0.5, light 0.25, or a number")
    s.add_argument("--gpu", type=_gpu, default="auto", metavar="auto|none|N",
                   help="auto: least busy GPU with room (default); none: no GPU choice; N: that device")
    s.add_argument("--cwd", type=Path, default=None, metavar="DIR", help="working directory (default: here)")
    s.add_argument("--timeout", type=_seconds, default=None, metavar="SECONDS",
                   help="stop the job after this long (SIGTERM, then SIGKILL 10 s later)")
    s.add_argument("--env", type=_kv, action="append", default=[], metavar="K=V",
                   help="extra environment variable for the job (repeatable)")
    s.add_argument("script_args", nargs="*", metavar="CMD", help="the command and its arguments, after --")

    w = sub.add_parser("wait", help="wait for a job; exits with its exit code (124 timeout, 125 cancelled)")
    w.add_argument("job", type=_job_id, metavar="ID")
    w.add_argument("--timeout", type=_seconds, default=3600.0, metavar="S",
                   help="give up after S seconds with exit 124 (default 3600)")

    for name in ("status", "list"):
        st = sub.add_parser(name, help="one line per job: id, name, load, state, GPU, elapsed, exit code")
        st.add_argument("--all", action="store_true", help="every kept job (default: unfinished plus the last 10)")

    lg = sub.add_parser("log", help="print a job's output")
    lg.add_argument("job", type=_job_id, metavar="ID")
    lg.add_argument("-f", "--follow", action="store_true", help="keep printing until the job ends")

    c = sub.add_parser("cancel", help="cancel jobs: SIGTERM to the job's process group, SIGKILL 10 s later")
    c.add_argument("jobs", type=_job_id, nargs="*", metavar="ID")
    c.add_argument("--all", action="store_true", help="cancel every queued and running job")

    r = sub.add_parser("run", help="run the queue runner in the foreground (normally started by submit)")
    r.add_argument("--idle-exit", type=_seconds, default=None, metavar="S",
                   help="exit after S seconds with nothing to do (default: config queue.idle_exit, else 600)")


def _settings(env) -> Q.Settings:
    return Q.Settings.from_config(load_user_config(env.config_dir / "config.toml"))


@register("queue", "Job queue for GPU-heavy work: queue submit|wait|status|log|cancel|run", add_args=_add_args,
          check_game_version=False)
def cmd_queue(args, env) -> int:
    store = Q.Store(args.queue_dir if args.queue_dir is not None else env.cache_dir / "queue")
    try:
        return _dispatch(args, env, store)
    except Q.QueueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _dispatch(args, env, store: Q.Store) -> int:
    what = args.queue_cmd
    if what == "submit":
        argv = list(args.script_args)
        if argv[:1] == ["--"]:
            argv = argv[1:]
        _settings(env)   # a broken [queue] table is reported now, not later in the runner log
        job = store.submit(argv, name=args.name, load=args.load, gpu=args.gpu, cwd=args.cwd, timeout=args.timeout,
                           env=dict(args.env))
        print(job.id)
        Q.ensure_runner(store)
        return 0
    if what == "wait":
        code = Q.wait(store, args.job, args.timeout)
        job = store.load(args.job)
        if code == Q.EXIT_TIMEOUT and (job is None or not job.final):
            print(f"timed out after {args.timeout:g}s waiting for job {args.job}", file=sys.stderr)
        return code
    if what in ("status", "list"):
        jobs = store.all()
        if not args.all:
            done = [j for j in jobs if j.final]
            jobs = [j for j in jobs if not j.final or j in done[-10:]]
        if store.runner_alive():
            pid = store.runner_pid()
            runner = f"running (pid {pid})" if pid else "running"
        else:
            runner = "not running"
        print(f"runner: {runner}; log: {store.runner_log}")
        if jobs:
            print("\n".join(Q.status_lines(store, jobs)))
        return 0
    if what == "log":
        return _log(store, args.job, args.follow)
    if what == "cancel":
        if not args.all and not args.jobs:
            raise Q.QueueError("Name a job id, or use --all.")
        changed = store.cancel(None if args.all else args.jobs)
        for job in changed:
            print(f"cancelled job {job.id} {job.name}" if job.final else
                  f"stopping job {job.id} {job.name} (SIGTERM, then SIGKILL after {Q.KILL_GRACE:g}s)")
        if not changed:
            print("nothing to cancel")
        return 0
    if what == "run":
        return Q.Runner(store, _settings(env)).run(idle_exit=args.idle_exit)
    raise Q.QueueError(f"unknown queue command {what}")


def _log(store: Q.Store, job_id: int, follow: bool) -> int:
    if store.load(job_id) is None:
        raise Q.QueueError(f"There is no job {job_id} in {store.root}.")
    path, pos = store.log_path(job_id), 0
    try:
        while True:
            job = store.load(job_id)
            try:
                with path.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos += len(chunk)
            except FileNotFoundError:
                chunk = b""
            if chunk:
                sys.stdout.write(chunk.decode(errors="replace"))
                sys.stdout.flush()
            if not follow or job is None or job.final:
                return 0
            _follow_sleep(0.5)
    except KeyboardInterrupt:
        return 130
