from se2modkit.diag import framestats


def perf_data():
    frames = [16.7] * 100 + [120.0, 45.0]
    n = len(frames)
    stages = {"ServerSession": [5.0] * 100 + [90.0, 3.0], "Draw": [8.0] * 100 + [10.0, 35.0]}
    gcgen = [-1] * 100 + [2, -1]
    jit = [0.0] * 100 + [0.0, 12.0]
    locks = [0] * 101 + [3]
    start = {"wall_ms": 0, "server_game_s": 100.0, "client_game_s": 100.0, "gc_gen0": 1, "gc_gen1": 1, "gc_gen2": 1,
             "gc_pause_ms": 10.0, "gc_allocated": 0, "jit_methods": 10, "jit_ms": 5.0, "lock_contentions": 0,
             "threadpool_completed": 0, "pso": {"graphics": 100, "compute": 50, "raytracing": None}}
    end = dict(start, wall_ms=10000, server_game_s=108.0, client_game_s=110.0, gc_gen2=2, gc_pause_ms=310.0,
               gc_allocated=50_000_000, jit_methods=500, jit_ms=400.0, lock_contentions=6000, threadpool_completed=99,
               pso={"graphics": 104, "compute": 50, "raytracing": None})
    return {"frames_ms": frames, "stages_ms": stages, "gc_gen": gcgen, "jit_ms": jit, "lock_contentions": locks,
            "gc_pause_ms": [0.0] * n, "start": start, "end": end,
            "stats_start": [{"path": "Memory/GC/GC Total", "value": 100.0}, {"path": "Performance/CPU Load", "value": 0.5}],
            "stats_end": [{"path": "Memory/GC/GC Total", "value": 150.0, "text": "150B"}, {"path": "Performance/CPU Load", "value": 0.5}]}


def test_distribution_and_hitches():
    a = framestats.analyze(perf_data())
    assert a["frames"]["n"] == 102 and abs(a["frames"]["p50"] - 16.7) < 1e-9 and a["frames"]["max"] == 120.0
    assert a["hitches"] == {"over_threshold": 2, "over_50ms": 1, "over_100ms": 1, "over_250ms": 0}
    assert a["hitch_causes"]["gc_gen2"] == 1 and a["hitch_causes"]["jit>5ms"] == 1 and a["hitch_causes"]["lock_contention"] == 1
    assert a["hitch_dominant_stage"] == {"ServerSession": 1, "Draw": 1}
    assert a["worst_frames"][0]["ms"] == 120.0
    assert abs(a["stages"]["Draw"]["share_of_frame"] - a["stages"]["Draw"]["mean"] / a["frames"]["mean"]) < 1e-12


def test_sim_speed_runtime_pso():
    a = framestats.analyze(perf_data())
    assert abs(a["server_sim_speed"] - 0.8) < 1e-9 and abs(a["client_sim_speed"] - 1.0) < 1e-9
    assert a["runtime"]["gc_gen2"] == 1 and abs(a["gc_pause_pct"] - 3.0) < 1e-9 and abs(a["alloc_mb_per_s"] - 5.0) < 1e-9
    assert a["pso"]["graphics"]["new"] == 4 and a["pso"]["raytracing"]["new"] is None
    assert a["stat_changes"][0]["path"] == "Memory/GC/GC Total" and a["stat_changes"][0]["delta"] == 50.0
    f = framestats.findings(a)
    assert any("server sim speed 0.80" in x for x in f)
    assert any("4 new graphics PSO" in x for x in f)
    assert any("GC pauses took 3.0%" in x for x in f)
    assert any("lock contentions/s" in x for x in f)


def test_empty():
    a = framestats.analyze({"frames_ms": [], "start": {}, "end": {}})
    assert framestats.findings(a)[0].startswith("no frames")


def test_pct():
    assert framestats.pct([1, 2, 3, 4], 50) == 2.5 and framestats.pct([5], 99) == 5 and framestats.pct([], 50) is None
