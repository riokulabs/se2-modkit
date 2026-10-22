"""Progress lines for long steps (publishing, queued jobs), on stderr so the output on stdout stays unchanged.

    [20:48:11 +125s] > SDK builder: prepare for publish
    [20:48:26 +140s]   ... still running (15s); log: <latest builder log line>
    [20:50:02 +236s] done: SDK builder: prepare for publish (111s)
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path

HEARTBEAT = 15.0           # seconds between "still running" lines
_T0 = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.monotonic() - _T0:.0f}s] {msg}", file=sys.stderr, flush=True)


@contextmanager
def step(name: str):
    log(f"> {name}")
    start = time.monotonic()
    try:
        yield
    except BaseException:
        log(f"FAILED: {name} ({time.monotonic() - start:.0f}s)")
        raise
    log(f"done: {name} ({time.monotonic() - start:.0f}s)")


def last_line(path: Path, limit: int = 160) -> str:
    """The last non-empty line of a growing log file (reads only its tail)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            lines = [ln.strip() for ln in f.read().decode(errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return lines[-1][:limit] if lines else ""


class Heartbeat:
    """Call tick() in a wait loop; it logs at most once per HEARTBEAT seconds."""

    def __init__(self, what: str, log_file: Path | None = None, every: float = HEARTBEAT):
        self.what, self.log_file, self.every = what, log_file, every
        self.start = self.last = time.monotonic()

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last < self.every:
            return
        self.last = now
        tail = f"; log: {last_line(self.log_file)}" if self.log_file else ""
        log(f"  ... {self.what} still running ({now - self.start:.0f}s){tail}")
