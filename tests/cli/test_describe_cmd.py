"""`se2mod describe`: the queued Blender job, the spec it gets and the verdict (the scene reading is the Blender
test's job)."""
import json

import pytest

from se2modkit import cli
from se2modkit import describe as D
from se2modkit import jobqueue as Q


@pytest.fixture
def blend(tmp_path, monkeypatch):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SE2_BLENDER", "/bin/true")
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "toy.blend"
    f.write_bytes(b"BLENDER")
    return f


def _report(spec):
    parts = [{"name": "Body", "collection": "Block", "role": "hull", "lo": [-500, -500, -500], "hi": [500, 500, 500],
              "tris": 12},
             {"name": "Nozzle", "collection": "Block", "role": "port", "lo": [-100, -100, -800], "hi": [100, 100, -500],
              "tris": 12}]
    body = D.analyze(parts)
    return D.finish(body, envelope=spec["envelope"], targets=spec["targets"], source=spec["source"],
                    collections=spec["collections"], missing=[])


@pytest.fixture
def queued(monkeypatch):
    """Fake queue: records the job and, on wait, writes a report built from the spec to its status file."""
    seen = {"code": 0, "error": None}

    def submit(self, argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return type("Job", (), {"id": 9})()

    def wait(store, job_id, timeout, **kw):
        spec_path, status = seen["argv"][-2:]
        with open(spec_path, encoding="utf-8") as f:
            seen["spec"] = json.load(f)
        rep = {"error": seen["error"]} if seen["error"] else _report(seen["spec"])
        with open(status, "w", encoding="utf-8") as f:
            json.dump({"describe": rep}, f)
        return seen["code"]
    monkeypatch.setattr(Q.Store, "submit", submit)
    monkeypatch.setattr(Q, "wait", wait)
    monkeypatch.setattr(Q, "ensure_runner", lambda store, *a, **k: None)
    return seen


def test_describe_registered():
    assert "describe" in cli.commands()


def test_queues_one_light_headless_job_on_the_file(blend, queued, capsys, tmp_path):
    out = tmp_path / "rep.json"
    assert cli.main(["describe", str(blend), "--collection", "Block", "--envelope", "1,1,1.5", "--json",
                     str(out)]) == 0
    argv = queued["argv"]
    assert argv[1:4] == ["-b", "--disable-autoexec", str(blend.resolve())]    # scripts in the file never run
    assert argv[argv.index("--python") + 1].endswith("describe_job.py")
    assert queued["kw"]["load"] == Q.LOADS["light"] and queued["kw"]["name"] == "describe:toy.blend:Block"
    assert queued["spec"] == {"source": "toy.blend", "collections": ["Block"], "envelope": [1.0, 1.0, 1.5],
                              "targets": []}
    text = capsys.readouterr().out
    assert text.startswith("toy.blend [Block]: 2 parts, 24 tris") and "#2 Nozzle port" in text
    assert json.loads(out.read_text())["block"]["parts"] == 2
    assert not list((tmp_path / "xdg").rglob("describe/*.json"))                      # spec and status cleaned up


def test_a_failed_target_exits_1(blend, queued, capsys, tmp_path):
    kit = tmp_path / "kit.md"
    kit.write_text('```targets\n[[target]]\nname = "nozzle share"\na = "Nozzle.z"\nb = "Body.z"\nvalue = 0.3\n'
                   'tol = 0.01\n\n[[target]]\nname = "ghost"\na = "Ghost*.x"\nvalue = 1\ntol = 1\n```\n')
    assert cli.main(["describe", str(blend), "--targets", str(kit)]) == 1
    out = capsys.readouterr().out
    assert "PASS target nozzle share: 0.3" in out and "FAIL target ghost" in out and "matches no part" in out


def test_bad_targets_and_missing_file_exit_2_before_queuing(blend, queued, capsys, tmp_path):
    kit = tmp_path / "kit.md"
    kit.write_text('```targets\n[[target]]\nname = "x"\na = "Body"\nvalue = 1\ntol = 1\n```\n')
    assert cli.main(["describe", str(blend), "--targets", str(kit)]) == 2
    assert cli.main(["describe", str(tmp_path / "nope.blend")]) == 2
    assert "argv" not in queued
    assert "GLOB.AXIS" in capsys.readouterr().err


def test_a_missing_collection_or_failed_job_exits_2(blend, queued, capsys):
    queued["error"] = "no collection named Nope in the file; it has: Block"
    assert cli.main(["describe", str(blend), "--collection", "Nope"]) == 2
    assert "no collection named Nope" in capsys.readouterr().err
    queued["error"], queued["code"] = None, 1
    assert cli.main(["describe", str(blend)]) == 2


def test_print_only_queues_nothing(blend, queued, capsys):
    assert cli.main(["describe", str(blend), "--print-only"]) == 0
    assert "argv" not in queued
    assert capsys.readouterr().out.startswith("se2mod queue submit --name describe:toy.blend --load light")


def test_envelope_must_be_three_positive_numbers(blend, queued):
    with pytest.raises(SystemExit):
        cli.main(["describe", str(blend), "--envelope", "1,2"])
