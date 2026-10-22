"""`sdkbuild`: SDK builder invocation, project patching and the guarantees of the legacy `se2mod.py build`."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import fake_sdk
import pytest

from se2modkit import sdkbuild
from se2modkit.env import procs
from se2modkit.env.core import SE2Env

# Synthetic GUIDs (hand-written, not from any game file).
G_A = "aaaaaaaa-0000-4000-8000-00000000000a"
G_B = "bbbbbbbb-0000-4000-8000-00000000000b"
T_BLOCK = "Game2:Keen.Game2.Test.BlockDefinitionObjectBuilder"
T_OTHER = "Game2:Keen.Game2.Test.OtherDefinitionObjectBuilder"
EXE = "VRage.ContentPipeline.Builder.exe"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("SE2_WORKSPACE", raising=False)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)


def _env(tmp_path: Path, platform: str = "linux") -> SE2Env:
    """Steam library, SDK and mods under folders with spaces; a Proton prefix whose Z: maps `/`."""
    lib = tmp_path / "Steam Library"
    game = lib / "steamapps" / "common" / "SpaceEngineers2"
    sdk = lib / "steamapps" / "common" / "Space Engineers 2 - Mod SDK"
    fake_sdk.make_game(game)
    fake_sdk.make_sdk(sdk)
    proton = tmp_path / "compat tools" / "Proton Test"
    wine = proton / "files" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_text("", encoding="utf-8")
    prefix = tmp_path / "compatdata" / "pfx"
    (prefix / "drive_c").mkdir(parents=True)
    (prefix / "dosdevices").mkdir()
    os.symlink("../drive_c", prefix / "dosdevices" / "c:")
    os.symlink("/", prefix / "dosdevices" / "z:")
    linux = platform.startswith("linux")
    return SE2Env(
        platform=platform, steam_root=lib, libraries=(lib,), game_dir=game, sdk_dir=sdk,
        sdk_assets=sdk / "GameData" / "Vanilla" / "Assets", sdk_editor=sdk / "Editor", game_mods_dir=None,
        prefix=prefix if linux else None, proton=proton if linux else None, pulsar=None, blender=None,
        logs_dir=None, decompiled_dir=None, cache_dir=tmp_path / "cache dir", config_dir=tmp_path / "config",
        game_version="4.0.0.3155", game_buildid=None, sdk_buildid=None,
    )


def _wine(p: Path) -> str:
    return "Z:" + str(p.resolve()).replace("/", "\\")


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _mod(tmp_path: Path, name: str = "Jörg Mod") -> Path:
    d = tmp_path / "mods dir" / name
    _write(d / f"{name}.vrgproj", {"$Bundles": {}, "$Type": "VRage:Keen.VRage.Core.Project.VRageProjectData",
                                    "$Value": {"Name": name, "ProjectDirectories": ["Existing"]}})
    return d


def _block_def(d: Path, rel: str = "Blocks/A.def", guid: str = G_A, typ: str = T_BLOCK, **members) -> Path:
    return _write(d / "Assets" / rel, {"$Bundles": {}, "$Type": typ, "$Value": {"Guid": guid, **members}})


def _output(d: Path, rel: str, typ: str = T_BLOCK, **members) -> Path:
    return _write(d / "Content" / rel, {"$Bundles": {}, "$Type": typ, "$Value": members})


def _age(p: Path, seconds: int = 100) -> None:
    st = p.stat()
    os.utime(p, (st.st_atime - seconds, st.st_mtime - seconds))


class FakeRun:
    """Records each call; `effect(call_index, argv)` may write outputs, raise, or return a return code."""

    def __init__(self, effect=None):
        self.calls: list[tuple[list[str], Path, dict]] = []
        self.effect = effect

    def __call__(self, argv, *, cwd, env):
        self.calls.append((list(argv), Path(cwd), dict(env)))
        rc = self.effect(len(self.calls) - 1, argv) if self.effect else 0
        return SimpleNamespace(returncode=rc or 0)


# --- invocation -------------------------------------------------------------------------------------------------


def test_invocation_linux(tmp_path):
    env, d = _env(tmp_path), _mod(tmp_path)
    proj = d / "Jörg Mod.vrgproj"
    argv, cwd, run_env = sdkbuild.builder_invocation(env, proj, dry_run=False, verbose=False)
    assert argv == [str(env.proton / "files" / "bin" / "wine"), EXE, "-p", _wine(proj)]
    assert cwd == env.sdk_editor
    assert run_env["WINEPREFIX"] == str(env.prefix)
    assert run_env["WINEDEBUG"] == "-all"
    argv, _, _ = sdkbuild.builder_invocation(env, proj, dry_run=True, verbose=True)
    assert argv[-2:] == ["--dryRun", "--verbose"]


def test_invocation_windows(tmp_path):
    env, d = _env(tmp_path, platform="win32"), _mod(tmp_path)
    proj = d / "Jörg Mod.vrgproj"
    argv, cwd, run_env = sdkbuild.builder_invocation(env, proj, dry_run=False, verbose=False)
    assert argv == [str(env.sdk_editor / EXE), "-p", str(proj)]
    assert cwd == env.sdk_editor
    assert "WINEPREFIX" not in run_env


def test_invocation_missing_wine_is_one_sentence(tmp_path):
    env, d = _env(tmp_path), _mod(tmp_path)
    (env.proton / "files" / "bin" / "wine").unlink()
    with pytest.raises(sdkbuild.EnvError, match=r"^Proton build has no Wine at .*; set --proton"):
        sdkbuild.builder_invocation(env, d / "Jörg Mod.vrgproj", dry_run=False, verbose=False)


# --- project patching -------------------------------------------------------------------------------------------


def test_project_paths_with_spaces(tmp_path):
    env, d = _env(tmp_path), _mod(tmp_path)
    standin = env.cache_dir / "standin" / "SDK" / "GameData"
    standin.mkdir(parents=True)
    proj = d / "Jörg Mod.vrgproj"
    original = proj.read_bytes()
    with sdkbuild.patched_project(proj, env):
        v = json.loads(proj.read_text(encoding="utf-8"))["$Value"]
        assert v["ProjectDirectories"] == ["Existing", _wine(standin)]
        game_bin = _wine(env.game_dir / "Game2")
        assert v["GameExecutableSearchPaths"] == [game_bin]
        assert v["ReferencedAssembliesSearchPaths"] == [game_bin]
        assert "\\Steam Library\\" in game_bin
        argv, _, _ = sdkbuild.builder_invocation(env, proj, dry_run=False, verbose=False)
        assert argv[3] == _wine(proj) and "Jörg Mod" in argv[3]  # one argv element, spaces and umlaut intact
    assert proj.read_bytes() == original


def test_project_patch_is_idempotent_and_native_on_windows(tmp_path):
    env, d = _env(tmp_path, platform="win32"), _mod(tmp_path)
    proj = d / "Jörg Mod.vrgproj"
    sdk_gamedata = str(env.sdk_dir / "GameData")
    game_bin = str(env.game_dir / "Game2")
    data = json.loads(proj.read_text(encoding="utf-8"))
    data["$Value"]["ProjectDirectories"].append(sdk_gamedata)
    data["$Value"]["GameExecutableSearchPaths"] = [game_bin]
    _write(proj, data)
    with sdkbuild.patched_project(proj, env):
        v = json.loads(proj.read_text(encoding="utf-8"))["$Value"]
        assert v["ProjectDirectories"] == ["Existing", sdk_gamedata]
        assert v["GameExecutableSearchPaths"] == [game_bin]
        assert v["ReferencedAssembliesSearchPaths"] == [game_bin]


def test_sdk_project_dirs_prefers_cache_standin_then_workspace(tmp_path, monkeypatch):
    env = _env(tmp_path)
    ws = tmp_path / "work space"
    (ws / "tools" / "standin" / "SDK" / "GameData").mkdir(parents=True)
    assert sdkbuild.sdk_project_dirs(env, environ={}) == [env.sdk_dir / "GameData"]
    assert sdkbuild.sdk_project_dirs(env, environ={"SE2_WORKSPACE": str(ws)}) == [
        ws / "tools" / "standin" / "SDK" / "GameData"]
    (env.cache_dir / "standin" / "SDK" / "GameData").mkdir(parents=True)
    assert sdkbuild.sdk_project_dirs(env, environ={"SE2_WORKSPACE": str(ws)}) == [
        env.cache_dir / "standin" / "SDK" / "GameData"]


def test_hidden_scripts_restores_leftover_and_on_error(tmp_path):
    d = _mod(tmp_path)
    (d / ".Scripts.hidden-for-build").mkdir()  # left over from an interrupted build
    (d / ".Scripts.hidden-for-build" / "A.cs").write_text("//", encoding="utf-8")
    with pytest.raises(KeyboardInterrupt), sdkbuild.hidden_scripts(d):
        assert not (d / "Scripts").exists()
        assert (d / ".Scripts.hidden-for-build" / "A.cs").is_file()
        raise KeyboardInterrupt
    assert (d / "Scripts" / "A.cs").is_file()
    assert not (d / ".Scripts.hidden-for-build").exists()


# --- definition outputs -----------------------------------------------------------------------------------------


def test_definition_outputs_copy_and_override(tmp_path):
    d = _mod(tmp_path)
    _block_def(d, "A.def", Size=1)
    _write(d / "Assets" / "B.partialdef", {"$Type": "X", "$Value": {
        "PartialDefinitionKind": "Copy", "DefinitionType": T_OTHER, "Manipulator": {"Guid": G_B, "N": 2}}})
    _write(d / "Assets" / "C.partialdef", {"$Type": "X", "$Value": {"PartialDefinitionKind": "Override"}})
    (d / "Assets" / "broken.def").write_text("{", encoding="utf-8")
    got = [(s.name, o.relative_to(d).as_posix(), cls, own) for s, o, cls, own in sdkbuild.definition_outputs(d)]
    assert got == [
        ("A.def", "Content/A.def", "Keen.Game2.Test.BlockDefinition", {"Guid": G_A, "Size": 1}),
        ("B.partialdef", "Content/B.def", "Keen.Game2.Test.OtherDefinition", {"Guid": G_B, "N": 2}),
    ]


def test_stale_outputs_by_mtime(tmp_path):
    d = _mod(tmp_path)
    src = _block_def(d, "A.def", Size=1)
    out = _output(d, "A.def", Guid=G_A, Size=1)
    assert sdkbuild.stale_outputs(d, by_mtime=True) == []
    _age(out)
    assert sdkbuild.stale_outputs(d, by_mtime=True) == [(src, out, "older than its asset")]
    assert sdkbuild.stale_outputs(d) == []  # content still matches


def test_stale_outputs_reasons(tmp_path):
    d = _mod(tmp_path)
    a = _block_def(d, "A.def", Size=1)
    b = _block_def(d, "B.def", guid=G_B, Size=2, Gone=None)
    c = _block_def(d, "C.def", guid=G_B)
    m = _block_def(d, "M.def", guid=G_B)
    _output(d, "A.def", typ=T_OTHER, Guid=G_A, Size=1)
    _output(d, "B.def", Guid=G_B)
    (d / "Content" / "C.def").write_text("{", encoding="utf-8")
    assert sdkbuild.stale_outputs(d) == [
        (a, d / "Content/A.def", "class OtherDefinition != BlockDefinition"),
        (b, d / "Content/B.def", "lacks ['Size']"),
        (c, d / "Content/C.def", "unparseable"),
        (m, d / "Content/M.def", "missing"),
    ]


def test_ensure_metas(tmp_path):
    d = _mod(tmp_path)
    _block_def(d, "Blocks/A.def")
    (d / "Assets" / "tex.png").write_bytes(b"x")
    sdkbuild.ensure_metas(d, bundles={"VRage": "9"})
    a = json.loads((d / "Assets/Blocks/A.def.meta").read_text(encoding="utf-8"))
    assert a["$Value"] == {"AssetID": G_A, "InternalItems": []}
    assert a["$Bundles"] == {"VRage": "9"}
    assert (d / "Assets/Blocks.meta").is_file()  # folders need a sidecar too
    assert (d / "Assets/tex.png.meta").is_file()
    assert not (d / "Assets/Blocks/A.def.meta.meta").exists()


# --- build ------------------------------------------------------------------------------------------------------


def _buildable(tmp_path):
    """A mod with Scripts/, a current output and a stale one (older than its asset)."""
    env, d = _env(tmp_path), _mod(tmp_path)
    (d / "Scripts").mkdir()
    (d / "Scripts" / "Main.cs").write_text("// script", encoding="utf-8")
    _block_def(d, "A.def", Size=1)
    _block_def(d, "B.def", guid=G_B, Size=2)
    _output(d, "A.def", Guid=G_A, Size=1)
    old = _output(d, "B.def", Guid=G_B, Size=1)
    _age(old)
    return env, d


def _rebuild(d):
    """Builder stand-in: writes current outputs for every asset (what a good SDK build does)."""
    def effect(i, argv):
        for _src, out, cls, own in sdkbuild.definition_outputs(d):
            _write(out, {"$Bundles": {}, "$Type": "Game2:" + cls + "ObjectBuilder", "$Value": own})
        return 0
    return effect


def test_build_restores_project_on_failure(tmp_path):
    env, d = _buildable(tmp_path)
    proj = d / "Jörg Mod.vrgproj"
    original = proj.read_bytes()
    stale_bytes = (d / "Content/B.def").read_bytes()
    seen = {}

    def interrupt(i, argv):
        seen["scripts"] = (d / "Scripts").exists()
        seen["pruned"] = (d / "Content.pruned" / "B.def").is_file()
        seen["patched"] = proj.read_bytes() != original
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        sdkbuild.build(env, d, run=FakeRun(interrupt))
    assert seen == {"scripts": False, "pruned": True, "patched": True}
    assert proj.read_bytes() == original
    assert (d / "Scripts" / "Main.cs").is_file()
    assert not (d / ".Scripts.hidden-for-build").exists()
    assert (d / "Content/B.def").read_bytes() == stale_bytes
    assert not (d / "Content.pruned").exists()


def test_build_failure_puts_pruned_outputs_back(tmp_path, capsys):
    env, d = _buildable(tmp_path)
    stale_bytes = (d / "Content/B.def").read_bytes()
    assert sdkbuild.build(env, d, run=FakeRun(lambda i, argv: 7)) == 7
    assert (d / "Content/B.def").read_bytes() == stale_bytes
    assert not (d / "Content.pruned").exists()
    assert "put 1 moved-aside definition outputs back" in capsys.readouterr().err


def test_build_refuses_while_game_runs(tmp_path, monkeypatch, capsys):
    env, d = _buildable(tmp_path)
    monkeypatch.setattr(procs, "game_running", lambda **_: True)
    run = FakeRun()
    assert sdkbuild.build(env, d, run=run) == 4
    assert run.calls == []
    err = capsys.readouterr().err.strip()
    assert err.count("\n") == 0 and "Space Engineers 2 is running" in err
    assert not (d / "Content.pruned").exists() and (d / "Scripts").is_dir()


def test_generate_caches_second_pass(tmp_path, capsys):
    env, d = _buildable(tmp_path)
    (d / "Content.tmp" / "x").mkdir(parents=True)  # builder scratch left by an interrupted build
    run = FakeRun(_rebuild(d))
    assert sdkbuild.build(env, d, run=run) == 0
    assert len(run.calls) == 2
    first, second = run.calls
    assert first[0][-1] != "--generateCaches"
    assert second[0] == first[0] + ["--generateCaches"]
    assert first[1] == second[1] == env.sdk_editor
    assert not (d / "Content.tmp").exists() and not (d / "Content.pruned").exists()
    assert (d / "Assets/A.def.meta").is_file()
    assert "verified 2 definition outputs current" in capsys.readouterr().out


def test_dry_run_single_pass_prunes_nothing(tmp_path):
    env, d = _buildable(tmp_path)
    run = FakeRun()
    assert sdkbuild.build(env, d, dry_run=True, run=run) == 0
    assert len(run.calls) == 1 and run.calls[0][0][-1] == "--dryRun"
    assert (d / "Content/B.def").is_file()


def test_stale_output_after_build_exits_3(tmp_path, capsys):
    env, d = _buildable(tmp_path)
    assert sdkbuild.build(env, d, run=FakeRun()) == 3  # the builder wrote nothing: B was pruned, now missing
    err = capsys.readouterr().err
    assert "1 definition outputs are stale" in err and "B.def: missing" in err


def test_leftover_pruned_restored_before_build(tmp_path):
    env, d = _buildable(tmp_path)
    left = _write(d / "Content.pruned" / "Old.def", {"x": 1})
    _write(d / "Content.pruned" / "A.def", {"older": True})  # Content/A.def exists: not overwritten
    sdkbuild.build(env, d, run=FakeRun(_rebuild(d)))
    assert not left.exists() and (d / "Content" / "Old.def").is_file()
    assert "older" not in (d / "Content/A.def").read_text(encoding="utf-8")


# --- unreadable defs, SIGTERM, left-patched project -------------------------------------------------------------


def test_unreadable_def_stops_build_before_any_meta(tmp_path):
    env, d = _buildable(tmp_path)
    (d / "Assets" / "broken.def").write_text("{ not json", encoding="utf-8")
    run = FakeRun()
    with pytest.raises(sdkbuild.ModError, match=r"^Cannot read Assets/broken\.def as JSON .*; fix it"):
        sdkbuild.build(env, d, run=run)
    assert run.calls == []
    assert list((d / "Assets").rglob("*.meta")) == []  # not even for the readable A.def/B.def
    assert (d / "Content/B.def").is_file() and not (d / "Content.pruned").exists()
    assert (d / "Scripts").is_dir()


def test_sigterm_during_build_restores_everything(tmp_path):
    import signal

    env, d = _buildable(tmp_path)
    proj = d / "Jörg Mod.vrgproj"
    original = proj.read_bytes()
    stale_bytes = (d / "Content/B.def").read_bytes()
    before = signal.getsignal(signal.SIGTERM)

    def killed(i, argv):
        os.kill(os.getpid(), signal.SIGTERM)
        return 0

    with pytest.raises(KeyboardInterrupt):
        sdkbuild.build(env, d, run=FakeRun(killed))
    assert signal.getsignal(signal.SIGTERM) is before
    assert proj.read_bytes() == original
    assert (d / "Scripts" / "Main.cs").is_file() and not (d / ".Scripts.hidden-for-build").exists()
    assert (d / "Content/B.def").read_bytes() == stale_bytes and not (d / "Content.pruned").exists()
    assert not [p for p in d.iterdir() if p.name.endswith(".tmp")]  # atomic restore leaves no temp file


def test_left_patched_project_warns_and_is_kept(tmp_path, capsys):
    env, d = _env(tmp_path), _mod(tmp_path)
    proj = d / "Jörg Mod.vrgproj"
    data = json.loads(proj.read_text(encoding="utf-8"))
    data["$Value"]["GameExecutableSearchPaths"] = [_wine(env.game_dir / "Game2")]
    _write(proj, data)
    left = proj.read_bytes()
    with sdkbuild.patched_project(proj, env):
        pass
    assert "already holds the build-time SDK/game paths" in capsys.readouterr().err
    assert proj.read_bytes() == left


def test_clean_project_does_not_warn(tmp_path, capsys):
    env, d = _env(tmp_path), _mod(tmp_path)
    with sdkbuild.patched_project(d / "Jörg Mod.vrgproj", env):
        pass
    assert "already holds" not in capsys.readouterr().err
