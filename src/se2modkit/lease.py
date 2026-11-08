"""Named, timed leases on shared files.

Two sessions (or two people) sometimes edit the same file, a shared `.blend` most often, and saving over each other
loses work. A lease says who is editing a file and until when. It is advisory: nothing stops a write, but tools and
agents check `active()` before saving and take a lease first. Leases live as JSON files in `<cache>/locks/`, one per
file (keyed by the file's resolved path, case-folded where the OS ignores case), and every change happens under one
`FileLock` and lands by an atomic replace, so a reader never sees a half-written lease.

A few well-known names lease a shared resource rather than a file: `live-blender` is the one live, windowed Blender
(`se2mod queue blender`), which every session drives through the same MCP port. The name means that resource wherever
it is used, never a file of that name in the current folder.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .jobqueue import FileLock, QueueError

DEFAULT_TTL = 4 * 3600.0
LIVE_BLENDER = "live-blender"
NAMED = {LIVE_BLENDER: "the live, windowed Blender (se2mod queue blender) and the file open in it"}
LOCK_TIMEOUT = 60.0  # seconds to wait for another se2mod to finish with the lease directory


@dataclass(frozen=True)
class Lease:
    path: str      # the leased file, resolved
    owner: str
    since: float   # epoch seconds
    ttl: float     # seconds

    def expired(self, now: float) -> bool:
        return now > self.since + self.ttl


class LeaseHeld(Exception):
    """The file is leased to someone else."""

    def __init__(self, lease: Lease):
        super().__init__(f"{lease.path} is held by {lease.owner}")
        self.lease = lease


class LeaseBusy(Exception):
    """Another se2mod kept the lease directory locked past LOCK_TIMEOUT."""


def parse_ttl(text: str) -> float:
    """Seconds from `90`, `30m`, `4h` or `1.5h`."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", text)
    if not m:
        raise ValueError(f"not a duration: {text!r} (use seconds, or a number with s, m or h)")
    seconds = float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]
    _check_ttl(seconds)
    return seconds


def _check_ttl(ttl: float) -> None:
    if ttl <= 0:
        raise ValueError(f"a lease must last more than zero seconds, not {ttl:g}")


def _dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / "locks"


def target(path: Path | str) -> str:
    """What a lease on `path` is keyed by: a well-known name (see NAMED) as it is, else the file's resolved path."""
    return str(path) if str(path) in NAMED else str(Path(path).resolve())


def _file(cache_dir: Path, path: Path | str) -> tuple[Path, str]:
    resolved = target(path)
    key = resolved if resolved in NAMED else os.path.normcase(resolved)  # C:\Foo and c:\foo: one file on Windows
    return _dir(cache_dir) / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json"), resolved


class _locked:
    """The lease directory's FileLock, with a timeout that raises LeaseBusy instead of the queue's error."""

    def __init__(self, cache_dir: Path):
        self._lock = FileLock(_dir(cache_dir) / ".lock")

    def __enter__(self) -> None:
        try:
            self._lock.acquire(timeout=LOCK_TIMEOUT)
        except QueueError:
            raise LeaseBusy(f"Another se2mod kept the lease list locked for {LOCK_TIMEOUT:g} s; try again.") from None

    def __exit__(self, *exc) -> None:
        self._lock.release()


def _read(f: Path) -> Lease | None:
    try:
        return Lease(**json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _write(f: Path, lease: Lease) -> None:
    tmp = f.with_name(f.name + ".tmp")  # one writer at a time (we hold the lock), so a fixed name is safe
    try:
        tmp.write_text(json.dumps(asdict(lease)), encoding="utf-8")
        os.replace(tmp, f)
    finally:
        tmp.unlink(missing_ok=True)


def take(cache_dir: Path, path: Path | str, *, owner: str, ttl: float = DEFAULT_TTL, now: float | None = None) -> Lease:
    """Lease `path` to `owner` (renewing it if they already hold it); raises LeaseHeld if someone else does."""
    _check_ttl(ttl)
    now = time.time() if now is None else now
    f, resolved = _file(cache_dir, path)
    with _locked(cache_dir):
        held = _read(f)
        if held and not held.expired(now) and held.owner != owner:
            raise LeaseHeld(held)
        lease = Lease(path=resolved, owner=owner, since=now, ttl=ttl)
        _write(f, lease)
        return lease


def release(cache_dir: Path, path: Path | str, *, owner: str, force: bool = False, now: float | None = None) -> bool:
    """Drop the lease on `path`; False when there was none. Only its owner may, unless `force`."""
    now = time.time() if now is None else now
    f, _ = _file(cache_dir, path)
    with _locked(cache_dir):
        held = _read(f)
        if held is None:
            return False
        if held.owner != owner and not force and not held.expired(now):
            raise LeaseHeld(held)
        f.unlink(missing_ok=True)
        return True


def active(cache_dir: Path, *, now: float | None = None) -> list[Lease]:
    """Every lease not yet expired, oldest first."""
    now = time.time() if now is None else now
    leases = [x for f in sorted(_dir(cache_dir).glob("*.json")) if (x := _read(f)) and not x.expired(now)]
    return sorted(leases, key=lambda x: x.since)


def holder_name(owner: str | None) -> str:
    """The name a lease is held under: `owner`, else $SE2_SESSION, else the user name (as `se2mod lock` does)."""
    return owner or os.environ.get("SE2_SESSION") or getpass.getuser()


def held_by_other(cache_dir: Path, path: Path | str, *, owner: str, now: float | None = None) -> Lease | None:
    """The lease on `path` when someone other than `owner` holds it and it has not expired, else None. A read only:
    leases land by an atomic replace, so no lock is needed."""
    now = time.time() if now is None else now
    held = _read(_file(cache_dir, path)[0])
    return held if held and not held.expired(now) and held.owner != owner else None
