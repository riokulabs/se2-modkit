from se2modkit.diag import loadtime, logs

MAIN = "SpaceEngineers2_261020_030623_018_4242.log"
OTHER = "SpaceEngineers2_261019_120000_000_777.log"


def prof(fix, name=MAIN, pulsar=True):
    return loadtime.profile(fix / name, pulsar_log=(fix / "pulsar_info.log") if pulsar else None)


def test_phases(fix):
    p = prof(fix)
    m = p["main"]
    assert m["info"]["version"] == "4.0.0.3151" and m["info"]["runtime"] == ".NET 10.0.12"
    assert m["phases_ms"]["EntitySpawning"] == 56555.9402
    assert m["phases_ms"]["Session"] == 129210.3109
    assert m["phases_full_ms"]["Render_Init"] == 1051.4912
    assert m["metrics"]["GCMemoryTotal"] == 2149424376        # bytes, not a duration
    assert "GCMemoryTotal" not in m["phases_ms"]
    assert m["definition_sets_ms"]["World"] == 28516.0
    assert m["definition_sets_ms"]["CoreConfigurations"] == 430.0
    assert m["projects"] == ["Engine", "Vanilla", "Demo"]
    assert "Render_Window" in m["unfinished"] and "WorldStartup" not in m["unfinished"]


def test_component_inits_and_gaps(fix):
    m = prof(fix)["main"]
    inits = {x["component"]: x["ms"] for x in m["component_inits_ms"]}
    assert inits["ProjectBasedDefinitionSetManagerComponent"] == 6436.0
    assert inits["HavokPhysicsEngineComponent"] == 265.0
    # the 3-minute idle gap after the save is outside the load window and must not be reported
    assert all(g["ms"] < 100000 for g in m["largest_gaps"])
    assert m["largest_gaps"][0]["ms"] == 56556.0


def test_render_shaders(fix):
    r = prof(fix)["render"]
    assert r["shaders_compiled"] == 3 and r["distinct_compiled"] == 2
    assert r["shaders_missing_in_content"] == 2
    assert r["reasons"] == {"file does not exist": 2, "Cache version mismatch": 1}
    assert r["pso_failures"] == 1
    assert r["compile_window_ms"] == 4238.0
    assert r["compile_threads"] == {"VRageWorker N": 3}


def test_pulsar(fix):
    p = prof(fix)["pulsar"]
    assert p["pulsar"] == "v2.4.3"
    assert p["matches_this_log"] and p["pulsar_to_game_log_ms"] == 40026.0
    assert p["plugins"] == ["DemoPlugin (DemoPlugin.dll)", "SeMcp2 (24B77A03-E6BD-4849-809F-4E73CEB99B1A)"]
    assert p["categories_ms"]["download"] == 7000.0
    assert p["categories_ms"]["publicize+build"] == 8000.0
    assert len(p["errors"]) == 2
    assert p["to_plugins_initialized_ms"] == 37000.0


def test_compare_flags(fix):
    a, b = prof(fix, OTHER, pulsar=False), prof(fix)
    rows = {r["name"]: r for r in loadtime.compare(a, b)}
    assert rows["EntitySpawning"]["flag"] == "slower" and rows["EntitySpawning"]["ratio"] > 10
    assert rows["PushEngineDefinitionSet"]["flag"] == ""            # +23 ms: under the 500 ms floor
    assert rows["ProtonOnly"]["flag"] == "only in A"
    assert rows["shaders_compiled"]["flag"] == "only in B"
    names = [r["name"] for r in loadtime.compare(a, b)]
    assert names.index("Main") < names.index("Session") < names.index("EntitySpawning")


def test_findings(fix):
    f = loadtime.findings(prof(fix))
    assert any("EntitySpawning took 56.6 s" in x for x in f)
    assert any("Pulsar error:" in x for x in f)
    assert any("never timed" in x for x in f)
