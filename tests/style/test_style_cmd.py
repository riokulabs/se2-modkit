"""`se2mod style check [MOD] [--warn-only]` on a synthetic mod with a hand-written build_report.json."""

import dataclasses
import json

import pytest

PYPROJECT = """
[tool.se2modkit]
mod_name = "Demo"
profile = "vanilla"
[tool.se2modkit.style]
warn_only = true
"""


def _block(colorable, dm_tris=1000):
    return {"lods": [{"area_pct": {"PaintedMetalColorable": colorable, "PaintedMetalDarker": 100.0 - colorable},
                      "material_names": ["PaintedMetalColorable", "PaintedMetalDarker"], "materials": 2,
                      "tris": 10, "max_tris": 100, "max_materials": 18}],
            "damage": {"dm_tris": dm_tris, "fr_tris": 1000, "dent_mm": 8.0}}


@pytest.fixture
def mod(tmp_path, monkeypatch):
    root = tmp_path / "Demo Mod"
    (root / "Source" / "Model").mkdir(parents=True)
    (root / "Demo.vrgproj").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    report = {"blocks": {"Demo_Good": _block(60.0), "Demo_Dark": _block(20.0), "Demo_Dense": _block(60.0, 2000)}}
    (root / "Source" / "Model" / "build_report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_style_check_fails_on_unwaived_fail(run, mod):
    code, out, err = run("style", "check")
    assert code == 1, err
    assert "FAIL" in out and "Demo_Dark" in out and "paint.colorable_min_share" in out
    assert "WARN" in out and "Demo_Dense" in out and "dm.tri_ratio_max" in out
    assert "not measured" in out


def test_style_check_warn_only_exits_zero(run, mod):
    code, out, _ = run("style", "check", "Demo", "--warn-only")
    assert code == 0
    assert "Demo_Dark" in out


def test_style_check_waiver_clears_the_fail(run, mod):
    (mod / "style-waivers.toml").write_text(
        '[[waiver]]\nblock = "Demo_Dark"\nkey = "paint.colorable_min_share"\nreason = "dark cabinet"\n'
        'date = 2026-10-21\n', encoding="utf-8")
    code, out, _ = run("style", "check", str(mod))
    assert code == 0
    assert "waived: dark cabinet (2026-10-21)" in out


def test_style_check_without_report_is_one_sentence(run, mod):
    (mod / "Source" / "Model" / "build_report.json").unlink()
    code, _, err = run("style", "check")
    assert code == 1
    assert "build_report.json" in err and "Traceback" not in err


def test_style_check_bad_waiver_is_one_sentence(run, mod):
    (mod / "style-waivers.toml").write_text('[[waiver]]\nblock = "X"\nkey = "k.k"\n', encoding="utf-8")
    code, _, err = run("style", "check")
    assert code == 1
    assert "reason" in err and "Traceback" not in err


def test_style_check_relaxing_override_is_one_sentence(run, mod):
    (mod / "pyproject.toml").write_text(PYPROJECT + "[tool.se2modkit.style.paint]\ncolorable_min_share = 0.2\n",
                                        encoding="utf-8")
    code, _, err = run("style", "check")
    assert code == 1
    assert "paint.colorable_min_share" in err and "Traceback" not in err


def test_gate_failures_honour_warn_only(mod):
    from se2modkit.project import load_project
    from se2modkit.style import check
    m = load_project(mod).mod(None)
    assert m.style["warn_only"] is True
    assert check.gate_failures(m) == []
    strict = dataclasses.replace(m, style={})
    assert [(f.block, f.key) for f in check.gate_failures(strict)] == [("Demo_Dark", "paint.colorable_min_share")]
