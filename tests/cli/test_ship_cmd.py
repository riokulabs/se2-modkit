"""`se2mod ship check`: the queue-order gate, the queued Blender run and the verdict (the check itself is the Blender
test's job)."""
import json

import pytest

from se2modkit import cli
from se2modkit import jobqueue as Q


@pytest.fixture
def mod(tmp_path, monkeypatch):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))   # the cache (and queue) on Windows: one per test
    monkeypatch.setenv("SE2_BLENDER", "/bin/true")
    root = tmp_path / "Toy Mod"
    (root / "Source" / "Model").mkdir(parents=True)
    (root / "ToyMod.vrgproj").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "ToyMod"\nprefix = "XX_"\n', encoding="utf-8")
    (root / "Source" / "Model" / "build_toymod.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _report(blend="", coplanar=()):
    return {"block": "XX_Box", "family": "box", "status": "checked", "error": None,
            "shipping": [{"model": "LOD0", "collection": "XX_Box_LOD0", "objects": [{"name": "a", "tris": 12}]}],
            "collision": [], "zfight": {"coplanar": list(coplanar)} if coplanar else {},
            "sources": [{"blend": blend, "collection": "Box_v1", "objects": []}] if blend else [],
            "fracture": {"block": "XX_Box", "family": "box", "status": "checked", "cell_groups": 1, "tris": 12,
                         "loose": {}, "seams": [], "zones": [{"id": "Fracture_01", "label": "all", "share": 1.0}]},
            "zone_bounds": {}, "preview": "/x/exploded.png", "preview_error": None}


@pytest.fixture
def queued(monkeypatch):
    """Fake queue: records the submitted job and, on wait, writes `reports` to its --status file. The real
    Store.all() still reads the (temporary) queue folder, so tests can put jobs there."""
    seen = {"reports": [_report()], "code": 0, "submitted": 0}
    real_submit = Q.Store.submit

    def submit(self, argv, **kw):
        if kw.get("name", "").startswith("ship-check:"):
            seen["argv"], seen["kw"] = argv, kw
            seen["submitted"] += 1
            return type("Job", (), {"id": 7})()
        return real_submit(self, argv, **kw)

    def wait(store, job_id, timeout, **kw):
        status = next(a.split("=", 1)[1] for a in seen["argv"] if a.startswith("--status="))
        with open(status, "w", encoding="utf-8") as f:
            json.dump({"selected": ["XX_Box"], "ship": seen["reports"]}, f)
        return seen["code"]
    monkeypatch.setattr(Q.Store, "submit", submit)
    monkeypatch.setattr(Q, "wait", wait)
    monkeypatch.setattr(Q, "ensure_runner", lambda store, *a, **k: None)
    return seen


def _store():
    from se2modkit.env.core import discover_env
    return Q.Store(discover_env().cache_dir / "queue")


def test_ship_registered():
    assert "ship" in cli.commands()


def test_a_passing_block_queues_a_headless_check_and_writes_its_report(mod, queued, capsys):
    assert cli.main(["ship", "check", "ToyMod", "XX_Box"]) == 0
    argv = queued["argv"]
    assert "-b" in argv and "--check-ship" in argv and "--no-render" in argv and "XX_Box" in argv
    out_dir = mod / "Source" / "Model" / "preview" / "ship"
    assert f"--ship-out={out_dir}" in argv
    assert queued["kw"]["name"] == "ship-check:ToyMod:XX_Box" and queued["kw"]["load"] == Q.LOADS["medium"]
    out = capsys.readouterr().out
    assert "PASS XX_Box" in out and "FAIL" not in out
    saved = json.loads((out_dir / "XX_Box" / "ship.json").read_text(encoding="utf-8"))
    assert saved["problems"] == [] and saved["queue"] == {"fail": [], "warn": []}


def test_a_coplanar_row_exits_1(mod, queued, capsys):
    queued["reports"] = [_report(coplanar=[{"model": "block_LOD0", "a": "x/A", "b": "x/B", "area_mm2": 5.0}])]
    assert cli.main(["ship", "check", "ToyMod", "XX_Box"]) == 1
    assert "FAIL XX_Box" in capsys.readouterr().out


def test_a_save_behind_an_export_fails_before_anything_is_queued(mod, queued, capsys, tmp_path):
    blend = tmp_path / "toy.blend"
    store = _store()
    store.submit(["python", "-c", "x", "build-models", "ToyMod", "XX_Box", "--export"], name="export:ToyMod:XX_Box")
    store.submit(["blender", "-b", str(blend), "--python", "save.py"], name="save toy")
    assert cli.main(["ship", "check", "ToyMod", "XX_Box", "--blend", str(blend)]) == 1
    out = capsys.readouterr().out
    assert "saves toy.blend but runs after job 1" in out and "nothing was queued" in out
    assert queued["submitted"] == 0


def test_the_files_the_run_loaded_are_checked_against_the_queue_after_it(mod, queued, capsys, tmp_path):
    blend = tmp_path / "toy.blend"
    queued["reports"] = [_report(blend=str(blend))]
    _store().submit(["blender", "-b", str(blend), "--python", "save.py"], name="save toy")
    assert cli.main(["ship", "check", "ToyMod", "XX_Box"]) == 0           # a save with no export behind it: a WARN
    out = capsys.readouterr().out
    assert "WARN XX_Box: job 1 (save toy) saves toy.blend: save and release before the export" in out


def test_json_prints_the_reports(mod, queued, capsys):
    assert cli.main(["ship", "check", "ToyMod", "XX_Box", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["block"] == "XX_Box" and data[0]["problems"] == [] and "queue" in data[0]
    assert "ship_json" not in data[0]


def test_print_only_queues_nothing(mod, queued, capsys):
    assert cli.main(["ship", "check", "ToyMod", "XX_Box", "--print-only"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("se2mod queue submit --name ship-check:ToyMod:XX_Box --load medium") and "--check-ship" in out
    assert queued["submitted"] == 0


def test_a_failed_job_says_where_its_log_is(mod, queued, capsys):
    queued["code"] = 1
    assert cli.main(["ship", "check", "ToyMod", "XX_Box"]) == 2
    assert "se2mod queue log 7" in capsys.readouterr().err


def test_blocks_are_required(mod, capsys):
    with pytest.raises(SystemExit):
        cli.main(["ship", "check", "ToyMod"])
