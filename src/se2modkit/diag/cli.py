"""se2diag command line. Every command prints a plain-language report, or JSON with --json (and --out FILE writes
the JSON too). In-game commands need the game running with the in-game bridge plugin; host and log commands do not."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import __version__
from . import (client as gclient, framestats, host, loadtime, logs, locations as paths, modchecks, probes, report,
               screens, statstree, traces)
from .report import fmt_bytes, fmt_dur, fmt_ms, fmt_num, kv, section, table

EXIT_OK, EXIT_FAIL, EXIT_UNAVAILABLE, EXIT_USAGE = 0, 1, 3, 2


class Ctx:
    def __init__(self, args):
        self.args = args
        self._client = None

    def game(self):
        if self._client is None:
            self._client = gclient.GameClient(timeout=self.args.timeout)
        return self._client

    def probe(self, name, params=None, target="main"):
        return probes.run(self.game(), name, params, target)


def warn(msg):
    print(f"se2diag: {msg}", file=sys.stderr)


def probe_notes(res) -> list:
    lines = []
    if res.missing:
        lines += section("missing in this game build (reported, not fatal)") + ["  " + m for m in res.missing[:40]]
    if res.notes:
        lines += section("probe notes") + ["  " + n for n in res.notes[:20]]
    return lines


# ---------------------------------------------------------------- status
def cmd_status(ctx, a):
    pid = host.game_pid(a.pid) if paths.is_linux() else None
    clk = host.clocks()
    obj = {"pid": pid, "clocks": clk, "process": host.process_info(pid) if pid else None}
    lines = section("host")
    lines += kv([("game process", f"{paths.process_name()} pid {pid}" if pid else "not running" if paths.is_linux()
                  else "not supported here: the process lookup reads /proc, which only Linux has"),
                 ("system uptime", f"{fmt_dur(clk['boot_uptime_s'])} (monotonic {fmt_dur(clk['monotonic_s'])})")])
    if clk["timespan_ms_overflow"]:
        lines.append("  ! uptime is past 10.7 days: the game's millisecond timer overflows and"
                     " crashes on item drops. Needs LinuxCompat >= 1.0.9 or another uptime-fix plugin.")
    else:
        lines.append(f"  uptime crash threshold (10.7 days) in {fmt_dur(clk['timespan_ms_overflow_in_s'])}")
    if pid:
        p = obj["process"]
        lines += kv([("process uptime", fmt_dur(p["uptime_s"])), ("threads", p["threads"]),
                     ("resident memory", fmt_bytes(p["rss_bytes"])), ("swap", p["vm_swap"] or "-"),
                     ("diagnostics socket", host.diag_socket(pid) or "not found (dotnet-trace/counters cannot attach)")])
    log = logs.newest_log(pid=pid)
    obj["log"] = str(log) if log else None
    if log:
        ents = logs.read_entries(log)
        sc = logs.scan(ents, since_session=False, samples=1)
        obj["log_scan"] = {k: v["count"] for k, v in sc["patterns"].items()}
        bad = {k: v for k, v in sc["patterns"].items() if v["count"] and v["severity"] != "info"}
        lines += section("newest game log") + kv([("file", str(log)),
                                                    ("problems", ", ".join(f"{k} {v['count']}" for k, v in bad.items()) or "none of the known-bad patterns")])
    try:
        pl = paths.pulsar_info_log()
    except paths.NotSupported as e:
        pl = None
        obj["pulsar_error"] = str(e)
        lines += section("Pulsar") + ["  " + str(e)]
    if pl is not None and pl.is_file():
        pp = loadtime.parse_pulsar(pl.read_text(errors="replace").splitlines())
        obj["pulsar"] = pp
        lines += section("Pulsar (latest launch)") + kv([("version", pp.get("pulsar") or "-"), ("started", pp.get("start") or "-"),
                                                          ("plugins", "; ".join(pp.get("plugins") or []) or "-")])
        for e in (pp.get("errors") or [])[:4]:
            lines.append("  ! " + e[:220])
    if not pid or a.no_game:
        if not pid:
            lines.append("\n(game not running: in-game status skipped)")
        report.emit(obj, a.json, lines, a.out)
        return EXIT_OK
    try:
        res = ctx.probe("status")
    except (gclient.GameUnavailable, probes.ProbeError) as e:
        obj["game_error"] = str(e)
        lines.append(f"\n(in-game status unavailable: {e})")
        report.emit(obj, a.json, lines, a.out)
        return EXIT_UNAVAILABLE
    d = res.data or {}
    obj["game"] = res.to_json()
    g, c, m, s = d.get("game", {}), d.get("clocks", {}), d.get("memory", {}), d.get("session", {})
    lines += section("game") + kv([
        ("version", f"{g.get('version')} (assembly {g.get('assembly_version')})"), ("runtime", g.get("runtime")),
        ("-loadScripts", fmt_num(g.get("load_scripts"))),
        ("Stopwatch", f"{fmt_num(c.get('stopwatch_frequency'))} Hz, {fmt_dur(c.get('stopwatch_seconds'))} since boot"),
        ("uptime bugs", ("TimeSpan-ms overflow ACTIVE" if c.get("timespan_ms_overflow") else "TimeSpan-ms ok") + ", "
         + ("x1e7 overflow active (ContinuationQueue clock wraps every 30.7 min)" if c.get("x1e7_overflow") else "x1e7 ok"))])
    lines += section("session") + kv([
        ("world", s.get("world") or "-"), ("type", s.get("world_type") or "-"),
        ("server / client", f"{fmt_num(s.get('server_session'))} / {fmt_num(s.get('client_session'))}"),
        ("game time", f"server {fmt_dur(s.get('server_game_seconds'))}, client {fmt_dur(s.get('client_game_seconds'))}"),
        ("paused", f"server {fmt_num(s.get('server_paused'))}, client {fmt_num(s.get('client_paused'))}"),
        ("mods", ", ".join(x.get("name") or "?" for x in s.get("mods", [])) or ("none" if s.get("mod_list_found") else "mod list not found"))])
    for side in ("client", "server"):
        e = d.get("entities_" + side)
        if e:
            lines.append(f"  {side}: {fmt_num(e['entities'])} entities, {fmt_num(e['grids'])} grids, {fmt_num(e['blocks'])} blocks, "
                         f"{fmt_num(e['characters'])} characters, pending close {fmt_num(e.get('pending_close'))}")
    lines += section("memory / GC") + kv([
        ("working set", fmt_bytes(m.get("working_set"))), ("GC heap", f"{fmt_bytes(m.get('gc_heap_size'))} (committed {fmt_bytes(m.get('gc_committed'))}, fragmented {fmt_bytes(m.get('gc_fragmented'))})"),
        ("collections", f"gen0 {m.get('gc_gen0')}, gen1 {m.get('gc_gen1')}, gen2 {m.get('gc_gen2')}; total pause {fmt_ms(m.get('gc_total_pause_ms'))} ({fmt_num(m.get('gc_pause_percent'))}% recent)"),
        ("GC mode", f"{'server' if m.get('gc_server') else 'workstation'}, {m.get('gc_latency_mode')}"),
        ("JIT", f"{fmt_num(m.get('jit_methods'))} methods, {fmt_ms(m.get('jit_ms'))}"),
        ("threads", f"{m.get('threads')} OS, thread pool {m.get('threadpool_threads')} (pending {m.get('threadpool_pending')}), lock contentions {fmt_num(m.get('lock_contentions'))}")])
    pl_ = d.get("plugins", {})
    lines += section("plugins and patches") + kv([
        ("harmony patched methods", fmt_num(pl_.get("harmony_patched_methods"))),
        ("patches by owner", ", ".join(f"{k} {v}" for k, v in sorted((pl_.get("harmony_patches_by_owner") or {}).items(), key=lambda kv: -kv[1])) or "-")])
    lines += ["  extra assemblies: " + ", ".join(x["name"] for x in pl_.get("assemblies", [])[:40])]
    fnd = []
    up = (obj.get("process") or {}).get("uptime_s") or 0
    if m.get("jit_ms") and up and m["jit_ms"] / 1000 > 0.2 * up:
        fnd.append(f"JIT has used {m['jit_ms'] / 1000:.0f} s of CPU in {up:.0f} s of process life ({fmt_num(m.get('jit_methods'))} methods): "
                   "on native Linux the game's win-x64 ReadyToRun code is unusable, so most methods are JIT-compiled (slower loads, "
                   "hitches on first use). Compare with Proton.")
    if c.get("timespan_ms_overflow"):
        owners = (pl_.get("harmony_patches_by_owner") or {})
        fnd.append("uptime is past the 10.7-day Stopwatch threshold; item drops need LinuxCompat's (or another uptime-fix plugin's) "
                   f"floating-object patch (patch owners: {', '.join(owners) or 'none'}; check `se2diag patches --owner LinuxCompat`)")
    if (m.get("gc_pause_percent") or 0) > 5:
        fnd.append(f"recent GC pause share {m['gc_pause_percent']:.1f}% (high right after a load is normal)")
    obj["findings"] = fnd
    lines += section("findings") + (["  - " + x for x in fnd] or ["  nothing stands out"])
    lines += probe_notes(res)
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


# ---------------------------------------------------------------- perf game
def cmd_perf_game(ctx, a):
    if a.from_file:
        data = json.loads(Path(a.from_file).read_text())
        data = data.get("data", data)
        res = None
    else:
        try:
            res = ctx.probe("perf", {"seconds": a.seconds, "stats": not a.no_stats, "stat_filter": a.stat_filter or ""})
        except (gclient.GameUnavailable, probes.ProbeError) as e:
            warn(str(e))
            return EXIT_UNAVAILABLE
        data = res.data
        raw = host.capture_dir("perf-game") / f"{host.stamp()}.json"
        raw.write_text(json.dumps(res.to_json()))
    an = framestats.analyze(data)
    obj = {"analysis": an, "findings": framestats.findings(an)}
    if not a.from_file:
        obj["raw_file"] = str(raw)
    f = an["frames"]
    lines = section(f"in-game frame sampling ({an['wall_s']:.1f} s, {f.get('n', 0)} frames)")
    if f.get("n"):
        lines += kv([("FPS", f"{an['fps']:.1f} (1% low {an['fps_1pct_low']:.1f})"),
                     ("frame ms", f"mean {f['mean']:.2f}  p50 {f['p50']:.2f}  p95 {f['p95']:.2f}  p99 {f['p99']:.2f}  max {f['max']:.1f}"),
                     ("hitches", f"{an['hitches']['over_threshold']} > {an['hitch_threshold_ms']:.0f} ms, {an['hitches']['over_50ms']} > 50 ms, {an['hitches']['over_100ms']} > 100 ms"),
                     ("sim speed", f"server {fmt_num(an['server_sim_speed'])}, client {fmt_num(an['client_sim_speed'])}"
                      + (" (paused)" if an.get("server_paused") else ""))])
        lines += section("main-thread stages (engine stats, ms per frame)")
        lines += table([dict(stage=k, **v) for k, v in an["stages"].items() if v.get("n")],
                       [("stage", "stage"), ("mean", lambda r: f"{r['mean']:.2f}"), ("p95", lambda r: f"{r['p95']:.2f}"),
                        ("max", lambda r: f"{r['max']:.1f}"), ("share", lambda r: f"{100 * r.get('share_of_frame', 0):.0f}%")])
    rt = an["runtime"]
    lines += section("runtime during the sample") + kv([
        ("GC", f"gen0 {rt['gc_gen0']}, gen1 {rt['gc_gen1']}, gen2 {rt['gc_gen2']}; pause {fmt_ms(rt['gc_pause_ms'])} ({fmt_num(an.get('gc_pause_pct'))}%); alloc {fmt_num(an.get('alloc_mb_per_s'))} MB/s"),
        ("JIT", f"{fmt_num(rt['jit_methods'])} methods, {fmt_ms(rt['jit_ms'])}"),
        ("locks", f"{fmt_num(rt['lock_contentions'])} contentions ({fmt_num(an.get('lock_contentions_per_s'))}/s)"),
        ("thread pool", f"{fmt_num(rt['threadpool_completed'])} work items; end: {an['threadpool_end']}"),
        ("VRage pool", f"{an['keen_pool']['end']}"),
        ("PSO caches", ", ".join(f"{k} {v['end']} (+{v['new']})" for k, v in an["pso"].items()))])
    if an["worst_frames"]:
        lines += section("worst frames")
        lines += table(an["worst_frames"][:8], [("frame", "frame"), ("ms", lambda r: f"{r['ms']:.1f}"),
                                                 ("stages", lambda r: ", ".join(f"{k} {v:.1f}" for k, v in r["stages"].items() if v > 1)),
                                                 ("gc", lambda r: "-" if r["gc_gen"] in (None, -1) else f"gen{r['gc_gen']} {r['gc_pause_ms']:.1f} ms"),
                                                 ("jit", lambda r: f"{r['jit_ms']:.1f}" if r.get("jit_ms") else "-"),
                                                 ("locks", "lock_contentions")])
    if an["stat_changes"]:
        lines += section("engine stats that changed most")
        lines += table(an["stat_changes"][:15], [("stat", "path"), ("start", lambda r: f"{r['start']:.6g}"),
                                                 ("end", lambda r: f"{r['end']:.6g}"), ("text", "text")])
    lines += section("findings") + (["  - " + x for x in obj["findings"]] or ["  nothing stands out"])
    if res:
        lines += probe_notes(res)
        lines.append(f"\n  raw samples: {raw} (re-analyse with --from)")
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


# ---------------------------------------------------------------- perf host
def need_pid(a):
    paths.require_linux("host sampling of the game process")
    pid = host.game_pid(getattr(a, "pid", None))
    if not pid:
        warn(f"{paths.process_name()} is not running (pgrep -x {paths.process_name()}); start the game first")
    return pid


def cmd_perf_host(ctx, a):
    pid = need_pid(a)
    if not pid:
        return EXIT_UNAVAILABLE
    d = host.sample(pid, a.seconds)
    fnd = host.thread_findings(d)
    obj = {"pid": pid, "sample": d, "findings": fnd}
    lines = section(f"host sample of pid {pid} over {d['seconds']} s ({d['cores']} cores)")
    lines += kv([("process CPU", f"{d['process_cpu_pct']}% of one core"), ("page faults", f"{d['minflt_per_s']}/s minor, {d['majflt']} major"),
                 ("resident", f"{fmt_bytes(d['rss_bytes'])} ({'+' if d['rss_delta'] >= 0 else ''}{fmt_bytes(d['rss_delta'])})"),
                 ("I/O per s", ", ".join(f"{k} {fmt_bytes(v)}" if 'byte' in k or 'char' in k else f"{k} {v}" for k, v in d["io_per_s"].items()) or "-")])
    lines += section("thread groups") + table(d["groups"][:15], [("group", "group"), ("threads", "threads"), ("cpu %", "cpu_pct"),
                                                                 ("max thread %", "max_thread_pct"), ("invol. switches/s", "nvcs_per_s")])
    lines += section(f"top {a.top} threads") + table(d["threads"][:a.top], [("tid", "tid"), ("name", "name"), ("cpu %", "cpu_pct"),
                                                                            ("user", "user_pct"), ("sys", "sys_pct"), ("vol/s", "vcs_per_s"),
                                                                            ("invol/s", "nvcs_per_s"), ("state", "state"), ("core", "cpu_core")])
    lines += section("findings") + (["  - " + x for x in fnd] or ["  nothing stands out"])
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


def ensure_tool(name, a) -> bool:
    if host.tool_path(name):
        return True
    if getattr(a, "install", False):
        return host.install_tool(name, lambda m: print(m, file=sys.stderr))
    warn(f"{name} is not installed. Install it into your home with `se2diag tools --install` "
         f"(runs `dotnet tool install --global {name}`, no sudo), or pass --install here.")
    return False


def cmd_tools(ctx, a):
    st = host.tools_status()
    if a.install:
        for n in host.DOTNET_TOOLS:
            if not st.get(n):
                host.install_tool(n, lambda m: print(m, file=sys.stderr))
        st = host.tools_status()
    lines = section("diagnostic tools") + kv([(k, str(v) if v is not None else "missing") for k, v in st.items()])
    par = st.get("perf_event_paranoid")
    if par is not None and par > 2:
        lines.append(f"  note: kernel.perf_event_paranoid = {par}: `perf` cannot attach to the game. To allow it for this boot "
                     "(your decision, it is a system setting): sudo sysctl kernel.perf_event_paranoid=2")
    report.emit(st, a.json, lines, a.out)
    return EXIT_OK


def cmd_perf_counters(ctx, a):
    if a.from_file:
        summ = traces.load_counters(Path(a.from_file).read_text())
        out_file = a.from_file
    else:
        pid = need_pid(a)
        if not pid or not ensure_tool("dotnet-counters", a):
            return EXIT_UNAVAILABLE
        r, out_file = host.counters(pid, a.seconds, a.providers)
        if not Path(out_file).exists():
            warn(f"dotnet-counters failed (rc {r.rc}): {(r.err or r.out)[-800:]}")
            return EXIT_FAIL
        summ = traces.load_counters(Path(out_file).read_text())
    obj = {"file": str(out_file), **summ}
    rows = [dict(name=k, **v) for k, v in sorted(summ["series"].items())]
    lines = section(f"dotnet-counters ({out_file})") + table(rows, [("counter", "name"), ("min", lambda r: f"{r['min']:.4g}"),
                                                                    ("mean", lambda r: f"{r['mean']:.4g}"), ("max", lambda r: f"{r['max']:.4g}"),
                                                                    ("last", lambda r: f"{r['last']:.4g}")])
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


def _load_patched(path):
    if not path:
        return None
    doc = json.loads(Path(path).read_text())
    methods = (doc.get("game") or doc).get("data", doc).get("methods", []) if isinstance(doc, dict) else []
    return {f"{m.get('type')}.{m.get('name')}" for m in methods if m.get("type")}


def cmd_perf_trace(ctx, a):
    marks = dict(traces.DEFAULT_MARKS)
    for m in a.mark or []:
        k, _, v = m.partition("=")
        marks[k] = [x for x in v.split(",") if x]
    if a.from_file:
        sc = Path(a.from_file)
    else:
        pid = need_pid(a)
        if not pid or not ensure_tool("dotnet-trace", a):
            return EXIT_UNAVAILABLE
        print(f"tracing pid {pid} for {a.seconds} s ({a.profile}) ...", file=sys.stderr)
        names = {tid: t["name"] for tid, t in host.snapshot_threads(pid).items()}
        r, nettrace = host.trace(pid, a.seconds, a.profile)
        names.update({tid: t["name"] for tid, t in host.snapshot_threads(pid).items()})
        Path(str(nettrace) + ".threads.json").write_text(json.dumps(names))
        if not nettrace.exists():
            warn(f"dotnet-trace failed (rc {r.rc}): {(r.err or r.out)[-800:]}")
            return EXIT_FAIL
        rc, sc = host.trace_convert(nettrace)
        if sc is None:
            warn(f"dotnet-trace convert failed (rc {rc.rc if rc else '?'}): {((rc.err or rc.out) if rc else '')[-800:]}; the trace is {nettrace}")
            return EXIT_FAIL
    tn_file = Path(str(sc).replace(".speedscope.json", ".nettrace.threads.json"))
    names = {int(k): v for k, v in json.loads(tn_file.read_text()).items()} if tn_file.is_file() else None
    an = traces.analyze_speedscope(json.loads(sc.read_text()), marks, _load_patched(a.patches), top=a.top, thread_names=names)
    obj = {"file": str(sc), **an}
    u = an["unit"]
    lines = section(f"thread-time sampling ({sc.name}): {an['total']:.0f} {u} of thread time across {len(an['threads'])} threads, "
                    f"{an['cpu_total']:.0f} {u} of it running managed code")
    lines += kv([("thread states", ", ".join(f"{k} {v:.0f}" for k, v in an["states"].items() if v >= 1))])
    lines += section("busiest threads (managed CPU time)") + table(an["threads"][:12], [("thread", "thread"), ("cpu", "cpu"), ("sampled", "time")])
    lines += section("top CPU self time (where managed code burns CPU)") + table(an["top_cpu"], [("%", "pct"), ("frame", "frame"), ("tags", lambda r: ",".join(r["tags"]))])
    lines += section("top self time (incl. waiting in native/unmanaged code)") + table(an["top_self"][:12], [("%", "pct"), ("frame", "frame"), ("tags", lambda r: ",".join(r["tags"]))])
    lines += section("top inclusive time") + table(an["top_inclusive"][:15], [("%", "pct"), ("frame", "frame"), ("tags", lambda r: ",".join(r["tags"]))])
    lines += section("marked code (Harmony / LinuxCompat / plugins / native graphics)")
    for t, v in sorted(an["by_tag"].items(), key=lambda kv: -kv[1]["self"]):
        lines.append(f"  {t}: {v['cpu_pct']}% of managed CPU, {v['pct']}% of thread time, in {v['frames']} frames")
        for h in v["hottest_inclusive"][:5]:
            lines.append(f"      {h['time']:>10.1f} {u}  {h['frame']}")
    if not an["by_tag"]:
        lines.append("  none (patched methods appear as DMD<...> frames only if the runtime's rundown names them)")
    obj["findings"] = traces.findings(an)
    lines += section("findings") + (["  - " + x for x in obj["findings"]] or ["  nothing stands out"])
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


def cmd_perf_gcdump(ctx, a):
    if a.from_file:
        if not ensure_tool("dotnet-gcdump", a):
            return EXIT_UNAVAILABLE
        rep = host.run([host.tool_path("dotnet-gcdump"), "report", a.from_file], timeout=300)
        out = a.from_file
    else:
        if not a.allow_gc:
            warn("a gcdump forces a full blocking GC in the game (a visible pause of up to seconds). "
                 "Re-run with --allow-gc to accept that.")
            return EXIT_USAGE
        pid = need_pid(a)
        if not pid or not ensure_tool("dotnet-gcdump", a):
            return EXIT_UNAVAILABLE
        r, rep, out = host.gcdump(pid)
        if rep is None:
            warn(f"dotnet-gcdump failed (rc {r.rc}): {(r.err or r.out)[-800:]}")
            return EXIT_FAIL
    parsed = traces.parse_gcdump_report(rep.out)
    obj = {"file": str(out), **parsed}
    lines = section(f"managed heap by type ({out}); heap {fmt_bytes(parsed['heap_bytes'])}")
    lines += table(parsed["types"], [("bytes", lambda r: fmt_bytes(r["bytes"])), ("count", "count"), ("type", "type")])
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


def cmd_perf_native(ctx, a):
    pid = need_pid(a)
    if not pid:
        return EXIT_UNAVAILABLE
    st = host.tools_status()
    if not st["perf"]:
        warn("`perf` not found (linux-tools)")
        return EXIT_UNAVAILABLE
    if (st.get("perf_event_paranoid") or 0) > 2:
        warn(f"kernel.perf_event_paranoid = {st['perf_event_paranoid']}: perf cannot attach. That is a system setting; "
             "if you want it: sudo sysctl kernel.perf_event_paranoid=2 (until reboot)")
        return EXIT_UNAVAILABLE
    r, rep, out = host.perf_record(pid, a.seconds, a.freq)
    if rep is None or rep.rc != 0:
        warn(f"perf failed (rc {r.rc}): {(r.err or '')[-600:]} {(rep.err if rep else '')[-400:]}")
        return EXIT_FAIL
    rows = host.parse_perf_report(rep.out, a.top)
    jitmap = Path(f"/tmp/perf-{pid}.map").exists()
    obj = {"file": str(out), "rows": rows, "dso": host.perf_dso_totals(rows), "jit_map": jitmap}
    lines = section(f"native CPU profile ({out})") + table(obj["dso"][:15], [("%", "pct"), ("library", "dso")])
    lines += section("hottest symbols") + table(rows, [("%", "pct"), ("library", "dso"), ("symbol", "symbol")])
    if not jitmap:
        lines.append("\n  JIT frames are unnamed: start the game with DOTNET_PerfMapEnabled=1 to get /tmp/perf-<pid>.map")
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


# ---------------------------------------------------------------- loadtime
def _resolve_log(spec, proton=False):
    if spec in (None, "newest", "latest"):
        return logs.newest_log(paths.proton_log_dir() if proton else None)
    if spec in ("proton", "proton-newest"):
        return logs.newest_log(paths.proton_log_dir())
    p = Path(spec).expanduser()
    if not p.is_file():
        d = paths.log_dir()
        cand = d / spec if d else None
        p = cand if cand and cand.is_file() else p
    return p if p.is_file() else None


def _loadtime_lines(prof):
    m = prof["main"]
    lines = section(f"load profile: {prof['log']}")
    lines += kv([("game", f"{m['info'].get('version')} on {m['info'].get('runtime')}"), ("log span", f"{m['first']} .. {m['last']}"),
                 ("projects", ", ".join(m["projects"]) or "-")])
    rows = [{"phase": k, "ms": v, "full": m["phases_full_ms"].get(k)} for k, v in m["phases_ms"].items()]
    lines += section("engine load phases (LOADING_TIME markers)") + table(rows, [("phase", "phase"), ("time", lambda r: fmt_ms(r["ms"])),
                                                                                 ("full (incl. waits)", lambda r: fmt_ms(r["full"]))])
    lines += section("definition sets") + table([{"set": k, "ms": v} for k, v in m["definition_sets_ms"].items()],
                                                [("set", "set"), ("time", lambda r: fmt_ms(r["ms"]))])
    lines += section("slowest engine component inits") + table(m["component_inits_ms"][:8], [("component", "component"), ("time", lambda r: fmt_ms(r["ms"]))])
    lines += section("largest silent gaps on the main thread (during load)") + table(m["largest_gaps"][:6], [("gap", lambda r: fmt_ms(r["ms"])), ("line", "after_line"), ("after", "after")])
    r = prof.get("render")
    if r:
        lines += section("shaders (_Render12 log)") + kv([
            ("missing in shipped cache", fmt_num(r["shaders_missing_in_content"])),
            ("compiled (not in Temp/ShaderCache either)", f"{fmt_num(r['shaders_compiled'])} variants of {r['distinct_compiled']} shaders over {fmt_ms(r['compile_window_ms'])}"),
            ("PSO failures", r["pso_failures"])])
    p = prof.get("pulsar")
    if p:
        lines += section("Pulsar " + ("(this launch)" if p.get("matches_this_log") else "(latest launch, not this log)")) + kv([
            ("version", p.get("pulsar") or "-"), ("total", fmt_ms(p.get("total_ms"))),
            ("before game log", fmt_ms(p.get("pulsar_to_game_log_ms")) if p.get("matches_this_log") else "-"),
            ("where", ", ".join(f"{k} {fmt_ms(v)}" for k, v in (p.get("categories_ms") or {}).items()) or "-")])
    lines += section("findings") + (["  - " + x for x in loadtime.findings(prof)] or ["  nothing stands out"])
    return lines


def _pulsar_log_or_note():
    """Pulsar's launch log, or None after one stderr line saying why the Pulsar timings are skipped."""
    try:
        return paths.pulsar_info_log()
    except paths.NotSupported as e:
        warn(f"Pulsar timings skipped: {e}")
        return None


def cmd_loadtime(ctx, a):
    la = _resolve_log(a.log, a.proton)
    if not la:
        warn(f"no game log found ({a.log or 'newest'})")
        return EXIT_UNAVAILABLE
    pulsar = None if a.no_pulsar else (Path(a.pulsar) if a.pulsar else _pulsar_log_or_note())
    pa = loadtime.profile(la, pulsar_log=pulsar)
    if not a.compare:
        report.emit(pa, a.json, _loadtime_lines(pa), a.out)
        return EXIT_OK
    lb = _resolve_log(a.compare)
    if not lb:
        warn(f"comparison log not found: {a.compare}")
        return EXIT_UNAVAILABLE
    pb = loadtime.profile(lb, pulsar_log=pulsar)
    rows = loadtime.compare(pa, pb, a.min_ms, a.min_ratio)
    obj = {"a": pa, "b": pb, "comparison": rows}
    lines = section(f"A = {la}") + section(f"B = {lb}")
    lines += table(rows, [("phase", "name"),
                          ("A", lambda r: (fmt_ms(r["a"]) if not r["name"].startswith("shaders_") else fmt_num(r["a"])) if r["a"] is not None else None),
                          ("B", lambda r: (fmt_ms(r["b"]) if not r["name"].startswith("shaders_") else fmt_num(r["b"])) if r["b"] is not None else None),
                          ("B-A", lambda r: (fmt_ms(r["delta"]) if not r["name"].startswith("shaders_") else fmt_num(r["delta"])) if r["delta"] is not None else None),
                          ("B/A", lambda r: f"{r['ratio']:.2f}x" if r["ratio"] else None), ("", "flag")])
    lines += section("findings A") + ["  - " + x for x in loadtime.findings(pa)] + section("findings B") + ["  - " + x for x in loadtime.findings(pb)]
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


# ---------------------------------------------------------------- logs
def cmd_logs(ctx, a):
    log = _resolve_log(a.log, a.proton) if a.log or a.proton else logs.newest_log(pid=host.game_pid())
    if not log:
        warn("no game log found")
        return EXIT_UNAVAILABLE
    if a.tail:
        print(f"following {log} for {a.tail} s" + (f" (filter {a.grep!r})" if a.grep else ""), file=sys.stderr)
        logs.tail(log, a.grep, a.tail, sys.stdout)
        return EXIT_OK
    ents = logs.read_entries(log)
    if a.grep:
        hits = logs.grep(ents, a.grep, a.since_session, a.limit)
        lines = section(f"{len(hits)} match(es) in {log}") + [f"  {h['line']:>6}  {h['time']}  [{h['thread']}]  {h['text'][:220]}" for h in hits]
        report.emit({"log": str(log), "hits": hits}, a.json, lines, a.out)
        return EXIT_OK
    sc = logs.scan(ents, a.pattern, a.since_session, a.samples)
    obj = {"log": str(log), **sc}
    lines = section(f"{log.name}: {sc['entries']} entries since {sc['since']} ({sc['start']} .. {sc['end']})")
    rows = [dict(key=k, **v) for k, v in sc["patterns"].items()]
    rows.sort(key=lambda r: ({"error": 0, "warn": 1, "info": 2}[r["severity"]], -r["count"]))
    lines += table(rows, [("pattern", "key"), ("severity", "severity"), ("count", "count"),
                          ("first", lambda r: f"line {r['first']['line']} {r['first']['time']}" if r["first"] else None),
                          ("last", lambda r: f"line {r['last']['line']} {r['last']['time']}" if r["last"] else None)])
    for r in rows:
        if r["count"] and r["severity"] != "info":
            lines += section(f"{r['key']} ({r['hint']})") + [f"  line {s['line']} {s['time']} [{s['thread']}] {s['text'][:200]}" for s in r["samples"]]
    bad = sum(r["count"] for r in rows if r["severity"] == "error")
    report.emit(obj, a.json, lines, a.out)
    return EXIT_FAIL if (a.strict and bad) else EXIT_OK


# ---------------------------------------------------------------- inspect / inventory
def cmd_inspect(ctx, a):
    params = {"target": a.target, "side": a.side, "depth": a.depth, "dump": not a.no_dump, "max_dist": a.max_dist}
    try:
        res = ctx.probe("inspect", params)
    except (gclient.GameUnavailable, probes.ProbeError) as e:
        warn(str(e))
        return EXIT_UNAVAILABLE
    d = res.data or {}
    obj = {"inspect": res.to_json()}
    lines = []
    if not d.get("found"):
        lines += [f"nothing found: {d.get('how')}"] + ["  candidate: " + c for c in d.get("candidates", [])]
        report.emit(obj, a.json, lines + probe_notes(res), a.out)
        return EXIT_FAIL
    e = d["entity"]
    lines += section(f"{e['name']} ({d['side']}, {d['how']})") + kv([("id", e["id"]), ("position", e.get("position")), ("parent", e.get("parent"))])
    if len(d.get("candidates", [])) > 1:
        lines += ["  other candidates:"] + ["    " + c for c in d["candidates"][1:6]]
    if d.get("block"):
        b = d["block"]
        lines += section("block") + kv([(k, fmt_num(v) if not isinstance(v, str) else v) for k, v in b.items()])
    if d.get("state"):
        lines += section("state") + kv([(k, fmt_num(v)) for k, v in d["state"].items() if v is not None])
    if d.get("fractures"):
        f = d["fractures"]
        lines += section("fracture zones") + kv([("zones", f["zones"]), ("critical", f["critical"]), ("damaged", len(f["damaged"])), ("dead", f["dead"])])
        lines += ["    " + json.dumps(x) for x in f["damaged"][:12]]
    if d.get("conditional_subparts"):
        lines += section("conditional subparts") + kv([(k, str(v)) for k, v in d["conditional_subparts"].items()])
    if d.get("render"):
        r = d["render"]
        lines += section("render") + kv([(k, str(v)) for k, v in r.items() if k != "hidden_mesh_parts"])
        lines += [f"  hidden mesh parts ({len(r['hidden_mesh_parts'])}):"] + [f"    {k}: {v}" for k, v in list(r["hidden_mesh_parts"].items())[:30]]
    if d.get("mount_points"):
        lines += section("mount points (groups per face)") + kv([(k, v) for k, v in d["mount_points"].items()])
    inv = d.get("inventories") or {}
    if inv.get("components"):
        lines += section("inventories") + table(inv["components"], [("comp#", "component_index"), ("definition", "definition"),
                                                                    ("stacks", "max_stacks"), ("max mass", "max_mass"), ("whitelist", "whitelist")])
        if inv.get("slot_order") is not None:
            lines.append(f"  InventorySlotsServer order (component indices): {inv['slot_order']}, bitmask {inv.get('slot_bitmask')}")
    if not a.no_inventory and (inv.get("components") or (d.get("block_key") and a.side == "client")):
        try:
            tgt = f"key:{d['block_key']}" if d.get("block_key") else a.target
            ir = ctx.probe("inventory", {"target": tgt, "side": "server", "max_dist": a.max_dist})
            if not inv.get("components") and (ir.data or {}).get("inventories"):
                lines += section("inventories (server)")
            obj["inventory"] = ir.to_json()
            for i in (ir.data or {}).get("inventories", []):
                lines.append(f"  [{i['component_index']}] {i['definition']}: mass {fmt_num(i.get('mass'))} / {i.get('max_mass')} kg, "
                             f"{len(i['stacks'])} stack(s): " + ", ".join(f"{s['amount']} x {s['item']}" for s in i["stacks"][:10]))
        except (gclient.GameUnavailable, probes.ProbeError) as ex:
            lines.append(f"  (item stacks unavailable: {str(ex).splitlines()[0]})")
    lines += section("components") + ["  " + ", ".join(c for c in e["tree"]["components"])]
    if d.get("components"):
        lines.append(f"  (full field dump of {len(d['components'])} components in --json output)")
    lines += probe_notes(res)
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


# ---------------------------------------------------------------- modcheck
def _project_mods() -> list[tuple[str, str]]:
    """(name, prefix) of every mod of the project around the current directory; none outside a project."""
    from ..project import ProjectError, load_project
    try:
        return [(m.name, m.prefix) for m in load_project(Path.cwd()).mods.values()]
    except (ProjectError, OSError):
        return []


def cmd_modcheck(ctx, a):
    pairs = modchecks.parse_mods(a.mods) if a.mods else _project_mods()
    mods = [f"{n}:{p}" if p else n for n, p in pairs]
    obj = {"mods": mods}
    probe_rows = []
    lines = []
    if not pairs:
        lines.append("(no mod named: pass --mods NAME:PREFIX or run inside a mod project; only the generic checks ran)")
    try:
        res = ctx.probe("modcheck", {"mods": ",".join(mods)})
        probe_rows = (res.data or {}).get("checks", [])
        obj["probe"] = res.to_json()
        if not (res.data or {}).get("world"):
            lines.append("(no world loaded: only the log checks ran)")
    except (gclient.GameUnavailable, probes.ProbeError) as e:
        lines.append(f"(in-game checks unavailable: {str(e).splitlines()[0]}; only the log checks ran)")
        obj["game_error"] = str(e)
    log = _resolve_log(a.log) if a.log else logs.newest_log(pid=host.game_pid())
    lrows = modchecks.log_checks(logs.read_entries(log), pairs, since_session=not a.all_log) if log else []
    rows = modchecks.merge(probe_rows, lrows)
    v = modchecks.verdict(rows)
    obj.update({"log": str(log) if log else None, "checks": rows, "verdict": v})
    lines += section(f"mod checks ({', '.join(mods)}) - verdict {v}")
    lines += table(rows, [("id", "id"), ("status", "status"), ("check", "title"), ("detail", "detail")])
    if a.json is False and log:
        lines.append(f"\n  log: {log} ({'whole file' if a.all_log else 'since the last world load'})")
    report.emit(obj, a.json, lines, a.out)
    return EXIT_FAIL if v == "FAIL" else EXIT_OK


# ---------------------------------------------------------------- shot / stats / patches / render / probe
def cmd_shot(ctx, a):
    try:
        res = ctx.game().screenshot()
    except gclient.GameUnavailable as e:
        warn(str(e))
        return EXIT_UNAVAILABLE
    if not res.images:
        warn("no image returned: " + " ".join(res.texts)[:300])
        return EXIT_FAIL
    p = screens.save(res.images[0], a.label, Path(a.dir) if a.dir else None)
    report.emit({"file": str(p)}, a.json, [str(p)], a.out)
    return EXIT_OK


def cmd_stats(ctx, a):
    if a.file:
        nodes = statstree.parse_stats_log(Path(a.file).read_text(errors="replace"))
        src = a.file
    else:
        try:
            res = ctx.probe("stats", {"filter": a.filter or "", "samples": a.samples, "interval_ms": a.interval_ms})
        except (gclient.GameUnavailable, probes.ProbeError) as e:
            warn(str(e))
            return EXIT_UNAVAILABLE
        snaps = (res.data or {}).get("snapshots", [])
        nodes = snaps[-1]["nodes"] if snaps else []
        src = "game"
        if a.samples > 1 and len(snaps) > 1:
            ch = framestats.stat_changes(snaps[0]["nodes"], snaps[-1]["nodes"], top=40)
            obj = {"snapshots": snaps, "changes": ch}
            dt = (snaps[-1]["t_ms"] - snaps[0]["t_ms"]) / 1000
            lines = section(f"stat changes over {dt:.1f} s") + table(ch, [("stat", "path"), ("start", lambda r: f"{r['start']:.6g}"),
                                                                         ("end", lambda r: f"{r['end']:.6g}"), ("per s", lambda r: f"{r['delta'] / dt:.4g}" if dt else None)])
            report.emit(obj, a.json, lines, a.out)
            return EXIT_OK
    if a.compare:
        other = statstree.parse_stats_log(Path(a.compare).read_text(errors="replace"))
        rows = [r for r in statstree.compare(nodes, other) if r["flag"] or a.all]
        lines = section(f"A = {src}, B = {a.compare}") + table(rows, [("stat", "path"), ("A", "a"), ("B", "b"), ("", "flag")])
        report.emit({"comparison": rows}, a.json, lines, a.out)
        return EXIT_OK
    lines = section(f"engine stats ({src})") + statstree.render(nodes, show_hidden=a.all)
    report.emit({"source": src, "nodes": nodes}, a.json, lines, a.out)
    return EXIT_OK


def cmd_patches(ctx, a):
    try:
        res = ctx.probe("patches", {"owner": a.owner or ""})
    except (gclient.GameUnavailable, probes.ProbeError) as e:
        warn(str(e))
        return EXIT_UNAVAILABLE
    d = res.data or {}
    obj = {"game": res.to_json()}
    lines = section("Harmony patches by owner") + kv([(k, v) for k, v in sorted((d.get("owners") or {}).items(), key=lambda kv: -kv[1])])
    lines += section(f"{len(d.get('methods', []))} patched methods" + (f" (owner ~ {a.owner})" if a.owner else ""))
    for m in d.get("methods", [])[: a.limit]:
        lines.append(f"  {m['type']}.{m['name']}({m.get('params') or ''})  [{m.get('assembly')}]")
        for p in m.get("patches", []):
            lines.append(f"      {p['kind']:<10} {p['owner']:<28} {p.get('method')}  prio {p.get('priority')}")
    lines += probe_notes(res)
    report.emit(obj, a.json, lines, a.out)
    return EXIT_OK


def cmd_render(ctx, a):
    try:
        res = ctx.probe("render", {"filter": a.filter} if a.filter else None)
    except (gclient.GameUnavailable, probes.ProbeError) as e:
        warn(str(e))
        return EXIT_UNAVAILABLE
    d = res.data or {}
    lines = section("Render12 managers")
    for k, v in (d.get("managers") or {}).items():
        lines.append(f"  {k}: " + ("missing" if v is None else f"{v['type']} " + ", ".join(f"{fk}={fv}" for fk, fv in v["fields"].items())))
    lines += kv([("ignore shader missing in content", str(d.get("ignore_shader_missing_in_content"))), ("adapters", str(d.get("adapter")))])
    lines += section("render stats") + statstree.render(d.get("stats") or [])
    lines += probe_notes(res)
    report.emit({"game": res.to_json()}, a.json, lines, a.out)
    return EXIT_OK


def parse_kv(items):
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep:
            raise SystemExit(f"bad -p {it!r}: expected key=value")
        out[k] = v
    return out


def cmd_probe(ctx, a):
    if a.action == "list":
        print("\n".join(probes.list_probes()))
        return EXIT_OK
    if not a.name:
        warn("probe name required")
        return EXIT_USAGE
    params = parse_kv(a.param)
    if a.action == "build":
        code = probes.build(a.name, params)
        if a.output:
            Path(a.output).write_text(code)
            print(a.output)
        else:
            sys.stdout.write(code)
        return EXIT_OK
    try:
        res = ctx.probe(a.name, params, a.target)
    except (gclient.GameUnavailable, probes.ProbeError) as e:
        warn(str(e))
        return EXIT_UNAVAILABLE
    json.dump(res.to_json(), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return EXIT_OK


# ---------------------------------------------------------------- parser
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print JSON instead of the text report")
    common.add_argument("--out", metavar="FILE", help="also write the JSON report to FILE")
    common.add_argument("--timeout", type=float, default=150.0, help="seconds to wait on the game per request (default 150)")

    p = argparse.ArgumentParser(prog="se2diag", description=__doc__)
    p.add_argument("--version", action="version", version=f"se2diag {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", parents=[common], help="game build, mods, plugins, session, counts, memory, uptime")
    s.add_argument("--pid", type=int)
    s.add_argument("--no-game", action="store_true", help="host and logs only; do not contact the game")
    s.set_defaults(fn=cmd_status)

    perf = sub.add_parser("perf", help="performance: game (in-game sampling), host, counters, trace, gcdump, native")
    ps = perf.add_subparsers(dest="perf_cmd", required=True)
    g = ps.add_parser("game", parents=[common], help="sample frame times, sim speed, GC/JIT/locks, PSOs, engine stats in game")
    g.add_argument("--seconds", type=float, default=10)
    g.add_argument("--no-stats", action="store_true", help="skip the stat-tree snapshots")
    g.add_argument("--stat-filter", help="stat path prefixes, comma separated (default: the whole tree)")
    g.add_argument("--from", dest="from_file", metavar="FILE", help="re-analyze a saved raw sample instead")
    g.set_defaults(fn=cmd_perf_game)
    h = ps.add_parser("host", parents=[common], help="/proc sampling: CPU per thread (with names), faults, I/O, switches")
    h.add_argument("--seconds", type=float, default=5)
    h.add_argument("--top", type=int, default=25)
    h.add_argument("--pid", type=int)
    h.set_defaults(fn=cmd_perf_host)
    c = ps.add_parser("counters", parents=[common], help="dotnet-counters for N seconds, summarized")
    c.add_argument("--seconds", type=float, default=10)
    c.add_argument("--providers", default="System.Runtime")
    c.add_argument("--pid", type=int)
    c.add_argument("--install", action="store_true", help="install dotnet-counters into ~/.dotnet/tools if missing")
    c.add_argument("--from", dest="from_file", metavar="FILE", help="summarize an existing counters JSON")
    c.set_defaults(fn=cmd_perf_counters)
    t = ps.add_parser("trace", parents=[common], help="dotnet-trace CPU sampling -> hot methods, Harmony/LinuxCompat marked")
    t.add_argument("--seconds", type=float, default=15)
    t.add_argument("--profile", default="dotnet-sampled-thread-time",
                   help="dotnet-trace profile (default dotnet-sampled-thread-time: ~100 Hz stack samples of every managed thread, wall time; "
                        "`dotnet-trace list-profiles` shows the others)")
    t.add_argument("--patches", metavar="FILE", help="JSON from `se2diag patches --json` to mark patched originals")
    t.add_argument("--mark", action="append", metavar="TAG=needle,needle", help="extra frame-name markers")
    t.add_argument("--top", type=int, default=25)
    t.add_argument("--pid", type=int)
    t.add_argument("--install", action="store_true")
    t.add_argument("--from", dest="from_file", metavar="FILE.speedscope.json", help="analyze an existing speedscope file")
    t.set_defaults(fn=cmd_perf_trace)
    gd = ps.add_parser("gcdump", parents=[common], help="managed heap by type (forces a full GC: needs --allow-gc)")
    gd.add_argument("--allow-gc", action="store_true", help="accept the full blocking GC pause in the game")
    gd.add_argument("--pid", type=int)
    gd.add_argument("--install", action="store_true")
    gd.add_argument("--from", dest="from_file", metavar="FILE.gcdump")
    gd.set_defaults(fn=cmd_perf_gcdump)
    n = ps.add_parser("native", parents=[common], help="Linux perf: native hot spots (vkd3d, DXVK, Havok, kernel)")
    n.add_argument("--seconds", type=float, default=10)
    n.add_argument("--freq", type=int, default=199)
    n.add_argument("--top", type=int, default=40)
    n.add_argument("--pid", type=int)
    n.set_defaults(fn=cmd_perf_native)

    tl = sub.add_parser("tools", parents=[common], help="check (or --install) dotnet-counters/-trace/-gcdump and perf")
    tl.add_argument("--install", action="store_true", help="dotnet tool install --global for each missing tool (your home, no sudo)")
    tl.set_defaults(fn=cmd_tools)

    lt = sub.add_parser("loadtime", parents=[common], help="load-phase timings from a game log; --compare another log")
    lt.add_argument("log", nargs="?", help="log file or name (default newest native log; 'proton' = newest Proton log)")
    lt.add_argument("--compare", metavar="LOG", help="second log (B), e.g. a Proton log or one from before an update")
    lt.add_argument("--proton", action="store_true", help="take the newest Proton log as A")
    lt.add_argument("--pulsar", metavar="FILE", help="Pulsar info.log (default: Modern/info.log next to the discovered Pulsar; SE2_PULSAR_LOG)")
    lt.add_argument("--no-pulsar", action="store_true")
    lt.add_argument("--min-ms", type=float, default=500.0)
    lt.add_argument("--min-ratio", type=float, default=0.2)
    lt.set_defaults(fn=cmd_loadtime)

    lg = sub.add_parser("logs", parents=[common], help="known-bad pattern counts in the current game log; --grep, --tail")
    lg.add_argument("log", nargs="?")
    lg.add_argument("--proton", action="store_true")
    lg.add_argument("--since-session", action="store_true", help="only since the last world load started")
    lg.add_argument("--grep", metavar="REGEX")
    lg.add_argument("--pattern", action="append", metavar="REGEX", help="extra pattern to count")
    lg.add_argument("--tail", type=float, metavar="SECONDS", help="follow the log for SECONDS (bounded)")
    lg.add_argument("--samples", type=int, default=3)
    lg.add_argument("--limit", type=int, default=200)
    lg.add_argument("--strict", action="store_true", help="exit 1 when an error-severity pattern matched")
    lg.set_defaults(fn=cmd_logs)

    i = sub.add_parser("inspect", parents=[common], help="inspect an entity: look | id:<id> | name:<text>")
    i.add_argument("target", nargs="?", default="look")
    i.add_argument("--side", choices=["client", "server"], default="client")
    i.add_argument("--depth", type=int, default=1, help="field dump depth (0-4)")
    i.add_argument("--max-dist", type=float, default=30.0)
    i.add_argument("--no-dump", action="store_true", help="skip the generic component field dump")
    i.add_argument("--no-inventory", action="store_true", help="skip the item stacks probe")
    i.set_defaults(fn=cmd_inspect)

    m = sub.add_parser("modcheck", parents=[common], help="PASS/FAIL per checklist ID for the named mods")
    m.add_argument("--mods", default=None, metavar="NAME:PREFIX[,...]",
                   help="mods to check (default: the mods of the project in the current directory)")
    m.add_argument("--log", help="game log to grep (default newest)")
    m.add_argument("--all-log", action="store_true", help="grep the whole log, not only since the last world load")
    m.set_defaults(fn=cmd_modcheck)

    sh = sub.add_parser("shot", parents=[common], help="screenshot into resources/previews/diag/ (never committed)")
    sh.add_argument("label", nargs="?")
    sh.add_argument("--dir", help="other target folder")
    sh.set_defaults(fn=cmd_shot)

    st = sub.add_parser("stats", parents=[common], help="engine stat tree (in game, or --file a *_Stats.log); --compare")
    st.add_argument("--filter", help="path prefixes, comma separated, e.g. Performance,Memory/GC")
    st.add_argument("--samples", type=int, default=1)
    st.add_argument("--interval-ms", type=float, default=1000)
    st.add_argument("--file", help="parse a *_Stats.log instead of asking the game")
    st.add_argument("--compare", metavar="STATS_LOG", help="compare with a *_Stats.log (e.g. Proton's)")
    st.add_argument("--all", action="store_true", help="show hidden stats / unchanged rows")
    st.set_defaults(fn=cmd_stats)

    pa = sub.add_parser("patches", parents=[common], help="Harmony-patched methods and their owners (LinuxCompat etc.)")
    pa.add_argument("--owner", help="only methods patched by an owner containing this text")
    pa.add_argument("--limit", type=int, default=400)
    pa.set_defaults(fn=cmd_patches)

    rd = sub.add_parser("render", parents=[common], help="PSO / shader cache state and render stats")
    rd.add_argument("--filter")
    rd.set_defaults(fn=cmd_render)

    pr = sub.add_parser("probe", parents=[common], help="list / build / run a raw probe (developers)")
    pr.add_argument("action", choices=["list", "build", "run"])
    pr.add_argument("name", nargs="?")
    pr.add_argument("-p", "--param", action="append", metavar="KEY=VALUE")
    pr.add_argument("-o", "--output")
    pr.add_argument("--target", choices=["main", "render", "parallel"], default="main")
    pr.set_defaults(fn=cmd_probe)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    paths.set_env(None)  # discover the environment afresh for this command
    try:
        return a.fn(Ctx(a), a)
    except paths.NotSupported as e:
        warn(str(e))
        return EXIT_UNAVAILABLE
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return EXIT_OK
