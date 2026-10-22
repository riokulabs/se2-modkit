"""`se2mod off | on | status`: mount the workspace mods folder as the game's Mods folder (symlink or junction)."""

import sys

import pytest

from se2modkit.cli import mount_cmd
from se2modkit.env import links


@pytest.fixture
def dirs(tmp_path):
    game_mods = tmp_path / "pfx" / "Documents" / "SpaceEngineers2" / "Mods"
    mods_root = tmp_path / "work space" / "mods"
    mods_root.mkdir(parents=True)
    (mods_root / "Alpha").mkdir()
    return game_mods, mods_root


def test_attach_symlink_linux(dirs):
    game_mods, mods_root = dirs
    mount_cmd.attach(game_mods, mods_root, platform="linux")
    assert game_mods.is_symlink() and game_mods.resolve() == mods_root.resolve()
    assert (game_mods / "Alpha").is_dir()
    assert mount_cmd.status(game_mods, mods_root, platform="linux")["attached"] is True
    mount_cmd.attach(game_mods, mods_root, platform="linux")  # again: already attached, no error
    mount_cmd.detach(game_mods, platform="linux")
    assert game_mods.is_dir() and not game_mods.is_symlink() and not any(game_mods.iterdir())
    assert (mods_root / "Alpha").is_dir()  # the target is never touched
    st = mount_cmd.status(game_mods, mods_root, platform="linux")
    assert st["attached"] is False and st["link"] is False
    mount_cmd.attach(game_mods, mods_root, platform="linux")  # an empty real folder is replaced
    assert game_mods.is_symlink()


def test_attach_refuses_nonempty_real_folder(dirs):
    game_mods, mods_root = dirs
    game_mods.mkdir(parents=True)
    (game_mods / "Workshop copy").mkdir()
    with pytest.raises(mount_cmd.ModError, match="real folder with files in it"):
        mount_cmd.attach(game_mods, mods_root, platform="linux")
    assert (game_mods / "Workshop copy").is_dir() and not game_mods.is_symlink()


def test_attach_refuses_link_elsewhere(dirs, tmp_path):
    game_mods, mods_root = dirs
    other = tmp_path / "other"
    other.mkdir()
    game_mods.parent.mkdir(parents=True)
    game_mods.symlink_to(other, target_is_directory=True)
    with pytest.raises(mount_cmd.ModError, match="se2mod off"):
        mount_cmd.attach(game_mods, mods_root, platform="linux")


def test_detach_never_deletes_a_real_folder(dirs):
    game_mods, mods_root = dirs
    game_mods.mkdir(parents=True)
    (game_mods / "keep.txt").write_text("x", encoding="utf-8")
    mount_cmd.detach(game_mods, platform="linux")
    assert (game_mods / "keep.txt").is_file()


def test_off_on_status_cli(run, tmp_path, monkeypatch):
    from fake_env import make_env

    from se2modkit.env import procs
    env = make_env(tmp_path)
    mods_root = tmp_path / "mods root"
    mods_root.mkdir()
    monkeypatch.setattr("se2modkit.cli.discover_env", lambda **_: env)
    monkeypatch.setattr(mount_cmd, "mods_root", lambda _env: mods_root)
    monkeypatch.setattr(procs, "find_processes", lambda names, **_: [])
    code, out, _ = mount_cmd_run("on")
    assert code == 0 and "ATTACHED" in out and env.game_mods_dir.is_symlink()
    code, out, _ = mount_cmd_run("status")
    assert code == 0 and "ATTACHED" in out and "running: no" in out
    code, out, _ = mount_cmd_run("off")
    assert code == 0 and "detached" in out and not env.game_mods_dir.is_symlink()


def _mount_env(tmp_path, monkeypatch):
    from fake_env import make_env

    from se2modkit.env import procs
    env = make_env(tmp_path)
    mods_root = tmp_path / "mods root"
    mods_root.mkdir()
    monkeypatch.setattr("se2modkit.cli.discover_env", lambda **_: env)
    monkeypatch.setattr(mount_cmd, "mods_root", lambda _env: mods_root)
    monkeypatch.setattr(procs, "find_processes", lambda names, **_: [])
    monkeypatch.setattr(procs, "listing_available", lambda **_: False)
    return env


@pytest.mark.parametrize("verb", ["on", "off"])
def test_on_off_refuse_when_process_list_unavailable(verb, tmp_path, monkeypatch):
    env = _mount_env(tmp_path, monkeypatch)
    code, out, err = mount_cmd_run(verb)
    assert code == 1 and "--assume-game-closed" in err and len(err.strip().splitlines()) == 1
    assert not env.game_mods_dir.exists()


def test_on_assume_game_closed_overrides(tmp_path, monkeypatch):
    env = _mount_env(tmp_path, monkeypatch)
    code, out, err = mount_cmd_run("on", "--assume-game-closed")
    assert code == 0 and env.game_mods_dir.is_symlink()


def test_status_without_workspace_folder_reports_the_link(tmp_path, monkeypatch):
    from fake_env import make_env

    from se2modkit.env import procs
    env = make_env(tmp_path)
    target = tmp_path / "somewhere"
    target.mkdir()
    env.game_mods_dir.symlink_to(target, target_is_directory=True)
    monkeypatch.delenv("SE2_WORKSPACE", raising=False)
    monkeypatch.setattr("se2modkit.cli.discover_env", lambda **_: env)
    monkeypatch.setattr(procs, "find_processes", lambda names, **_: [])
    code, out, err = mount_cmd_run("status")
    assert code == 0, err
    assert f"local mods: linked to {target}" in out and "workspace_mods" in out


def mount_cmd_run(*argv):
    import contextlib
    import io

    from se2modkit import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_attach_junction_windows(dirs):
    import os
    game_mods, mods_root = dirs
    mount_cmd.attach(game_mods, mods_root, platform="win32")
    assert links.is_link(game_mods)
    assert os.path.realpath(game_mods) == os.path.realpath(mods_root)
    assert (game_mods / "Alpha").is_dir()
    mount_cmd.detach(game_mods, platform="win32")
    assert game_mods.is_dir() and not links.is_link(game_mods) and (mods_root / "Alpha").is_dir()
