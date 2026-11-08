"""`se2mod tex new | check | view` through the CLI, with the SDK index faked."""

from __future__ import annotations

import shutil

import pytest

from se2modkit import cli
from se2modkit.cli import tex_cmd
from se2modkit.tex import view


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def with_sdk(monkeypatch, ix, sdk):
    monkeypatch.setattr(tex_cmd, "_sdk_index", lambda env: ix)
    monkeypatch.setattr(tex_cmd.vanilla_textures, "vanilla_roots", lambda env: [sdk["vanilla"]])
    return sdk


def test_registered():
    assert "tex" in cli.commands()


def test_new_then_check(run, mod, with_sdk):
    code, out, err = run("tex", "new", "Hull", "--like", "Painted", "--mod", str(mod), "--size", "16")
    assert code == 0, err
    assert "Assets/Textures/Hull/Hull_normal.png" in out and '"ColorMetalTexture": "{G}' in out
    assert ".meta" not in out
    code, out, err = run("tex", "check", str(mod))
    assert code == 0, out + err
    assert "1 texture set(s): 0 error(s), 0 warning(s)" in out


def test_check_fails_on_a_copied_game_texture(run, mod, with_sdk):
    run("tex", "new", "Hull", "--like", "Painted", "--mod", str(mod), "--size", "16")
    shutil.copyfile(with_sdk["albedo"], mod / "Assets" / "Textures" / "Hull" / "Hull_albedo.png")
    code, out, _err = run("tex", "check", str(mod))
    assert code == 1 and "exact copy of the game texture" in out


def test_new_errors_are_one_line(run, mod, with_sdk):
    code, _out, err = run("tex", "new", "Hull", "--like", "Nope", "--mod", str(mod))
    assert code == 1 and err.count("\n") == 1 and "No vanilla material named 'Nope'" in err


def test_new_without_sdk(run, mod, monkeypatch):
    monkeypatch.setattr(tex_cmd, "_sdk_index", lambda env: None)
    code, _out, err = run("tex", "new", "Hull", "--like", "Painted", "--mod", str(mod))
    assert code == 1 and "Mod SDK not found" in err


def test_view_no_serve(run, mod, with_sdk, tmp_path, monkeypatch):
    run("tex", "new", "Hull", "--like", "Painted", "--mod", str(mod), "--size", "16")
    monkeypatch.setattr(view, "serve", lambda *a, **k: pytest.fail("served with --no-serve"))
    code, out, err = run("tex", "view", str(mod / "Assets" / "Textures" / "Hull"), "--out", str(tmp_path / "v"),
                         "--no-serve")
    assert code == 0, err
    assert "Local-only preview" in out and (tmp_path / "v" / "index.html").is_file()


def test_view_serves(run, mod, with_sdk, tmp_path, monkeypatch):
    run("tex", "new", "Hull", "--like", "Painted", "--mod", str(mod), "--size", "16")
    seen = {}
    monkeypatch.setattr(view, "serve", lambda folder, port, **k: seen.update(folder=folder, port=port))
    code, _out, _err = run("tex", "view", str(mod / "Assets" / "Textures" / "Hull"), "--out", str(tmp_path / "v"),
                           "--port", "8123")
    assert code == 0 and seen == {"folder": tmp_path / "v", "port": 8123}


def test_view_refuses_a_game_folder_file(run, with_sdk, tmp_path):
    code, _out, err = run("tex", "view", str(with_sdk["albedo"].parent), "--no-serve", "--out", str(tmp_path / "v"))
    assert code == 1 and "game or Mod SDK folder" in err


def test_view_rejects_other_files(run, tmp_path):
    (tmp_path / "x.fbx").write_bytes(b"")
    code, _out, err = run("tex", "view", str(tmp_path / "x.fbx"))
    assert code == 1 and "neither a .glb" in err
