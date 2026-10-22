// inspect: one entity (by look / id / name, see _target.csx) in depth. Decoded views for blocks (definition, build
// progress, integrity, functional/working, power, fracture zones, conditional subparts, render mesh-part hiding,
// inventories layout, mount points), its hierarchy, and a generic field dump of every component (fields only, no
// property getters, so nothing is computed or lazily created). Read only.
//#include _target.csx
string target = Param("target", "look");
string side = Param("side", "client") == "server" ? "server" : "client";
int depth = Math.Max(0, Math.Min(4, ParamInt("depth", 1)));
bool dumpAll = ParamBool("dump", true);
var (ent, cands, how) = ResolveTarget(target, side, ParamNum("max_dist", 30));
if (ent == null) { Emit(D("found", false, "how", how, "candidates", cands)); yield break; }

bool Simple(object o) => o == null || o is string || o is bool || o is Enum || o is Guid || o is TimeSpan || o is DateTime || o.GetType().IsPrimitive || o is decimal;
bool SeenAdd(Dictionary<int, List<object>> seen, object o)
{
    int h = System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(o);
    if (!seen.TryGetValue(h, out var bucket)) seen[h] = bucket = new List<object>();
    foreach (var x in bucket) if (ReferenceEquals(x, o)) return false;
    bucket.Add(o);
    return true;
}
object Dump(object o, int d, Dictionary<int, List<object>> seen)
{
    if (Simple(o)) return o is Guid g ? g.ToString() : o;
    var t = o.GetType();
    if (o is Keen.VRage.DCS.Components.Entity en) return "Entity " + EName(en);
    if (o is Delegate) return "delegate " + t.Name;
    if (IsA(o, "Definition")) return D("definition", DefName(o), "guid", Str(GetQ(o, "Guid")), "type", t.Name);
    if (o is System.Collections.IEnumerable && !(o is string))
    {
        var items = Items(o, 8);
        return D("type", t.Name, "count", CountOf(o), "items", d <= 0 ? items.Select(x => (object)Str(x, 160)).ToList() : items.Select(x => Dump(x, d - 1, seen)).ToList());
    }
    if (d <= 0 || (!t.IsValueType && !SeenAdd(seen, o))) return Str(o, 200);
    if (t.IsValueType)
    {
        var s = Str(o, 200);
        if (s != t.FullName && s != t.Name) return s;      // math structs, ids, handles: their ToString is the useful view
    }
    var fields = new Dictionary<string, object>();
    for (var bt = t; bt != null && bt != typeof(object) && !(bt.Namespace ?? "").StartsWith("System") && bt.Name != "Component" && bt.Name != "GameComponent"; bt = bt.BaseType)
    {
        FieldInfo[] fs;
        try { fs = bt.GetFields(__BF); } catch { continue; }
        foreach (var f in fs)
        {
            if (fields.Count >= 80) break;
            if (f.IsStatic || f.FieldType.IsByRefLike || f.Name.StartsWith("___JobId") || f.Name == "__signalTableOffset") continue;
            string name = f.Name.StartsWith("<") && f.Name.Contains(">k__BackingField") ? f.Name.Substring(1, f.Name.IndexOf('>') - 1) : f.Name;
            if (fields.ContainsKey(name)) continue;
            object v;
            try { v = f.GetValue(o); } catch (Exception ex) { v = "<" + ex.GetType().Name + ">"; }
            fields[name] = IsA(v, "Component") ? "Component " + v.GetType().Name : Dump(v, d - 1, seen);
        }
    }
    return D("type", t.Name, "fields", fields);
}
object Brief(object e, int d)
{
    var kids = ChildrenOf(e);
    return D("name", EName(e), "id", EId(e), "block", DefName(BlockDefOf(e)),
        "components", CompsOf(e).Select(c => c.GetType().Name).ToList(),
        "children", d <= 0 ? (object)kids.Count : kids.Take(40).Select(k => Brief(k, d - 1)).ToList());
}
var outp = new Dictionary<string, object>();
outp["found"] = true; outp["how"] = how; outp["candidates"] = cands; outp["side"] = side; outp["block_key"] = BlockKey(ent);
var pos = WorldPos(ent);
outp["entity"] = D("name", EName(ent), "id", EId(ent), "position", pos == null ? null : new[] { pos.Value.X, pos.Value.Y, pos.Value.Z },
    "parent", EName(GetQ(CompOf(ent, "HierarchyComponent"), "Parent")), "tree", Brief(ent, 2));

var cb = CompOf(ent, "CubeBlockComponent");
if (cb != null)
{
    var def = GetQ(cb, "Definition");
    var grid = GetQ(cb, "Grid");
    outp["block"] = D("definition", DefName(def), "definition_path", DefPath(def), "guid", Str(GetQ(def, "Guid")), "component_type", cb.GetType().Name,
        "build_progress", GetQ(cb, "BuildProgress"), "health_integrity", GetQ(cb, "HealthIntegrity"),
        "effective_integrity", GetQ(cb, "EffectiveIntegrity"), "effective_build_progress", GetQ(cb, "EffectiveBuildProgress"),
        "absolute_health", GetQ(cb, "AbsoluteHealth"), "destroyed", GetQ(cb, "IsDestroyed"), "generated", GetQ(cb, "Generated"),
        "aabb_cells", Str(GetQ(cb, "AABB")), "orientation", Str(GetQ(cb, "BlockOrientation")), "color", Str(GetQ(cb, "Color")),
        "grid", EName(GetQ(grid, "Entity")), "mass", GetQ(def, "Mass"), "max_health", GetQ(def, "MaxHealth"));
    // functional / working / power: FunctionalBlockComponent and PowerableBlockComponent derive from CubeBlockComponent
    outp["state"] = D("functional", IsA(cb, "FunctionalBlockComponent") ? GetQ(cb, "Functional") : null,
        "working", IsA(cb, "FunctionalBlockComponent") ? GetQ(cb, "Working") : null,
        "enabled", IsA(cb, "PowerableBlockComponent") ? GetQ(cb, "Enabled") : null,
        "supplied", IsA(cb, "PowerableBlockComponent") ? GetQ(cb, "Supplied") : null);
    // mount points (definition data; per face direction: number of mount groups)
    var mp = new Dictionary<string, object>();
    foreach (var kv in Items(GetQ(def, "MountPointsGroupsPerDirection"))) mp[Str(GetQ(kv, "Key"))] = CountOf(GetQ(kv, "Value"));
    outp["mount_points"] = mp;
}
// fracture zones: definition zones (critical or not) and the damaged list (integrity per zone, dead or alive)
var bb = CompOf(ent, "BreakableBlockComponent");
if (bb != null)
{
    var bdef = GetQ(bb, "Definition");
    var zones = Items(GetQ(bdef, "Fractures"));
    var fr = GetQ(bb, "_fractures");
    var damaged = Items(GetQ(fr, "DamagedFractures")).Select(x => D("fracture", Str(GetQ(x, "Fracture")), "integrity", GetQ(x, "Integrity"), "dead", GetQ(x, "IsDead"))).ToList();
    outp["fractures"] = D("zones", zones.Count,
        "critical", zones.Select((z, i) => (z, i)).Where(p => GetQ(p.z, "IsCritical") is true || GetQ(GetQ(p.z, "Value"), "IsCritical") is true).Count(),
        "damaged", damaged, "dead", damaged.Count(x => ((Dictionary<string, object>)x)["dead"] is true),
        "raw", Dump(fr, 2, new Dictionary<int, List<object>>()));
}
// conditional subparts (client): spawned only while Functional; MeshPartsToHideWhenActive are hidden while spawned
var csp = CompOf(ent, "ConditionalSubPartsComponent");
var render = CompOf(ent, "BlockRenderComponent");
var renderer = GetQ(render, "_renderer");
var hidden = new Dictionary<string, string>();
foreach (var kv in Items(GetQ(GetQ(renderer, "_hidingCollection"), "HiddenMeshParts"))) hidden[Str(GetQ(kv, "Key"))] = Str(GetQ(kv, "Value"));
if (csp != null)
{
    var cdef = GetQ(csp, "_definition");
    var spawned = GetQ(csp, "_spawnedSubParts");
    var alive = Items(spawned).Select(w => GetQ(w, "Entity")).Where(x => x != null).ToList();
    var toHide = Items(GetQ(cdef, "MeshPartsToHideWhenActive")).Select(x => Str(x)).ToList();
    var func = GetQ(GetQ(csp, "_block"), "Functional");
    int hiddenNow = toHide.Count(p => hidden.TryGetValue(p, out var r) && r.Contains("RequestedHidden"));
    outp["conditional_subparts"] = D("definition", DefName(cdef), "functional", func, "spawned", spawned != null,
        "spawned_count", CountOf(spawned), "alive", alive.Select(EName).ToList(), "subpart_bindings", CountOf(GetQ(cdef, "SubParts")),
        "hide_when_active", toHide.Count, "hidden_now", hiddenNow,
        "consistent", func is bool fb && fb == (spawned != null) && (fb ? hiddenNow == toHide.Count : hiddenNow == 0));
}
if (render != null)
    outp["render"] = D("hidden_mesh_parts", hidden, "uses_damaged_model", GetQ(renderer, "_usesDamagedModel"),
        "build_stage_index", GetQ(renderer, "_buildStageIndex"), "build_model_index", GetQ(renderer, "_buildModelIndex"),
        "full_model", Str(GetQ(renderer, "_currentFullRenderModel")), "damaged_model", Str(GetQ(renderer, "_currentDamagedRenderModel")),
        "default_full_model", Str(GetQ(renderer, "_defaultFullRenderModel")),
        "material_replacement_sets", CountOf(GetQ(renderer, "_materialReplacements")),
        "full_render_entity", Str(GetQ(renderer, "_fullRenderModel")), "damaged_render_entity", Str(GetQ(renderer, "_damagedRenderModel")));
// inventories: component order, definitions; the slot order InventorySlotsServerComponent uses (server side)
var invs = new List<object>();
int ci = 0;
foreach (var c in CompsOf(ent))
{
    if (IsA(c, "InventoryComponent"))
    {
        var idef = GetQ(c, "Definition");
        invs.Add(D("component_index", ci, "type", c.GetType().Name, "definition", DefName(idef), "max_stacks", GetQ(idef, "MaxStacks"),
            "max_mass", Str(GetQ(idef, "MaxMass")), "whitelist", CountOf(GetQ(idef, "WhitelistedItems")), "inventory_index", GetQ(c, "InventoryIndex")));
    }
    ci++;
}
var slots = CompOf(ent, "InventorySlotsServerComponent");
var compList = CompsOf(ent).ToList();
outp["inventories"] = D("components", invs,
    "slot_order", slots == null ? null : Items(GetQ(slots, "Inventories")).Select(x => (object)compList.IndexOf(x)).ToList(),
    "slot_bitmask", GetQ(slots, "_bitMask"));
if (dumpAll)
{
    var seen = new Dictionary<int, List<object>>();
    outp["components"] = CompsOf(ent).Select(c => D("type", c.GetType().FullName, "dump", Dump(c, depth + 1, seen))).ToList();
}
Emit(outp);
