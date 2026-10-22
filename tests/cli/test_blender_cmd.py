"""`se2mod blender run`: the Blender argv puts only the se2modkit package parent and the mod source on sys.path."""

import ast
from pathlib import Path

import pytest
from fake_env import make_env, make_mod

import se2modkit
from se2modkit.cli import blender_cmd
from se2modkit.env.core import EnvError

PKG_PARENT = Path(se2modkit.__file__).resolve().parent.parent


def _expr_paths(argv: list[str]) -> list[str]:
    expr = argv[argv.index("--python-expr") + 1]
    tree = ast.parse(expr)
    lists = [n for n in ast.walk(tree) if isinstance(n, ast.List)]
    assert len(lists) == 1
    return [ast.literal_eval(e) for e in lists[0].elts]


def test_blender_argv_prepends_only_package_parent(tmp_path):
    blender = tmp_path / "Blender 4.2" / "blender"
    env = make_env(tmp_path, blender=blender)
    script = tmp_path / "build model.py"
    src = tmp_path / "Mod" / "Source" / "Model"
    argv = blender_cmd.blender_argv(env, script, ["--mod", "Jörg"], mod_source=src, windowed=False)
    assert argv[:2] == [str(blender), "-b"]
    assert argv[-5:] == ["--python", str(script), "--", "--mod", "Jörg"]
    assert argv[argv.index("--python-exit-code") + 1] == "1"                    # a failed script fails the shell
    assert argv.index("--python-exit-code") < argv.index("--python")
    paths = _expr_paths(argv)
    if PKG_PARENT.name in ("site-packages", "dist-packages"):  # an installed (non-editable) se2modkit
        assert paths[0] == str(env.cache_dir / "blender-path")
    else:
        assert paths[0] == str(PKG_PARENT)
    assert paths == [paths[0], str(src)]
    assert not any("site-packages" in p for p in paths)
    windowed = blender_cmd.blender_argv(env, script, [], mod_source=src, windowed=True)
    assert "-b" not in windowed and windowed[0] == str(blender)


def test_installed_package_gets_a_shim(tmp_path, monkeypatch):
    site = tmp_path / "venv" / "lib" / "site-packages"
    (site / "se2modkit").mkdir(parents=True)
    (site / "numpy").mkdir()
    (site / "se2modkit" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(blender_cmd, "_package_dir", lambda: site / "se2modkit")
    env = make_env(tmp_path, blender=tmp_path / "blender")
    argv = blender_cmd.blender_argv(env, tmp_path / "s.py", [], mod_source=tmp_path / "src", windowed=False)
    shim = Path(_expr_paths(argv)[0])
    assert shim == env.cache_dir / "blender-path"
    assert sorted(p.name for p in shim.iterdir()) == ["se2modkit"]
    assert (shim / "se2modkit" / "__init__.py").is_file()


def test_blender_missing_is_one_sentence(tmp_path):
    env = make_env(tmp_path)
    with pytest.raises(EnvError, match="Blender not found"):
        blender_cmd.blender_argv(env, tmp_path / "s.py", [], mod_source=tmp_path, windowed=False)


def test_blender_run_cli(tmp_path, monkeypatch):
    import contextlib
    import io

    from se2modkit import cli
    env = make_env(tmp_path, blender=tmp_path / "blender")
    mod = make_mod(tmp_path / "mods", "Alpha")
    monkeypatch.setattr("se2modkit.cli.discover_env", lambda **_: env)
    seen = []
    monkeypatch.setattr(blender_cmd, "_run", lambda argv, env=None: seen.append(argv) or 0)
    script = tmp_path / "s.py"
    with contextlib.redirect_stdout(io.StringIO()):
        code = cli.main(["blender", "run", str(script), "--mod", str(mod), "--", "--fast", "x"])
    assert code == 0
    argv = seen[0]
    assert argv[-3:] == ["--", "--fast", "x"]
    assert _expr_paths(argv)[1] == str(mod / "Source" / "Model")


def test_multi_mod_project_without_mod_asks_which(tmp_path, monkeypatch):
    import contextlib
    import io

    from se2modkit import cli
    env = make_env(tmp_path, blender=tmp_path / "blender")
    repo = tmp_path / "repo"
    for name in ("Alpha", "Beta"):
        make_mod(repo / "mods", name)
    (repo / "pyproject.toml").write_text('[tool.se2modkit.mods.Alpha]\npath = "mods/Alpha"\n'
                                         '[tool.se2modkit.mods.Beta]\npath = "mods/Beta"\n', encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.setattr("se2modkit.cli.discover_env", lambda **_: env)
    monkeypatch.setattr(blender_cmd, "_run", lambda argv, env=None: pytest.fail("Blender must not run"))
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = cli.main(["blender", "run", str(tmp_path / "s.py")])
    assert code == 1 and "Which mod?" in err.getvalue() and "Alpha, Beta" in err.getvalue()
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = cli.main(["blender", "run", "--print-only", "--mod", "Beta", str(tmp_path / "s.py")])
    assert code == 0 and str(repo / "mods" / "Beta" / "Source" / "Model") in out.getvalue()


def test_build_env_carries_game_and_sdk_versions(tmp_path):
    from fake_env import make_env
    env = make_env(tmp_path)
    child = blender_cmd.build_env(env, {"KEEP": "1"})
    assert child["KEEP"] == "1"
    assert child["SE2MODKIT_GAME_VERSION"] == str(env.game_version or "none")
    assert child["SE2MODKIT_SDK_BUILDID"] == str(env.sdk_buildid or "none")


def test_blender_argv_installs_the_read_hook_before_the_script(tmp_path):
    from fake_env import make_env
    env = make_env(tmp_path, blender=tmp_path / "blender")
    argv = blender_cmd.blender_argv(env, tmp_path / "build.py", [], mod_source=tmp_path, windowed=False)
    expr = argv[argv.index("--python-expr") + 1]
    assert expr.endswith("import se2modkit.pipeline")
    assert argv.index("--python-expr") < argv.index("--python")


def test_scaffold_build_script_imports_the_pipeline_first():
    from pathlib import Path

    import se2modkit
    text = (Path(se2modkit.__file__).parent / "scaffold" / "files" / "build.py.tmpl").read_text(encoding="utf-8")
    assert text.index("import se2modkit.pipeline") < text.index("import catalog")
