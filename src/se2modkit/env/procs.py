"""Running-process discovery (cross-platform); `game_running()` replaces every `pgrep` call."""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

GAME_PROCS = ("Modern.bin", "SpaceEngineers2.exe", "Interim.bin")
HUB_PROCS = ("VRAGE3.ModHUB.exe", "VRage.Editor.exe")
TEXCONV = ("texconv.exe", "texconv")


def _basename(arg: str) -> str:
    return arg.replace("\\", "/").rsplit("/", 1)[-1]


def _from_proc(proc: Path) -> list[tuple[int, str, str, str]]:
    """(pid, comm, argv0 basename, cmdline); argv0 is split on NUL so Wine paths with spaces stay whole."""
    out = []
    me = os.getpid()
    try:
        entries = list(proc.iterdir())
    except OSError:
        return out
    for d in entries:
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            comm = (d / "comm").read_text(encoding="utf-8", errors="replace").strip()
            raw = (d / "cmdline").read_bytes()
        except OSError:
            continue
        argv0 = raw.split(b"\0", 1)[0].decode(errors="replace")
        cmd = raw.replace(b"\0", b" ").decode(errors="replace").strip()
        out.append((int(d.name), comm, _basename(argv0), cmd))
    return out


def _parse_tasklist(text: str) -> list[tuple[int, str, str, str]]:
    """Rows of `tasklist /fo csv /nh`: image name, PID, ... (no command line is available there)."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[1].isdigit():
            out.append((int(row[1]), row[0], row[0], row[0]))
    return out


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def running_processes(*, proc: Path | None = None) -> list[tuple[int, str, str]]:
    """(pid, name, cmdline) for every process we can see; an empty list if the platform gives no answer."""
    return [(pid, comm, cmd) for pid, comm, _, cmd in _entries(proc)]


def _entries(proc: Path | None) -> list[tuple[int, str, str, str]]:
    if proc is not None or sys.platform.startswith("linux"):
        return _from_proc(proc or Path("/proc"))
    if sys.platform.startswith("win"):
        return _parse_tasklist(_run(["tasklist", "/fo", "csv", "/nh"]))
    out = []
    me = os.getpid()
    for line in _run(["ps", "-axo", "pid=,args="]).splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != me:
            cmd = cmd.strip()
            name = Path(cmd.split(" ", 1)[0]).name
            out.append((int(pid), name, name, cmd))
    return out


def listing_available(*, proc: Path | None = None) -> bool:
    """True if the platform gave a process list at all (it always holds some process besides ours when it did)."""
    return bool(_entries(proc))


def find_processes(names: tuple[str, ...], *, proc: Path | None = None) -> list[tuple[int, str]]:
    """Processes whose name (comm, or argv[0] basename for Wine processes) matches one of `names`, ignoring case."""
    hits = []
    low = {n.lower() for n in names}
    comms = low | {n[:15] for n in low}  # Linux truncates comm to 15 characters
    for pid, comm, argv0, cmd in _entries(proc):
        if comm.lower() in comms or argv0.lower() in low:
            hits.append((pid, cmd or comm))
    return hits


def game_running(*, proc: Path | None = None, environ: Mapping[str, str] = os.environ) -> bool:
    """True if Space Engineers 2 (native, Proton or Pulsar `SE2_PROCESS_NAME`) is running."""
    names = GAME_PROCS + ((environ["SE2_PROCESS_NAME"],) if environ.get("SE2_PROCESS_NAME") else ())
    return bool(find_processes(names, proc=proc))
