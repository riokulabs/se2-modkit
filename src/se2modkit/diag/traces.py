"""Parsers for the external tools' output: speedscope (dotnet-trace convert), dotnet-counters JSON,
dotnet-gcdump report. Pure functions over text/JSON, unit-tested with fixtures.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

# Frames whose names contain one of these are "marked" (patched / plugin code). Harmony replacement methods are
# DynamicMethods named DMD<...>; LinuxCompat's code lives in its own namespaces.
DEFAULT_MARKS = {
    "harmony": ["DMD<", "Harmony", "MonoMod"],
    "linuxcompat": ["LinuxCompat", "ClientPlugin.", "linux_compat", "LinuxPlatform"],
    "pulsar/plugins": ["Pulsar", "SeMcp2", "DisableAlphaBanner"],
    "native/vkd3d": ["vkd3d", "dxvk", "libvulkan", "radv", "amdgpu"],
}


def short(name: str, n: int = 140) -> str:
    return name if len(name) <= n else name[: n - 3] + "..."


# dotnet-trace's speedscope export: each stack is rooted in structural frames ("Threads", "(Non-Activities)",
# "Process64 <exe> (<pid>) Args: ...", "Thread (<tid>)") and ends in a pseudo frame telling what the thread was doing.
PSEUDO = {"CPU_TIME", "UNMANAGED_CODE_TIME", "BLOCKED_TIME", "WAIT_TIME", "AWAIT_TIME", "READIED_TIME"}
STRUCT_RE = re.compile(r"^(Threads|\(Non-Activities\)|Process(32|64) .*|Thread \(\d+\).*)$")
THREAD_RE = re.compile(r"Thread \((\d+)\)")


def is_meta(name: str) -> bool:
    return name in PSEUDO or bool(STRUCT_RE.match(name))


def analyze_speedscope(doc: dict, marks: dict | None = None, patched: set | None = None, top: int = 25,
                       thread_names: dict | None = None) -> dict:
    """Self, CPU-self and inclusive time per frame over all thread profiles (evented or sampled).

    Self time of a stack goes to its deepest real frame; the pseudo leaf (CPU_TIME, UNMANAGED_CODE_TIME, ...) says
    what the thread was doing, so "cpu_self" counts only time spent running managed code on a CPU.
    patched: "Namespace.Type.Method" names of Harmony-patched methods (from `se2diag patches --json`); frames
    running those methods are marked "patched (original)". thread_names: tid -> name (from /proc at trace time).
    """
    marks = marks or DEFAULT_MARKS
    frames = [f.get("name", "?") for f in doc.get("shared", {}).get("frames", [])]
    def name(i):
        return frames[i] if isinstance(i, int) and 0 <= i < len(frames) else str(i)
    self_t, cpu_t, incl_t = defaultdict(float), defaultdict(float), defaultdict(float)
    states = defaultdict(float)
    threads = []
    total = 0.0

    def account(stack, dt, tstates):
        if not stack or dt <= 0:
            return 0.0
        leaf = name(stack[-1])
        state = leaf if leaf in PSEUDO else "CODE"
        real = next((f for f in reversed(stack) if not is_meta(name(f))), None)
        states[state] += dt
        tstates[state] += dt
        if real is not None:
            self_t[real] += dt
            if state in ("CPU_TIME", "CODE"):
                cpu_t[real] += dt
        return dt

    for prof in doc.get("profiles", []):
        ptotal = 0.0
        tstates = defaultdict(float)
        if prof.get("type") == "evented":
            stack, opened = [], []
            on_stack = defaultdict(int)
            last_at = None
            for ev in prof.get("events", []):
                at = float(ev.get("at", 0))
                if last_at is not None:
                    ptotal += account(stack, at - last_at, tstates)
                last_at = at
                fr = ev.get("frame")
                if ev.get("type") == "O":
                    stack.append(fr)
                    opened.append(at)
                    on_stack[fr] += 1
                elif ev.get("type") == "C" and stack:
                    for i in range(len(stack) - 1, -1, -1):
                        if stack[i] == fr:
                            stack.pop(i)
                            t0 = opened.pop(i)
                            on_stack[fr] -= 1
                            if on_stack[fr] == 0:          # count recursion once
                                incl_t[fr] += at - t0
                            break
        elif prof.get("type") == "sampled":
            weights = prof.get("weights") or [1.0] * len(prof.get("samples", []))
            for sample, w in zip(prof.get("samples", []), weights):
                if not sample:
                    continue
                ptotal += account(sample, w, tstates)
                for fr in set(sample):
                    incl_t[fr] += w
        total += ptotal
        pname = prof.get("name", "?")
        m = THREAD_RE.search(pname)
        tid = int(m.group(1)) if m else None
        label = f"{pname} {thread_names[tid]}" if thread_names and tid in thread_names else pname
        threads.append({"thread": label, "tid": tid, "time": round(ptotal, 3),
                        "cpu": round(tstates.get("CPU_TIME", 0.0) + tstates.get("CODE", 0.0), 3), "unit": prof.get("unit")})

    def tags(n):
        t = [k for k, needles in marks.items() if any(x in n for x in needles)]
        if patched and any(p and p in n.replace("::", ".") for p in patched):
            t.append("patched (original)")
        return t

    def rows(d, denom):
        out = []
        for k, v in sorted(d.items(), key=lambda kv: -kv[1]):
            n = name(k)
            if is_meta(n):
                continue
            out.append({"frame": short(n), "time": round(v, 3), "pct": round(100 * v / denom, 2) if denom else 0.0, "tags": tags(n)})
            if len(out) >= top:
                break
        return out

    cpu_total = sum(cpu_t.values())
    by_tag = defaultdict(lambda: {"self": 0.0, "cpu": 0.0, "frames": 0})
    for k, v in self_t.items():
        for t in tags(name(k)):
            by_tag[t]["self"] += v
            by_tag[t]["cpu"] += cpu_t.get(k, 0.0)
            by_tag[t]["frames"] += 1
    marked_incl = defaultdict(list)
    for k, v in incl_t.items():
        n = name(k)
        if is_meta(n):
            continue
        for t in tags(n):
            marked_incl[t].append((v, n))
    unit = (doc.get("profiles") or [{}])[0].get("unit", "?")
    return {
        "unit": unit, "total": round(total, 3), "cpu_total": round(cpu_total, 3),
        "states": {k: round(v, 3) for k, v in sorted(states.items(), key=lambda kv: -kv[1])},
        "threads": sorted(threads, key=lambda t: (-t["cpu"], -t["time"]))[:25],
        "top_self": rows(self_t, total), "top_cpu": rows(cpu_t, cpu_total), "top_inclusive": rows(incl_t, total),
        "by_tag": {t: {"self": round(v["self"], 3), "pct": round(100 * v["self"] / total, 2) if total else 0.0,
                       "cpu": round(v["cpu"], 3), "cpu_pct": round(100 * v["cpu"] / cpu_total, 2) if cpu_total else 0.0,
                       "frames": v["frames"],
                       "hottest_inclusive": [{"frame": short(n), "time": round(x, 3)} for x, n in sorted(marked_incl[t], reverse=True)[:8]]}
                   for t, v in by_tag.items()},
    }


def load_counters(text: str) -> dict:
    """dotnet-counters --format json. Tolerates a truncated file (tool killed mid-write)."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        fixed = text.rstrip().rstrip(",")
        for tail in ("]}", "}]}", "\n]}"):
            try:
                doc = json.loads(fixed + tail)
                break
            except json.JSONDecodeError:
                continue
        else:
            return {"series": {}, "error": "unparseable counters file"}
    series = defaultdict(list)
    for ev in doc.get("Events", doc.get("events", [])):
        key = ev.get("provider", "?") + " / " + ev.get("name", "?") + (f" [{ev['tags']}]" if ev.get("tags") else "")
        try:
            series[key].append(float(ev.get("value")))
        except (TypeError, ValueError):
            continue
    summary = {k: {"n": len(v), "min": min(v), "max": max(v), "mean": sum(v) / len(v), "last": v[-1]}
               for k, v in series.items() if v}
    return {"target": doc.get("TargetProcess"), "start": doc.get("StartTime"), "series": summary}


GCDUMP_ROW = re.compile(r"^\s*([\d,]+)\s+([\d,]+)\s+(.+?)\s*$")


def parse_gcdump_report(text: str, top: int = 30) -> dict:
    rows = []
    total = None
    for line in text.splitlines():
        m = re.match(r"^\s*([\d,]+)\s+GC Heap bytes", line)
        if m:
            total = int(m.group(1).replace(",", ""))
            continue
        m = GCDUMP_ROW.match(line)
        if m and not m.group(3).lower().startswith(("object bytes", "count")):
            rows.append({"bytes": int(m.group(1).replace(",", "")), "count": int(m.group(2).replace(",", "")),
                         "type": m.group(3)})
    rows.sort(key=lambda r: -r["bytes"])
    return {"heap_bytes": total, "types": rows[:top]}


def findings(an: dict) -> list:
    """Plain-language reading of a sampling trace. Hypotheses are worded as such."""
    out = []
    cpu = an.get("cpu_total") or 0
    def cpu_pct(needle):
        return sum(r["pct"] for r in an.get("top_cpu", []) if needle in r["frame"])
    jit = cpu_pct("InvokeCompileMethod") + cpu_pct("CompileMethodHook")
    if jit >= 5:
        out.append(f"{jit:.0f}% of managed CPU is JIT compilation routed through MonoMod's JIT hook (Harmony/Pulsar). "
                   "The game's assemblies carry Windows (win-x64) ReadyToRun code, which the Linux runtime cannot use, so "
                   "methods are JIT-compiled on first use; every compile also passes through the hook. Compare `status` JIT "
                   "totals under Proton to size it.")
    lock = cpu_pct("Monitor.Enter_Slowpath")
    if lock >= 5:
        out.append(f"{lock:.0f}% of managed CPU spins in Monitor.Enter_Slowpath: lock contention; the inclusive view shows who takes the lock.")
    poll = cpu_pct("PollGC")
    if poll >= 5:
        out.append(f"{poll:.0f}% of managed CPU in Thread.PollGC: threads waiting for a GC suspension (GC or JIT activity).")
    lc = an.get("by_tag", {}).get("linuxcompat")
    if lc:
        for h in lc["hottest_inclusive"]:
            if "Patch" in h["frame"] and an["threads"] and h["time"] > 0.1 * max(t["time"] for t in an["threads"]):
                out.append(f"LinuxCompat patch {h['frame'][:120]} has {h['time']:.0f} {an['unit']} inclusive: a hot patched path.")
    if cpu == 0:
        out.append("no managed CPU samples (wrong process, or the trace was empty)")
    return out
