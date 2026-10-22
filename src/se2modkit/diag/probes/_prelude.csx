#pragma warning disable CS8321, CS0168, CS0219
// ---- se2diag prelude: shared read-only helpers, prepended to every probe by se2diag/probes.py ----
// Rules for every probe (README "Probe rules"):
//  * read only: no setters, no method calls that change state, no Harmony, no event subscriptions;
//  * defensive: every reflection access goes through Get/GetQ/St/T, which return null and record what was missing
//    in __missing instead of throwing; a probe reports missing members, it never fails on them;
//  * cheap: no per-frame heavy work; long scans yield (`yield return null`) between chunks so one game frame never
//    carries more than a few milliseconds of probe work (the bridge plugin's per-frame budget is 1000 ms for all scripts);
//  * the result is one line "@@SE2DIAG@@{json}" written by Emit(); everything else on the console is a note.
// Game types are found by name at run time (reflection), so a renamed member degrades to a "missing" entry.
const BindingFlags __BF = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
const BindingFlags __SBF = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.FlattenHierarchy;
var __missing = new List<string>();
var __notes = new List<string>();
long __t0 = System.Diagnostics.Stopwatch.GetTimestamp();
var __params = new Dictionary<string, string>();
/*@@PARAMS@@*/
string __probe = __params.TryGetValue("__probe", out var __pn) ? __pn : "probe";

string Param(string name, string dflt) => __params.TryGetValue(name, out var v) ? v : dflt;
int ParamInt(string name, int dflt) => int.TryParse(Param(name, ""), NumberStyles.Integer, CultureInfo.InvariantCulture, out var v) ? v : dflt;
double ParamNum(string name, double dflt) => double.TryParse(Param(name, ""), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : dflt;
bool ParamBool(string name, bool dflt) { var s = Param(name, ""); return s == "" ? dflt : s == "1" || s.Equals("true", StringComparison.OrdinalIgnoreCase); }
void Miss(string what) { if (__missing.Count < 300 && !__missing.Contains(what)) __missing.Add(what); }
void Note(string what) { if (__notes.Count < 300) __notes.Add(what); }
double MsSince(long ts) => (System.Diagnostics.Stopwatch.GetTimestamp() - ts) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;

var __asms = AppDomain.CurrentDomain.GetAssemblies();
var __typeCache = new Dictionary<string, Type>();
Type TQ(string fullName)
{
    if (__typeCache.TryGetValue(fullName, out var hit)) return hit;
    Type found = null;
    foreach (var a in __asms) { try { found = a.GetType(fullName, false); } catch { } if (found != null) break; }
    __typeCache[fullName] = found;
    return found;
}
Type T(string fullName) { var t = TQ(fullName); if (t == null) Miss("type " + fullName); return t; }

MemberInfo FindMember(Type t, string n)
{
    for (; t != null; t = t.BaseType)
    {
        try
        {
            var p = t.GetProperty(n, __BF);
            if (p != null && p.GetIndexParameters().Length == 0 && !p.PropertyType.IsByRefLike && !p.PropertyType.IsByRef) return p;
            var f = t.GetField(n, __BF);
            if (f != null && !f.FieldType.IsByRefLike) return f;
        }
        catch { }
    }
    return null;
}
// GetQ: quiet read of a property or field (any visibility, walking base types); null when absent or throwing.
object GetQ(object o, string n)
{
    if (o == null) return null;
    try
    {
        var m = FindMember(o.GetType(), n);
        if (m is PropertyInfo p) return p.GetValue(o);
        if (m is FieldInfo f) return f.GetValue(o);
    }
    catch { }
    return null;
}
// Get: like GetQ but records "<Type>.<name>" in the missing list when the member does not exist.
object Get(object o, string n)
{
    if (o == null) return null;
    try
    {
        var m = FindMember(o.GetType(), n);
        if (m == null) { Miss(o.GetType().Name + "." + n); return null; }
        if (m is PropertyInfo p) return p.GetValue(o);
        if (m is FieldInfo f) return f.GetValue(o);
    }
    catch (Exception ex) { Note($"{o.GetType().Name}.{n} threw {ex.GetType().Name}"); }
    return null;
}
object PathGet(object o, string dotted) { foreach (var part in dotted.Split('.')) { if (o == null) return null; o = Get(o, part); } return o; }
object St(Type t, string n)
{
    if (t == null) return null;
    try
    {
        var p = t.GetProperty(n, __SBF);
        if (p != null) return p.GetValue(null);
        var f = t.GetField(n, __SBF);
        if (f != null) return f.GetValue(null);
        Miss(t.Name + "." + n + " (static)");
    }
    catch (Exception ex) { Note($"{t.Name}.{n} threw {ex.GetType().Name}"); }
    return null;
}
List<object> Items(object o, int max = 1000000)
{
    var list = new List<object>();
    if (o == null || o is string) return list;
    try { if (o is System.Collections.IEnumerable en) foreach (var x in en) { list.Add(x); if (list.Count >= max) break; } }
    catch { }
    return list;
}
int? CountOf(object o)
{
    if (o == null) return null;
    try
    {
        if (o is System.Collections.ICollection c) return c.Count;
        var p = o.GetType().GetProperty("Count", BindingFlags.Instance | BindingFlags.Public) ?? o.GetType().GetProperty("Length", BindingFlags.Instance | BindingFlags.Public);
        if (p != null && p.GetIndexParameters().Length == 0) return Convert.ToInt32(p.GetValue(o), CultureInfo.InvariantCulture);
    }
    catch { }
    return null;
}
bool IsA(object o, string typeName)
{
    for (var t = o?.GetType(); t != null; t = t.BaseType) if (t.Name == typeName) return true;
    return false;
}
string Str(object o, int max = 300)
{
    if (o == null) return null;
    string s;
    try { s = o.ToString(); } catch (Exception ex) { s = "<ToString threw " + ex.GetType().Name + ">"; }
    return s != null && s.Length > max ? s.Substring(0, max) + "..." : s;
}
// DebugNameShort is the asset name ("XX_Box"); DebugName the project path ("MyMod/MyMod/XX_Box/XX_Box.def").
string DefName(object def) { if (def == null) return null; var s = GetQ(def, "DebugNameShort") as string; return string.IsNullOrEmpty(s) ? (GetQ(def, "DebugName") as string ?? Str(def)) : s; }
string DefPath(object def) => def == null ? null : (GetQ(def, "DebugName") as string ?? Str(def));

// ---- JSON (no dependency on System.Text.Json; invariant numbers; NaN/Infinity become null) ----
Dictionary<string, object> D(params object[] kv)
{
    var d = new Dictionary<string, object>();
    for (int i = 0; i + 1 < kv.Length; i += 2) d[kv[i]?.ToString() ?? "null"] = kv[i + 1];
    return d;
}
void JStr(StringBuilder sb, string s)
{
    sb.Append('"');
    foreach (var ch in s)
    {
        switch (ch)
        {
            case '"': sb.Append("\\\""); break;
            case '\\': sb.Append("\\\\"); break;
            case '\n': sb.Append("\\n"); break;
            case '\r': sb.Append("\\r"); break;
            case '\t': sb.Append("\\t"); break;
            default:
                if (ch < 0x20 || ch == (char)0x2028 || ch == (char)0x2029) sb.Append("\\u").Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
                else sb.Append(ch);
                break;
        }
    }
    sb.Append('"');
}
void JW(StringBuilder sb, object o, int depth)
{
    if (depth > 40) { JStr(sb, "<too deep>"); return; }
    switch (o)
    {
        case null: sb.Append("null"); return;
        case string s: JStr(sb, s); return;
        case bool b: sb.Append(b ? "true" : "false"); return;
        case double d: if (double.IsNaN(d) || double.IsInfinity(d)) sb.Append("null"); else sb.Append(d.ToString("R", CultureInfo.InvariantCulture)); return;
        case float f: if (float.IsNaN(f) || float.IsInfinity(f)) sb.Append("null"); else sb.Append(((double)f).ToString("R", CultureInfo.InvariantCulture)); return;
        case decimal m: sb.Append(m.ToString(CultureInfo.InvariantCulture)); return;
        case int or long or short or byte or sbyte or uint or ulong or ushort:
            sb.Append(Convert.ToString(o, CultureInfo.InvariantCulture)); return;
        case Enum e: JStr(sb, e.ToString()); return;
        case char c: JStr(sb, c.ToString()); return;
        case TimeSpan ts: sb.Append(ts.TotalMilliseconds.ToString("R", CultureInfo.InvariantCulture)); return;
        case DateTime dt: JStr(sb, dt.ToString("o", CultureInfo.InvariantCulture)); return;
        case System.Collections.IDictionary dict:
        {
            sb.Append('{');
            bool first = true;
            foreach (System.Collections.DictionaryEntry kv in dict)
            {
                if (!first) sb.Append(',');
                first = false;
                JStr(sb, Str(kv.Key) ?? "null");
                sb.Append(':');
                JW(sb, kv.Value, depth + 1);
            }
            sb.Append('}');
            return;
        }
        case System.Collections.IEnumerable en:
        {
            sb.Append('[');
            bool first = true;
            int n = 0;
            try
            {
                foreach (var x in en)
                {
                    if (n++ >= 20000) break;
                    if (!first) sb.Append(',');
                    first = false;
                    JW(sb, x, depth + 1);
                }
            }
            catch { }
            sb.Append(']');
            return;
        }
        default: JStr(sb, Str(o, 500)); return;
    }
}
string J(object o) { var sb = new StringBuilder(); JW(sb, o, 0); return sb.ToString(); }
void Emit(object data)
{
    var result = D("probe", __probe, "ok", true, "elapsed_ms", Math.Round(MsSince(__t0), 3),
        "missing", __missing, "notes", __notes, "data", data);
    Console.WriteLine("@@SE2DIAG@@" + J(result));
}

// ---- the game: sessions, entities, components ----
object __app = null;
try { __app = Singleton<VRageCore>.Instance.Engine.Get<GameAppComponent>(); } catch (Exception ex) { Note("GameAppComponent: " + ex.GetType().Name); }
object App() => __app;
object SessionOf(string side) => side == "server" ? GetQ(__app, "ServerSession") : GetQ(__app, "ClientSession");
List<Keen.VRage.DCS.Components.Entity> EntitiesOf(object session)
{
    var list = new List<Keen.VRage.DCS.Components.Entity>();
    if (session == null) return list;
    try { foreach (var x in Items(Get(session, "Entities"))) if (x is Keen.VRage.DCS.Components.Entity e) list.Add(e); } catch { }
    return list;
}
IEnumerable<object> CompsOf(object entity) { if (entity is Keen.VRage.DCS.Components.Entity e) { foreach (var c in e.Components) if (c != null) yield return c; } else foreach (var c in Items(GetQ(entity, "Components"))) if (c != null) yield return c; }
object CompOf(object entity, string typeName) { foreach (var c in CompsOf(entity)) if (IsA(c, typeName)) return c; return null; }
List<object> SessionComps(object session) => CompsOf(GetQ(session, "SessionComponents")).ToList();
object SessionComp(object session, string typeName) { foreach (var c in SessionComps(session)) if (IsA(c, typeName)) return c; return null; }
string EName(object entity) { var n = GetQ(entity, "DebugName") as string; return string.IsNullOrEmpty(n) ? Str(entity, 120) : n; }
string EId(object entity) => Str(GetQ(entity, "DEntity"), 80);
List<object> ChildrenOf(object entity) => Items(GetQ(CompOf(entity, "HierarchyComponent"), "Children"));
object BlockDefOf(object entity) => GetQ(CompOf(entity, "CubeBlockComponent"), "Definition");

MethodInfo __getWT = null;
bool __getWTsearched = false;
// World position via EntityTransformFunctions.GetWorldTransform(DEntityContext): a pure read of the transform chain.
(double X, double Y, double Z)? WorldPos(object entity)
{
    try
    {
        if (!__getWTsearched)
        {
            __getWTsearched = true;
            var ef = T("Keen.VRage.Core.Game.Data.EntityTransformFunctions");
            __getWT = ef?.GetMethods(BindingFlags.Public | BindingFlags.Static).FirstOrDefault(m => m.Name == "GetWorldTransform" && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType.Name == "DEntityContext");
            if (__getWT == null) Miss("EntityTransformFunctions.GetWorldTransform(DEntityContext)");
        }
        var data = GetQ(entity, "Data");
        if (__getWT == null || data == null) return null;
        var wt = __getWT.Invoke(null, new[] { data });
        var p = GetQ(wt, "Position");
        return (Convert.ToDouble(GetQ(p, "X")), Convert.ToDouble(GetQ(p, "Y")), Convert.ToDouble(GetQ(p, "Z")));
    }
    catch { return null; }
}
object WorldOrientation(object entity)
{
    try { var data = GetQ(entity, "Data"); return __getWT == null || data == null ? null : GetQ(__getWT.Invoke(null, new[] { data }), "Orientation"); } catch { return null; }
}
// ---- end of prelude ----
