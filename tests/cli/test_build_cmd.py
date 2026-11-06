"""`se2mod build`: the CLI wrapper around `sdkbuild.build`."""

import json

import pytest

from se2modkit import cli, sdkbuild
from se2modkit.env import procs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _mod(tmp_path):
    d = tmp_path / "mods" / "My Mod"
    d.mkdir(parents=True)
    (d / "My Mod.vrgproj").write_text(json.dumps({"$Value": {}}), encoding="utf-8")
    return d


def test_build_registered():
    assert "build" in cli.commands()


def test_build_refuses_while_game_runs(run, tmp_path, monkeypatch):
    d = _mod(tmp_path)
    monkeypatch.setattr(procs, "game_running", lambda **_: True)
    code, out, err = run("build", str(d))
    assert code == 4
    lines = [x for x in err.splitlines() if not x.startswith("warning:")]  # the unknown-version warning
    assert len(lines) == 1 and "running" in lines[0]


def test_build_without_sdk_is_one_sentence(run, tmp_path, monkeypatch):
    d = _mod(tmp_path)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    code, out, err = run("build", str(d))
    assert code == 1
    assert "Traceback" not in err and "Mod SDK" in err and "set --sdk" in err


def test_build_passes_flags(run, tmp_path, monkeypatch):
    d = _mod(tmp_path)
    seen = {}

    def fake_build(env, mod, *, dry_run=False, verbose=False, assume_game_closed=False, lock_timeout=None):
        seen.update(mod=mod, dry_run=dry_run, verbose=verbose)
        return 0

    monkeypatch.setattr(sdkbuild, "build", fake_build)
    code, _, _ = run("build", str(d), "--dry-run", "-v")
    assert code == 0
    assert seen == {"mod": d, "dry_run": True, "verbose": True}


def test_unknown_mod_is_one_sentence(run, tmp_path):
    code, _, err = run("build", "NoSuchMod")
    assert code == 1 and "Mod 'NoSuchMod' not found" in err


def test_unreadable_def_is_one_sentence(run, tmp_path, monkeypatch):
    d = _mod(tmp_path)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)

    def boom(env, mod, *, dry_run=False, verbose=False, assume_game_closed=False, lock_timeout=None):
        raise sdkbuild.ModError("Cannot read Assets/x.def as JSON (bad); fix it, then build again.")

    monkeypatch.setattr(sdkbuild, "build", boom)
    code, _, err = run("build", str(d))
    assert code == 1 and "Traceback" not in err and "Cannot read Assets/x.def" in err


def test_build_refuses_when_process_list_unavailable(run, tmp_path, monkeypatch):
    d = _mod(tmp_path)
    monkeypatch.setattr(procs, "listing_available", lambda **_: False)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    code, _, err = run("build", str(d))
    assert code == 4
    lines = [x for x in err.splitlines() if not x.startswith("warning:")]
    assert len(lines) == 1 and "--assume-game-closed" in lines[0]


def test_build_assume_game_closed_overrides(run, tmp_path, monkeypatch):
    d = _mod(tmp_path)
    monkeypatch.setattr(procs, "listing_available", lambda **_: False)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    code, _, err = run("build", str(d), "--assume-game-closed")
    assert code == 1 and "Mod SDK" in err  # got past the guard to the missing SDK


def test_build_refuses_a_copied_game_texture_before_the_builder(run, tmp_path, monkeypatch):
    from se2modkit.validate import vanilla_textures

    d = _mod(tmp_path)
    vanilla = tmp_path / "vanilla"
    (vanilla / "BlockMaterials").mkdir(parents=True)
    (vanilla / "BlockMaterials" / "Steel_cm.dds").write_bytes(b"D" * 4096)
    (d / "Assets").mkdir()
    (d / "Assets" / "Panel.dds").write_bytes(b"D" * 4096)
    monkeypatch.setattr(vanilla_textures, "vanilla_roots", lambda env: [vanilla])
    monkeypatch.setattr(sdkbuild, "build", lambda *a, **k: pytest.fail("the builder must not run"))
    code, _, err = run("build", str(d))
    assert code == sdkbuild.EXIT_VANILLA_TEXTURE
    assert "Assets/Panel.dds" in err and "Steel_cm.dds" in err


def test_build_warns_when_no_game_textures_can_be_checked(run, tmp_path, monkeypatch):
    from se2modkit.validate import vanilla_textures

    d = _mod(tmp_path)
    monkeypatch.setattr(vanilla_textures, "vanilla_roots", lambda env: [])
    monkeypatch.setattr(sdkbuild, "build", lambda *a, **k: 0)
    code, _, err = run("build", str(d))
    assert code == 0 and "were not checked" in err
