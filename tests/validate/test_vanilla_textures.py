"""The game-texture copy guard: synthetic files only, never real game data."""

import json
import os
from types import SimpleNamespace

from se2modkit.validate import vanilla_textures as vt


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _tree(tmp_path):
    vanilla = tmp_path / "game" / "GameData" / "Vanilla"
    tex = _write(vanilla / "Content" / "BlockMaterials" / "Steel_cm.dds", b"D" * 4096)
    _write(vanilla / "Content" / "Other_ng.png", b"N" * 1000)
    mod = tmp_path / "mod"
    return vanilla, tex, mod


def test_an_exact_copy_is_found_under_any_name(tmp_path):
    vanilla, tex, mod = _tree(tmp_path)
    copy = _write(mod / "Assets" / "Textures" / "MyPanel.dds", tex.read_bytes())
    _write(mod / "Assets" / "Textures" / "Own.dds", b"O" * 4096)  # same size, other bytes
    assert vt.find_copies(vt.image_files(mod), [vanilla]) == [(copy, tex)]


def test_a_mod_with_only_its_own_textures_passes(tmp_path):
    vanilla, _, mod = _tree(tmp_path)
    _write(mod / "Assets" / "a.png", b"x" * 1000)
    assert vt.find_copies(vt.image_files(mod), [vanilla]) == []


def test_tiny_files_previews_and_git_are_ignored(tmp_path):
    vanilla, tex, mod = _tree(tmp_path)
    _write(vanilla / "dot.png", b"1" * 10)
    _write(mod / "dot.png", b"1" * 10)
    _write(mod / "preview" / "look.dds", tex.read_bytes())
    _write(mod / ".git" / "objects" / "x.dds", tex.read_bytes())
    assert vt.find_copies(vt.image_files(mod), [vanilla]) == []


def test_non_images_are_not_judged(tmp_path):
    vanilla, tex, mod = _tree(tmp_path)
    _write(mod / "notes.bin", tex.read_bytes())
    assert vt.image_files(mod) == []


def test_hashes_are_cached_and_refreshed_when_the_file_changes(tmp_path):
    vanilla, tex, mod = _tree(tmp_path)
    copy = _write(mod / "t.dds", tex.read_bytes())
    cache = tmp_path / "cache" / vt.CACHE_NAME
    assert vt.find_copies([copy], [vanilla], cache) == [(copy, tex)]
    stored = json.loads(cache.read_text())
    assert list(stored) == [str(tex)]
    _write(tex, b"E" * 4096)  # same size, new bytes
    os.utime(tex, ns=(1, 1))
    assert vt.find_copies([copy], [vanilla], cache) == []


def test_a_corrupt_cache_is_ignored(tmp_path):
    vanilla, tex, mod = _tree(tmp_path)
    copy = _write(mod / "t.dds", tex.read_bytes())
    cache = _write(tmp_path / "cache.json", b"{not json")
    assert vt.find_copies([copy], [vanilla], cache) == [(copy, tex)]


def test_vanilla_roots_lists_only_what_exists(tmp_path):
    vanilla, _, _ = _tree(tmp_path)
    env = SimpleNamespace(game_dir=tmp_path / "game", sdk_dir=tmp_path / "nosdk")
    assert vt.vanilla_roots(env) == [vanilla]
    assert vt.vanilla_roots(SimpleNamespace(game_dir=None, sdk_dir=None)) == []


def test_refusal_names_each_copy(tmp_path):
    vanilla, tex, mod = _tree(tmp_path)
    copy = _write(mod / "Assets" / "t.dds", tex.read_bytes())
    msg = vt.refusal([(copy, tex)], mod)
    assert "Assets/t.dds" in msg and "BlockMaterials/Steel_cm.dds" in msg and "GUID" in msg
    assert vt.refusal([], mod) == ""
