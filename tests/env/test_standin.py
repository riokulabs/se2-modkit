"""`env.standin`: the SDK standin tree in the user cache, rebuilt when the game or SDK version changes."""

import dataclasses
import json

import pytest
from fake_env import make_env

from se2modkit.env import standin
from se2modkit.env.core import EnvError


def test_build_layout(tmp_path):
    env = make_env(tmp_path)
    dest = env.cache_dir / "standin"
    assert standin.build(env, dest) == dest
    assert (dest / "Game2").resolve() == (env.game_dir / "Game2").resolve()
    assert (dest / "SDK" / "Game2").resolve() == (env.game_dir / "Game2").resolve()
    assert (dest / "SDK" / "GameData" / "Vanilla").resolve() == (env.sdk_dir / "GameData" / "Vanilla").resolve()
    assert (dest / "SDK" / "GameData" / "Engine").resolve() == (env.sdk_dir / "GameData" / "Engine").resolve()
    meta = json.loads((dest / "standin.json").read_text(encoding="utf-8"))
    assert meta["game_version"] == "4.0.0.3155" and meta["game_buildid"] == "100" and meta["sdk_buildid"] == "200"
    assert standin.current(env, dest)


def test_standin_rebuilt_on_version_change(tmp_path):
    env = make_env(tmp_path)
    dest = env.cache_dir / "standin"
    standin.build(env, dest)
    newer = dataclasses.replace(env, game_version="4.0.0.3200", sdk_buildid="201")
    assert not standin.current(newer, dest)
    marker = env.sdk_dir / "GameData" / "Vanilla" / "keep.txt"
    marker.write_text("x", encoding="utf-8")
    standin.ensure(newer, dest)
    assert standin.current(newer, dest)
    meta = json.loads((dest / "standin.json").read_text(encoding="utf-8"))
    assert meta["game_version"] == "4.0.0.3200" and meta["sdk_buildid"] == "201"
    assert marker.is_file()  # rebuilding replaces links, never the SDK behind them


def test_standin_not_current_when_target_moves(tmp_path):
    env = make_env(tmp_path)
    dest = env.cache_dir / "standin"
    standin.build(env, dest)
    moved = tmp_path / "Other Library" / "SpaceEngineers2"
    (moved / "Game2").mkdir(parents=True)
    assert not standin.current(dataclasses.replace(env, game_dir=moved), dest)


def test_standin_needs_game_and_sdk(tmp_path):
    env = make_env(tmp_path, sdk_dir=None)
    with pytest.raises(EnvError, match="Mod SDK not found"):
        standin.build(env, env.cache_dir / "standin")


def test_standin_refuses_foreign_folder(tmp_path):
    env = make_env(tmp_path)
    dest = tmp_path / "some dir"
    (dest / "Game2").mkdir(parents=True)
    (dest / "Game2" / "real.txt").write_text("x", encoding="utf-8")
    with pytest.raises(EnvError, match="not a link"):
        standin.build(env, dest)
    assert (dest / "Game2" / "real.txt").is_file()


def test_sdk_standin_cli(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setattr("se2modkit.cli.discover_env", lambda **_: env)
    code, out, _ = run_cli("sdk", "standin")
    assert code == 0 and "standin" in out and standin.current(env, env.cache_dir / "standin")
    code, out, _ = run_cli("sdk", "standin")
    assert code == 0 and "up to date" in out


def run_cli(*argv):
    import contextlib
    import io

    from se2modkit import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def test_build_command_ensures_standin(tmp_path, monkeypatch):
    from fake_env import make_mod

    from se2modkit import sdkbuild
    env = make_env(tmp_path)
    mod = make_mod(tmp_path / "mods", "Alpha")
    monkeypatch.setattr("se2modkit.cli.discover_env", lambda **_: env)
    monkeypatch.setattr(sdkbuild, "build", lambda env, mod, **_: 0)
    code, out, _ = run_cli("build", str(mod))
    assert code == 0 and "SDK standin built" in out and standin.current(env, env.cache_dir / "standin")
    code, out, _ = run_cli("build", str(mod))
    assert code == 0 and "standin" not in out
