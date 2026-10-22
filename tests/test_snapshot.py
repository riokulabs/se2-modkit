import json
from pathlib import Path

from se2modkit import snapshot
from se2modkit.project import ModConfig, ModPaths


def _mod(root: Path) -> ModConfig:
    p = ModPaths(models=root / "Models", report=root / "report.json", staged=root / "Staged",
                 golden_states=root / "states.json", source_model=root)
    (root / "Models" / "sub").mkdir(parents=True)
    (root / "Staged").mkdir()
    (root / "Models" / "a.vrm").write_bytes(b"A")
    (root / "Models" / "sub" / "b.vrm").write_bytes(b"B")
    (root / "Staged" / "s.xml").write_text("<x/>", encoding="utf-8")
    (root / "states.json").write_text("{}", encoding="utf-8")
    (root / "report.json").write_text(json.dumps({"a": 1, "se2modkit": {"v": 1}}), encoding="utf-8")
    return ModConfig(name="M", root=root, profile="vanilla", prefix="", gates=(), style={}, paths=p)


def test_snapshot_identical_trees(tmp_path):
    a, b = _mod(tmp_path / "a"), _mod(tmp_path / "b")
    assert snapshot.compare(snapshot.take(a), snapshot.take(b)) == []


def test_snapshot_detects_model_change(tmp_path):
    a, b = _mod(tmp_path / "a"), _mod(tmp_path / "b")
    (b.paths.models / "a.vrm").write_bytes(b"changed")
    (b.paths.models / "new.vrm").write_bytes(b"N")
    (b.paths.models / "sub" / "b.vrm").unlink()
    diff = snapshot.compare(snapshot.take(a), snapshot.take(b))
    assert sorted(diff) == ["added: models/new.vrm", "changed: models/a.vrm", "removed: models/sub/b.vrm"]


def test_snapshot_ignores_modkit_provenance_key(tmp_path):
    a, b = _mod(tmp_path / "a"), _mod(tmp_path / "b")
    b.paths.report.write_text(json.dumps({"se2modkit": {"v": 2}, "a": 1}), encoding="utf-8")
    assert snapshot.compare(snapshot.take(a), snapshot.take(b)) == []
    b.paths.report.write_text(json.dumps({"a": 2}), encoding="utf-8")
    assert snapshot.compare(snapshot.take(a), snapshot.take(b)) == ["changed: report"]


def test_snapshot_path_keys_are_posix(tmp_path):
    s = snapshot.take(_mod(tmp_path / "a"))
    assert set(s["models"]) == {"a.vrm", "sub/b.vrm"}
    assert all("\\" not in k for k in (*s["models"], *s["staged"]))
    assert set(s) == {"report", "report_stats", "models", "staged", "golden_states"}


def test_snapshot_cli_roundtrip(tmp_path, run):
    mod = _mod(tmp_path / "proj")
    (mod.root / "m.vrgproj").write_text("<Project/>", encoding="utf-8")
    (mod.root / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "M"\n[tool.se2modkit.paths]\n'
                                             'models = "Models"\nreport = "report.json"\nstaged = "Staged"\n'
                                             'golden_states = "states.json"\n', encoding="utf-8")
    out_file = tmp_path / "snap.json"
    assert run("snapshot", str(mod.root), "--out", str(out_file))[0] == 0
    assert run("snapshot", str(mod.root), "--compare", str(out_file))[0] == 0
    (mod.paths.models / "a.vrm").write_bytes(b"zzz")
    code, out, _ = run("snapshot", str(mod.root), "--compare", str(out_file))
    assert code == 1 and "changed: models/a.vrm" in out
    code, out, err = run("snapshot", str(tmp_path / "nowhere"), "--out", str(out_file))
    assert code == 1 and "Traceback" not in err and err.strip()
