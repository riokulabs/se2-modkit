from pathlib import Path

import fake_sdk
import fake_steam

from se2modkit.env.steam import GAME_APP_ID, SDK_APP_ID


def test_doctor_lists_checks(tmp_path, run):
    code, out, _ = run("doctor")
    assert code == 0
    for label in ("Blender", "VRAGE Tools add-on", "Mod SDK", "Standin", "Python"):
        assert any(ln.startswith(f"{label}:") for ln in out.splitlines()), label
    assert "Mod SDK: missing (set --sdk, SE2_SDK_ASSETS or config sdk)" in out.splitlines()


def test_doctor_ok_with_tools(tmp_path, run, monkeypatch):
    # the add-on search looks in the user's Blender folders: point them at the synthetic home
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    root = tmp_path / "home" / ".steam" / "steam"
    fake_steam.make(root, apps={GAME_APP_ID: {"installdir": "G"}, SDK_APP_ID: {"installdir": "S"}})
    fake_sdk.make_game(root / "steamapps/common/G")
    fake_sdk.make_sdk(root / "steamapps/common/S")
    blender = tmp_path / "bin" / "blender"
    blender.parent.mkdir()
    blender.write_text("", encoding="utf-8")
    addon = tmp_path / "home" / ".config/blender/4.2/scripts/addons/vrage_tools"
    addon.mkdir(parents=True)
    code, out, _ = run("doctor", "--steam-root", str(root), "--blender", str(blender))
    lines = out.splitlines()
    assert code == 0
    assert any(ln.startswith("Blender: ok") for ln in lines)
    assert any(ln.startswith("VRAGE Tools add-on: ok") for ln in lines)
    assert any(ln.startswith("Mod SDK: ok") for ln in lines)
