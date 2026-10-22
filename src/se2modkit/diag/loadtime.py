"""Load-time profiler: turn a game log (+ its _Render12 log, + Pulsar's info.log) into phase timings, and compare two.

Sources, all written by the game or Pulsar themselves:
  * `LOADING_BEGIN|X`, `LOADING_TIME|X|ms`, `LOADING_TIME_FULL|X|ms` markers (engine load profiler; some LOADING_TIME
    names are session metrics, e.g. GCMemoryTotal in bytes);
  * `Started/Finished loading definition set X`, `[Project Manager] Pushing Project X` (mods mounted);
  * `Initializing <Component>` lines (engine component init order; each lasts until the next main-thread entry);
  * the largest gaps between consecutive main-thread entries (time spent where nothing is logged);
  * _Render12.log: `Shader missing in content` (not in the shipped cache), `Compiling shader` (also not in
    Temp/ShaderCache: a real compile), the reason line, `Can't compile pso`;
  * Pulsar info.log: plugin download, publicize, build, errors, total time before the game starts.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import logs

MARK_RE = re.compile(r"\bLOADING_(BEGIN|TIME|TIME_FULL)\|([^|\s]+)(?:\|([-0-9.eE]+))?")
DEFSET_RE = re.compile(r"(Started|Finished) loading definition set (\S+)")
PROJECT_RE = re.compile(r"\[Project Manager\] Pushing Project (\S+)")
INIT_RE = re.compile(r"^\s*Initializing (\S+?)(?:\s*-\s*Entering)?\s*(?:\.\.\.)?$")
VERSION_RE = re.compile(r"App Version: (\S+)")
RUNTIME_RE = re.compile(r"Runtime Framework: (.+)$")
ARGS_RE = re.compile(r"Running application with arguments: (.*)$")
PULSAR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) \[(\w+)\] \((\d+)\) (.*)$")
METRIC_UNITS = {"GCMemoryTotal": "bytes", "WorkerPoolLoad": "ms", "CLRStalls": "ms"}
SUMMARY_KEYS = ["Main", "Application", "Render", "Session", "WorldStartup", "PushWorldDefinitionSet",
                "WorldTransitionToGame", "WorldDeserialization", "SessionComponentDeserialization", "EntitySpawning"]


def _ms(a: datetime | None, b: datetime | None) -> float | None:
    return None if a is None or b is None else (b - a).total_seconds() * 1000.0


def _key(name: str, seen: Counter) -> str:
    seen[name] += 1
    return name if seen[name] == 1 else f"{name}#{seen[name]}"


def parse_main(entries: list) -> dict:
    phases, full, metrics, begins = {}, {}, {}, {}
    seen_t, seen_f, seen_b, seen_d = Counter(), Counter(), Counter(), Counter()
    defsets, open_sets = {}, {}
    projects, plugins = [], []
    info = {"version": None, "runtime": None, "args": None}
    inits = []
    main_entries = []
    for e in entries:
        t = e.text
        if e.thread == "Main Thread":
            main_entries.append(e)
        for m in MARK_RE.finditer(t):
            kind, name, val = m.group(1), m.group(2), m.group(3)
            if kind == "BEGIN":
                begins[_key(name, seen_b)] = e.ts
            elif val is not None:
                v = float(val)
                if kind == "TIME":
                    if name in METRIC_UNITS and METRIC_UNITS[name] == "bytes":
                        metrics[_key(name, seen_t)] = v
                    else:
                        phases[_key(name, seen_t)] = v
                else:
                    full[_key(name, seen_f)] = v
        m = DEFSET_RE.search(t)
        if m:
            if m.group(1) == "Started":
                open_sets[m.group(2)] = e.ts
            else:
                start = open_sets.pop(m.group(2), None)
                defsets[_key(m.group(2), seen_d)] = _ms(start, e.ts)
        m = PROJECT_RE.search(t)
        if m:
            projects.append(m.group(1))
        if ("Loaded plugin" in t or "SeMcp2: Successfully loaded" in t) and len(plugins) < 50:
            plugins.append(t[:160])
        for rx, k in ((VERSION_RE, "version"), (RUNTIME_RE, "runtime"), (ARGS_RE, "args")):
            mm = rx.search(t)
            if mm and info[k] is None:
                info[k] = mm.group(1).strip()
    # component init durations (until the next main-thread entry)
    for i, e in enumerate(main_entries):
        m = INIT_RE.match(e.text)
        if m and i + 1 < len(main_entries):
            d = _ms(e.ts, main_entries[i + 1].ts)
            if d is not None:
                inits.append((m.group(1), d))
    # gaps only inside the load window (up to the last LOADING_TIME marker), not idle play time
    last_mark = max((e.lineno for e in entries if "LOADING_TIME" in e.text), default=None)
    load_main = [e for e in main_entries if last_mark is None or e.lineno <= last_mark]
    gaps = []
    for a, b in zip(load_main, load_main[1:]):
        d = _ms(a.ts, b.ts)
        if d is not None:
            gaps.append({"ms": round(d, 1), "after_line": a.lineno, "after": a.text[:140], "before": b.text[:140]})
    gaps.sort(key=lambda g: -g["ms"])
    first = next((e.ts for e in entries if e.ts), None)
    last = next((e.ts for e in reversed(entries) if e.ts), None)
    return {
        "info": info, "first": first.isoformat(sep=" ") if first else None, "last": last.isoformat(sep=" ") if last else None,
        "phases_ms": phases, "phases_full_ms": full, "metrics": metrics,
        "unfinished": [k for k in begins if k not in phases and k not in full],
        "definition_sets_ms": defsets, "projects": projects, "plugins": plugins,
        "component_inits_ms": sorted(({"component": c, "ms": round(d, 1)} for c, d in inits), key=lambda x: -x["ms"])[:15],
        "largest_gaps": gaps[:10],
    }


def parse_render(entries: list) -> dict:
    compiled, missing = Counter(), Counter()
    reasons = Counter()
    threads = Counter()
    pso_fail = 0
    first = last = None
    for e in entries:
        t = e.text
        if t.startswith("Info: Compiling shader:") or t.startswith("Compiling shader:"):
            name = t.split("Compiling shader:", 1)[1].strip()
            compiled[name] += 1
            threads[re.sub(r"\d+", "N", e.thread)] += 1
            for x in e.extra[:2]:
                if x.strip().startswith("Reason:"):
                    reasons[x.strip()[7:].strip()[:80]] += 1
            first = first or e.ts
            last = e.ts or last
        elif t.startswith("Shader missing in content:"):
            missing[t.split(":", 1)[1].strip().rstrip(".")] += 1
        elif "Can't compile pso" in t:
            pso_fail += 1
    return {
        "shaders_compiled": sum(compiled.values()), "distinct_compiled": len(compiled),
        "shaders_missing_in_content": sum(missing.values()),
        "compile_window_ms": _ms(first, last), "first_compile": first.isoformat(sep=" ") if first else None,
        "last_compile": last.isoformat(sep=" ") if last else None,
        "reasons": dict(reasons.most_common(6)), "compile_threads": dict(threads),
        "top_compiled": [{"shader": k, "variants": v} for k, v in compiled.most_common(10)],
        "pso_failures": pso_fail,
    }


def parse_pulsar(lines: list) -> dict:
    rows = []
    for raw in lines:
        m = PULSAR_RE.match(raw.rstrip("\n"))
        if m:
            try:
                ts = datetime.strptime(m.group(1)[:26], "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                continue
            rows.append((ts, m.group(2), m.group(4)))
    if not rows:
        return {}
    cats = defaultdict(float)
    errors, plugins, downloads = [], None, []
    for (ts, lvl, msg), nxt in zip(rows, rows[1:] + [None]):
        d = _ms(ts, nxt[0]) if nxt else 0.0
        if msg.startswith("Downloading"):
            cats["download"] += d
            downloads.append(msg[12:160])
        elif msg.startswith("Using publicized"):
            cats["publicize+build"] += d
        elif msg.startswith(("Hashing", "Checking version", "Reading whitelist", "Downloading plugin statistics")):
            cats["hub/version checks"] += d
        elif lvl == "ERROR":
            cats["after errors"] += d
        if lvl in ("ERROR", "FATAL"):
            errors.append(msg[:300])
        if msg.startswith("Enabled Plugins:"):
            plugins = [p.strip() for p in re.split(r"(?<=\)),\s*", msg.split(":", 1)[1]) if p.strip()]
    start, end = rows[0][0], rows[-1][0]
    init = next((ts for ts, _, msg in rows if re.match(r"Initialized \d+ of \d+ plugins", msg)), None)
    version = next((msg.split()[-1] for _, _, msg in rows if msg.startswith("Starting Pulsar")), None)
    return {"pulsar": version, "start": start.isoformat(sep=" "), "end": end.isoformat(sep=" "),
            "total_ms": _ms(start, end), "to_plugins_initialized_ms": _ms(start, init),
            "categories_ms": {k: round(v, 1) for k, v in cats.items()}, "errors": errors,
            "plugins": plugins, "downloads": downloads}


def profile(main_log: Path, render_log: Path | None = None, pulsar_log: Path | None = None) -> dict:
    out = {"log": str(main_log), "main": parse_main(logs.read_entries(main_log))}
    r = render_log if render_log is not None else logs.sibling(main_log, "render12")
    if r and r.is_file():
        out["render_log"] = str(r)
        out["render"] = parse_render(logs.read_entries(r))
    if pulsar_log and pulsar_log.is_file():
        p = parse_pulsar(pulsar_log.read_text(encoding="utf-8", errors="replace").splitlines())
        # only meaningful if that Pulsar run started this game process (just before the game log's first line)
        first = out["main"].get("first")
        if p and first:
            gap = _ms(datetime.fromisoformat(p["start"]), datetime.fromisoformat(first))
            p["matches_this_log"] = gap is not None and 0 <= gap <= 15 * 60 * 1000
            p["pulsar_to_game_log_ms"] = gap
        out["pulsar"] = p
    return out


def summary(prof: dict) -> dict:
    m = prof["main"]
    s = {k: m["phases_ms"].get(k) for k in SUMMARY_KEYS if k in m["phases_ms"]}
    for k, v in m["definition_sets_ms"].items():
        s[f"defset:{k}"] = v
    r = prof.get("render")
    if r:
        s["shaders_compiled"] = r["shaders_compiled"]
        s["shaders_missing_in_content"] = r["shaders_missing_in_content"]
    p = prof.get("pulsar")
    if p and p.get("matches_this_log"):
        s["pulsar_before_game_ms"] = p.get("pulsar_to_game_log_ms")
    return s


def compare(a: dict, b: dict, min_ms: float = 500.0, min_ratio: float = 0.2) -> list:
    """Rows for every phase/defset/count in either profile; `flag` marks a notable difference."""
    rows = []
    sa, sb = flat(a), flat(b)
    for k in sorted(set(sa) | set(sb), key=lambda k: (_order(k), k)):
        va, vb = sa.get(k), sb.get(k)
        row = {"name": k, "a": va, "b": vb, "delta": None, "ratio": None, "flag": ""}
        if va is not None and vb is not None:
            row["delta"] = vb - va
            row["ratio"] = (vb / va) if va else None
            is_count = k.startswith(("shaders_", "count:"))
            big = abs(vb - va) >= (1 if is_count else min_ms) and (va == 0 or abs(vb - va) / abs(va) >= min_ratio)
            if big:
                row["flag"] = "slower" if vb > va else "faster"
                if is_count:
                    row["flag"] = "more" if vb > va else "fewer"
        elif va is None or vb is None:
            row["flag"] = "only in " + ("B" if va is None else "A")
        rows.append(row)
    return rows


def flat(prof: dict) -> dict:
    m = prof["main"]
    out = {}
    for k, v in m["phases_ms"].items():
        out[k] = v
    for k, v in m["definition_sets_ms"].items():
        if v is not None:
            out[f"defset:{k}"] = v
    r = prof.get("render")
    if r:
        out["shaders_compiled"] = r["shaders_compiled"]
        out["shaders_missing_in_content"] = r["shaders_missing_in_content"]
    p = prof.get("pulsar")
    if p and p.get("matches_this_log") and p.get("pulsar_to_game_log_ms") is not None:
        out["pulsar_before_game_log"] = p["pulsar_to_game_log_ms"]
    return out


def _order(k: str) -> int:
    base = k.split("#")[0]
    if base in SUMMARY_KEYS:
        return SUMMARY_KEYS.index(base)
    if k.startswith("defset:"):
        return 50
    if k.startswith("shaders_"):
        return 90
    return 60


def findings(prof: dict) -> list:
    """Plain-language observations a human should look at."""
    out = []
    r = prof.get("render") or {}
    if r.get("shaders_compiled", 0) > 200:
        out.append(f"{r['shaders_compiled']} shader variants were compiled this run (not in the shipped cache nor in "
                   f"Temp/ShaderCache); compiling took a {(r.get('compile_window_ms') or 0) / 1000:.0f} s window. "
                   "Normal once after a game update or driver change; if it repeats every launch, the Temp cache "
                   "is not being kept.")
    if r.get("shaders_missing_in_content", 0) > 200 and r.get("shaders_compiled", 0) < 50:
        out.append(f"{r['shaders_missing_in_content']} shaders are 'missing in content' (the shipped cache does not "
                   "match on this platform) and were read from Temp/ShaderCache instead. Under Proton this count is 0; "
                   "on native Linux it is expected with LinuxCompat but costs a disk lookup per shader.")
    m = prof["main"]
    es = m["phases_ms"].get("EntitySpawning")
    if es and es > 20000:
        out.append(f"EntitySpawning took {es / 1000:.1f} s: the world's entities, not the engine, dominate this load.")
    pw = m["phases_ms"].get("PushWorldDefinitionSet")
    if pw and pw > 5000:
        out.append(f"PushWorldDefinitionSet took {pw / 1000:.1f} s (definition set with the world's mods).")
    if m["largest_gaps"] and m["largest_gaps"][0]["ms"] > 3000:
        g = m["largest_gaps"][0]
        out.append(f"Longest silent main-thread gap: {g['ms'] / 1000:.1f} s after line {g['after_line']} "
                   f"({g['after'][:80]!r}).")
    if m["unfinished"]:
        out.append("Phases begun but never timed (crash or quit during load?): " + ", ".join(m["unfinished"][:8]))
    p = prof.get("pulsar") or {}
    tag = "" if p.get("matches_this_log") else " (from the latest Pulsar run, which is not this log's launch)"
    for err in p.get("errors", [])[:3]:
        out.append("Pulsar error" + tag + ": " + err[:200])
    return out
