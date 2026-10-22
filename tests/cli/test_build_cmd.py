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

    def fake_build(env, mod, *, dry_run=False, verbose=False, assume_game_closed=False):
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

    def boom(env, mod, *, dry_run=False, verbose=False, assume_game_closed=False):
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
