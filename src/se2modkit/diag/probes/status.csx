// status: game build, runtime, mods, plugins (non-game assemblies + Harmony owners), session, entity/grid/block
// counts, memory and GC, clocks (the Stopwatch-overflow uptime bugs). Read only. Scans entities in chunks.
var result = new Dictionary<string, object>();

// -- game and runtime --
var ga = __asms.FirstOrDefault(a => { try { return a.GetName().Name == "Game2.Game"; } catch { return false; } });
var args = Environment.GetCommandLineArgs();
result["game"] = D(
    "version", Str(GetQ(App(), "GameVersion")),
    "assembly_version", Str(typeof(GameAppComponent).Assembly.GetName().Version),
    "game2_game_version", Str(ga?.GetName().Version),
    "runtime", System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
    "os", System.Runtime.InteropServices.RuntimeInformation.OSDescription,
    "arch", System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(),
    "load_scripts", args.Contains("-loadScripts"),
    "args", args.Skip(1).Select(a => a.Length > 300 ? a.Substring(0, 300) + "..." : a).ToList());

// -- clocks: native Linux Stopwatch counts ns since boot (Frequency 1e9); Windows/Proton 1e7 --
long ts = System.Diagnostics.Stopwatch.GetTimestamp();
long freq = System.Diagnostics.Stopwatch.Frequency;
var proc = System.Diagnostics.Process.GetCurrentProcess();
DateTime start = DateTime.MinValue;
try { start = proc.StartTime; } catch { }
result["clocks"] = D(
    "stopwatch_frequency", freq,
    "stopwatch_seconds", ts / (double)freq,
    "tickcount64_ms", Environment.TickCount64,
    "process_start_local", start == DateTime.MinValue ? null : start.ToString("o", CultureInfo.InvariantCulture),
    "process_uptime_s", start == DateTime.MinValue ? (object)null : (DateTime.Now - start).TotalSeconds,
    // the timestamp read as milliseconds overflows a TimeSpan (the 10.7-day crash)
    "timespan_ms_overflow", ts > TimeSpan.MaxValue.TotalMilliseconds,
    // the timestamp x 10_000_000 overflows long past 922 s of uptime at 1 GHz, wraps every 30.7 min
    "x1e7_overflow", ts > long.MaxValue / 10_000_000L);

// -- memory and GC --
var gi = GC.GetGCMemoryInfo();
result["memory"] = D(
    "working_set", proc.WorkingSet64, "private_bytes", proc.PrivateMemorySize64, "virtual_bytes", proc.VirtualMemorySize64,
    "threads", proc.Threads.Count, "handles", proc.HandleCount,
    "gc_heap", GC.GetTotalMemory(false), "gc_heap_size", gi.HeapSizeBytes, "gc_committed", gi.TotalCommittedBytes,
    "gc_fragmented", gi.FragmentedBytes, "gc_memory_load", gi.MemoryLoadBytes, "gc_total_available", gi.TotalAvailableMemoryBytes,
    "gc_pause_percent", gi.PauseTimePercentage, "gc_total_pause_ms", GC.GetTotalPauseDuration().TotalMilliseconds,
    "gc_total_allocated", GC.GetTotalAllocatedBytes(false),
    "gc_gen0", GC.CollectionCount(0), "gc_gen1", GC.CollectionCount(1), "gc_gen2", GC.CollectionCount(2),
    "gc_server", System.Runtime.GCSettings.IsServerGC, "gc_latency_mode", System.Runtime.GCSettings.LatencyMode.ToString(),
    "jit_methods", System.Runtime.JitInfo.GetCompiledMethodCount(false),
    "jit_ms", System.Runtime.JitInfo.GetCompilationTime(false).TotalMilliseconds,
    "lock_contentions", System.Threading.Monitor.LockContentionCount,
    "threadpool_threads", System.Threading.ThreadPool.ThreadCount,
    "threadpool_pending", System.Threading.ThreadPool.PendingWorkItemCount);

// -- plugins: assemblies that are neither the runtime nor the game; Harmony patch owners --
bool Framework(string n) => n.StartsWith("System") || n.StartsWith("Microsoft.") || n == "mscorlib" || n == "netstandard" || n.StartsWith("Keen.") || n.StartsWith("VRage") || n.StartsWith("Game2") || n == "SpaceEngineers2" || n.StartsWith("Avalonia") || n.StartsWith("SkiaSharp") || n.StartsWith("HarfBuzz") || n.StartsWith("Steamworks") || n.StartsWith("Anonymously Hosted");
var extra = new List<object>();
foreach (var a in __asms)
{
    string n = null;
    try { n = a.GetName().Name; } catch { }
    if (n == null || Framework(n) || n.StartsWith("__REPL__")) continue;
    string loc = null;
    try { loc = a.IsDynamic ? "(dynamic)" : a.Location; } catch { }
    extra.Add(D("name", n, "version", Str(a.GetName().Version), "location", string.IsNullOrEmpty(loc) ? "(in memory)" : loc));
}
var owners = new Dictionary<string, int>();
int patchedMethods = 0;
foreach (var h in __asms.Where(a => { try { return a.GetName().Name == "0Harmony"; } catch { return false; } }))
{
    var harmony = h.GetType("HarmonyLib.Harmony", false);
    var all = harmony?.GetMethod("GetAllPatchedMethods", BindingFlags.Public | BindingFlags.Static);
    var info = harmony?.GetMethod("GetPatchInfo", BindingFlags.Public | BindingFlags.Static);
    if (all == null || info == null) { Miss("HarmonyLib.Harmony.GetAllPatchedMethods/GetPatchInfo in " + h.FullName); continue; }
    foreach (var mb in Items(all.Invoke(null, null)))
    {
        patchedMethods++;
        object pi = null;
        try { pi = info.Invoke(null, new[] { mb }); } catch { }
        foreach (var kind in new[] { "Prefixes", "Postfixes", "Transpilers", "Finalizers" })
            foreach (var p in Items(GetQ(pi, kind)))
            {
                var o = GetQ(p, "owner") as string ?? "?";
                owners[o] = owners.TryGetValue(o, out var c) ? c + 1 : 1;
            }
    }
}
result["plugins"] = D("assemblies", extra, "harmony_patched_methods", patchedMethods, "harmony_patches_by_owner", owners);

// -- mods and world (AnalyticsSessionComponent keeps the ModList; GameSaveInfoSessionComponent the save name) --
var cs = SessionOf("client");
var ss = SessionOf("server");
var analytics = cs == null ? null : SessionComp(cs, "AnalyticsSessionComponent");
var modList = GetQ(analytics, "_modList");
var mods = new List<object>();
foreach (var r in Items(GetQ(modList, "Records")))
    mods.Add(D("name", Str(GetQ(r, "DisplayName")), "project", Str(GetQ(r, "Project"), 200)));
var saveInfo = (ss == null ? null : SessionComp(ss, "GameSaveInfoSessionComponent")) ?? (cs == null ? null : SessionComp(cs, "GameSaveInfoSessionComponent"));
object Elapsed(object tick) { var t = GetQ(tick, "_elapsedTime"); return t is TimeSpan s ? s.TotalSeconds : (object)null; }
result["session"] = D(
    "client_session", cs != null, "server_session", ss != null,
    "world", Str(GetQ(saveInfo, "SaveName")) ?? Str(GetQ(analytics, "_gameSaveInfo") == null ? null : GetQ(GetQ(analytics, "_gameSaveInfo"), "SaveName")),
    "world_type", Str(GetQ(saveInfo, "WorldType")), "new_world", GetQ(saveInfo, "IsNewWorld"),
    "mod_list_found", modList != null, "mods", mods,
    "server_paused", GetQ(GetQ(App(), "Server"), "Pause"), "client_paused", GetQ(GetQ(App(), "Client"), "Pause"),
    "server_game_seconds", Elapsed(GetQ(App(), "Server")), "client_game_seconds", Elapsed(GetQ(App(), "Client")),
    "client_session_components", cs == null ? 0 : SessionComps(cs).Count,
    "server_session_components", ss == null ? 0 : SessionComps(ss).Count);

// -- entity / grid / block / character counts (chunked: 4000 entities per frame) --
foreach (var side in new[] { "client", "server" })
{
    var s = SessionOf(side);
    if (s == null) continue;
    var ents = EntitiesOf(s);
    int grids = 0, blocks = 0, chars = 0, floating = 0, i = 0;
    var blockDefs = new Dictionary<string, int>();
    foreach (var e in ents)
    {
        bool isBlock = false;
        foreach (var c in e.Components)
        {
            var tn = c?.GetType().Name;
            if (tn == "CubeGridComponent") grids++;
            else if (tn == "CharacterComponent") chars++;
            else if (tn != null && tn.StartsWith("FloatingObject") && tn.EndsWith("Component")) floating++;
            if (!isBlock && c != null && IsA(c, "CubeBlockComponent"))
            {
                isBlock = true;
                blocks++;
                var dn = DefName(GetQ(c, "Definition")) ?? "?";
                blockDefs[dn] = blockDefs.TryGetValue(dn, out var k) ? k + 1 : 1;
            }
        }
        if (++i % 4000 == 0) yield return null;
    }
    result["entities_" + side] = D("entities", ents.Count, "pending_close", GetQ(s, "PendingEntityCloseCount"),
        "grids", grids, "blocks", blocks, "characters", chars, "floating_object_components", floating,
        "top_blocks", blockDefs.OrderByDescending(kv => kv.Value).Take(15).Select(kv => D("definition", kv.Key, "count", kv.Value)).ToList());
}
Emit(result);
