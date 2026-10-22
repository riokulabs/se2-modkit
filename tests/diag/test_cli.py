import json
import os

import pytest

from se2modkit.diag import cli

from test_framestats import perf_data


@pytest.fixture
def env(monkeypatch, fix, tmp_path, mcp_cfg):
    logdir = tmp_path / "Logs"
    logdir.mkdir()
    for i, name in enumerate(["SpaceEngineers2_261019_120000_000_777.log", "SpaceEngineers2_261020_030623_018_4242.log",
                              "SpaceEngineers2_261020_030623_018_4242_Render12.log"]):
        (logdir / name).write_bytes((fix / name).read_bytes())
        os.utime(logdir / name, (1_700_000_000 + i, 1_700_000_000 + i))     # 4242 is the newest
    monkeypatch.setenv("SE2_LOG_DIR", str(logdir))
    monkeypatch.setenv("SE2_PULSAR_LOG", str(fix / "pulsar_info.log"))
    monkeypatch.setenv("SE2_DIAG_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("SE2_DIAG_PREVIEWS", str(tmp_path / "previews"))
    monkeypatch.setenv("SE2_MCP_CFG", str(mcp_cfg))
    monkeypatch.setenv("SE2_MCP_URL", "http://localhost:9/")
    monkeypatch.setenv("SE2_PROCESS_NAME", "no-such-process-x")
    return tmp_path


def run(capsys, *argv):
    rc = cli.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def reply(data):
    return "note line\n@@SE2DIAG@@" + json.dumps({"probe": "p", "ok": True, "elapsed_ms": 3.0, "missing": ["Foo.bar"],
                                                  "notes": [], "data": data})


def test_loadtime_text_and_json(env, capsys, fix):
    rc, out, _ = run(capsys, "loadtime", "SpaceEngineers2_261020_030623_018_4242.log")
    assert rc == 0 and "EntitySpawning" in out and "shaders" in out and "Pulsar (this launch)" in out
    rc, out, _ = run(capsys, "loadtime", "SpaceEngineers2_261019_120000_000_777.log", "--compare",
                     str(fix / "SpaceEngineers2_261020_030623_018_4242.log"), "--json")
    doc = json.loads(out)
    assert rc == 0 and any(r["name"] == "EntitySpawning" and r["flag"] == "slower" for r in doc["comparison"])


def test_logs_scan_grep_strict(env, capsys):
    rc, out, _ = run(capsys, "logs", "--json")
    assert rc == 0 and json.loads(out)["patterns"]["client_on_server"]["count"] == 1
    rc, out, _ = run(capsys, "logs", "--strict")
    assert rc == 1 and "client_on_server" in out
    rc, out, _ = run(capsys, "logs", "--grep", "Finished save")
    assert "1 match" in out


def test_stats_file_and_compare(env, capsys, fix):
    rc, out, _ = run(capsys, "stats", "--file", str(fix / "demo_Stats.log"))
    assert rc == 0 and "GC Total Pause Duration: 500.28ms" in out
    rc, out, _ = run(capsys, "stats", "--file", str(fix / "demo_Stats.log"), "--compare", str(fix / "demo2_Stats.log"), "--json")
    rows = json.loads(out)["comparison"]
    assert any(r["path"] == "Process Memory" and r["flag"] == "changed" for r in rows)


def test_perf_game_from_file(env, capsys, tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"probe": "perf", "data": perf_data()}))
    rc, out, _ = run(capsys, "perf", "game", "--from", str(raw))
    assert rc == 0 and "FPS" in out and "server sim speed 0.80" in out and "worst frames" in out


def test_perf_trace_counters_from_file(env, capsys, fix):
    rc, out, _ = run(capsys, "perf", "trace", "--from", str(fix / "trace.speedscope.json"))
    assert rc == 0 and "SpinWait" in out and "harmony" in out and "linuxcompat" in out
    rc, out, _ = run(capsys, "perf", "counters", "--from", str(fix / "counters.json"), "--json")
    assert "System.Runtime / CPU Usage (%)" in json.loads(out)["series"]


def test_host_commands_need_the_game(env, capsys):
    rc, _, err = run(capsys, "perf", "host", "--seconds", "0.1")
    assert rc == cli.EXIT_UNAVAILABLE and "not running" in err
    rc, _, err = run(capsys, "perf", "gcdump")
    assert rc == cli.EXIT_USAGE and "--allow-gc" in err


def test_status_offline(env, capsys):
    rc, out, _ = run(capsys, "status", "--json")
    doc = json.loads(out)
    assert rc == 0 and doc["pid"] is None and doc["log_scan"]["client_on_server"] == 1
    assert doc["pulsar"]["errors"]


def test_game_unreachable(env, capsys):
    rc, _, err = run(capsys, "patches")
    assert rc == cli.EXIT_UNAVAILABLE and "not reachable" in err and "test-token" not in err


def test_status_with_fake_game(env, capsys, fake_mcp, monkeypatch):
    monkeypatch.setenv("SE2_MCP_URL", fake_mcp.url)
    fake_mcp.reply_text = reply({
        "game": {"version": "4.0.0.3151", "assembly_version": "4.0.0.3151", "runtime": ".NET 10.0.12", "load_scripts": True},
        "clocks": {"stopwatch_frequency": 1000000000, "stopwatch_seconds": 2873109.0, "timespan_ms_overflow": True, "x1e7_overflow": True},
        "memory": {"working_set": 5e9, "gc_heap_size": 2e9, "gc_gen0": 5, "gc_gen1": 2, "gc_gen2": 1, "gc_server": False,
                   "gc_latency_mode": "SustainedLowLatency", "jit_methods": 1000, "jit_ms": 900.0, "threads": 120},
        "session": {"world": "Demo World", "world_type": "Creative", "server_session": True, "client_session": True,
                    "mods": [{"name": "Demo Mod"}], "mod_list_found": True, "server_game_seconds": 600.0},
        "entities_client": {"entities": 5000, "grids": 3, "blocks": 900, "characters": 1, "pending_close": 0},
        "plugins": {"assemblies": [{"name": "LinuxCompat"}], "harmony_patched_methods": 42,
                    "harmony_patches_by_owner": {"linux-compat": 40, "SeMcp2": 2}}})
    rc, out, _ = run(capsys, "status", "--pid", str(os.getpid()))
    assert rc == 0
    for s in ("Demo World", "Demo Mod", "5,000 entities", "TimeSpan-ms overflow ACTIVE", "linux-compat 40", "Foo.bar"):
        assert s in out, s


def test_modcheck_with_fake_game(env, capsys, fake_mcp, monkeypatch):
    monkeypatch.setenv("SE2_MCP_URL", fake_mcp.url)
    fake_mcp.reply_text = reply({"world": True, "checks": [
        {"id": "XX-SUB", "title": "subparts", "status": "PASS", "detail": "6/6 blocks"},
        {"id": "XX-DBL", "title": "doubled", "status": "SKIP", "detail": "no XX_* block"}]})
    rc, out, _ = run(capsys, "modcheck", "--json", "--mods", "DemoMod:XX_")
    doc = json.loads(out)
    assert rc == 1 and doc["verdict"] == "FAIL"           # the fixture log has a client-on-server line
    ids = [r["id"] for r in doc["checks"]]
    assert "XX-SUB" in ids and "LOG-1" in ids and "XX-LOG" in ids and "XX-DBL" in ids
    assert doc["mods"] == ["DemoMod:XX_"]


def test_inspect_not_found(env, capsys, fake_mcp, monkeypatch):
    monkeypatch.setenv("SE2_MCP_URL", fake_mcp.url)
    fake_mcp.reply_text = reply({"found": False, "how": "no block within 30 m", "candidates": []})
    rc, out, _ = run(capsys, "inspect")
    assert rc == 1 and "no block within 30 m" in out


def test_inspect_found(env, capsys, fake_mcp, monkeypatch):
    monkeypatch.setenv("SE2_MCP_URL", fake_mcp.url)
    fake_mcp.reply_text = reply({"found": True, "how": "look", "side": "client", "candidates": ["a", "b"],
                                 "entity": {"name": "Rack 1", "id": "DEntity(7)", "position": [1, 2, 3], "parent": "Grid",
                                            "tree": {"components": ["CubeBlockComponent", "ConditionalSubPartsComponent"]}},
                                 "block": {"definition": "XX_Box_Open", "build_progress": 1.0},
                                 "state": {"functional": True, "working": True},
                                 "conditional_subparts": {"functional": True, "spawned": True, "consistent": True},
                                 "render": {"hidden_mesh_parts": {"Fracture_01-Paint_Subpart": "RequestedHidden"}},
                                 "inventories": {"components": []}})
    rc, out, _ = run(capsys, "inspect", "--no-inventory")
    assert rc == 0 and "XX_Box_Open" in out and "RequestedHidden" in out and "consistent" in out


def test_shot_saves(env, capsys, fake_mcp, monkeypatch, tmp_path):
    monkeypatch.setenv("SE2_MCP_URL", fake_mcp.url)
    import base64
    jpg = b"\xff\xd8\xff\xe0fake"
    # the fake server returns text only; patch the client screenshot to return an image
    from se2modkit.diag import client
    monkeypatch.setattr(client.GameClient, "screenshot", lambda self: client.ToolResult([], [jpg], False))
    rc, out, _ = run(capsys, "shot", "rack test")
    p = out.strip()
    assert rc == 0 and p.endswith("_rack-test.jpg") and open(p, "rb").read() == jpg
    assert str(tmp_path / "previews") in p


def test_probe_build(env, capsys, tmp_path):
    rc, out, _ = run(capsys, "probe", "list")
    assert "inspect" in out.split()
    target = tmp_path / "b.csx"
    rc, out, _ = run(capsys, "probe", "build", "perf", "-p", "seconds=3", "-o", str(target))
    assert rc == 0 and '__params["seconds"] = "3";' in target.read_text()


def _windows(monkeypatch, hermetic_env):
    from se2modkit.diag import locations
    monkeypatch.setattr(locations, "_discover", lambda: hermetic_env(platform="win32"))


def test_diag_windows_without_pulsar_message(env, capsys, monkeypatch, hermetic_env):
    monkeypatch.delenv("SE2_PULSAR_LOG")
    _windows(monkeypatch, hermetic_env)
    rc, out, err = run(capsys, "loadtime", "SpaceEngineers2_261020_030623_018_4242.log")
    assert rc == 0 and "EntitySpawning" in out and "Pulsar (" not in out
    assert err.count("\n") == 1 and "not supported here: Pulsar not found" in err and "Traceback" not in err
    rc, out, err = run(capsys, "loadtime", "--proton")
    assert rc == 3 and err.count("\n") == 1 and "not supported here: Proton game logs exist only on Linux" in err
    rc, out, err = run(capsys, "perf", "host")
    assert rc == 3 and err.count("\n") == 1 and "not supported here:" in err
    rc, out, err = run(capsys, "status", "--no-game")
    assert rc == 0 and "not supported here: Pulsar not found" in out and "Traceback" not in err


def test_diag_linux_without_prefix_or_pulsar(env, capsys, monkeypatch):
    monkeypatch.delenv("SE2_PULSAR_LOG")
    rc, _, err = run(capsys, "loadtime", "--proton")
    assert rc == 3 and err.count("\n") == 1 and "not supported here: no Proton prefix" in err


def test_diag_pulsar_log_from_discovered_pulsar(env, capsys, monkeypatch, fix, tmp_path):
    monkeypatch.delenv("SE2_PULSAR_LOG")
    pulsar = tmp_path / "Pulsar"
    (pulsar / "Modern").mkdir(parents=True)
    (pulsar / "Modern.bin").write_bytes(b"")
    (pulsar / "Modern" / "info.log").write_bytes((fix / "pulsar_info.log").read_bytes())
    monkeypatch.setenv("SE2_PULSAR", str(pulsar / "Modern.bin"))
    rc, out, err = run(capsys, "loadtime", "SpaceEngineers2_261020_030623_018_4242.log")
    assert rc == 0 and "Pulsar (this launch)" in out and err == ""


def test_diag_default_dirs_come_from_env(env, monkeypatch):
    from se2modkit.diag import locations
    for name in ("SE2_DIAG_OUT", "SE2_DIAG_PREVIEWS", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    cache = locations.env().cache_dir
    assert locations.output_dir() == cache / "diag"
    assert locations.previews_dir() == cache / "diag" / "previews"
    monkeypatch.setenv("SE2_WORKSPACE", str(env / "ws"))
    assert locations.previews_dir() == env / "ws" / "resources" / "previews" / "diag"
