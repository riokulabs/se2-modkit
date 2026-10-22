"""Host-side views of the game process: /proc (threads, CPU, I/O, faults, memory), clocks and uptime, and wrappers
for dotnet-counters / dotnet-trace / dotnet-gcdump and Linux perf. Nothing here writes into the game process; the
external tools attach through .NET's diagnostics IPC (or perf_events) and only read.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import locations as paths

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
# The game reads a raw Stopwatch timestamp as milliseconds in places, which overflows a TimeSpan once it passes
# TimeSpan.MaxValue.TotalMilliseconds; on native Linux Stopwatch = CLOCK_MONOTONIC in ns, so that is 922,337.2 s
# (10.7 days) of monotonic uptime.
TIMESPAN_MS_OVERFLOW_S = 922337203685477.5807 / 1e9                  # = 922,337.2 s
X1E7_OVERFLOW_S = (2**63 - 1) / 10_000_000 / 1e9                      # a timestamp x 1e7 overflows long: 922.3 s


# ---------------- /proc parsing (pure functions take text so they can be unit-tested) ----------------
def parse_stat(text: str) -> dict:
    """/proc/<pid>/stat or /proc/<pid>/task/<tid>/stat. The comm field may contain spaces and parentheses."""
    lp, rp = text.index("("), text.rindex(")")
    f = text[rp + 2:].split()
    def i(n):  # field number n (1-based, as in proc(5))
        return int(f[n - 3])
    return {"pid": int(text[:lp]), "comm": text[lp + 1:rp], "state": f[0], "minflt": i(10), "majflt": i(12),
            "utime": i(14), "stime": i(15), "priority": i(18), "nice": i(19), "threads": i(20),
            "starttime": i(22), "vsize": i(23), "rss_pages": i(24), "processor": i(39) if len(f) >= 37 else None}


def parse_status(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def parse_io(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.strip().isdigit():
            out[k.strip()] = int(v)
    return out


def _read(p: Path) -> str | None:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return None


def find_pids(name: str | None = None) -> list:
    name = name or paths.process_name()
    out = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:     # no /proc (not Linux): no process can be found this way
        return []
    for d in entries:
        if not d.name.isdigit():
            continue
        comm = _read(d / "comm")
        if comm and comm.strip() == name[:15]:
            out.append(int(d.name))
    return sorted(out)


def game_pid(explicit: int | None = None) -> int | None:
    if not paths.is_linux():
        return None  # /proc only; callers that need a pid say "not supported here" via locations.require_linux
    if explicit:
        return explicit if Path(f"/proc/{explicit}").exists() else None
    pids = find_pids()
    return pids[-1] if pids else None


def system_uptime_s() -> float:
    t = _read(Path("/proc/uptime"))
    return float(t.split()[0]) if t else 0.0


def clocks() -> dict:
    mono = time.monotonic()   # CLOCK_MONOTONIC: the same clock .NET's Stopwatch reads on Linux
    up = system_uptime_s()    # CLOCK_BOOTTIME (includes suspend)
    return {
        "monotonic_s": mono, "boot_uptime_s": up, "boot_uptime_days": up / 86400,
        "timespan_ms_overflow": mono > TIMESPAN_MS_OVERFLOW_S,
        "timespan_ms_overflow_in_s": TIMESPAN_MS_OVERFLOW_S - mono,
        "x1e7_overflow": mono > X1E7_OVERFLOW_S,
        "x1e7_wrap_period_s": 2 * X1E7_OVERFLOW_S,
    }


def process_info(pid: int) -> dict:
    base = Path(f"/proc/{pid}")
    st = parse_stat(_read(base / "stat") or "")
    status = parse_status(_read(base / "status") or "")
    up = system_uptime_s()
    started_s = st["starttime"] / CLK_TCK
    exe = None
    try:
        exe = os.readlink(base / "exe")
    except OSError:
        pass
    cmd = (_read(base / "cmdline") or "").split("\0")
    return {"pid": pid, "comm": st["comm"], "exe": exe, "args": [a for a in cmd if a][:40],
            "uptime_s": max(0.0, up - started_s), "threads": st["threads"],
            "rss_bytes": st["rss_pages"] * PAGE, "vsize_bytes": st["vsize"],
            "vm_swap": status.get("VmSwap"), "vm_hwm": status.get("VmHWM"),
            "voluntary_ctxt": status.get("voluntary_ctxt_switches"),
            "nonvoluntary_ctxt": status.get("nonvoluntary_ctxt_switches")}


def diag_env(pid: int) -> dict:
    """Only the two diagnostics-related variables are looked at; nothing else from the environment is kept."""
    raw = _read(Path(f"/proc/{pid}/environ")) or ""
    keep = {}
    for kv in raw.split("\0"):
        k, _, v = kv.partition("=")
        if k in ("DOTNET_EnableDiagnostics", "DOTNET_PerfMapEnabled", "TMPDIR"):
            keep[k] = v
    return keep


def diag_socket(pid: int) -> str | None:
    tmp = Path(diag_env(pid).get("TMPDIR") or "/tmp")
    for p in tmp.glob(f"dotnet-diagnostic-{pid}-*-socket"):
        return str(p)
    return None


# ---------------- thread sampling ----------------
def norm_thread(name: str) -> str:
    return re.sub(r"\d+", "#", name).strip()


def snapshot_threads(pid: int) -> dict:
    out = {}
    tdir = Path(f"/proc/{pid}/task")
    try:
        tids = [t for t in tdir.iterdir() if t.name.isdigit()]
    except OSError:
        return out
    for t in tids:
        s = _read(t / "stat")
        if not s:
            continue
        try:
            st = parse_stat(s)
        except (ValueError, IndexError):
            continue
        status = parse_status(_read(t / "status") or "")
        out[int(t.name)] = {"name": (_read(t / "comm") or st["comm"]).strip(), "cpu": st["utime"] + st["stime"],
                            "utime": st["utime"], "stime": st["stime"], "state": st["state"], "processor": st["processor"],
                            "minflt": st["minflt"], "majflt": st["majflt"],
                            "vcs": int(status.get("voluntary_ctxt_switches", 0) or 0),
                            "nvcs": int(status.get("nonvoluntary_ctxt_switches", 0) or 0)}
    return out


def snapshot_process(pid: int) -> dict:
    base = Path(f"/proc/{pid}")
    st = parse_stat(_read(base / "stat") or "")
    return {"t": time.monotonic(), "cpu": st["utime"] + st["stime"], "minflt": st["minflt"], "majflt": st["majflt"],
            "rss": st["rss_pages"] * PAGE, "io": parse_io(_read(base / "io") or ""), "threads": snapshot_threads(pid)}


def diff_snapshots(a: dict, b: dict) -> dict:
    """Pure: two snapshot_process() results -> per-thread and per-group CPU %, faults, context switches, I/O."""
    dt = max(1e-6, b["t"] - a["t"])
    rows = []
    for tid, tb in b["threads"].items():
        ta = a["threads"].get(tid)
        if ta is None:
            continue
        cpu = (tb["cpu"] - ta["cpu"]) / CLK_TCK / dt * 100
        rows.append({"tid": tid, "name": tb["name"], "cpu_pct": round(cpu, 1),
                     "user_pct": round((tb["utime"] - ta["utime"]) / CLK_TCK / dt * 100, 1),
                     "sys_pct": round((tb["stime"] - ta["stime"]) / CLK_TCK / dt * 100, 1),
                     "vcs_per_s": round((tb["vcs"] - ta["vcs"]) / dt, 1), "nvcs_per_s": round((tb["nvcs"] - ta["nvcs"]) / dt, 1),
                     "minflt_per_s": round((tb["minflt"] - ta["minflt"]) / dt, 1), "majflt": tb["majflt"] - ta["majflt"],
                     "state": tb["state"], "cpu_core": tb["processor"]})
    rows.sort(key=lambda r: -r["cpu_pct"])
    groups = {}
    for r in rows:
        g = groups.setdefault(norm_thread(r["name"]), {"group": norm_thread(r["name"]), "threads": 0, "cpu_pct": 0.0,
                                                       "nvcs_per_s": 0.0, "max_thread_pct": 0.0})
        g["threads"] += 1
        g["cpu_pct"] = round(g["cpu_pct"] + r["cpu_pct"], 1)
        g["nvcs_per_s"] = round(g["nvcs_per_s"] + r["nvcs_per_s"], 1)
        g["max_thread_pct"] = max(g["max_thread_pct"], r["cpu_pct"])
    io = {k: round((b["io"].get(k, 0) - a["io"].get(k, 0)) / dt) for k in ("rchar", "wchar", "read_bytes", "write_bytes", "syscr", "syscw")
          if k in b["io"]}
    return {"seconds": round(dt, 2), "process_cpu_pct": round((b["cpu"] - a["cpu"]) / CLK_TCK / dt * 100, 1),
            "cores": os.cpu_count(), "minflt_per_s": round((b["minflt"] - a["minflt"]) / dt, 1),
            "majflt": b["majflt"] - a["majflt"], "rss_bytes": b["rss"], "rss_delta": b["rss"] - a["rss"],
            "io_per_s": io, "threads": rows, "groups": sorted(groups.values(), key=lambda g: -g["cpu_pct"])}


def thread_findings(d: dict) -> list:
    out = []
    for r in d["threads"][:12]:
        if r["cpu_pct"] >= 90:
            hint = ""
            n = r["name"].lower()
            if "render" in n:
                hint = " (render thread: LinuxCompat's fence wait spins with SpinWait.SpinUntil; also GPU-bound frames)"
            elif "main" in n or n in ("modern.bin",):
                hint = " (main thread: simulation-bound; see `se2diag perf game` stage timings)"
            out.append(f"thread '{r['name']}' (tid {r['tid']}) used {r['cpu_pct']}% of a core{hint}")
    workers = [g for g in d["groups"] if g["group"].startswith("VRageWorker")]
    for g in workers:
        if g["threads"] and g["cpu_pct"] / g["threads"] < 3 and g["nvcs_per_s"] > 200 * g["threads"]:
            out.append(f"{g['group']}: {g['threads']} workers, {g['cpu_pct']}% CPU total but {g['nvcs_per_s']} "
                       "involuntary switches/s: oversubscribed or wake/sleep churn (LinuxCompat's ThreadPoolSleepGate)")
    if d["majflt"] > 50:
        out.append(f"{d['majflt']} major page faults during the sample: memory is being paged in from disk")
    return out


def sample(pid: int, seconds: float) -> dict:
    a = snapshot_process(pid)
    time.sleep(seconds)
    b = snapshot_process(pid)
    return diff_snapshots(a, b)


# ---------------- external tools ----------------
DOTNET_TOOLS = ("dotnet-counters", "dotnet-trace", "dotnet-gcdump")


def tool_path(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    tools = paths.dotnet_tools_dir()
    cand = tools / name if tools else None
    return str(cand) if cand and cand.is_file() and os.access(cand, os.X_OK) else None


def tools_status() -> dict:
    st = {n: tool_path(n) for n in DOTNET_TOOLS}
    st["dotnet"] = shutil.which("dotnet")
    st["perf"] = shutil.which("perf")
    par = _read(Path("/proc/sys/kernel/perf_event_paranoid"))
    st["perf_event_paranoid"] = int(par) if par and par.strip().lstrip("-").isdigit() else None
    return st


def install_tool(name: str, log) -> bool:
    """`dotnet tool install --global <name>`: installs into ~/.dotnet/tools (the user's home), never with sudo."""
    if name not in DOTNET_TOOLS:
        raise ValueError(name)
    dotnet = shutil.which("dotnet")
    if not dotnet:
        log(f"cannot install {name}: no `dotnet` on PATH")
        return False
    log(f"installing {name} into {paths.dotnet_tools_dir() or '~/.dotnet/tools'} (dotnet tool install --global {name}; no sudo)")
    r = subprocess.run([dotnet, "tool", "install", "--global", name], capture_output=True, text=True, timeout=300)
    log((r.stdout + r.stderr).strip()[-600:])
    return r.returncode == 0 and tool_path(name) is not None


@dataclass
class Run:
    rc: int
    out: str
    err: str
    cmd: list


def _kill_group(p: subprocess.Popen, sig: int) -> None:
    """Signal the child's whole session (POSIX); Windows has no process groups to signal, so kill the child."""
    if hasattr(os, "killpg"):
        os.killpg(p.pid, sig)
    else:
        p.kill()


def run(cmd: list, timeout: float) -> Run:
    """Run with a hard timeout; on expiry the child is killed (never left in the background)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            _kill_group(p, 15)
            out, err = p.communicate(timeout=10)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                _kill_group(p, 9)
            except ProcessLookupError:
                pass
            out, err = p.communicate()
        return Run(124, out or "", (err or "") + "\n[se2diag: timed out and killed]", cmd)
    return Run(p.returncode, out, err, cmd)


def duration_arg(seconds: float) -> str:
    """dotnet-counters/-trace --duration format dd:hh:mm:ss."""
    s = int(max(1, round(seconds)))
    return f"{s // 86400:02d}:{s % 86400 // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def capture_dir(kind: str) -> Path:
    d = paths.output_dir() / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def counters(pid: int, seconds: float, providers: str = "System.Runtime") -> tuple:
    exe = tool_path("dotnet-counters")
    out = capture_dir("counters") / f"{stamp()}-{pid}.json"
    r = run([exe, "collect", "-p", str(pid), "--format", "json", "-o", str(out), "--refresh-interval", "1",
             "--duration", duration_arg(seconds), "--counters", providers], timeout=seconds + 60)
    return r, out


def trace(pid: int, seconds: float, profile: str) -> tuple:
    exe = tool_path("dotnet-trace")
    out = capture_dir("traces") / f"{stamp()}-{pid}.nettrace"
    r = run([exe, "collect", "-p", str(pid), "--profile", profile, "--duration", duration_arg(seconds), "-o", str(out),
             "--format", "Speedscope"], timeout=seconds + 180)
    return r, out


def speedscope_for(nettrace: Path) -> Path | None:
    cands = sorted(nettrace.parent.glob(nettrace.stem + "*.speedscope.json"), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def trace_convert(nettrace: Path) -> tuple:
    """collect writes the .speedscope.json itself (--format Speedscope); convert only when it is missing."""
    have = speedscope_for(nettrace)
    if have:
        return None, have
    exe = tool_path("dotnet-trace")
    r = run([exe, "convert", "--format", "Speedscope", str(nettrace)], timeout=600)
    return r, speedscope_for(nettrace)


def gcdump(pid: int) -> tuple:
    exe = tool_path("dotnet-gcdump")
    out = capture_dir("gcdumps") / f"{stamp()}-{pid}.gcdump"
    r = run([exe, "collect", "-p", str(pid), "-o", str(out)], timeout=600)
    rep = run([exe, "report", str(out)], timeout=300) if r.rc == 0 and out.exists() else None
    return r, rep, out


def perf_record(pid: int, seconds: float, freq: int = 199) -> tuple:
    out = capture_dir("perf") / f"{stamp()}-{pid}.data"
    r = run(["perf", "record", "-F", str(freq), "-g", "-p", str(pid), "-o", str(out), "--", "sleep", str(int(seconds))],
            timeout=seconds + 60)
    rep = None
    if out.exists():
        rep = run(["perf", "report", "-i", str(out), "--stdio", "--no-children", "--sort", "dso,symbol",
                   "--percent-limit", "0.3", "-g", "none"], timeout=300)
    return r, rep, out


PERF_LINE = re.compile(r"^\s*([0-9.]+)%\s+(\S+)\s+\[(.)\]\s+(.*)$")


def parse_perf_report(text: str, top: int = 40) -> list:
    rows = []
    for line in text.splitlines():
        m = PERF_LINE.match(line)
        if m:
            rows.append({"pct": float(m.group(1)), "dso": m.group(2), "kind": {"k": "kernel", ".": "user"}.get(m.group(3), m.group(3)),
                         "symbol": m.group(4).strip()})
    return rows[:top]


def perf_dso_totals(rows: list) -> list:
    tot = {}
    for r in rows:
        tot[r["dso"]] = tot.get(r["dso"], 0.0) + r["pct"]
    return sorted(({"dso": k, "pct": round(v, 2)} for k, v in tot.items()), key=lambda x: -x["pct"])
