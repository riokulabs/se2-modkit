// stats: dump the engine stat tree (optionally filtered by path prefixes, comma separated, e.g.
// "Performance,Memory/GC,RenderSceneStats"). With samples > 1, take that many snapshots `interval_ms` apart (one
// yield per frame while waiting) so counters can be turned into rates. Read only.
//#include _stats.csx
string filter = Param("filter", "");
int maxNodes = Math.Max(10, Math.Min(20000, ParamInt("max_nodes", 6000)));
int samples = Math.Max(1, Math.Min(20, ParamInt("samples", 1)));
double intervalMs = Math.Max(50, Math.Min(60000, ParamNum("interval_ms", 1000)));
var snaps = new List<object>();
for (int i = 0; i < samples; i++)
{
    if (i > 0)
    {
        long w = System.Diagnostics.Stopwatch.GetTimestamp();
        while (MsSince(w) < intervalMs) yield return null;
    }
    snaps.Add(D("t_ms", Math.Round(MsSince(__t0), 3), "nodes", StatsSnapshot(filter, maxNodes)));
}
Emit(D("filter", filter, "snapshots", snaps));
