// perf: sample the game for N seconds, one cheap sample per main-thread frame (the script is resumed once per frame
// by `yield return null`). Per frame: wall delta, the engine's own main-thread stage timings (stat tree
// Performance/Main Thread/*), GC pause and collections, JIT time, monitor lock contentions. Every 30 frames: PSO cache
// sizes (a PSO compiled mid-game is a stutter on the Vulkan/vkd3d path). Start and end: game time (sim speed),
// GC/JIT/thread-pool totals, the VRage worker-pool stats and (optionally) the whole stat tree. Read only.
//#include _stats.csx
double seconds = Math.Max(1, Math.Min(300, ParamNum("seconds", 10)));
bool withStats = ParamBool("stats", true);
string statFilter = Param("stat_filter", "");

object ServerTick() => GetQ(App(), "Server");
object ClientTick() => GetQ(App(), "Client");
double? GameSeconds(object tick) => GetQ(tick, "_elapsedTime") is TimeSpan s ? s.TotalSeconds : (double?)null;
var coreSystems = TQ("Keen.VRage.Render12.Core.CoreSystems");
if (coreSystems == null) Miss("type Keen.VRage.Render12.Core.CoreSystems");
int? PsoCount(string field) { var mgr = coreSystems == null ? null : St(coreSystems, field); return CountOf(GetQ(mgr, "_dictionary")); }
Dictionary<string, object> PsoCounts() => D("graphics", PsoCount("GraphicsPSOs"), "compute", PsoCount("ComputePSOs"), "raytracing", PsoCount("RayTracingPSOs"));
object KeenPool()
{
    var par = TQ("Keen.VRage.Library.Threading.Parallel");
    var s = par == null ? null : St(par, "ThreadPoolStats");
    return s == null ? null : D("missed_work", GetQ(s, "MissedWork"), "work", GetQ(s, "WorkCount"), "sleeps", GetQ(s, "SleepCount"), "active_workers", GetQ(s, "ActiveWorkers"));
}
Dictionary<string, object> Totals() => D(
    "wall_ms", MsSince(__t0),
    "server_game_s", GameSeconds(ServerTick()), "client_game_s", GameSeconds(ClientTick()),
    "server_paused", GetQ(ServerTick(), "Pause"), "client_paused", GetQ(ClientTick(), "Pause"),
    "gc_gen0", GC.CollectionCount(0), "gc_gen1", GC.CollectionCount(1), "gc_gen2", GC.CollectionCount(2),
    "gc_pause_ms", GC.GetTotalPauseDuration().TotalMilliseconds, "gc_allocated", GC.GetTotalAllocatedBytes(false),
    "gc_heap", GC.GetTotalMemory(false),
    "jit_methods", System.Runtime.JitInfo.GetCompiledMethodCount(false), "jit_ms", System.Runtime.JitInfo.GetCompilationTime(false).TotalMilliseconds,
    "lock_contentions", System.Threading.Monitor.LockContentionCount,
    "threadpool_threads", System.Threading.ThreadPool.ThreadCount, "threadpool_pending", System.Threading.ThreadPool.PendingWorkItemCount,
    "threadpool_completed", System.Threading.ThreadPool.CompletedWorkItemCount,
    "keen_pool", KeenPool(), "pso", PsoCounts());

// main-thread stage stats (seconds per frame); missing ones are reported and skipped
var stageNames = new[] { "ClientSession", "ServerSession", "Draw", "Input", "Platform" };
var stages = new List<(string Name, Keen.VRage.Library.Diagnostics.StatReader R)>();
foreach (var n in stageNames) { var r = StatAt("Performance/Main Thread/" + n); if (r.HasValue) stages.Add((n, r.Value)); }
var uiStat = StatAt("Performance/UI");
if (uiStat.HasValue) stages.Add(("UI", uiStat.Value));

yield return null;   // start on a frame boundary
var startTotals = Totals();
var startStats = withStats ? StatsSnapshot(statFilter, 4000) : null;
var frames = new List<double>();
var stageSeries = stages.ToDictionary(s => s.Name, s => new List<double>());
var gcPause = new List<double>();
var gcGen = new List<int>();
var jit = new List<double>();
var locks = new List<long>();
var psoSeries = new List<object>();
long last = System.Diagnostics.Stopwatch.GetTimestamp();
long sampleStart = last;
double lastPause = GC.GetTotalPauseDuration().TotalMilliseconds, lastJit = System.Runtime.JitInfo.GetCompilationTime(false).TotalMilliseconds;
int g0 = GC.CollectionCount(0), g1 = GC.CollectionCount(1), g2 = GC.CollectionCount(2);
long lastLocks = System.Threading.Monitor.LockContentionCount;
while (true)
{
    yield return null;
    long now = System.Diagnostics.Stopwatch.GetTimestamp();
    frames.Add(Math.Round((now - last) * 1000.0 / System.Diagnostics.Stopwatch.Frequency, 3));
    last = now;
    foreach (var s in stages) { try { stageSeries[s.Name].Add(Math.Round(s.R.Value * 1000.0, 3)); } catch { stageSeries[s.Name].Add(-1); } }
    double pause = GC.GetTotalPauseDuration().TotalMilliseconds;
    gcPause.Add(Math.Round(pause - lastPause, 3)); lastPause = pause;
    int n0 = GC.CollectionCount(0), n1 = GC.CollectionCount(1), n2 = GC.CollectionCount(2);
    gcGen.Add(n2 != g2 ? 2 : n1 != g1 ? 1 : n0 != g0 ? 0 : -1); g0 = n0; g1 = n1; g2 = n2;
    double j = System.Runtime.JitInfo.GetCompilationTime(false).TotalMilliseconds;
    jit.Add(Math.Round(j - lastJit, 3)); lastJit = j;
    long lc = System.Threading.Monitor.LockContentionCount;
    locks.Add(lc - lastLocks); lastLocks = lc;
    if (frames.Count % 30 == 0) psoSeries.Add(D("frame", frames.Count, "pso", PsoCounts()));
    if ((now - sampleStart) >= seconds * System.Diagnostics.Stopwatch.Frequency) break;
}
var endTotals = Totals();
var endStats = withStats ? StatsSnapshot(statFilter, 4000) : null;
Emit(D("seconds", seconds, "start", startTotals, "end", endTotals,
    "frames_ms", frames, "stages_ms", stageSeries, "gc_pause_ms", gcPause, "gc_gen", gcGen, "jit_ms", jit,
    "lock_contentions", locks, "pso_series", psoSeries,
    "stats_start", startStats, "stats_end", endStats));
