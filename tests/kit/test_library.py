"""`se2mod kit library`: unpacking Keen's Blender material library zip into the user cache, and reading a probe.

The zips here are tiny synthetic ones; nothing from the SDK is used. Blender itself only runs through the queue, so
the command is tested with --print-only."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from se2modkit import cli
from se2modkit.kit import KitError
from se2modkit.kit import library as L


def _sdk(tmp_path, members=None) -> Path:
    sdk = tmp_path / "SDK"
    lib = sdk / "BlenderMaterialLibrary"
    lib.mkdir(parents=True)
    with zipfile.ZipFile(lib / "Decal&Material.zip", "w") as z:
        for name, data in (members or {"Lib.blend": b"BLENDER", "blender_assets.cats.txt": b"VERSION 1\n",
                                       "addon.zip": b"PK"}).items():
            z.writestr(name, data)
    return sdk


def test_unpack_once_into_the_cache(tmp_path):
    z = L.find_zip(_sdk(tmp_path))
    lib, new = L.unpack(z, tmp_path / "cache")
    assert new and lib.parent == tmp_path / "cache" / "blender-library" and (lib / L.DONE).is_file()
    assert (lib / "Lib.blend").read_bytes() == b"BLENDER" and (lib / "addon.zip").is_file()
    assert L.unpack(z, tmp_path / "cache") == (lib, False)
    assert L.find_blend(lib) == lib / "Lib.blend"
    assert "&" not in lib.name


def test_a_half_finished_unpack_is_redone(tmp_path):
    z = L.find_zip(_sdk(tmp_path))
    lib = L.library_dir(z, tmp_path / "cache")
    lib.mkdir(parents=True)
    (lib / "Lib.blend").write_bytes(b"partial")
    assert L.unpack(z, tmp_path / "cache") == (lib, True)
    assert (lib / "Lib.blend").read_bytes() == b"BLENDER"


def test_unsafe_or_oversized_zips_are_refused(tmp_path):
    z = L.find_zip(_sdk(tmp_path, {"../evil.blend": b"x"}))
    with pytest.raises(KitError, match="unsafe"):
        L.unpack(z, tmp_path / "cache")
    z2 = L.find_zip(_sdk(tmp_path / "b", {"big.blend": b"x" * 100}))
    with pytest.raises(KitError, match="over"):
        L.unpack(z2, tmp_path / "cache", max_bytes=10)
    assert not list((tmp_path / "cache" / "blender-library").glob("*/" + L.DONE))


def test_no_sdk_or_no_zip_is_one_sentence(tmp_path):
    with pytest.raises(KitError, match="Mod SDK"):
        L.find_zip(None)
    with pytest.raises(KitError, match="no material library zip"):
        L.find_zip(tmp_path)


def test_local_equivalent_maps_keen_paths_into_this_install(tmp_path):
    game, assets = tmp_path / "Game", tmp_path / "SDK" / "GameData" / "Vanilla" / "Assets"
    p = "//../../../KSHProjects/Dev2/Vanilla/Vanilla/Content/BlockMaterials/MetalDull/MetalDull_cm.dds"
    assert L.local_equivalent(p, game, assets) == (game / "GameData" / "Vanilla" / "Content" / "BlockMaterials" /
                                                   "MetalDull" / "MetalDull_cm.dds")
    p2 = "//../KSH/PerforceSE2Dev/Vanilla/Vanilla/Assets/BlockMaterials/Decals/x_normal.png"
    assert L.local_equivalent(p2, game, assets) == assets / "BlockMaterials" / "Decals" / "x_normal.png"
    assert L.local_equivalent("//forest.exr", game, assets) is None
    assert L.local_equivalent(p, None, assets) is None


def test_summarize_counts_missing_and_found(tmp_path):
    game = tmp_path / "Game"
    here = game / "GameData" / "Vanilla" / "Content" / "BlockMaterials" / "X" / "X_cm.dds"
    here.parent.mkdir(parents=True)
    here.write_bytes(b"DDS")
    probe = {"images": [
        {"filepath": "//../../K/Vanilla/Vanilla/Content/BlockMaterials/X/X_cm.dds", "packed": False, "exists": False},
        {"filepath": "//../../K/Vanilla/Vanilla/Content/BlockMaterials/Y/Y_cm.dds", "packed": False, "exists": False},
        {"filepath": "//Preview.png", "packed": False, "exists": True},
        {"filepath": "", "packed": True, "exists": False}], "materials": {"A": {}, "B": {}}}
    s = L.summarize(probe, game, None)
    assert (s["images"], s["packed"], len(s["missing"]), s["materials"]) == (4, 1, 2, 2)
    assert list(s["found_locally"].values()) == [str(here)]


@pytest.fixture
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SE2_BLENDER", "/bin/true")
    monkeypatch.chdir(tmp_path)


def test_command_unpacks_and_prints_the_queue_jobs(_isolated, tmp_path, capsys):
    sdk = _sdk(tmp_path)
    assert cli.main(["kit", "--sdk", str(sdk), "library"]) == 0
    out = capsys.readouterr().out
    assert "unpacked:" in out and "asset library" in out
    assert cli.main(["kit", "--sdk", str(sdk), "library", "--check", "--register", "--print-only"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("se2mod queue submit")]
    assert len(lines) == 2
    assert "--load medium" in lines[0] and "probe" in lines[0] and "--factory-startup" in lines[0]
    assert "--load light" in lines[1] and "register" in lines[1] and "--factory-startup" not in lines[1]
    assert "library_blend.py" in lines[0]


def test_command_without_an_sdk_fails_plainly(_isolated, tmp_path, capsys):
    empty = tmp_path / "NoSDK"
    empty.mkdir()
    assert cli.main(["kit", "--sdk", str(empty), "library"]) == 1
    assert "no material library zip" in capsys.readouterr().err


def test_print_only_unpacks_nothing(_isolated, tmp_path, capsys):
    sdk = _sdk(tmp_path)
    assert cli.main(["kit", "--sdk", str(sdk), "library", "--check", "--print-only"]) == 0
    out = capsys.readouterr().out
    assert "would unpack" in out and "Lib.blend" in out and "se2mod queue submit" in out
    assert not (tmp_path / "xdg").exists() or not list((tmp_path / "xdg").rglob("blender-library"))


def test_relink_stages_a_copy_beside_the_library_and_register_prefers_it(tmp_path):
    lib, _ = L.unpack(L.find_zip(_sdk(tmp_path, {"Lib.blend": b"BLENDER", "blender_assets.cats.txt": b"VERSION 1\n",
                                                  "previews/a.png": b"PNG"})), tmp_path / "cache")
    (lib / "probe.json").write_text("{}")
    assert L.register_dir(lib) == lib
    stage, mapping, out = L.prepare_relink(lib, lib / "Lib.blend", {"//../x/a.dds": "/game/a.dds"})
    assert stage.parent == lib.parent and out == stage / "Lib.blend" and not out.exists()
    assert (stage / "blender_assets.cats.txt").read_bytes() == b"VERSION 1\n"
    assert (stage / "previews" / "a.png").is_file() and not (stage / "probe.json").exists()
    assert not (stage / L.DONE).exists()
    assert json.loads(mapping.read_text()) == {"//../x/a.dds": "/game/a.dds"}
    assert L.register_dir(lib) == lib                       # staged, not finished
    out.write_bytes(b"RELINKED")
    final = L.finish_relink(lib, stage)
    assert final == L.relinked_dir(lib) == lib.with_name(lib.name + "-relinked") and not stage.exists()
    assert L.register_dir(lib) == final and (final / L.RELINKED).is_file()
    assert (lib / "Lib.blend").read_bytes() == b"BLENDER"    # the unpacked copy is left as it was
    stage2, _, out2 = L.prepare_relink(lib, lib / "Lib.blend", {})
    out2.write_bytes(b"AGAIN")
    assert (L.finish_relink(lib, stage2) / "Lib.blend").read_bytes() == b"AGAIN"


def test_relink_print_only_shows_the_jobs_and_registers_the_copy(_isolated, tmp_path, capsys):
    sdk = _sdk(tmp_path)
    assert cli.main(["kit", "--sdk", str(sdk), "library", "--relink", "--register", "--print-only"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("se2mod queue submit")]
    assert len(lines) == 3
    assert " probe " in lines[0] and " relink " in lines[1] and " register " in lines[2]
    assert "-relinked" in lines[1] and "-relinked" in lines[2]
    assert not list((tmp_path / "xdg").rglob("blender-library")) if (tmp_path / "xdg").exists() else True
