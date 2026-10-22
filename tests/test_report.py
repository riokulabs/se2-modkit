"""`se2mod report`: compact LOD table from build_report.json, budget % of Keen limits, deltas vs a baseline."""

import json

import pytest

from se2modkit import snapshot
from se2modkit.project import ModConfig, ModPaths


def _lod(tris, mats, max_tris=None):
    d = {"tris": tris, "materials": mats, "max_materials": 18}
    if max_tris is not None:
        d["max_tris"] = max_tris
    return d


def _report(blocks):
    return {"blocks": blocks, "se2modkit": {"v": 1}}


BASE = _report({
    "XX_Rack": {"model_size": [1.0, 1.0, 2.0], "lods": [_lod(1000, 4, 4000), _lod(500, 3, 2000), _lod(100, 1, 400)]},
    "XX_Rack_Wide": {"model_size": [2.0, 1.0, 2.0], "lods": [_lod(2000, 5, 8000), _lod(900, 3, 4000)]},
    "XX_Door": {"model_size": [2.5, 2.5, 2.5], "lods": [_lod(3000, 6)]},  # no max_tris: derived from model/limits
})


@pytest.fixture
def mod_dir(tmp_path, monkeypatch):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    d = tmp_path / "mods" / "Xx"
    (d / "Source" / "Model").mkdir(parents=True)
    (d / "Xx.vrgproj").write_text(json.dumps({"$Value": {}}), encoding="utf-8")
    (d / "Source" / "Model" / "build_report.json").write_text(json.dumps(BASE), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "Xx"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return d


def _single(tmp_path, monkeypatch, d):
    """Project root = the mod dir itself."""
    (d / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "Xx"\n', encoding="utf-8")
    monkeypatch.chdir(d)


def test_report_default_one_row_per_block(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    code, out, err = run("report", ".")
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].split() == ["block", "LOD", "tris", "materials", "%limit"]
    rows = {ln.split()[0]: ln.split() for ln in lines[1:]}
    assert rows["XX_Rack"][1:] == ["0", "1000", "4", "25.0%"]
    assert rows["XX_Door"][2] == "3000" and rows["XX_Door"][4] == "12.5%"  # 24000 from model/limits (2.5 m cube)
    assert len(rows) == 3


def test_report_block_shows_all_lods(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    code, out, _ = run("report", ".", "--block", "XX_Rack")
    rows = [ln.split() for ln in out.splitlines()[1:]]
    assert [r[1] for r in rows] == ["0", "1", "2"] and [r[2] for r in rows] == ["1000", "500", "100"]


def test_report_fields_select_columns(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    code, out, _ = run("report", ".", "--fields", "tris")
    assert out.splitlines()[0].split() == ["block", "tris"]
    code, out, _ = run("report", ".", "--block", "XX_Rack_Wide", "--fields", "lods,tris")
    assert out.splitlines()[0].split() == ["block", "LOD", "tris"] and len(out.splitlines()) == 3
    code, out, err = run("report", ".", "--fields", "tris,nope")
    assert code == 1 and out == "" and len(err.strip().splitlines()) == 1 and "nope" in err


def test_report_unknown_block_lists_close_matches(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    code, out, err = run("report", ".", "--block", "XX_Rak")
    assert code == 1 and out == ""
    assert len(err.strip().splitlines()) == 1 and "XX_Rack" in err and "Traceback" not in err
    code, _, err = run("report", ".", "--block", "zzzzzz")
    assert code == 1 and len(err.strip().splitlines()) == 1


def test_report_max_lines_footer(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    code, out, _ = run("report", ".", "--max", "2")
    lines = out.splitlines()
    assert len(lines) == 4 and lines[-1] == "… 1 more lines (use --max)"


def test_report_missing_report_is_one_sentence(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    (mod_dir / "Source" / "Model" / "build_report.json").unlink()
    code, out, err = run("report", ".")
    assert code == 1 and out == "" and len(err.strip().splitlines()) == 1 and "build_report.json" in err


def test_report_corrupt_report(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    (mod_dir / "Source" / "Model" / "build_report.json").write_text("{nope", encoding="utf-8")
    code, _, err = run("report", ".")
    assert code == 1 and len(err.strip().splitlines()) == 1


def _changed():
    r = json.loads(json.dumps(BASE))
    r["blocks"]["XX_Rack"]["lods"][0]["tris"] = 1250
    del r["blocks"]["XX_Door"]
    r["blocks"]["XX_New"] = {"model_size": [1, 1, 1], "lods": [_lod(10, 1, 4000)]}
    return r


def test_report_vs_report_file_shows_deltas(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    old = tmp_path / "old_report.json"
    old.write_text(json.dumps(BASE), encoding="utf-8")
    (mod_dir / "Source" / "Model" / "build_report.json").write_text(json.dumps(_changed()), encoding="utf-8")
    code, out, err = run("report", ".", "--vs", str(old))
    assert code == 0, err
    rows = {ln.split()[0]: ln for ln in out.splitlines()[1:]}
    assert "+250" in rows["XX_Rack"] and "+250" not in rows["XX_Rack_Wide"]
    assert "(new)" in rows["XX_New"]
    assert any(ln.startswith("removed: XX_Door") for ln in out.splitlines())


def test_report_vs_snapshot(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    code, out, err = run("snapshot", ".", "--out", str(tmp_path / "snap.json"))
    assert code == 0, err
    (mod_dir / "Source" / "Model" / "build_report.json").write_text(json.dumps(_changed()), encoding="utf-8")
    code, out, err = run("report", ".", "--vs", str(tmp_path / "snap.json"))
    assert code == 0, err
    assert "+250" in out


def test_report_vs_old_snapshot_without_stats(run, mod_dir, tmp_path, monkeypatch):
    _single(tmp_path, monkeypatch, mod_dir)
    (tmp_path / "old.json").write_text(json.dumps({"report": "abc", "models": {}}), encoding="utf-8")
    code, out, err = run("report", ".", "--vs", str(tmp_path / "old.json"))
    assert code == 1 and out == "" and len(err.strip().splitlines()) == 1
    code, _, err = run("report", ".", "--vs", str(tmp_path / "missing.json"))
    assert code == 1 and len(err.strip().splitlines()) == 1


def test_snapshot_records_report_stats(tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    (root / "r.json").write_text(json.dumps(BASE), encoding="utf-8")
    p = ModPaths(models=root / "M", report=root / "r.json", staged=root / "S", golden_states=root / "g.json",
                 source_model=root)
    mod = ModConfig(name="M", root=root, profile="vanilla", prefix="", gates=(), style={}, paths=p)
    s = snapshot.take(mod)
    assert s["report_stats"]["XX_Rack"][0] == [1000, 4, 4000]
    assert snapshot.compare(s, snapshot.take(mod)) == []
