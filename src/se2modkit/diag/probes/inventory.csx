// inventory: the item stacks of every inventory on one entity (target as in inspect). Items live in shared DCS
// data behind a ReadOnlySpan, which reflection cannot read, so this probe uses the game type InventoryComponent
// directly (compile-checked against 4.0.0.3151; it is kept apart from inspect so a rename here only breaks this one).
// Read only.
//#include _target.csx
string target = Param("target", "look");
string side = Param("side", "server") == "client" ? "client" : "server";
var (ent, cands, how) = ResolveTarget(target, side, ParamNum("max_dist", 30));
if (ent == null) { Emit(D("found", false, "how", how, "candidates", cands)); yield break; }
List<object> Stacks(Keen.Game2.Simulation.WorldObjects.Items.InventoryComponent inv)
{
    var list = new List<object>();
    try
    {
        var span = inv.Items;
        for (int i = 0; i < span.Length && i < 200; i++)
            list.Add(D("index", i, "item", DefName(span[i].Definition), "amount", Str(span[i].Amount), "meta", span[i].MetaData == null ? null : span[i].MetaData.GetType().Name));
    }
    catch (Exception ex) { Note("items: " + ex.GetType().Name + ": " + ex.Message); }
    return list;
}
var outp = new List<object>();
int ci = 0;
foreach (var c in CompsOf(ent))
{
    if (c is Keen.Game2.Simulation.WorldObjects.Items.InventoryComponent inv)
    {
        double? mass = null;
        try { mass = inv.DynamicMass; } catch { }
        outp.Add(D("component_index", ci, "definition", DefName(inv.Definition), "inventory_index", inv.InventoryIndex,
            "mass", mass, "max_mass", Str(GetQ(inv.Definition, "MaxMass")), "max_stacks", GetQ(inv.Definition, "MaxStacks"), "stacks", Stacks(inv)));
    }
    ci++;
}
Emit(D("found", true, "how", how, "side", side, "entity", EName(ent), "id", EId(ent), "block", DefName(BlockDefOf(ent)), "inventories", outp));
