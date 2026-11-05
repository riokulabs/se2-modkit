"""`se2mod fracture check`: the queued Blender run and the verdict (the check itself is the Blender test's job)."""
import json

import pytest

from se2modkit import cli
from se2modkit import jobqueue as Q


@pytest.fixture
def mod(tmp_path, monkeypatch):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SE2_BLENDER", "/bin/true")
    root = tmp_path / "Toy Mod"
    (root / "Source" / "Model").mkdir(parents=True)
    (root / "ToyMod.vrgproj").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "ToyMod"\nprefix = "XX_"\n', encoding="utf-8")
    (root / "Source" / "Model" / "build_toymod.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _report(share=0.18, loose=None):
    return {"block": "XX_Box", "family": "box", "status": "checked", "error": None, "cell_groups": 2, "tris": 900,
            "loose": loose or {}, "seams": [],
            "zones": [{"id": "Fracture_01", "label": "mount", "critical": True, "share": share, "lifted": False},
                      {"id": "Fracture_02", "label": "body", "critical": False, "share": 1 - share, "lifted": False}]}


@pytest.fixture
def queued(monkeypatch):
    """Fake queue: records the submitted job and, on wait, writes `reports` to its --status file."""
    seen = {"reports": [_report()], "code": 0}

    def submit(self, argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return type("Job", (), {"id": 7})()

    def wait(store, job_id, timeout, **kw):
        status = next(a.split("=", 1)[1] for a in seen["argv"] if a.startswith("--status="))
        with open(status, "w", encoding="utf-8") as f:
            json.dump({"selected": ["XX_Box"], "fracture": seen["reports"]}, f)
        return seen["code"]
    monkeypatch.setattr(Q.Store, "submit", submit)
    monkeypatch.setattr(Q, "wait", wait)
    monkeypatch.setattr(Q, "ensure_runner", lambda store, *a, **k: None)
    return seen


def test_fracture_registered():
    assert "fracture" in cli.commands()


def test_a_passing_block_exits_0_and_queues_a_headless_check(mod, queued, capsys):
    assert cli.main(["fracture", "check", "ToyMod", "XX_Box"]) == 0
    argv = queued["argv"]
    assert "-b" in argv and "--check-fracture" in argv and "--no-render" in argv and "XX_Box" in argv
    assert argv[argv.index("--python") + 1].endswith("build_toymod.py")
    assert queued["kw"]["name"] == "fracture-check:ToyMod:XX_Box" and queued["kw"]["load"] == Q.LOADS["medium"]
    out = capsys.readouterr().out
    assert "Fracture_01" in out and "FAIL" not in out


def test_loose_geometry_exits_1(mod, queued, capsys):
    queued["reports"] = [_report(loose={"pipe": {"verts": 5, "edges": 0}})]
    assert cli.main(["fracture", "check", "ToyMod", "XX_Box"]) == 1
    assert capsys.readouterr().out.count("FAIL") == 1


def test_a_large_key_zone_warns_unless_a_limit_is_given(mod, queued, capsys):
    queued["reports"] = [_report(share=0.25)]
    assert cli.main(["fracture", "check", "ToyMod", "XX_Box"]) == 0
    assert "WARN" in capsys.readouterr().out
    assert cli.main(["fracture", "check", "ToyMod", "XX_Box", "--max-key-share", "0.3"]) == 0
    assert cli.main(["fracture", "check", "ToyMod", "XX_Box", "--max-key-share", "0.2"]) == 1


def test_json_prints_the_reports(mod, queued, capsys):
    assert cli.main(["fracture", "check", "ToyMod", "XX_Box", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["block"] == "XX_Box" and data[0]["problems"] == [] and data[0]["warnings"] == []


def test_a_failed_job_says_where_its_log_is(mod, queued, capsys):
    queued["code"] = 1
    assert cli.main(["fracture", "check", "ToyMod", "XX_Box"]) == 2
    assert "se2mod queue log 7" in capsys.readouterr().err


def test_blocks_are_required(mod, capsys):
    with pytest.raises(SystemExit):
        cli.main(["fracture", "check", "ToyMod"])
