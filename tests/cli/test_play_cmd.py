"""`se2mod play`: the Steam and Pulsar launch argv on Linux and Windows, and the refusals of the legacy script."""

import os
from pathlib import Path

import pytest
from fake_env import make_env, make_mod

from se2modkit import cli
from se2modkit.cli import play_cmd
from se2modkit.env import procs
from se2modkit.env.core import EnvError

MODS_WIN = r"C:\users\steamuser\Documents\SpaceEngineers2\Mods"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE", "SE2_PROCESS_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _attached(tmp_path, platform="linux", **changes):
    env = make_env(tmp_path, platform, **changes)
    root = tmp_path / "mods root"
    root.mkdir()
    os.symlink(root, env.game_mods_dir, target_is_directory=True)
    return env, root


def test_steam_argv_linux_matches_legacy(tmp_path):
    env, root = _attached(tmp_path)
    a = make_mod(root, "Alpha", scripts=True)
    b = make_mod(root, "Beta")
    argv = play_cmd.steam_argv(env, [a, b], scripts=True)
    # Literal of the legacy `se2mod.py play Alpha Beta` argv (cmd_play): projects through the prefix's Mods folder,
    # global dirs through the S: drive the game library is mapped to.
    assert argv == [
        "steam", "-applaunch", "1133870", "-startupTag:HUB",
        rf"-projectPaths:{MODS_WIN}\Alpha\Alpha.vrgproj;{MODS_WIN}\Beta\Beta.vrgproj",
        r"-globalProjectDirs:S:\steamapps\common\SpaceEngineers2\GameData;"
        r"S:\steamapps\common\SpaceEngineers2\VRage\GameData",
        "-loadScripts",
    ]
    assert play_cmd.steam_argv(env, [], scripts=False) == ["steam", "-applaunch", "1133870", "-startupTag:HUB"]


def test_steam_argv_mod_outside_mount_uses_its_own_wine_path(tmp_path):
    env, root = _attached(tmp_path)
    other = make_mod(tmp_path / "elsewhere", "Gamma")
    argv = play_cmd.steam_argv(env, [other], scripts=False)
    assert argv[4] == "-projectPaths:Z:" + str((other / "Gamma.vrgproj").resolve()).replace("/", "\\")


def test_steam_argv_windows_native_paths(tmp_path):
    env, root = _attached(tmp_path, "win32")
    a = make_mod(root, "Jörg Mod")
    argv = play_cmd.steam_argv(env, [a], scripts=False)
    assert argv == [
        str(env.steam_root / "steam.exe"), "-applaunch", "1133870", "-startupTag:HUB",
        "-projectPaths:" + str(env.game_mods_dir / "Jörg Mod" / "Jörg Mod.vrgproj"),
        "-globalProjectDirs:" + ";".join(str(env.game_dir / p) for p in ("GameData", Path("VRage", "GameData"))),
    ]


def test_steam_refuses_when_detached(tmp_path):
    env = make_env(tmp_path)
    a = make_mod(tmp_path / "mods root", "Alpha")
    with pytest.raises(play_cmd.ModError, match="detached"):
        play_cmd.steam_argv(env, [a], scripts=False)


def test_pulsar_argv_linux(tmp_path):
    pulsar = tmp_path / "Pulsar dir" / "Modern.bin"
    pulsar.parent.mkdir()
    pulsar.write_text("", encoding="utf-8")
    env = make_env(tmp_path, pulsar=pulsar)
    a = make_mod(tmp_path / "mods root", "Alpha", scripts=True)
    argv, cwd = play_cmd.pulsar_argv(env, [a], scripts=True)
    assert cwd == pulsar.parent
    assert argv == [
        str(pulsar), "-startupTag:HUB", "-projectPaths:" + str(a / "Alpha.vrgproj"),
        "-globalProjectDirs:" + str(env.game_dir / "GameData") + ";" + str(env.game_dir / "VRage" / "GameData"),
        "-loadScripts",
    ]


def test_pulsar_argv_windows_from_config(tmp_path):
    pulsar_dir = tmp_path / "Program Files" / "Pulsar"
    pulsar_dir.mkdir(parents=True)
    (pulsar_dir / "Modern.exe").write_text("", encoding="utf-8")
    env = make_env(tmp_path, "win32", pulsar=pulsar_dir)  # config `pulsar` may name the folder
    argv, cwd = play_cmd.pulsar_argv(env, [], scripts=False)
    assert argv == [str(pulsar_dir / "Modern.exe"), "-startupTag:HUB"] and cwd == pulsar_dir


def test_pulsar_missing_message(tmp_path):
    env = make_env(tmp_path)
    with pytest.raises(EnvError) as e:
        play_cmd.pulsar_argv(env, [], scripts=False)
    assert str(e.value).startswith("Pulsar not found") and "SE2_PULSAR" in str(e.value)


def test_want_scripts(tmp_path):
    a = make_mod(tmp_path, "A", scripts=True)
    b = make_mod(tmp_path, "B")
    assert play_cmd.want_scripts([a, b], None) is True
    assert play_cmd.want_scripts([b], None) is False
    assert play_cmd.want_scripts([a], False) is False
    assert play_cmd.want_scripts([b], True) is True


def test_launch_options(tmp_path):
    env = make_env(tmp_path)
    cfg = env.steam_root / "userdata" / "123" / "config" / "localconfig.vdf"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('"UserLocalConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
                   '\t\t\t\t"apps"\n\t\t\t\t{\n\t\t\t\t\t"1133870"\n\t\t\t\t\t{\n'
                   '\t\t\t\t\t\t"LaunchOptions"\t\t"-loadScripts %command%"\n'
                   '\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n', encoding="utf-8")
    other = env.steam_root / "userdata" / "456" / "config" / "localconfig.vdf"
    other.parent.mkdir(parents=True)
    other.write_text('"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "1133870" { } } } } } }',
                     encoding="utf-8")
    assert sorted(play_cmd.launch_options(env)) == ["", "-loadScripts %command%"]


def _launch_spy(monkeypatch):
    seen = []
    monkeypatch.setattr(play_cmd, "_launch", lambda argv, cwd=None: seen.append((argv, cwd)))
    return seen


def test_play_refuses_when_running(run, tmp_path, monkeypatch):
    pulsar = tmp_path / "Modern.bin"
    pulsar.write_text("", encoding="utf-8")
    seen = _launch_spy(monkeypatch)
    monkeypatch.setattr(procs, "game_running", lambda **_: True)
    code, out, err = run("play", "--native", environ={"SE2_PULSAR": str(pulsar)})
    assert code == 4 and not seen and out == ""  # no launch command printed for a launch that does not happen
    assert "native SE2 (Pulsar) is already running; quit it first, mods are only read at startup" in err
    code, out, _ = run("play", "--native", "--print-only", environ={"SE2_PULSAR": str(pulsar)})
    assert code == 0 and out.strip() == f"{pulsar} -startupTag:HUB" and not seen


def test_play_print_only_launches_nothing(run, tmp_path, monkeypatch):
    pulsar = tmp_path / "Modern.bin"
    pulsar.write_text("", encoding="utf-8")
    seen = _launch_spy(monkeypatch)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    code, out, err = run("play", "--native", "--print-only", "--no-scripts", environ={"SE2_PULSAR": str(pulsar)})
    assert code == 0 and not seen
    assert out.strip() == f"{pulsar} -startupTag:HUB"


def test_play_native_launches_detached(run, tmp_path, monkeypatch):
    pulsar = tmp_path / "Modern.bin"
    pulsar.write_text("", encoding="utf-8")
    seen = _launch_spy(monkeypatch)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    code, _, _ = run("play", "--native", environ={"SE2_PULSAR": str(pulsar)})
    assert code == 0 and seen == [([str(pulsar), "-startupTag:HUB"], tmp_path)]


def test_play_registered_with_every_legacy_command():
    assert {"new", "override", "show", "build", "play", "list", "copy", "off", "on", "status"} <= set(cli.commands())
