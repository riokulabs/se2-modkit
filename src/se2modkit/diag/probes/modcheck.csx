// modcheck: the programmatically checkable items of our mods' INGAME-CHECKLIST.md files. Each check returns
// PASS / FAIL / WARN / SKIP (SKIP: nothing to check in this world, e.g. no rack placed) with a detail line.
// IDs: <TAG>-* per mod, TAG = the mod's block prefix without its underscore (see se2modkit/diag/modchecks.py).
// Parameter "mods": "Name:PREFIX,Name2:PREFIX2" (e.g. "MyMod:MM_"). Product-specific checks live in each mod's own
// probes, not here.
// Read only: queries definitions, components and fields; never toggles, spawns or applies anything.
string mods = Param("mods", "");
var modList = mods.Split(',').Select(x => x.Trim()).Where(x => x.Length > 0)
    .Select(x => { var mp = x.Split(':'); return (Name: mp[0].Trim(), Prefix: mp.Length > 1 ? mp[1].Trim() : ""); }).ToList();
var allPrefixes = modList.Select(x => x.Prefix).Where(x => x.Length > 0).ToArray();
var checks = new List<object>();
void Check(string id, string title, string status, string detail) => checks.Add(D("id", id, "title", title, "status", status, "detail", detail));

var cs = SessionOf("client");
var ss = SessionOf("server");
if (cs == null && ss == null) { Emit(D("world", false, "checks", checks)); yield break; }
var clientEnts = EntitiesOf(cs);
var serverEnts = EntitiesOf(ss);
yield return null;

// -- definitions (DefinitionManager via reflection) --
var dmType = T("Keen.VRage.Library.Definitions.DefinitionManager");
object dm = null;
try { dm = dmType == null ? null : typeof(Singleton<>).MakeGenericType(dmType).GetProperty("Instance")?.GetValue(null); } catch { }
if (dmType != null && dm == null) Miss("Singleton<DefinitionManager>.Instance");
List<object> DefsOf(string typeName)
{
    var t = TQ(typeName);
    if (dm == null || t == null) { if (t == null) Miss("type " + typeName); return new List<object>(); }
    try
    {
        var m = dmType.GetMethods(BindingFlags.Public | BindingFlags.Instance).First(x => x.Name == "GetDefinitionsOfTypeAndDescendants" && x.IsGenericMethodDefinition && x.GetParameters().Length == 0);
        return Items(m.MakeGenericMethod(t).Invoke(dm, null));
    }
    catch (Exception ex) { Note("definitions of " + typeName + ": " + ex.GetType().Name); return new List<object>(); }
}
object Config(string typeName)
{
    var t = TQ(typeName);
    if (dm == null || t == null) return null;
    try
    {
        var m = dmType.GetMethods(BindingFlags.Public | BindingFlags.Instance).First(x => x.Name == "TryGetConfiguration" && !x.IsGenericMethod && x.GetParameters().Length == 2);
        var a = new object[] { t, null };
        return m.Invoke(dm, a) is true ? a[1] : null;
    }
    catch { Miss("DefinitionManager.TryGetConfiguration(Type, out Configuration)"); return null; }
}
var allDefs = DefsOf("Keen.VRage.Library.Definitions.Definition");
// the world's mod list (AnalyticsSessionComponent keeps it); names compared without spaces, case-insensitive
var worldMods = Items(GetQ(GetQ(cs == null ? null : SessionComp(cs, "AnalyticsSessionComponent"), "_modList"), "Records"))
    .Select(r => (Str(GetQ(r, "DisplayName")) ?? "").Replace(" ", "").ToLowerInvariant()).ToList();
bool InWorld(string mod) => worldMods.Contains(mod.ToLowerInvariant());
yield return null;

double Num(object o) { try { return Convert.ToDouble(o, CultureInfo.InvariantCulture); } catch { } return double.TryParse(Str(o), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : double.NaN; }
// -- blocks by definition prefix --
List<Keen.VRage.DCS.Components.Entity> Blocks(List<Keen.VRage.DCS.Components.Entity> ents, string prefix)
    => ents.Where(e => (DefName(BlockDefOf(e)) ?? "").StartsWith(prefix, StringComparison.Ordinal)).ToList();

// ConditionalSubParts invariant (ConditionalSubPartsComponent.UpdateSpawn/SpawnSubParts): spawned exactly while the
// block is Functional, and its MeshPartsToHideWhenActive (the baked stand-ins) hidden exactly while spawned.
(int Ok, List<string> Bad, int Total) SubpartConsistency(List<Keen.VRage.DCS.Components.Entity> blocks)
{
    int ok = 0, total = 0;
    var bad = new List<string>();
    foreach (var e in blocks)
    {
        var csp = CompOf(e, "ConditionalSubPartsComponent");
        if (csp == null) continue;
        total++;
        var func = GetQ(GetQ(csp, "_block"), "Functional");
        var spawned = GetQ(csp, "_spawnedSubParts");
        var toHide = Items(GetQ(GetQ(csp, "_definition"), "MeshPartsToHideWhenActive")).Select(x => Str(x)).ToList();
        var hidden = new Dictionary<string, string>();
        foreach (var kv in Items(GetQ(GetQ(GetQ(CompOf(e, "BlockRenderComponent"), "_renderer"), "_hidingCollection"), "HiddenMeshParts")))
            hidden[Str(GetQ(kv, "Key"))] = Str(GetQ(kv, "Value"));
        int hiddenNow = toHide.Count(p => hidden.TryGetValue(p, out var r) && r.Contains("RequestedHidden"));
        int alive = Items(spawned).Count(w => GetQ(w, "Entity") != null);
        bool f = func is true;
        bool good = func is bool && f == (spawned != null) && (!f || alive == CountOf(spawned)) && (f ? hiddenNow == toHide.Count : hiddenNow == 0);
        if (good) ok++;
        else if (bad.Count < 12) bad.Add($"{DefName(BlockDefOf(e))} '{EName(e)}': functional {func}, spawned {(spawned == null ? "no" : alive + "/" + CountOf(spawned) + " alive")}, stand-ins hidden {hiddenNow}/{toHide.Count}");
    }
    return (ok, bad, total);
}
void SubpartCheck(string id, string title, List<Keen.VRage.DCS.Components.Entity> blocks, string what)
{
    var (ok, bad, total) = SubpartConsistency(blocks);
    if (total == 0) Check(id, title, "SKIP", $"no {what} with ConditionalSubParts in this world");
    else Check(id, title, bad.Count == 0 ? "PASS" : "FAIL", bad.Count == 0 ? $"{ok}/{total} {what}: subparts follow Functional, stand-ins hidden only while spawned" : $"{bad.Count} of {total} inconsistent: " + string.Join("; ", bad));
}
// Two children with the same name at the same place = a doubled part (z-fighting), e.g. a static child and a
// ConditionalSubParts spawn of the same panel.
List<string> Doubled(List<Keen.VRage.DCS.Components.Entity> blocks)
{
    var found = new List<string>();
    foreach (var e in blocks)
    {
        var kids = ChildrenOf(e);
        for (int i = 0; i < kids.Count; i++)
            for (int j = i + 1; j < kids.Count; j++)
            {
                if (EName(kids[i]) != EName(kids[j])) continue;
                var a = WorldPos(kids[i]); var b = WorldPos(kids[j]);
                if (a == null || b == null) continue;
                double dx = a.Value.X - b.Value.X, dy = a.Value.Y - b.Value.Y, dz = a.Value.Z - b.Value.Z;
                if (dx * dx + dy * dy + dz * dz < 1e-6 && found.Count < 12) found.Add($"{DefName(BlockDefOf(e))}: two '{EName(kids[i])}' at the same position");
            }
    }
    return found;
}
string Counts(List<Keen.VRage.DCS.Components.Entity> blocks) => string.Join(", ", blocks.GroupBy(e => DefName(BlockDefOf(e))).OrderBy(g => g.Key).Select(g => $"{g.Key} x{g.Count()}"));

// Recipe order: in every assembler-type recipe list, all recipes of our mods come after every vanilla recipe, and
// each mod's recipes are contiguous ("our recipes stay together after vanilla").
string RecipeKey(object r)
{
    var n = DefName(r) ?? "";
    var outItem = Items(GetQ(r, "Outputs")).Select(o => DefName(GetQ(o, "Item"))).FirstOrDefault() ?? "";
    return n + " -> " + outItem;
}
string ModOf(string key, string[] prefixes) => prefixes.FirstOrDefault(p => key.StartsWith(p) || key.Contains("-> " + p));
var recipeLists = new List<(string Converter, string Category, List<string> Keys)>();
foreach (var conv in DefsOf("Keen.Game2.Simulation.WorldObjects.CubeBlocks.Production.ItemConverters.ItemConverterDefinition"))
    foreach (var kv in Items(GetQ(conv, "RecipeDefinitions")))
    {
        // RecipeDefinitions: category -> ListReader<ItemRecipesDefinition> (each with an ordered Recipes array)
        var val = GetQ(kv, "Value");
        var sets = GetQ(val, "Recipes") != null ? new List<object> { val } : Items(val);
        foreach (var set in sets)
            recipeLists.Add((DefName(conv), DefName(GetQ(kv, "Key")) + "/" + DefName(set), Items(GetQ(set, "Recipes")).Select(RecipeKey).ToList()));
    }
void RecipeOrderCheck(string id, string title, string prefix, int expected)
{
    var prefixes = allPrefixes;
    var bad = new List<string>();
    var ours = new HashSet<string>();
    int lists = 0;
    foreach (var (conv, cat, keys) in recipeLists)
    {
        var idx = keys.Select((k, i) => (k, i)).Where(x => ModOf(x.k, prefixes) == prefix).Select(x => x.i).ToList();
        if (idx.Count == 0) continue;
        lists++;
        foreach (var i in idx) ours.Add(keys[i]);
        int lastVanilla = keys.Select((k, i) => (k, i)).Where(x => ModOf(x.k, prefixes) == null).Select(x => x.i).DefaultIfEmpty(-1).Max();
        bool contiguous = idx.Last() - idx.First() + 1 == idx.Count;
        if (idx.First() < lastVanilla || !contiguous)
            bad.Add($"{conv}/{cat}: ours at [{string.Join(",", idx)}] of {keys.Count}, last vanilla at {lastVanilla}{(contiguous ? "" : ", not contiguous")}");
    }
    if (lists == 0) { Check(id, title, recipeLists.Count == 0 ? "SKIP" : "FAIL", recipeLists.Count == 0 ? "no ItemConverterDefinition recipe lists readable" : $"no {prefix} recipe in any of {recipeLists.Count} recipe lists"); return; }
    string status = bad.Count > 0 ? "FAIL" : (expected > 0 && ours.Count != expected ? "WARN" : "PASS");
    Check(id, title, status, $"{ours.Count} distinct {prefix} recipe(s) in {lists} list(s){(expected > 0 ? $" (expected {expected})" : "")}" + (bad.Count > 0 ? ": " + string.Join("; ", bad) : ", all after vanilla and contiguous"));
}
yield return null;

// ======================= every listed mod =======================
bool scriptsOn = Environment.GetCommandLineArgs().Contains("-loadScripts");
foreach (var (mName, mPrefix) in modList)
{
    string mTag = mPrefix.Length > 0 ? mPrefix.TrimEnd('_') : mName;
    var mDefs = mPrefix.Length == 0 ? 0 : allDefs.Count(d => (DefName(d) ?? "").StartsWith(mPrefix, StringComparison.Ordinal));
    if (!InWorld(mName.Replace(" ", "")) && mDefs == 0)
    {
        Check(mTag + "-0", mName + " loaded in this world", "SKIP", "not in the world's mod list (" + (worldMods.Count == 0 ? "no mods" : string.Join(", ", worldMods)) + "): " + mName + " checks skipped");
        continue;
    }
    Check(mTag + "-0", mName + " definitions loaded", mPrefix.Length == 0 ? "SKIP" : mDefs > 0 ? "PASS" : "FAIL",
        mPrefix.Length == 0 ? "no prefix given (--mods NAME:PREFIX)" : $"{mDefs} definitions named {mPrefix}*");
    var mAsm = mName.Replace(" ", "");
    var mSim = __asms.FirstOrDefault(a => { try { return a.GetName().Name == mAsm + ".Simulation"; } catch { return false; } });
    var mCli = __asms.FirstOrDefault(a => { try { return a.GetName().Name == mAsm + ".Client"; } catch { return false; } });
    bool mAnyScript = mSim != null || mCli != null;
    Check(mTag + "-0b", "script assemblies match the -loadScripts switch", mAnyScript && !scriptsOn ? "FAIL" : !mAnyScript && scriptsOn ? "WARN" : "PASS",
        $"-loadScripts {(scriptsOn ? "on" : "off")}; {mAsm}.Simulation {(mSim != null ? "loaded" : "absent")}, {mAsm}.Client {(mCli != null ? "loaded" : "absent")}" + (!mAnyScript && scriptsOn ? " (fine for a mod without scripts)" : ""));
    if (mPrefix.Length == 0) continue;
    var mBlocksC = Blocks(clientEnts, mPrefix);
    SubpartCheck(mTag + "-SUB", "conditional subparts spawn only while Functional; stand-ins hidden exactly then", mBlocksC, mPrefix + " blocks");
    var mDbl = Doubled(mBlocksC);
    Check(mTag + "-DBL", "nothing doubled (no two same-named children at one place)", mBlocksC.Count == 0 ? "SKIP" : mDbl.Count == 0 ? "PASS" : "FAIL",
        mBlocksC.Count == 0 ? $"no {mPrefix}* block in this world" : mDbl.Count == 0 ? $"{mBlocksC.Count} blocks checked ({Counts(mBlocksC)})" : string.Join("; ", mDbl));
    int mScripted = serverEnts.Count(e => e.Components.Any(c => c?.GetType().Namespace?.StartsWith(mAsm) == true));
    Check(mTag + "-7a", $"save safety: no server entity carries a {mAsm} script component", ss == null ? "SKIP" : mScripted == 0 ? "PASS" : "FAIL", ss == null ? "no server session" : $"{mScripted} server entities with a {mAsm}.* component");
    RecipeOrderCheck(mTag + "-REC", mName + " recipes after vanilla and together", mPrefix, 0);
}

Emit(D("world", true, "checks", checks, "recipe_lists", recipeLists.Count));
