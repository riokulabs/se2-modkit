import json

from se2modkit.diag import traces


def test_speedscope(fix):
    doc = json.loads((fix / "trace.speedscope.json").read_text())
    an = traces.analyze_speedscope(doc, patched={"Keen.Game2.Foo.Bar"})
    assert an["total"] == 120.0                         # 100 ms evented + 20 ms sampled
    self_ = {r["frame"]: r["time"] for r in an["top_self"]}
    assert self_["System.Threading.SpinWait.SpinOnce()"] == 50.0      # 40 evented + 10 sampled
    assert self_["dynamicClass.DMD<Keen.Game2.Foo::Bar>(Keen.Game2.Foo)"] == 30.0
    assert self_["Keen.VRage.DCS.Scenes.Scene.Tick()"] == 30.0          # 20 evented + 10 sampled
    incl = {r["frame"]: r["time"] for r in an["top_inclusive"]}
    assert incl["Main"] == 120.0 and incl["LinuxCompat.Render.FenceWait()"] == 60.0
    tags = {r["frame"]: r["tags"] for r in an["top_self"]}
    assert "harmony" in tags["dynamicClass.DMD<Keen.Game2.Foo::Bar>(Keen.Game2.Foo)"]
    assert "patched (original)" in tags["dynamicClass.DMD<Keen.Game2.Foo::Bar>(Keen.Game2.Foo)"]
    assert an["by_tag"]["linuxcompat"]["self"] == 10.0               # FenceWait: 5 + 5 ms of its own time (evented)
    assert an["by_tag"]["linuxcompat"]["hottest_inclusive"][0]["time"] == 60.0
    assert an["threads"][0]["thread"].startswith("Thread (101)")


def test_counters(fix):
    full = traces.load_counters((fix / "counters.json").read_text())
    k = "System.Runtime / CPU Usage (%)"
    assert full["series"][k]["mean"] == 20.0 and full["series"][k]["max"] == 30.0 and full["target"] == "Modern.bin"
    trunc = traces.load_counters((fix / "counters_truncated.json").read_text())
    assert trunc["series"][k]["n"] == 2
    assert traces.load_counters("garbage{")["error"]


def test_gcdump(fix):
    r = traces.parse_gcdump_report((fix / "gcdump_report.txt").read_text())
    assert r["heap_bytes"] == 1234567890
    assert r["types"][0]["type"].startswith("Keen.VRage.Library.Collections.Big[]") and r["types"][0]["count"] == 3
    assert [t["bytes"] for t in r["types"]] == sorted((t["bytes"] for t in r["types"]), reverse=True)


def test_speedscope_dotnet_trace_shape():
    """dotnet-trace 10 export: structural roots, real frames, and a pseudo leaf saying what the thread did."""
    frames = ["Threads", "(Non-Activities)", "Process64 dotnet (5) Args: x", "Thread (7)", "busy!P.Main()", "busy!P.Spin(int32)",
              "CPU_TIME", "System.Private.CoreLib!System.Threading.Thread.Sleep(int32)", "UNMANAGED_CODE_TIME"]
    root = [0, 1, 2, 3, 4]
    doc = {"shared": {"frames": [{"name": n} for n in frames]},
           "profiles": [{"type": "sampled", "name": "Thread (7)", "unit": "milliseconds",
                         "samples": [root + [5, 6], root + [5, 6], root + [5, 6], root + [7, 8]], "weights": [10, 10, 10, 10]}]}
    an = traces.analyze_speedscope(doc, marks={"busy": ["P.Spin"]}, thread_names={7: "Render thread"})
    assert an["total"] == 40 and an["cpu_total"] == 30
    assert an["states"] == {"CPU_TIME": 30, "UNMANAGED_CODE_TIME": 10}
    assert an["top_cpu"][0] == {"frame": "busy!P.Spin(int32)", "time": 30, "pct": 100.0, "tags": ["busy"]}
    assert [r["frame"] for r in an["top_self"]] == ["busy!P.Spin(int32)", "System.Private.CoreLib!System.Threading.Thread.Sleep(int32)"]
    assert all(not traces.is_meta(r["frame"]) for r in an["top_inclusive"])
    assert an["threads"][0]["thread"] == "Thread (7) Render thread" and an["threads"][0]["cpu"] == 30
    assert an["by_tag"]["busy"]["cpu_pct"] == 100.0


def test_findings():
    an = {"cpu_total": 100, "unit": "ms", "threads": [{"time": 1000}],
          "top_cpu": [{"frame": "M!MonoMod.Core.Interop.CoreCLR+V60.InvokeCompileMethod(int)", "pct": 17.0},
                      {"frame": "M!System.Threading.Monitor.Enter_Slowpath(class System.Object)", "pct": 19.0},
                      {"frame": "M!System.Threading.Thread.<PollGC>g__PollGCWorker|67_0()", "pct": 21.0}],
          "by_tag": {"linuxcompat": {"hottest_inclusive": [{"frame": "M!ClientPlugin.Patches.X.AppTimerElapsedTicksPatch.Prefix()", "time": 500.0}]}}}
    f = traces.findings(an)
    assert len(f) == 4 and "JIT" in f[0] and "lock contention" in f[1] and "PollGC" in f[2] and "AppTimerElapsedTicksPatch" in f[3]
