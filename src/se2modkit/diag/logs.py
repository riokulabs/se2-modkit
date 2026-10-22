"""Game log reading: entries (timestamp, thread, text with continuation lines), known-bad patterns, scans, tail.

Game logs: ~/.config/SpaceEngineers2/Temp/Logs/SpaceEngineers2_<yyMMdd>_<HHmmss>_<ms>_<pid>.log (the main log),
plus _Render12.log, _Mission.log and (Proton, on exit) _Stats.log. Line format:
    2026-10-20 03:06:23.026 - Main Thread ->  message
Lines that do not start with a timestamp continue the previous entry (JSON dumps, stack traces).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import locations as paths

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) - (.+?) -> ?(.*)$")
NAME_RE = re.compile(r"^SpaceEngineers2_(\d{6})_(\d{6})_(\d+)_(\d+)(?:_([A-Za-z0-9]+))?\.log$")
SESSION_MARK = "LOADING_BEGIN|Session"


@dataclass
class Entry:
    lineno: int
    ts: datetime | None
    thread: str
    text: str
    extra: list = field(default_factory=list)   # continuation lines

    @property
    def full(self) -> str:
        return self.text if not self.extra else self.text + "\n" + "\n".join(self.extra)


def parse_ts(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def iter_entries(lines):
    cur = None
    for i, raw in enumerate(lines, 1):
        line = raw.rstrip("\n").rstrip("\r")
        m = LINE_RE.match(line)
        if m:
            if cur is not None:
                yield cur
            cur = Entry(i, parse_ts(m.group(1)), m.group(2).strip(), m.group(3).lstrip())
        elif cur is not None:
            if len(cur.extra) < 400:
                cur.extra.append(line)
        else:
            cur = Entry(i, None, "", line)
    if cur is not None:
        yield cur


def read_entries(path: Path) -> list:
    with open(path, encoding="utf-8", errors="replace") as f:
        return list(iter_entries(f))


# ---- finding logs ----
def classify(path: Path) -> str | None:
    """'main', 'render12', 'mission', 'stats' (or the suffix lower-cased), None if not a game log name."""
    m = NAME_RE.match(path.name)
    if not m:
        return None
    return (m.group(5) or "main").lower()


def pid_of(path: Path) -> int | None:
    m = NAME_RE.match(path.name)
    return int(m.group(4)) if m else None


def sibling(main_log: Path, kind: str) -> Path:
    """The _Render12 / _Mission / _Stats file of the same run."""
    suffix = {"render12": "_Render12", "mission": "_Mission", "stats": "_Stats"}[kind]
    return main_log.with_name(main_log.stem + suffix + ".log")


def find_logs(directory: Path | None = None, kind: str = "main") -> list:
    d = directory or paths.log_dir()
    if d is None or not d.is_dir():
        return []
    logs = [p for p in d.iterdir() if p.is_file() and classify(p) == kind]
    return sorted(logs, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def newest_log(directory: Path | None = None, pid: int | None = None) -> Path | None:
    logs = find_logs(directory)
    if pid is not None:
        mine = [p for p in logs if pid_of(p) == pid]
        if mine:
            return mine[0]
    return logs[0] if logs else None


# ---- known-bad patterns ----
@dataclass(frozen=True)
class Pattern:
    key: str
    regex: str
    severity: str     # error | warn | info
    hint: str


PATTERNS = [
    Pattern("client_on_server", r"should not be on server compositions", "error",
            "a client component reached a server composition"),
    Pattern("missing_linked_definition", r"Definition that is linked from", "error",
            "a binding's server prefab/composite is missing"),
    Pattern("definition_validation", r"Error: During definition validation|Validation failed for", "error",
            "definition validation failed; the world may not load"),
    Pattern("definition_validation_warning", r"Warning: During definition validation", "warn",
            "a definition validation warning (the definition still loads)"),
    Pattern("exception", r"Exception occurred:|NOT FATAL exception|\bUnhandled exception\b|^\s*[A-Za-z0-9_.]+Exception: ", "error",
            "an exception was logged (see the entry's stack)"),
    Pattern("assertion", r"Assert\.FailInternal|AssertionFailed|\bAssertion\b", "warn",
            "a debug assertion fired (Release builds log and continue)"),
    Pattern("device_removal", r"device remov|DEVICE_REMOVED|DeviceRemoved|device lost|VK_ERROR_DEVICE_LOST", "error",
            "GPU device removed/lost (note: the crash log prints 'device removal' text for any render exception)"),
    Pattern("shader_compilation_failed", r"Compilation failed|Can't compile pso", "error",
            "a shader or PSO failed to compile"),
    Pattern("timespan_overflow", r"TimeSpan overflowed|TimeSpan does not accept", "error",
            "the Stopwatch/TimeSpan uptime bug (native Linux, uptime > ~10.7 days)"),
    Pattern("out_of_memory", r"OutOfMemory|out of memory", "error", "memory exhausted"),
    Pattern("fence_timeout", r"[Ff]ence.{0,40}(time ?out|timed out)|WaitForFence", "error",
            "render fence wait timed out (LinuxCompat present sync, SpaceGT/Pulsar#75)"),
    Pattern("missing_loc_key", r"Could not find key", "info",
            "a localisation key was not found; expected for mod texts (their culture loads with the world)"),
    Pattern("error_line", r"^Error:|\bError: ", "warn", "an Error-level log line"),
    Pattern("linuxcompat", r"\[LinuxCompat\]", "info", "a LinuxCompat log line"),
]


def compile_patterns(extra: list | None = None) -> list:
    pats = list(PATTERNS)
    for i, rx in enumerate(extra or []):
        pats.append(Pattern(f"grep{i + 1}", rx, "info", f"user pattern {rx!r}"))
    return [(p, re.compile(p.regex, re.M | (re.I if p.key in ("device_removal", "out_of_memory") else 0))) for p in pats]


def last_session_start(entries: list) -> int:
    """Index of the last world-load start (LOADING_BEGIN|Session), or 0."""
    idx = 0
    for i, e in enumerate(entries):
        if SESSION_MARK in e.text:
            idx = i
    return idx


def scan(entries: list, extra_patterns: list | None = None, since_session: bool = False, samples: int = 3) -> dict:
    start = last_session_start(entries) if since_session else 0
    pats = compile_patterns(extra_patterns)
    out = {p.key: {"severity": p.severity, "hint": p.hint, "count": 0, "first": None, "last": None, "samples": []}
           for p, _ in pats}
    for e in entries[start:]:
        # match the first line and the first continuation lines (exception type lines), not whole JSON dumps
        hay = e.text if not e.extra else e.text + "\n" + "\n".join(e.extra[:3])
        for p, rx in pats:
            if rx.search(hay):
                rec = out[p.key]
                rec["count"] += 1
                where = {"line": e.lineno, "time": e.ts.strftime("%H:%M:%S.%f")[:-3] if e.ts else None,
                         "thread": e.thread, "text": e.text[:300]}
                rec["first"] = rec["first"] or where
                rec["last"] = where
                if len(rec["samples"]) < samples:
                    rec["samples"].append(where)
    span = entries[start:]
    return {"entries": len(span), "from_line": span[0].lineno if span else None,
            "since": "last world load" if since_session else "process start",
            "start": span[0].ts.isoformat(sep=" ") if span and span[0].ts else None,
            "end": span[-1].ts.isoformat(sep=" ") if span and span[-1].ts else None,
            "patterns": out}


def grep(entries: list, regex: str, since_session: bool = False, limit: int = 200) -> list:
    rx = re.compile(regex)
    start = last_session_start(entries) if since_session else 0
    hits = []
    for e in entries[start:]:
        if rx.search(e.full):
            hits.append({"line": e.lineno, "time": e.ts.strftime("%H:%M:%S.%f")[:-3] if e.ts else None,
                         "thread": e.thread, "text": e.text[:500]})
            if len(hits) >= limit:
                break
    return hits


def tail(path: Path, regex: str | None, seconds: float, out, poll: float = 0.5) -> int:
    """Follow a log for `seconds` (bounded: never runs forever), printing new matching lines. Returns line count."""
    rx = re.compile(regex) if regex else None
    n = 0
    deadline = time.monotonic() + seconds
    with open(path, encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)
        while time.monotonic() < deadline:
            line = f.readline()
            if not line:
                time.sleep(poll)
                continue
            if rx is None or rx.search(line):
                out.write(line if line.endswith("\n") else line + "\n")
                out.flush()
                n += 1
    return n
