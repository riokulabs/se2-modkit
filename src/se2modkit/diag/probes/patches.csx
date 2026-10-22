// patches: every Harmony-patched method in the process (all loaded 0Harmony copies), with each patch's kind, owner
// (the Harmony id, e.g. LinuxCompat's), patch method and priority. Read only: Harmony's public query API.
string ownerFilter = Param("owner", "");
var methods = new List<object>();
var owners = new Dictionary<string, int>();
foreach (var h in __asms.Where(a => { try { return a.GetName().Name == "0Harmony"; } catch { return false; } }))
{
    var harmony = h.GetType("HarmonyLib.Harmony", false);
    var all = harmony?.GetMethod("GetAllPatchedMethods", BindingFlags.Public | BindingFlags.Static);
    var info = harmony?.GetMethod("GetPatchInfo", BindingFlags.Public | BindingFlags.Static);
    if (all == null || info == null) { Miss("HarmonyLib.Harmony.GetAllPatchedMethods/GetPatchInfo in " + h.FullName); continue; }
    foreach (var o in Items(all.Invoke(null, null)))
    {
        var mb = o as MethodBase;
        if (mb == null) continue;
        object pi = null;
        try { pi = info.Invoke(null, new object[] { mb }); } catch { }
        var patches = new List<object>();
        foreach (var kind in new[] { "Prefixes", "Postfixes", "Transpilers", "Finalizers" })
            foreach (var p in Items(GetQ(pi, kind)))
            {
                var owner = GetQ(p, "owner") as string ?? "?";
                var pm = GetQ(p, "PatchMethod") as MethodBase;
                patches.Add(D("kind", kind.TrimEnd('s').Replace("Prefixe", "Prefix"), "owner", owner,
                    "method", pm == null ? null : (pm.DeclaringType?.FullName + "." + pm.Name), "priority", GetQ(p, "priority")));
                owners[owner] = owners.TryGetValue(owner, out var c) ? c + 1 : 1;
            }
        if (ownerFilter.Length > 0 && !patches.Any(x => ((string)((Dictionary<string, object>)x)["owner"]).IndexOf(ownerFilter, StringComparison.OrdinalIgnoreCase) >= 0)) continue;
        string sig = null;
        try { sig = string.Join(", ", mb.GetParameters().Select(x => x.ParameterType.Name)); } catch { }
        methods.Add(D("type", mb.DeclaringType?.FullName, "name", mb.Name, "params", sig,
            "assembly", mb.DeclaringType?.Assembly.GetName().Name, "harmony", h.GetName().Version?.ToString(), "patches", patches));
    }
}
Emit(D("owners", owners, "methods", methods));
