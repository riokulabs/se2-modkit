"""`se2mod deps`: the dependency check over a synthetic recorded dependency file, and baselines in the cache only."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest
from fake_env import make_env

from se2modkit.deps import baseline, check, diff

G_DEF = "40000000-0000-4000-8000-000000000001"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path, decompiled_dir=tmp_path / "decompiled")
    f = e.sdk_dir / "GameData" / "Vanilla" / "Assets" / "Blocks" / "Widget.def"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"$Value": {"Guid": G_DEF}}), encoding="utf-8")
    acf = e.game_dir.parent.parent / "appmanifest_1133870.acf"
    acf.write_text('"AppState"\n{\n\t"appid"\t\t"1133870"\n\t"buildid"\t\t"100"\n}\n', encoding="utf-8")
    (tmp_path / "decompiled").mkdir()
    e.cache_dir.mkdir()
    (tmp_path / "decompiled" / "Thing.cs").write_text("a\nb\nclass Anchor {}\n", encoding="utf-8")
    return e


def _doc(old: bytes) -> dict:
    return {"baseline": {"game_version": "4.0.0.3155", "game_buildid": "100", "sdk_buildid": "200"},
            "dependencies": [
                {"id": "file:widget", "kind": "vanilla_file", "risk": "high", "title": "the widget def",
                 "guid": G_DEF, "game_source": [{"path": "SDK:GameData/Vanilla/Assets/Blocks/Widget.def",
                                                 "sha256": _sha(old)}]},
                {"id": "steam:1133870", "kind": "steam_build", "risk": "low", "title": "game build",
                 "buildid": "100"},
                {"id": "dec:anchor", "kind": "decompiled", "risk": "med", "title": "anchor",
                 "game_source": [{"decompiled": "Thing.cs:3", "anchor": "class Anchor"}]},
            ]}


def test_deps_check_reports_changed_hash(env, tmp_path, capsys):
    deps = tmp_path / "deps.json"
    deps.write_text(json.dumps(_doc(b"old content")), encoding="utf-8")
    code = check.run(env, deps_file=deps, workspace=tmp_path)
    out = capsys.readouterr().out
    assert code == 1  # a high-risk dependency changed
    assert "file:widget" in out and "sha256 differs" in out
    assert "dec:anchor" not in out and "steam:1133870" not in out


def test_deps_check_clean(env, tmp_path, capsys):
    cur = (env.sdk_dir / "GameData/Vanilla/Assets/Blocks/Widget.def").read_bytes()
    deps = tmp_path / "deps.json"
    deps.write_text(json.dumps(_doc(cur)), encoding="utf-8")
    assert check.run(env, deps_file=deps, workspace=tmp_path) == 0


def test_bundled_dependency_file_is_an_empty_list():
    doc = json.loads(check.BUNDLED.read_text(encoding="utf-8"))
    assert doc["dependencies"] == []


def test_rebaseline_writes_into_cache(env, tmp_path, capsys):
    deps = tmp_path / "deps.json"
    deps.write_text(json.dumps(_doc(b"old content")), encoding="utf-8")
    before = _tree(env.sdk_dir) | _tree(env.game_dir)
    check.run(env, deps_file=deps, workspace=tmp_path, rebaseline=True)
    out = env.cache_dir / "deps" / "our-game-dependencies.rebaselined.json"
    new = json.loads(out.read_text(encoding="utf-8"))
    cur = (env.sdk_dir / "GameData/Vanilla/Assets/Blocks/Widget.def").read_bytes()
    assert new["dependencies"][0]["game_source"][0]["sha256"] == _sha(cur)
    assert _tree(env.sdk_dir) | _tree(env.game_dir) == before


def test_refuses_to_write_under_game(env, tmp_path):
    deps = tmp_path / "deps.json"
    deps.write_text(json.dumps(_doc(b"x")), encoding="utf-8")
    with pytest.raises(check.DepsError, match="read-only"):
        check.run(env, deps_file=deps, workspace=tmp_path, report_json=env.game_dir / "r.json")


def test_baseline_writes_only_to_cache(env, tmp_path):
    (env.game_dir / "Game2" / "Game2.Game.dll").write_bytes(b"MZ fake")
    (env.game_dir / "GameData" / "Thing.def").write_text('{"a": 1}', encoding="utf-8")
    acf = env.game_dir.parent.parent / "appmanifest_3578990.acf"
    acf.write_text('"AppState"\n{\n\t"appid"\t\t"3578990"\n\t"buildid"\t\t"200"\n}\n', encoding="utf-8")
    game_before, sdk_before = _tree(env.game_dir), _tree(env.sdk_dir)
    lib_before = sorted(str(p) for p in (tmp_path / "Steam Library").rglob("*"))
    outside = sorted(str(p) for p in tmp_path.iterdir())

    dest = baseline.capture(env, "4.0.0.3155", workers=2)
    assert dest == env.cache_dir / "baseline" / "4.0.0.3155"
    assert (dest / "manifest-game.tsv.gz").is_file() and (dest / "version-evidence.json").is_file()
    with gzip.open(dest / "manifest-game.tsv.gz", "rt", encoding="utf-8") as f:
        assert "GameData/Thing.def" in f.read()
    ev = json.loads((dest / "version-evidence.json").read_text(encoding="utf-8"))
    assert ev["game_appmanifest"]["buildid"] == "100" and ev["sdk_appmanifest"]["buildid"] == "200"
    assert _tree(env.game_dir) == game_before and _tree(env.sdk_dir) == sdk_before
    assert sorted(str(p) for p in (tmp_path / "Steam Library").rglob("*")) == lib_before
    assert sorted(str(p) for p in tmp_path.iterdir()) == outside  # only the (existing) cache dir changed

    with pytest.raises(baseline.BaselineError, match="frozen"):
        baseline.capture(env, "4.0.0.3155")
    with pytest.raises(baseline.BaselineError, match="cache"):
        baseline.capture(env, "4.0.0.3156", dest=tmp_path / "elsewhere")

    (env.game_dir / "GameData" / "Thing.def").write_text('{"a": 2}', encoding="utf-8")
    code, report = diff.run(env, baseline_dir=dest, workers=2, no_api=True)
    assert code == 1
    assert report.parent == env.cache_dir / "baseline" / "reports" and report.name.startswith("4.0.0.3155-")
    assert not (dest / "reports").exists()  # a frozen baseline folder is never written into
    data = json.loads((report / "report.json").read_text(encoding="utf-8"))
    assert [c[1] for c in data["changed"]] == ["GameData/Thing.def"]
    assert sorted(str(p) for p in tmp_path.iterdir()) == outside
    with pytest.raises(baseline.BaselineError, match="cache"):
        diff.run(env, baseline_dir=dest, out=tmp_path / "reports", no_api=True)
    with pytest.raises(baseline.BaselineError, match="frozen"):
        diff.run(env, baseline_dir=dest, out=dest / "my-report", no_api=True)


def test_newest_baseline(env):
    for v in ("4.0.0.3151", "4.0.0.3155", "4.0.0.999"):
        d = env.cache_dir / "baseline" / v
        d.mkdir(parents=True)
        (d / "version-evidence.json").write_text("{}", encoding="utf-8")
    (env.cache_dir / "baseline" / "reports").mkdir()
    assert diff.newest_baseline(env) == env.cache_dir / "baseline" / "4.0.0.3155"


def test_deps_cli_missing_game_is_one_sentence(run, tmp_path):
    code, _, err = run("deps", "check")
    assert code == 1 and "Traceback" not in err and "not found" in err


def test_explicit_rebaseline_path_refreshes_recorded_file_in_place(env, tmp_path, monkeypatch):
    repo = tmp_path / "mod repo"
    repo.mkdir()
    deps = repo / "our-game-dependencies.json"
    deps.write_text(json.dumps(_doc(b"old content")), encoding="utf-8")
    monkeypatch.chdir(repo)
    check.run(env, deps_file=deps, workspace=tmp_path, rebaseline=Path("our-game-dependencies.json"))
    new = json.loads(deps.read_text(encoding="utf-8"))  # relative to the cwd, not moved into the cache
    cur = (env.sdk_dir / "GameData/Vanilla/Assets/Blocks/Widget.def").read_bytes()
    assert new["dependencies"][0]["game_source"][0]["sha256"] == _sha(cur)
    assert new["baseline"]["decompiled"].startswith("WS:decompiled") and new["roots"]["DECOMPILED"] == "WS:decompiled"
    report = tmp_path / "reports" / "r.json"
    check.run(env, deps_file=deps, workspace=tmp_path, report_json=report)
    assert report.is_file()


def test_rebaseline_never_records_an_absolute_decompiled_path(env, tmp_path):
    deps = tmp_path / "deps.json"
    deps.write_text(json.dumps(_doc(b"x")), encoding="utf-8")
    other_ws = tmp_path / "ws elsewhere"
    other_ws.mkdir()
    check.run(env, deps_file=deps, workspace=other_ws, rebaseline=True)
    text = (env.cache_dir / "deps" / check.REBASELINE_NAME).read_text(encoding="utf-8")
    assert str(tmp_path / "decompiled") not in text and '"DECOMPILED": "DECOMPILED:"' in text


def test_default_deps_file_is_the_projects(env, tmp_path, monkeypatch, capsys):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "Alpha"\n', encoding="utf-8")
    (proj / "Alpha.vrgproj").write_text("{}", encoding="utf-8")
    (proj / "Source").mkdir()
    ref = proj / "docs" / "reference"
    ref.mkdir(parents=True)
    (ref / "our-game-dependencies.json").write_text(json.dumps(_doc(b"old content")), encoding="utf-8")
    monkeypatch.chdir(proj / "Source")
    assert check.default_deps_file() == ref / "our-game-dependencies.json"
    assert check.run(env, workspace=tmp_path) == 1  # the project's file was checked
    assert "file:widget" in capsys.readouterr().out


def test_no_project_file_and_nothing_checked_is_an_error(env, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert check.default_deps_file() == check.BUNDLED
    assert check.run(env, workspace=tmp_path) == 3
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("warning: no dependencies checked") and "--json" in err[0]
