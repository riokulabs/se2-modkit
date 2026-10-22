import dataclasses
import os

import fake_sdk
import fake_steam
import pytest

from se2modkit.cli import env_cmd
from se2modkit.env.core import SE2Env
from se2modkit.env.steam import GAME_APP_ID, SDK_APP_ID

SDK_NAME = "Space Engineers 2 - Mod SDK"
FIELDS = [f.name for f in dataclasses.fields(SE2Env) if f.name != "sources"]


def _tree(tmp_path, version="4.0.0.3155"):
    root = tmp_path / "home" / ".steam" / "steam"
    fake_steam.make(root, apps={GAME_APP_ID: {"installdir": "SpaceEngineers2", "buildid": 5},
                                SDK_APP_ID: {"installdir": SDK_NAME, "buildid": 26083450}})
    fake_sdk.make_game(root / "steamapps/common/SpaceEngineers2", version)
    fake_sdk.make_sdk(root / "steamapps/common" / SDK_NAME, version)
    return root


def _lines(out):
    return {ln.split(":", 1)[0]: ln for ln in out.splitlines() if ":" in ln}


def test_env_prints_every_field_with_source(tmp_path, run):
    root = _tree(tmp_path)
    code, out, err = run("env", "--steam-root", str(root))
    assert code == 0
    lines = _lines(out)
    for name in FIELDS:
        assert name in lines, name
    assert f"{root.resolve()} [flag]" in lines["steam_root"]
    assert "[discovered]" in lines["game_dir"]
    assert lines["game_version"].endswith("4.0.0.3155 [discovered]")
    assert err == ""


def test_env_reports_missing_sdk(tmp_path, run):
    code, out, _ = run("env")
    assert code == 0
    assert "sdk_dir: missing (set --sdk, SE2_SDK_ASSETS or config sdk)" in out.splitlines()


def test_env_shows_override_that_does_not_exist(tmp_path, run):
    code, out, _ = run("env", "--sdk", str(tmp_path / "nope"))
    assert code == 0
    assert f"sdk_dir: {tmp_path / 'nope'} [flag] (does not exist)" in out.splitlines()


def test_env_untested_game_warns(tmp_path, run):
    root = _tree(tmp_path, version="4.0.0.3160")
    code, out, err = run("env", "--steam-root", str(root))
    assert code == 0 and "4.0.0.3160" in err and "se2mod deps check" in err
    code, out, err = run("env", "--steam-root", str(root), "--strict-game")
    assert code == 2 and "4.0.0.3160" in err


def test_env_shows_modkit_install_kind(tmp_path, run, monkeypatch):
    for kind, text in (("editable", '{"url": "file:///x", "dir_info": {"editable": true}}'),
                       ("installed", '{"url": "file:///x", "dir_info": {}}'),
                       ("installed", None)):
        class Dist:
            def read_text(self, name, _t=text):
                return _t

        monkeypatch.setattr(env_cmd.metadata, "distribution", lambda _n: Dist())
        code, out, _ = run("env")
        first = out.splitlines()[0]
        assert first.startswith("modkit: ") and first.endswith(f"({kind})"), first


def test_env_names_unresolved_proton_mapping(tmp_path, run):
    root = _tree(tmp_path)
    cfg = root / "config"
    cfg.mkdir()
    (cfg / "config.vdf").write_text(
        '"InstallConfigStore"\n{\n"Software"\n{\n"Valve"\n{\n"Steam"\n{\n"CompatToolMapping"\n{\n'
        f'"{GAME_APP_ID}"\n{{\n"name"\t"GE-Proton-Gone"\n}}\n}}\n}}\n}}\n}}\n}}\n', encoding="utf-8")
    code, out, _ = run("env", "--steam-root", str(root))
    assert code == 0
    assert any("GE-Proton-Gone" in ln and "could not be resolved" in ln for ln in out.splitlines())
