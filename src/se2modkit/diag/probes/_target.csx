// Target resolution shared by inspect and inventory. Parameters:
//   target = "look" (the block under the camera's centre ray) | "id:<entity id>" | "name:<text>" (debug name or block
//            definition name contains text, case-insensitive) ; side = "client" | "server" ; max_dist = metres (look)
// The look ray is the render camera's forward axis (-Z) from its world transform; each block counts as a sphere of
// half its local-bounds size, the nearest hit wins. Approximate (no physics query), so the candidates are listed.
(double X, double Y, double Z) Fwd(object q)
{
    double x = Convert.ToDouble(GetQ(q, "X")), y = Convert.ToDouble(GetQ(q, "Y")), z = Convert.ToDouble(GetQ(q, "Z")), w = Convert.ToDouble(GetQ(q, "W"));
    return (-2 * (w * y + x * z), 2 * (w * x - y * z), -1 + 2 * (x * x + y * y));
}
double BlockRadius(object entity)
{
    try
    {
        var lb = GetQ(CompOf(entity, "CubeBlockComponent"), "LocalBounds");
        var mn = GetQ(lb, "Min"); var mx = GetQ(lb, "Max");
        double dx = Convert.ToDouble(GetQ(mx, "X")) - Convert.ToDouble(GetQ(mn, "X"));
        double dy = Convert.ToDouble(GetQ(mx, "Y")) - Convert.ToDouble(GetQ(mn, "Y"));
        double dz = Convert.ToDouble(GetQ(mx, "Z")) - Convert.ToDouble(GetQ(mn, "Z"));
        double r = Math.Sqrt(dx * dx + dy * dy + dz * dz) / 2;
        return r > 0.2 && r < 100 ? r : 1.25;
    }
    catch { return 1.25; }
}
object CameraEntity(object session)
{
    foreach (var e in EntitiesOf(session))
        foreach (var c in e.Components)
            if (c != null && c.GetType().Name == "CameraSystemComponent") return GetQ(c, "RenderCameraEntity");
    foreach (var c in SessionComps(session)) if (IsA(c, "CameraSystemComponent")) return GetQ(c, "RenderCameraEntity");
    return null;
}
// A block's identity across client and server: definition + grid cells + grid name (debug names are not unique).
string BlockKey(object e)
{
    var cb = CompOf(e, "CubeBlockComponent");
    return cb == null ? null : $"{DefName(GetQ(cb, "Definition"))}|{Str(GetQ(cb, "AABB"))}|{EName(GetQ(GetQ(cb, "Grid"), "Entity"))}";
}
object TwinOf(object e, object session)
{
    var k = BlockKey(e);
    if (k == null) return null;
    foreach (var x in EntitiesOf(session)) if (BlockKey(x) == k) return x;
    return null;
}
string BlockLabel(object e) => $"{EName(e)} [{DefName(BlockDefOf(e)) ?? "no block"}] id={EId(e)}";
(object Entity, List<object> Candidates, string How) ResolveTarget(string target, string side, double maxDist)
{
    var cands = new List<object>();
    var session = SessionOf(side);
    if (session == null) return (null, cands, $"no {side} session (is a world loaded?)");
    if (target.StartsWith("key:"))
    {
        string want = target.Substring(4);
        foreach (var e in EntitiesOf(session)) if (BlockKey(e) == want) return (e, cands, "matched block key");
        return (null, cands, "no block with key " + want + " on the " + side);
    }
    if (target.StartsWith("id:") || target.StartsWith("name:"))
    {
        bool byId = target.StartsWith("id:");
        string want = target.Substring(byId ? 3 : 5);
        object first = null;
        foreach (var e in EntitiesOf(session))
        {
            bool hit;
            if (byId) hit = EId(e) == want || EName(e) == want;
            else
            {
                string n = EName(e) ?? "", dn = DefName(BlockDefOf(e)) ?? "";
                hit = n.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0 || dn.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0;
            }
            if (!hit) continue;
            first ??= e;
            if (cands.Count < 25) cands.Add(BlockLabel(e));
        }
        return (first, cands, first == null ? "no entity matched " + target : (cands.Count > 1 ? $"{cands.Count}+ matches, showing the first" : "matched"));
    }
    // look: the camera lives on the client
    var cam = CameraEntity(SessionOf("client"));
    if (cam == null) return (null, cands, "no CameraSystemComponent / RenderCameraEntity found");
    var cp = WorldPos(cam);
    var q = WorldOrientation(cam);
    if (cp == null || q == null) return (null, cands, "camera transform unavailable");
    var f = Fwd(q);
    var c0 = cp.Value;
    var hits = new List<(double Score, double Dist, object E)>();
    var lookSession = SessionOf("client");
    foreach (var e in EntitiesOf(lookSession))
    {
        bool block = false;
        foreach (var c in e.Components) if (c != null && IsA(c, "CubeBlockComponent")) { block = true; break; }
        if (!block) continue;
        var p = WorldPos(e);
        if (p == null) continue;
        double vx = p.Value.X - c0.X, vy = p.Value.Y - c0.Y, vz = p.Value.Z - c0.Z;
        double t = vx * f.X + vy * f.Y + vz * f.Z;
        if (t < 0 || t > maxDist) continue;
        double px = vx - t * f.X, py = vy - t * f.Y, pz = vz - t * f.Z;
        double perp = Math.Sqrt(px * px + py * py + pz * pz);
        double r = BlockRadius(e);
        if (perp > r) continue;
        hits.Add((t - r + perp * 0.25, t, e));
    }
    hits.Sort((a, b) => a.Score.CompareTo(b.Score));
    foreach (var h in hits.Take(8)) cands.Add($"{BlockLabel(h.E)} at {h.Dist:0.0} m");
    if (hits.Count == 0) return (null, cands, $"no block within {maxDist} m on the camera ray (camera at {c0.X:0.0}, {c0.Y:0.0}, {c0.Z:0.0})");
    var hit0 = hits[0].E;
    if (side == "client") return (hit0, cands, "look");
    // the same block on the server: same definition, grid cells and grid
    var twin = TwinOf(hit0, session);
    return twin != null ? (twin, cands, "look (server twin by block key)") : (null, cands, "looked-at block has no server twin with key " + BlockKey(hit0));
}
