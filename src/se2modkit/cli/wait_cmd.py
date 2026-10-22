"""`se2mod wait`: block until the game or hub closed, a PID exited, or a file appeared (exit 124 on timeout)."""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..env import procs
from . import register

TIMEOUT_EXIT = 124
_monotonic = time.monotonic  # replaced in tests
_sleep = time.sleep


def _seconds(text: str, *, allow_zero: bool) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise argparse.ArgumentTypeError(
            f"{text!r} must be a finite number of seconds " + (">= 0" if allow_zero else "> 0"))
    return value


def _timeout(text: str) -> float:
    return _seconds(text, allow_zero=True)


def _interval(text: str) -> float:
    return _seconds(text, allow_zero=False)


def _pid(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a process id") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("PID must be a positive integer")
    return value


def _add_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="wait_what", metavar="CONDITION", required=True)
    for name, text in (("game-closed", "no Space Engineers 2 process is running"),
                       ("hub-closed", "no Mod SDK hub/editor process is running"),
                       ("pid", "process PID has exited"),
                       ("file", "file PATH exists (and contains TEXT)")):
        s = sub.add_parser(name, help=text, description=text)
        if name == "pid":
            s.add_argument("pid_value", type=_pid, metavar="PID")
        if name == "file":
            s.add_argument("path", type=Path)
            s.add_argument("--contains", metavar="TEXT", default=None, help="also require this text in the file")
        s.add_argument("--timeout", type=_timeout, default=600.0, metavar="S", help="give up after S seconds (default 600)")
        s.add_argument("--interval", type=_interval, default=2.0, metavar="S", help="poll every S seconds (default 2)")


def _file_ready(path: Path, text: str | None) -> bool:
    try:
        if text is None:
            return path.exists()
        return text in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _pid_gone(pid: int) -> bool:
    listing = procs.running_processes()
    return bool(listing) and pid not in {p[0] for p in listing}  # an empty listing is "unknown", keep waiting


def _condition(args: argparse.Namespace) -> tuple[Callable[[], bool], str]:
    what = args.wait_what
    if what == "game-closed":
        return (lambda: procs.listing_available() and not procs.game_running()), "Space Engineers 2 to close"
    if what == "hub-closed":
        return (lambda: procs.listing_available() and not procs.find_processes(procs.HUB_PROCS)), "the Mod SDK hub to close"
    if what == "pid":
        pid = args.pid_value
        return (lambda: _pid_gone(pid)), f"process {pid} to exit"
    detail = f"{args.path} to contain {args.contains!r}" if args.contains else f"{args.path} to exist"
    return (lambda: _file_ready(args.path, args.contains)), detail


@register("wait", "Wait for a condition: game-closed, hub-closed, pid PID, file PATH [--contains TEXT]",
          add_args=_add_args, check_game_version=False)
def cmd_wait(args, env) -> int:
    done, label = _condition(args)
    interval = max(args.interval, 0.01)
    deadline = _monotonic() + max(args.timeout, 0.0)
    try:
        while True:
            if done():
                return 0
            remaining = deadline - _monotonic()
            if remaining <= 0:
                print(f"timed out after {args.timeout:g}s waiting for {label}", file=sys.stderr)
                return TIMEOUT_EXIT
            _sleep(min(interval, remaining))
    except KeyboardInterrupt:
        print(f"interrupted while waiting for {label}", file=sys.stderr)
        return 130
