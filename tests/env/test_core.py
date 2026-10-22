import dataclasses
import os
from pathlib import Path

import fake_sdk
import fake_steam
import pytest

from se2modkit.env.core import EnvError, SE2Env, discover_env, override_hint, require
from se2modkit.env.steam import GAME_APP_ID, SDK_APP_ID

SDK_NAME = "Space Engineers 2 - Mod SDK"
FIELDS = [f.name for f in dataclasses.fields(SE2Env) if f.name != "sources"]


def _linux_home(tmp_path: Path, *, with_prefix=True, with_pulsar=True) -> Path:
    """A synthetic home: Steam with game + SDK in different libraries, a Proton tool, prefix and Pulsar."""
    home = tmp_path / "home"
    root = home / ".steam" / "steam"
    lib2 = tmp_path / "Second Library"
    fake_steam.make(root, libraries=[lib2], apps={
        GAME_APP_ID: {"installdir": "SpaceEngineers2", "buildid": 111},
        SDK_APP_ID: {"library": lib2, "installdir": SDK_NAME, "buildid": 26049505},
    })
    fake_sdk.make_game(root / "steamapps/common/SpaceEngineers2")
    fake_sdk.make_sdk(lib2 / "steamapps/common" / SDK_NAME)
    tool = root / "compatibilitytools.d" / "SomeProton-1"
    tool.mkdir(parents=True)
    (tool / "proton").write_text("", encoding="utf-8")
    if with_prefix:
        dd = root / "steamapps/compatdata/1133870/pfx/dosdevices"
        dd.mkdir(parents=True)
        os.symlink("/", dd / "z:")
    if with_pulsar:
        (home / ".config/Pulsar").mkdir(parents=True)
        (home / ".config/Pulsar/Modern.bin").write_text("", encoding="utf-8")
    return home


def _discover(tmp_path, **kw):
    kw.setdefault("environ", {})
    kw.setdefault("platform", "linux")
    kw.setdefault("sysroot", tmp_path / "sys")
    kw.setdefault("config", {})
    return discover_env(**kw)


def test_env_all_missing_has_sources(tmp_path):
    env = _discover(tmp_path, home=tmp_path)
    always = {"platform", "cache_dir", "config_dir"}
    for name in FIELDS:
        if name in always:
            assert getattr(env, name) is not None
            continue
        assert getattr(env, name) in (None, ()), name
        assert env.sources[name] == "missing", name
    assert set(env.sources) == set(FIELDS)
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.sdk_dir = tmp_path  # type: ignore[misc]


def test_env_discovers_linux_tree(tmp_path):
    home = _linux_home(tmp_path)
    env = _discover(tmp_path, home=home)
    root = (home / ".steam/steam").resolve()
    assert env.steam_root == root and env.sources["steam_root"] == "discovered"
    assert env.game_dir == root / "steamapps/common/SpaceEngineers2"
    assert env.sdk_dir == (tmp_path / "Second Library/steamapps/common" / SDK_NAME)
    assert env.sdk_editor == env.sdk_dir / "Editor"
    assert env.sdk_assets == env.sdk_dir / "GameData/Vanilla/Assets"
    assert env.prefix == root / "steamapps/compatdata/1133870/pfx"
    assert env.game_mods_dir == env.prefix / "drive_c/users/steamuser/Documents/SpaceEngineers2/Mods"
    assert env.proton == root / "compatibilitytools.d/SomeProton-1"
    assert env.pulsar == home / ".config/Pulsar/Modern.bin"
    assert env.game_version == "4.0.0.3155"
    assert env.game_buildid == "111" and env.sdk_buildid == "26049505"
    assert env.decompiled_dir is None and env.sources["decompiled_dir"] == "missing"
    assert env.cache_dir == home / ".cache/se2modkit"
    assert env.config_dir == home / ".config/se2modkit"


def test_env_flag_beats_env(tmp_path):
    home = _linux_home(tmp_path)
    flag_sdk = fake_sdk.make_sdk(tmp_path / "flag sdk")
    env_sdk = fake_sdk.make_sdk(tmp_path / "env sdk")
    env = _discover(tmp_path, home=home, flags={"sdk": str(flag_sdk)},
                    environ={"SE2_SDK_ASSETS": str(env_sdk / "GameData/Vanilla/Assets")},
                    config={"sdk": str(tmp_path / "cfg")})
    assert env.sdk_dir == flag_sdk and env.sources["sdk_dir"] == "flag"
    assert env.sdk_editor == flag_sdk / "Editor"
    env2 = _discover(tmp_path, home=home, environ={"SE2_SDK_ASSETS": str(env_sdk / "GameData/Vanilla/Assets")},
                     config={"sdk": str(tmp_path / "cfg")})
    assert env2.sdk_dir == env_sdk and env2.sources["sdk_dir"] == "env"
    assert env2.sdk_assets == env_sdk / "GameData/Vanilla/Assets" and env2.sources["sdk_assets"] == "env"


def test_env_overrides_for_proton_pulsar_blender_decompiled(tmp_path):
    home = _linux_home(tmp_path)
    environ = {"SE2_PROTON": "Other-Proton", "SE2_PULSAR": str(tmp_path / "P/Modern.bin"),
               "SE2_BLENDER": "/b/blender", "SE2_DECOMPILED": str(tmp_path / "dec")}
    env = _discover(tmp_path, home=home, environ=environ)
    assert env.proton == env.steam_root / "compatibilitytools.d/Other-Proton"
    assert env.sources["proton"] == "env"
    assert env.pulsar == tmp_path / "P/Modern.bin" and env.sources["pulsar"] == "env"
    assert env.blender == Path("/b/blender") and env.sources["blender"] == "env"
    assert env.decompiled_dir == tmp_path / "dec" and env.sources["decompiled_dir"] == "env"
    env2 = _discover(tmp_path, home=home, environ=environ, flags={"proton": "Flag-Proton", "blender": None})
    assert env2.proton.name == "Flag-Proton" and env2.sources["proton"] == "flag"
    assert env2.sources["blender"] == "env"


def test_env_legacy_steamapps_variable(tmp_path):
    home = _linux_home(tmp_path)
    steamapps = home / ".steam/steam/steamapps"
    env = _discover(tmp_path, home=tmp_path / "empty", environ={"SE2_STEAMAPPS": str(steamapps)})
    assert env.steam_root == steamapps.parent and env.sources["steam_root"] == "env"
    assert env.game_dir == steamapps / "common/SpaceEngineers2"


def test_env_without_prefix_has_no_game_mods(tmp_path):
    env = _discover(tmp_path, home=_linux_home(tmp_path, with_prefix=False, with_pulsar=False))
    assert env.prefix is None and env.game_mods_dir is None and env.pulsar is None
    assert env.sources["game_mods_dir"] == "missing"
    assert env.proton is not None


def test_require_message_names_override(tmp_path):
    env = _discover(tmp_path, home=tmp_path)
    with pytest.raises(EnvError) as ei:
        require(env, "sdk_dir", "blender")
    msg = str(ei.value)
    assert "--sdk" in msg and "SE2_SDK_ASSETS" in msg
    assert "--blender" in msg and "SE2_BLENDER" in msg
    assert "\n" not in msg and "Traceback" not in msg
    assert override_hint("sdk_dir") == "--sdk, SE2_SDK_ASSETS or config sdk"


def test_require_passes_when_present(tmp_path):
    env = _discover(tmp_path, home=_linux_home(tmp_path))
    require(env, "sdk_dir", "game_dir", "proton", "prefix")


def test_require_unknown_field_is_programming_error(tmp_path):
    env = _discover(tmp_path, home=tmp_path)
    with pytest.raises(ValueError):
        require(env, "nonsense")


def test_windows_has_no_proton(tmp_path):
    profile = tmp_path / "Users" / "Jörg"
    pf86 = tmp_path / "Program Files (x86)"
    root = pf86 / "Steam"
    fake_steam.make(root, apps={GAME_APP_ID: {"installdir": "SpaceEngineers2"}})
    environ = {"USERPROFILE": str(profile), "ProgramFiles(x86)": str(pf86), "APPDATA": str(profile / "AppData/Roaming"),
               "LOCALAPPDATA": str(profile / "AppData/Local"), "SE2_PROTON": "ignored"}
    env = _discover(tmp_path, home=profile, platform="win32", environ=environ)
    assert env.steam_root == root.resolve()
    assert env.prefix is None and env.proton is None
    assert env.sources["prefix"] == "missing" and env.sources["proton"] == "missing"
    assert env.game_mods_dir == profile / "Documents" / "SpaceEngineers2" / "Mods"
    assert env.pulsar is None
    assert env.config_dir == profile / "AppData/Roaming/se2modkit"
    with pytest.raises(EnvError, match="only used on Linux"):
        require(env, "proton")


def test_windows_pulsar_from_config_only(tmp_path):
    cfg_pulsar = tmp_path / "Pulsar" / "Modern.exe"
    env = _discover(tmp_path, home=tmp_path, platform="win32", environ={}, config={"pulsar": str(cfg_pulsar)})
    assert env.pulsar == cfg_pulsar and env.sources["pulsar"] == "config"


def test_proton_module_not_imported_on_windows(tmp_path, monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "se2modkit.env.proton", raising=False)
    _discover(tmp_path, home=tmp_path, platform="win32")
    assert "se2modkit.env.proton" not in sys.modules
