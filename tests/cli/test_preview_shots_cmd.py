"""`se2mod preview`: previews.toml -> a job file -> one headless Blender run (faked here)."""

from __future__ import annotations

import argparse
import json

import pytest
from fake_env import make_env

from se2modkit import cli
from se2modkit.cli import preview_cmd
from se2modkit.env import procs

PLAN = """
[sets.review]
blocks = ["XX_A", "XX_B"]
[[sets.review.shots]]
name = "front"
[[sets.review.shots]]
name = "broken"
state = "zones-dead"
"""


@pytest.fixture
def mod(tmp_path, monkeypatch):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "Demo Mod"
    root.mkdir()
    (root / "DemoMod.vrgproj").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "DemoMod"\nprefix = "XX_"\nprofile = "p"\n'
                                         'gates = []\n', encoding="utf-8")
    (root / "previews.toml").write_text(PLAN, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _export(root, *blocks, export_json=False):
    for b in blocks:
        d = root / "Source/Model/Models" / b
        (d / "NonFractured").mkdir(parents=True, exist_ok=True)
        (d / "NonFractured" / f"{b}.fbx").write_bytes(b"fbx")
        if export_json:
            (d / "export.json").write_text("{}", encoding="utf-8")


def _args(**kw):
    base = dict(mod=None, set_name=None, block=[], shot=None, still=False, fast=False, plan=None, list=False,
                print_only=False, timeout=60)
    return argparse.Namespace(**{**base, **kw})


def _env(tmp_path):
    return make_env(tmp_path, blender=tmp_path / "blender" / "blender")


def test_registered():
    assert "preview" in cli.commands()


def test_list(mod, tmp_path, capsys):
    assert preview_cmd.preview_shots(_args(list=True), _env(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "review: front (intact; XX_A XX_B), broken (zones-dead; XX_A XX_B)" in out


def test_missing_exports_named(mod, tmp_path, capsys):
    _export(mod, "XX_A")
    assert preview_cmd.preview_shots(_args(), _env(tmp_path)) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "XX_B" in err and "export.json" in err


def test_no_plan_is_one_sentence(mod, tmp_path, capsys):
    (mod / "previews.toml").unlink()
    assert preview_cmd.preview_shots(_args(), _env(tmp_path)) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "previews.toml" in err


def test_still_writes_job_and_runs_blender(mod, tmp_path, capsys, monkeypatch):
    _export(mod, "XX_A", "XX_B", export_json=True)
    monkeypatch.setattr(procs, "listing_available", lambda **_: True)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    seen = {}

    def fake(argv, timeout):
        seen["argv"], seen["timeout"] = argv, timeout
        return 0, ["/x/front.png"], []
    monkeypatch.setattr(preview_cmd, "_run_blender", fake)
    env = _env(tmp_path)
    assert preview_cmd.preview_shots(_args(still=True, block=["XX_B"]), env) == 0
    argv = seen["argv"]
    assert argv[0] == str(env.blender) and argv[1] == "-b" and argv[-3].endswith("run.py")
    job = json.loads(open(argv[-1], encoding="utf-8").read())
    assert job["fast"] is True and [s["blocks"] for s in job["shots"]] == [["XX_B"]]
    assert job["out"] == str(mod / "Source/Model/preview/review")
    assert job["mod"]["name"] == "DemoMod" and job["mod"]["paths"]["models"] == str(mod / "Source/Model/Models")
    assert "1 image(s)" in capsys.readouterr().out


def test_refuses_while_game_runs(mod, tmp_path, capsys, monkeypatch):
    _export(mod, "XX_A", "XX_B", export_json=True)
    monkeypatch.setattr(procs, "listing_available", lambda **_: True)
    monkeypatch.setattr(procs, "game_running", lambda **_: True)
    monkeypatch.setattr(preview_cmd, "_run_blender", lambda *a: pytest.fail("Blender must not start"))
    assert preview_cmd.preview_shots(_args(), _env(tmp_path)) == 4
    assert "running" in capsys.readouterr().err


def test_blender_error_lines_reported(mod, tmp_path, capsys, monkeypatch):
    _export(mod, "XX_A", "XX_B", export_json=True)
    monkeypatch.setattr(procs, "listing_available", lambda **_: True)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    monkeypatch.setattr(preview_cmd, "_run_blender", lambda a, t: (2, [], ["XX_A: nothing to show."]))
    assert preview_cmd.preview_shots(_args(shot="broken"), _env(tmp_path)) == 1
    assert capsys.readouterr().err == "error: XX_A: nothing to show.\n"


def test_run_blender_timeout(tmp_path):
    import sys
    code, written, errors = preview_cmd._run_blender(
        [sys.executable, "-c", "import time; print('WROTE /a.png', flush=True); time.sleep(30)"], 1)
    assert written == ["/a.png"] and errors and "did not finish within 1 s" in errors[0]
