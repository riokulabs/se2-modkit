// Engine stat tree (Keen.VRage.Library.Diagnostics.Stats): every stat the engine registers (Performance, Memory/GC,
// Render, DCS scene tick stats, Havok, Voxels, Audio, ...). This is the same tree the game prints to *_Stats.log on
// exit under Proton. Read only: StatReader is the engine's own read API (values may change while read).
List<Dictionary<string, object>> StatsSnapshot(string filter, int maxNodes)
{
    var list = new List<Dictionary<string, object>>();
    var prefixes = (filter ?? "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries).Select(x => x.Trim().Trim('/')).Where(x => x.Length > 0).ToArray();
    bool Keep(string p) => prefixes.Length == 0 || prefixes.Any(x => p.Equals(x, StringComparison.OrdinalIgnoreCase) || p.StartsWith(x + "/", StringComparison.OrdinalIgnoreCase));
    bool Descend(string p) => prefixes.Length == 0 || prefixes.Any(x => p.Equals(x, StringComparison.OrdinalIgnoreCase) || p.StartsWith(x + "/", StringComparison.OrdinalIgnoreCase) || x.StartsWith(p + "/", StringComparison.OrdinalIgnoreCase));
    try
    {
        var root = Keen.VRage.Library.Diagnostics.Stats.GetRootReader();
        var stack = new Stack<(Keen.VRage.Library.Diagnostics.StatReader R, string P, int Depth)>();
        void PushChildren(Keen.VRage.Library.Diagnostics.StatReader parent, string path, int depth)
        {
            var kids = new List<Keen.VRage.Library.Diagnostics.StatReader>();
            try { parent.ReadChildren(kids); } catch { }
            for (int k = kids.Count - 1; k >= 0; k--)
            {
                string name = "?";
                try { name = kids[k].Description.Name; } catch { }
                stack.Push((kids[k], path == null ? name : path + "/" + name, depth));
            }
        }
        PushChildren(root, null, 0);
        var sb = new StringBuilder();
        while (stack.Count > 0 && list.Count < maxNodes)
        {
            var (r, p, depth) = stack.Pop();
            if (!Descend(p)) continue;
            Keen.VRage.Library.Diagnostics.StatDescription d;
            try { d = r.Description; } catch { continue; }
            bool mirror = (d.Flags & Keen.VRage.Library.Diagnostics.StatFlags.Mirror) != 0;
            if (Keep(p))
            {
                var node = new Dictionary<string, object> { ["path"] = p, ["kind"] = d.Kind.ToString(), ["unit"] = d.Unit };
                if (d.Kind != Keen.VRage.Library.Diagnostics.StatKind.None && d.Kind != Keen.VRage.Library.Diagnostics.StatKind.Composite)
                {
                    try { node["value"] = r.Value; } catch { }
                    if ((d.Flags & Keen.VRage.Library.Diagnostics.StatFlags.HasAggregator) != 0) { try { node["agg"] = r.AggregateValue; } catch { } }
                    try { sb.Clear(); r.FormatValue(sb); node["text"] = sb.ToString(); } catch { }
                }
                if (mirror) node["mirror"] = true;
                if ((d.Flags & Keen.VRage.Library.Diagnostics.StatFlags.GuiHidden) != 0) node["hidden"] = true;
                list.Add(node);
            }
            if (!mirror && depth < 14) PushChildren(r, p, depth + 1);
        }
        if (stack.Count > 0) Note($"stats: stopped at {maxNodes} nodes");
    }
    catch (Exception ex) { Note("stats tree unavailable: " + ex.GetType().Name + ": " + ex.Message); }
    return list;
}
Keen.VRage.Library.Diagnostics.StatReader? StatAt(string path)
{
    try
    {
        Keen.VRage.Library.Diagnostics.StatReader? r = Keen.VRage.Library.Diagnostics.Stats.GetRootReader();
        foreach (var part in path.Split('/'))
        {
            if (!r.HasValue || !r.Value.TryFindChild(part.AsSpan(), out var next)) { Miss("stat " + path); return null; }
            r = next;
        }
        return r;
    }
    catch { Miss("stat " + path); return null; }
}
