"""`se2mod mcp` tools: the registry and its hints, how tool arguments reach the commands' own functions, and the
structured results, with fakes (no Blender, no `mcp` package, no network)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from se2modkit import cli
from se2modkit import jobqueue as Q
from se2modkit.mcp import tools as T

G_A = "30000000-0000-4000-8000-000000000001"
G_B = "30000000-0000-4000-8000-000000000002"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(cache_dir=tmp_path / "cache", config_dir=tmp_path / "config", platform="linux",
                           blender=None, sdk_assets=None)


class Served:
    """The toolbox as the server sees it: each tool through its registered (guarded) function."""

    def __init__(self, env):
        self.toolbox = T.Toolbox(env)
        self.env = env
        self._fns = {s.name: s.fn for s in self.toolbox.specs()}

    def specs(self):
        return self.toolbox.specs()

    def __getattr__(self, name):
        return self._fns[name]


@pytest.fixture
def box(env):
    return Served(env)


def test_registry_names_and_hints(box):
    specs = {s.name: s for s in box.specs()}
    assert set(specs) == {"look", "look_finish", "geocheck", "describe", "ship_check", "queue_status", "queue_submit",
                          "queue_wait", "defs", "chain", "deps_check"}
    read_only = {n for n, s in specs.items() if s.read_only}
    assert read_only == {"geocheck", "queue_status", "queue_wait", "defs", "chain", "deps_check"}
    assert {n for n, s in specs.items() if s.destructive} == {"queue_submit"}
    assert all(s.description and s.title for s in specs.values())
    assert T.tool_names() == list(specs)


def test_annotations_follow_the_spec(box):
    from se2modkit.mcp.server import annotations
    specs = {s.name: s for s in box.specs()}
    assert annotations(specs["defs"]) == {"title": "Look up game definitions", "readOnlyHint": True,
                                          "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    a = annotations(specs["queue_submit"])
    assert a["readOnlyHint"] is False and a["destructiveHint"] is True
    a = annotations(specs["look"])
    assert a["readOnlyHint"] is False and a["destructiveHint"] is False


def test_the_command_is_registered_and_lists_its_tools(run):
    assert "mcp" in cli.commands()
    code, out, _ = run("mcp", "--list")
    assert code == 0 and "queue_submit" in out and "destructive" in out and "read-only" in out


def test_look_arguments_reach_the_look_command(box, monkeypatch, tmp_path):
    seen = {}

    def run_look(args, env, *, say=None):
        seen["args"], seen["env"] = args, env
        say("queued job 3: x")
        return {"code": 0, "warnings": [], "job_id": 3, "set_dir": tmp_path / "cache" / "look" / "XX_A" / "r2"}
    monkeypatch.setattr("se2modkit.cli.look_cmd.run_look", run_look)
    res = box.look("drive.glb", "XX_A", out=str(tmp_path / "cache" / "look"), mount_face="-Y", round=2,
                   close=["Nozzle"],
                   material={"rb_Bolt": "MetalDull"}, tris_budget=29000, interior=True, wait=False)
    a = seen["args"]
    assert seen["env"] is box.env
    assert (a.source, a.block, a.mount_face, a.rnd, a.close, a.material) == (
        Path("drive.glb"), "XX_A", "-Y", 2, ["Nozzle"], ["rb_Bolt=MetalDull"])
    assert a.tris_budget == 29000 and a.interior and a.no_wait and not a.full and a.set_name == "neighbors"
    assert res["ok"] and res["job_id"] == 3 and res["log"] == ["queued job 3: x"] and "look_finish" in res["next"]
    assert isinstance(res["set_dir"], str)


def test_look_print_only_path_is_real(box, tmp_path, monkeypatch):
    """The real command core with the fake env: the job is written, the queue gets it, the result has the paths."""
    submitted = {}

    def submit(self, argv, **kw):
        submitted["argv"], submitted["kw"] = argv, kw
        return SimpleNamespace(id=11)
    monkeypatch.setattr(Q.Store, "submit", submit)
    monkeypatch.setattr(Q, "ensure_runner", lambda store, *a, **k: None)
    monkeypatch.setattr("se2modkit.cli.blender_cmd.blender_argv",
                        lambda env, script, args, **kw: ["blender", "-b", "--python", str(script), "--", *args])
    src = tmp_path / "drive.glb"
    src.write_bytes(b"glTF")
    res = box.look(str(src), "XX_Drive", out=str(tmp_path / "cache" / "look"), wait=False)
    assert res["ok"] and res["job_id"] == 11
    assert Path(res["set_dir"]).parts[-2:] == ("XX_Drive", "r1") and Path(res["job_file"]).is_file()
    assert "{gpu}" in submitted["argv"] and submitted["kw"]["load"] == Q.LOADS["medium"]
    assert submitted["argv"][1] == "--disable-autoexec"      # never runs scripts saved in the working file


def test_bad_look_arguments_come_back_as_an_error(box, tmp_path):
    res = box.look(str(tmp_path / "x.glb"), "X", out=str(tmp_path / "cache" / "look"), material={"rb_Bolt": ""})
    assert res["ok"] is False and "NAME=SLOT" in res["error"]
    res = box.look("x.glb", "X", stage="nope")
    assert res["ok"] is False and "bad arguments" in res["error"]


def test_look_finish_and_geocheck_read_the_set(box, tmp_path, monkeypatch):
    out = tmp_path / "cache" / "look" / "XX_A" / "r3"
    out.mkdir(parents=True)
    (out / "geocheck.json").write_text(json.dumps({"ok": False, "failures": [{"what": "floating"}], "warnings": []}))
    (out / "report.json").write_text(json.dumps({"problems": ["missing view top"]}))
    seen = {}

    def finish(o, *, block, stage, rnd, interior=False):
        seen.update(block=block, stage=stage, rnd=rnd)
        return {"problems": ["missing view top"]}
    monkeypatch.setattr("se2modkit.preview.look.finish", finish)
    res = box.look_finish(str(out))
    assert seen == {"block": "XX_A", "stage": "look", "rnd": 3}
    assert res["ok"] is False and res["not_ready"] == ["missing view top"]
    assert res["geocheck"] == str(out / "geocheck.json") and res["sheet"] == str(out / "sheet.png")
    g = box.geocheck(str(out))
    assert g["ok"] and g["passed"] is False and g["failures"] == [{"what": "floating"}]
    assert g["not_ready"] == ["missing view top"]
    assert box.look_finish(str(tmp_path / "cache"))["ok"] is False
    assert box.geocheck(str(tmp_path / "nothing"))["ok"] is False


def test_describe_and_ship_arguments(box, monkeypatch, toymod):
    seen = {}

    def run_describe(args, env, *, say=None):
        seen["d"] = args
        return {"code": 1, "job_id": 4, "report": {"parts": [], "targets": [{"ok": False}]}, "text": ["x"]}

    def run_ship(args, env, *, say=None):
        seen["s"] = args
        return {"code": 0, "queue": {}, "reports": [{"block": "XX_A", "problems": []}]}
    monkeypatch.setattr("se2modkit.cli.describe_cmd.run_describe", run_describe)
    monkeypatch.setattr("se2modkit.cli.ship_cmd.run_ship", run_ship)
    res = box.describe("toy.blend", collection=["Block", "Extra"], envelope=[2.5, 2.5, 5], timeout=60)
    d = seen["d"]
    assert d.collection == ["Block", "Extra"] and d.envelope == [2.5, 2.5, 5.0] and d.timeout == 60
    assert res["ok"] is False and res["code"] == 1 and "text" not in res and res["report"]["targets"]
    res = box.ship_check("ToyMod", ["XX_A", "XX_B*"], max_key_share=0.5, blend=["a.blend"])
    s = seen["s"]
    assert s.mod == str(toymod.resolve()) and s.blocks == ["XX_A", "XX_B*"] and s.max_key_share == 0.5
    assert s.blend == [Path("a.blend")] and s.timeout == 1800
    assert res["ok"] and res["reports"][0]["block"] == "XX_A"
    assert box.ship_check("ToyMod", [])["ok"] is False


def test_queue_tools_use_the_real_store(box, monkeypatch, env):
    monkeypatch.setattr(Q, "ensure_runner", lambda store, *a, **k: None)
    res = box.queue_submit(["echo", "{gpu}"], name="hello", load="light", gpu="none", timeout=30,
                           env={"A": "1"}, priority=True)
    assert res["ok"] and res["job_id"] == 1 and res["log"].endswith("1.log")
    job = Q.Store(env.cache_dir / "queue").load(1)
    assert job.argv == ["echo", "{gpu}"] and job.load == Q.LOADS["light"] and job.gpu_req == "none"
    assert job.priority and job.env["A"] == "1" and job.timeout == 30
    st = box.queue_status()
    assert st["ok"] and st["jobs"][0]["name"] == "hello" and st["jobs"][0]["state"] == "queued"
    assert st["jobs"][0]["load"] == "light"
    assert box.queue_submit([], name="x")["ok"] is False
    assert box.queue_submit(["x"], load="huge")["ok"] is False


def test_queue_wait_returns_code_state_and_log(box, monkeypatch, env):
    monkeypatch.setattr(Q, "ensure_runner", lambda store, *a, **k: None)
    store = Q.Store(env.cache_dir / "queue")
    job = store.submit(["true"], name="t")
    store.log_path(job.id).parent.mkdir(parents=True, exist_ok=True)
    store.log_path(job.id).write_text("line 1\nline 2\n")
    monkeypatch.setattr(Q, "wait", lambda s, jid, timeout, **kw: 0)
    res = box.queue_wait(job.id, timeout=5)
    assert res["ok"] and res["code"] == 0 and res["log_tail"] == ["line 1", "line 2"]
    monkeypatch.setattr(Q, "wait", lambda s, jid, timeout, **kw: Q.EXIT_TIMEOUT)
    res = box.queue_wait(job.id, timeout=5)
    assert res["ok"] is False and "timed out" in res["error"] and res["state"] == "queued"
    monkeypatch.undo()
    monkeypatch.setattr(Q, "ensure_runner", lambda store, *a, **k: None)
    assert box.queue_wait(999, timeout=1)["ok"] is False


@pytest.fixture
def content(tmp_path):
    r = tmp_path / "Content"
    (r / "Blocks").mkdir(parents=True)
    (r / "Blocks" / "A.def").write_text(json.dumps({"$Type": "Game2:Keen.Game2.Test.ADefinitionObjectBuilder",
                                                    "$Value": {"Guid": G_A, "Parts": [{"Name": "p"}]}}))
    (r / "Blocks" / "B.def").write_text(json.dumps({"$Type": "Game2:Keen.Game2.Test.BDefinitionObjectBuilder",
                                                    "$Value": {"Guid": G_B, "Ref": G_A}}))
    return r


def test_defs_returns_rows(box, content):
    res = box.defs("types", root=str(content))
    assert res["ok"] and {r["type"] for r in res["results"]} == {"Keen.Game2.Test.ADefinitionObjectBuilder",
                                                                 "Keen.Game2.Test.BDefinitionObjectBuilder"}
    res = box.defs("guid", G_A, root=str(content))
    assert sorted((r["relation"], r["path"]) for r in res["results"]) == [("defines", "Blocks/A.def"),
                                                                          ("references", "Blocks/B.def")]
    res = box.defs("type", "BDefinition", root=str(content))
    assert res["results"] == [{"guid": G_B, "path": "Blocks/B.def"}]
    res = box.defs("show", G_A, path="$Value.Parts.0.Name", root=str(content))
    assert res["value"] == "p" and res["file"].endswith("A.def")
    res = box.defs("show", G_B, root=str(content), max_lines=2)
    assert res["truncated"] and "more lines" in res["text"]
    res = box.defs("grep", "ref", root=str(content), limit=0)
    assert res["count"] == 1 and res["truncated"] and res["results"] == []
    assert box.defs("grep", "(", root=str(content))["ok"] is False
    assert box.defs("guid", "", root=str(content))["ok"] is False


G_BASE = "10000000-0000-4000-8000-000000000001"
G_COPY = "10000000-0000-4000-8000-000000000002"
G_BIND = "20000000-0000-4000-8000-000000000001"
TT = "Game2:Keen.Game2.Test.{}ObjectBuilder"


def _w(root: Path, rel: str, data: dict) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def assets(tmp_path):
    r = tmp_path / "Assets"
    _w(r, "Templates/Base.def", {"$Type": TT.format("WidgetDefinition"), "$Value": {"Guid": G_BASE, "A": 1}})
    _w(r, "Things/Copy.partialdef", {"$Type": "x", "$Value": {
        "BaseDefinition": G_BASE, "PartialDefinitionKind": "Copy", "DefinitionType": TT.format("WidgetDefinition"),
        "Manipulator": {"Guid": G_COPY, "B": 2}}})
    _w(r, "Blocks/Widgets/Widget250.def", {"$Type": TT.format("PrefabBindingDefinition"), "$Value": {"Guid": G_BIND}})
    return r


def test_chain_resolves_and_keeps_its_index(box, assets, monkeypatch):
    from se2modkit.defs import block_chain as bc
    built = []
    real = bc.Index

    def index(root):
        built.append(root)
        return real(root)
    monkeypatch.setattr(bc, "Index", index)
    res = box.chain("resolve", G_COPY, root=str(assets))
    assert res["ok"] and res["value"] == {"Guid": G_COPY, "A": 1, "B": 2} and res["file"] == "Things/Copy.partialdef"
    res = box.chain("resolve", G_COPY, fields=["base", "type"], root=str(assets))
    assert res["fields"] == {"base": G_BASE, "type": "WidgetDefinition"}
    res = box.chain("lineage", G_COPY, root=str(assets))
    assert [e["guid"] for e in res["lineage"]] == [G_COPY, G_BASE]
    res = box.chain("find", "widget2", root=str(assets))
    assert res["results"] == [{"guid": G_BIND, "path": "Blocks/Widgets/Widget250.def"}]
    res = box.chain("show", "Widget250", root=str(assets))
    assert res["count"] == 1 and res["blocks"][0]["binding"] == G_BIND
    assert box.chain("resolve", "nope", root=str(assets))["ok"] is False
    assert box.chain("show", "nothing", root=str(assets))["ok"] is False
    assert box.chain("resolve", G_COPY, fields=["bogus"], root=str(assets))["ok"] is False
    assert len(built) == 1


def test_deps_check_returns_findings_and_keeps_stdout_clean(box, monkeypatch, capsys):
    from se2modkit.deps import check

    def run(env, *, report_json, quiet, min_risk, **kw):
        assert quiet and min_risk == "med" and kw["refs"] is True
        print("baseline: game 1 buildid 2; checked 3 dependencies (risk >= med)")
        print("  MISSING     1  (high 1, med 0, low 0)")
        Path(report_json).write_text(json.dumps([{"id": "x", "risk": "high", "kind": "file", "rank": 1,
                                                  "findings": [{"status": "missing", "detail": "gone"}]}]))
        print(f"\nreport written to {report_json}")
        return 2
    monkeypatch.setattr(check, "run", run)
    res = box.deps_check(min_risk="med", refs=True)
    assert res["ok"] is False and res["code"] == 2 and "missing" in res["verdict"]
    assert res["findings"][0]["id"] == "x" and len(res["summary"]) == 2
    assert capsys.readouterr().out == ""
    assert not list((Path(box.env.cache_dir) / "deps").glob("mcp-report-*"))


# -- what a client may write and run ---------------------------------------------------------------------------------

@pytest.fixture
def toymod(env, tmp_path, monkeypatch):
    """A mod found by name in a workspace mods folder."""
    monkeypatch.setenv("SE2_WORKSPACE", str(tmp_path / "ws"))
    root = tmp_path / "ws" / "mods" / "ToyMod"
    (root / "Source" / "Model").mkdir(parents=True)
    (root / "ToyMod.vrgproj").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "ToyMod"\nprefix = "XX_"\n', encoding="utf-8")
    return root


def test_mods_are_taken_by_name_only(box, monkeypatch, tmp_path, toymod):
    """A mod path would let a client run any folder's build script (ship check) or widen where tools write."""
    seen = []
    monkeypatch.setattr("se2modkit.cli.ship_cmd.run_ship",
                        lambda args, env, *, say=None: seen.append(args.mod) or {"code": 0, "queue": {}})
    monkeypatch.setattr("se2modkit.cli.look_cmd.run_look",
                        lambda args, env, *, say=None: seen.append(args.mod) or {"code": 0, "warnings": []})
    monkeypatch.setattr("se2modkit.cli.describe_cmd.run_describe",
                        lambda args, env, *, say=None: seen.append(args.mod) or {"code": 0})
    (tmp_path / "Stray").mkdir()          # a folder here with a plain name is not a mod either
    for mod in (str(toymod), "ws/mods/ToyMod", "./ToyMod", "~/ToyMod", "Stray", "..", "Nope"):
        for res in (box.ship_check(mod, ["XX_A"]), box.look("a.glb", "XX_A", mod=mod),
                    box.describe("t.blend", mod=mod)):
            assert res["ok"] is False and "mod" in res["error"], (mod, res)
    assert seen == []
    assert box.ship_check("ToyMod", ["XX_A"])["ok"] and box.look("a.glb", "XX_A", mod="ToyMod")["ok"]
    assert box.describe("t.blend", mod="ToyMod")["ok"]
    assert seen == [str(toymod.resolve())] * 3


def test_ship_check_reports_name_their_ship_json(box, monkeypatch, tmp_path, toymod):
    out = toymod / "Source" / "Model" / "preview" / "ship"
    monkeypatch.setattr("se2modkit.cli.ship_cmd.run_ship", lambda args, env, *, say=None: {
        "code": 0, "queue": {}, "out_dir": str(out), "reports": [{"block": "XX_A", "problems": []}]})
    res = box.ship_check("ToyMod", ["XX_A"])
    assert res["reports"][0]["ship_json"] == str(out / "XX_A" / "ship.json")


def test_ship_check_takes_no_script_and_refuses_option_like_names(box, monkeypatch, toymod):
    import inspect
    assert "script" not in inspect.signature(box.ship_check).parameters
    seen = {}
    monkeypatch.setattr("se2modkit.cli.ship_cmd.run_ship",
                        lambda args, env, *, say=None: seen.setdefault("args", args) and {"code": 0, "queue": {}})
    for mod, blocks in (("ToyMod", ["-x"]), ("ToyMod", ["XX_A", "--script=evil.py"]),
                        ("--script=evil.py", ["XX_A"])):
        res = box.ship_check(mod, blocks)
        assert res["ok"] is False and "-" in res["error"]
    assert "args" not in seen
    box.ship_check("ToyMod", ["XX_A"])
    assert seen["args"].script is None and seen["args"].blocks == ["XX_A"]


def test_positional_file_arguments_follow_a_double_dash(box, monkeypatch):
    seen = {}
    monkeypatch.setattr("se2modkit.cli.describe_cmd.run_describe",
                        lambda args, env, *, say=None: seen.setdefault("d", args) and {"code": 0})
    monkeypatch.setattr("se2modkit.cli.look_cmd.run_look",
                        lambda args, env, *, say=None: seen.setdefault("l", args) and {"code": 0, "warnings": []})
    box.describe("-v.blend")
    box.look("-v.glb", "XX_A")
    assert seen["d"].blend == Path("-v.blend") and seen["l"].source == Path("-v.glb")


def test_written_paths_must_be_in_the_cache_or_a_preview_folder(box, monkeypatch, tmp_path, toymod):
    seen = []
    monkeypatch.setattr("se2modkit.cli.describe_cmd.run_describe",
                        lambda args, env, *, say=None: seen.append(args.json) or {"code": 0})
    outside = tmp_path / "home" / ".bashrc"
    res = box.describe("toy.blend", json_out=str(outside))
    assert res["ok"] is False and "outside" in res["error"] and str(tmp_path / "cache") in res["error"]
    res = box.describe("toy.blend", json_out="../../etc/x.json")
    assert res["ok"] is False
    assert box.describe("toy.blend", json_out=str(tmp_path / "cache" / "d" / "toy.json"))["ok"]
    preview = toymod / "Source" / "Model" / "preview" / "describe" / "toy.json"
    assert box.describe("toy.blend", mod="ToyMod", json_out=str(preview))["ok"]
    assert box.describe("toy.blend", json_out=str(preview))["ok"] is False      # no mod: only the cache
    assert seen == [(tmp_path / "cache" / "d" / "toy.json").resolve(), preview.resolve()]


def test_look_out_block_and_finish_are_confined(box, monkeypatch, tmp_path, toymod):
    called = []
    monkeypatch.setattr("se2modkit.cli.look_cmd.run_look",
                        lambda args, env, *, say=None: called.append(args) or {"code": 0, "warnings": []})
    assert box.look("a.glb", "XX_A", out=str(tmp_path / "elsewhere"))["ok"] is False
    link = tmp_path / "cache" / "escape"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    assert box.look("a.glb", "XX_A", out=str(link))["ok"] is False
    for block in ("../../x", "a/b", "-x", "..", ""):
        assert box.look("a.glb", block, out=str(tmp_path / "cache" / "look"))["ok"] is False, block
    assert called == []
    assert box.look("a.glb", "XX_A", mod="ToyMod",
                    out=str(toymod / "Source" / "Model" / "preview" / "look"))["ok"]
    assert box.look("a.glb", "XX_A", mod="ToyMod")["ok"]          # the default folder needs no check
    elsewhere = tmp_path / "elsewhere" / "XX_A" / "r1"
    elsewhere.mkdir(parents=True)
    res = box.look_finish(str(elsewhere))
    assert res["ok"] is False and "outside" in res["error"]
