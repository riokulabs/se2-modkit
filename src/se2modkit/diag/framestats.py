"""Analysis of the in-game perf probe (probes/perf.csx): frame-time distribution, hitches and what coincided with
them, sim speed, GC / JIT / lock / thread-pool deltas, PSO cache growth, stat-tree changes. Pure functions.
"""
from __future__ import annotations

import math


def pct(sorted_vals: list, p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] if lo == hi else sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def dist(vals: list) -> dict:
    v = sorted(x for x in vals if x is not None and x >= 0)
    if not v:
        return {"n": 0}
    return {"n": len(v), "mean": sum(v) / len(v), "p50": pct(v, 50), "p90": pct(v, 90), "p95": pct(v, 95),
            "p99": pct(v, 99), "max": v[-1], "min": v[0]}


def _delta(a: dict, b: dict, k: str):
    if a.get(k) is None or b.get(k) is None:
        return None
    return b[k] - a[k]


def analyze(data: dict) -> dict:
    frames = data.get("frames_ms") or []
    start, end = data.get("start") or {}, data.get("end") or {}
    fd = dist(frames)
    total_ms = sum(frames)
    out = {"frames": fd, "sample_ms": total_ms}
    if frames:
        out["fps"] = len(frames) / (total_ms / 1000) if total_ms else None
        out["fps_1pct_low"] = 1000.0 / fd["p99"] if fd.get("p99") else None
    med = fd.get("p50") or 0
    threshold = max(2 * med, 33.4)
    out["hitch_threshold_ms"] = threshold
    out["hitches"] = {"over_threshold": sum(1 for f in frames if f > threshold),
                      "over_50ms": sum(1 for f in frames if f > 50), "over_100ms": sum(1 for f in frames if f > 100),
                      "over_250ms": sum(1 for f in frames if f > 250)}
    stages = data.get("stages_ms") or {}
    out["stages"] = {k: dist([x for x in v if x >= 0]) for k, v in stages.items()}
    if fd.get("mean"):
        for k, s in out["stages"].items():
            if s.get("mean") is not None:
                s["share_of_frame"] = s["mean"] / fd["mean"]
    # attribute hitches
    gcgen, jit, locks, gcp = (data.get(k) or [] for k in ("gc_gen", "jit_ms", "lock_contentions", "gc_pause_ms"))
    causes = {"gc_gen0": 0, "gc_gen1": 0, "gc_gen2": 0, "jit>5ms": 0, "lock_contention": 0}
    dominated = {}
    worst = []
    for i, f in enumerate(frames):
        if f <= threshold:
            continue
        g = gcgen[i] if i < len(gcgen) else -1
        if g is not None and g >= 0:
            causes[f"gc_gen{g}"] += 1
        if i < len(jit) and jit[i] > 5:
            causes["jit>5ms"] += 1
        if i < len(locks) and locks[i] > 0:
            causes["lock_contention"] += 1
        st = {k: v[i] for k, v in stages.items() if i < len(v) and v[i] >= 0}
        top = max(st.items(), key=lambda kv: kv[1]) if st else None
        if top:
            dominated[top[0]] = dominated.get(top[0], 0) + 1
        worst.append({"frame": i, "ms": f, "stages": st, "gc_gen": g, "gc_pause_ms": gcp[i] if i < len(gcp) else None,
                      "jit_ms": jit[i] if i < len(jit) else None, "lock_contentions": locks[i] if i < len(locks) else None})
    worst.sort(key=lambda w: -w["ms"])
    out["hitch_causes"] = causes
    out["hitch_dominant_stage"] = dominated
    out["worst_frames"] = worst[:10]
    # sim speed: game time advanced / wall time
    wall_s = (_delta(start, end, "wall_ms") or 0) / 1000.0
    out["wall_s"] = wall_s
    for side in ("server", "client"):
        dg = _delta(start, end, f"{side}_game_s")
        out[f"{side}_sim_speed"] = (dg / wall_s) if dg is not None and wall_s > 0 else None
        out[f"{side}_paused"] = end.get(f"{side}_paused")
    # GC / JIT / locks / pool
    g = {k: _delta(start, end, k) for k in ("gc_gen0", "gc_gen1", "gc_gen2", "gc_pause_ms", "gc_allocated",
                                           "jit_methods", "jit_ms", "lock_contentions", "threadpool_completed")}
    out["runtime"] = g
    if wall_s > 0:
        out["alloc_mb_per_s"] = (g["gc_allocated"] or 0) / 1e6 / wall_s
        out["gc_pause_pct"] = 100 * (g["gc_pause_ms"] or 0) / (wall_s * 1000)
        out["lock_contentions_per_s"] = (g["lock_contentions"] or 0) / wall_s
    out["gc_heap_end"] = end.get("gc_heap")
    out["threadpool_end"] = {"threads": end.get("threadpool_threads"), "pending": end.get("threadpool_pending")}
    out["keen_pool"] = {"start": start.get("keen_pool"), "end": end.get("keen_pool")}
    ps, pe = start.get("pso") or {}, end.get("pso") or {}
    out["pso"] = {k: {"start": ps.get(k), "end": pe.get(k), "new": (pe.get(k) - ps.get(k)) if isinstance(pe.get(k), int) and isinstance(ps.get(k), int) else None}
                  for k in sorted(set(ps) | set(pe))}
    out["stat_changes"] = stat_changes(data.get("stats_start") or [], data.get("stats_end") or [])
    return out


def stat_changes(a: list, b: list, top: int = 25) -> list:
    """Nodes whose value changed most (relative), for counters and totals."""
    va = {n["path"]: n.get("value") for n in a if isinstance(n.get("value"), (int, float))}
    rows = []
    for n in b:
        v = n.get("value")
        if not isinstance(v, (int, float)) or n["path"] not in va:
            continue
        d = v - va[n["path"]]
        if d == 0:
            continue
        rel = abs(d) / abs(va[n["path"]]) if va[n["path"]] else float("inf")
        rows.append({"path": n["path"], "start": va[n["path"]], "end": v, "delta": d, "unit": n.get("unit"),
                     "text": n.get("text"), "rel": rel})
    rows.sort(key=lambda r: (-(1 if ("Total" in r["path"] or "Count" in r["path"]) else 0), -min(r["rel"], 1e9)))
    return rows[:top]


def findings(a: dict) -> list:
    out = []
    fd = a["frames"]
    if fd.get("n", 0) == 0:
        return ["no frames sampled (game paused in a menu, or the probe was cut short)"]
    for side in ("server", "client"):
        s = a.get(f"{side}_sim_speed")
        if s is not None and s < 0.95 and not a.get(f"{side}_paused"):
            out.append(f"{side} sim speed {s:.2f}: the simulation is not keeping up with real time")
    if a.get("fps") and 55 < a["fps"] < 59.5:
        out.append(f"{a['fps']:.1f} FPS sits just under 60: consistent with LinuxCompat's client-to-render controller "
                   "adding ~1 ms per frame; compare with VSync off and under Proton")
    h = a["hitches"]
    if h["over_100ms"]:
        out.append(f"{h['over_100ms']} frame(s) over 100 ms (worst {a['worst_frames'][0]['ms']:.0f} ms)")
    c = a["hitch_causes"]
    n = h["over_threshold"]
    if n:
        parts = [f"{v} with {k}" for k, v in c.items() if v]
        dom = ", ".join(f"{k} {v}" for k, v in sorted(a["hitch_dominant_stage"].items(), key=lambda kv: -kv[1]))
        out.append(f"{n} hitch(es) over {a['hitch_threshold_ms']:.0f} ms" + (f": {', '.join(parts)}" if parts else "")
                   + (f"; dominant stage: {dom}" if dom else ""))
    if n and c.get("jit>5ms", 0) >= max(2, n // 2):
        out.append(f"{c['jit>5ms']} of {n} hitches coincide with >5 ms of JIT in that frame: first-use compilation "
                   "(native Linux cannot use the game's win-x64 ReadyToRun code); expect fewer as the session warms up")
    for k, v in a["pso"].items():
        if v.get("new"):
            out.append(f"{v['new']} new {k} PSO(s) compiled during the sample: each can stall a frame on the vkd3d path")
    if (a.get("gc_pause_pct") or 0) > 2:
        out.append(f"GC pauses took {a['gc_pause_pct']:.1f}% of wall time ({a['runtime']['gc_gen2'] or 0} gen2)")
    if (a["runtime"].get("jit_ms") or 0) > 200:
        out.append(f"JIT compiled {a['runtime']['jit_methods']} methods in {a['runtime']['jit_ms']:.0f} ms during the sample "
                   "(tiered JIT / Harmony patching / first use of code paths)")
    if (a.get("lock_contentions_per_s") or 0) > 500:
        out.append(f"{a['lock_contentions_per_s']:.0f} monitor lock contentions/s")
    return out
