// render: Render12 state that matters on the Vulkan (vkd3d-proton / DXVK) path: PSO cache sizes (graphics, compute,
// ray tracing), the shader manager's caches and its "missing in content" / "ran a compilation" flags (a shader not
// found in the shipped cache is compiled or read from Temp/ShaderCache), and the engine's render stat subtrees
// (draw/dispatch counts per target, scene and memory stats). Read only: static fields and collection counts.
//#include _stats.csx
var core = T("Keen.VRage.Render12.Core.CoreSystems");
Dictionary<string, object> Collections(object o)
{
    var d = new Dictionary<string, object>();
    for (var t = o?.GetType(); t != null && t != typeof(object); t = t.BaseType)
        foreach (var f in t.GetFields(__BF))
        {
            if (f.IsStatic || f.FieldType.IsByRefLike) continue;
            object v = null;
            try { v = f.GetValue(o); } catch { }
            if (v is bool b) d[f.Name] = b;
            else if (v is int || v is long) d[f.Name] = v;
            else if (v != null && !(v is string)) { var n = CountOf(v); if (n != null) d[f.Name] = n; }
        }
    return d;
}
var managers = new Dictionary<string, object>();
foreach (var name in new[] { "GraphicsPSOs", "ComputePSOs", "RayTracingPSOs", "GeometryPSOCache", "Shaders", "ShaderFileCache", "ShaderFileReaders", "ShaderAsserts" })
{
    var m = St(core, name);
    managers[name] = m == null ? null : D("type", m.GetType().Name, "fields", Collections(m));
}
var settings = St(core, "Settings");
Emit(D("managers", managers,
    "ignore_shader_missing_in_content", GetQ(GetQ(settings, "InternalState"), "IgnoreShaderMissingInContent"),
    "adapter", Str(St(core, "Adapters"), 300),
    "stats", StatsSnapshot(Param("filter", "RenderSceneStats,RenderMainTargetDrawStats,RenderOffscreenTargetDrawStats,RenderLocalLightsTargetDrawStats,RenderEnvProbesTargetDrawStats,RenderMemory,RenderManagedMemory,Performance/Render"), 3000)));
