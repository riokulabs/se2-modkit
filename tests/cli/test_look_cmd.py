"""`se2mod look`: the command around `preview.look` (the render itself is the Blender test's job)."""
import json

import pytest

from se2modkit import cli


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SE2_BLENDER", "/bin/true")
    monkeypatch.chdir(tmp_path)


def test_look_registered():
    assert "look" in cli.commands()


def test_print_only_writes_the_job_and_the_queue_command(tmp_path, capsys):
    src = tmp_path / "drive.glb"
    src.write_bytes(b"glTF")
    (tmp_path / "look" / "XX_Drive" / "r1").mkdir(parents=True)
    rc = cli.main(["look", str(src), "--block", "XX_Drive", "--out", str(tmp_path / "look"), "--views", "3q_front,top",
                   "--print-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("se2mod queue submit") and "--load medium" in out and "look_run.py" in out
    assert "{gpu}" in out
    job = json.loads((tmp_path / "xdg" / "se2modkit" / "look-jobs" / "XX_Drive-r2.json").read_text())
    assert job["round"] == 2 and job["views"] == ["3q_front", "top"] and job["set"] == "neighbors"
    assert job["out"].endswith("look/XX_Drive/r2")


def test_outside_a_mod_needs_out(tmp_path, capsys):
    src = tmp_path / "drive.glb"
    src.write_bytes(b"glTF")
    assert cli.main(["look", str(src), "--block", "XX_Drive", "--print-only"]) == 1
    assert "--out" in capsys.readouterr().err


def test_bad_source_is_one_plain_line(tmp_path, capsys):
    src = tmp_path / "drive.step"
    src.write_bytes(b"ISO-10303")
    assert cli.main(["look", str(src), "--block", "X", "--out", str(tmp_path), "--print-only"]) == 1
    assert "GLB" in capsys.readouterr().err


def test_material_flags_add_to_the_mod_table(tmp_path, capsys):
    src = tmp_path / "drive.glb"
    src.write_bytes(b"glTF")
    argv = ["look", str(src), "--block", "XX_Drive", "--out", str(tmp_path / "look"), "--print-only",
            "--material", "rb_Colorable=PaintedMetalColorable", "--material", "rb_Bolt=MetalDull"]
    assert cli.main(argv) == 0
    job = json.loads((tmp_path / "xdg" / "se2modkit" / "look-jobs" / "XX_Drive-r1.json").read_text())
    assert job["materials"] == {"rb_Colorable": "PaintedMetalColorable", "rb_Bolt": "MetalDull"}
    assert cli.main(argv[:-2] + ["--material", "no-equals-sign"]) == 1
    assert "NAME=SLOT" in capsys.readouterr().err
