"""Staging rules: git-HEAD script collection, the safety checks on the staged tree, limits, canonical folder."""
import shutil
from pathlib import Path

import pytest

from se2modkit.publish import limits
from se2modkit.publish.project import Refusal, check_canonical, load_config, load_project
from se2modkit.publish.stage import Limits, check_tree, collect_head_scripts, dirty_shipping_inputs, finish_stage

from pubfix import GUID, git, make_jpeg, vrgproj


def fake_builder_output(mod: Path, stage: Path) -> Path:
    """What --prepareForPublish leaves behind (enough for the checks)."""
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "Assets").mkdir(parents=True)
    (stage / "Assets/Block.def").write_text("{}")
    (stage / "Assets.meta").write_text("{}")
    (stage / "Content").mkdir()
    (stage / "Content/contentcache.vrb").write_bytes(b"\0" * 10)
    (stage / "Content/definitionsets.vrb").write_bytes(b"\0" * 10)
    shutil.copy(mod / "TestMod.vrgproj", stage / "TestMod.vrgproj")
    return stage


# --- scripts from git HEAD ---------------------------------------------------------------------------------------

def test_scripts_come_from_head_only(mod, tmp_path):
    (mod / "Scripts/Client/Thing.cs").write_text("// local edit not committed\n")
    (mod / "Scripts/Client/New.cs").write_text("class New {}\n")
    out = tmp_path / "out"
    shipped, warnings = collect_head_scripts(mod, out)
    assert shipped == ["Scripts/Client/Thing.cs"]
    assert (out / "Scripts/Client/Thing.cs").read_text() == "namespace TestMod { class Thing {} }\n"
    assert not (out / "Scripts/Client/New.cs").exists()
    assert not (out / "Scripts/notes.txt").exists()
    assert any("untracked Scripts/Client/New.cs" in w for w in warnings)
    assert any("uncommitted changes in Scripts/Client/Thing.cs" in w for w in warnings)
    assert any("notes.txt: not a .cs file" in w for w in warnings)


def test_decompiled_script_is_refused(mod):
    (mod / "Scripts/Client/Leak.cs").write_text("// Decompiled with ICSharpCode.Decompiler\nclass A {}\n")
    git(mod, "add", "-A")
    git(mod, "commit", "-qm", "oops")
    with pytest.raises(Refusal, match="decompiled"):
        collect_head_scripts(mod, None)


def test_lfs_pointer_script_is_refused(mod):
    (mod / "Scripts/Client/Big.cs").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:ab\n")
    git(mod, "add", "-A")
    git(mod, "commit", "-qm", "lfs")
    with pytest.raises(Refusal, match="lfs"):
        collect_head_scripts(mod, None)


def test_no_scripts_dir_is_fine(mod):
    git(mod, "rm", "-rq", "Scripts")
    git(mod, "commit", "-qm", "no scripts")
    assert collect_head_scripts(mod, None) == ([], [])


# --- the staged tree ---------------------------------------------------------------------------------------------

def test_finish_stage_builds_manifest(mod, tmp_path):
    p = load_project(mod)
    stage = fake_builder_output(mod, tmp_path / "stage")
    res = finish_stage(p, stage, (mod / "docs/workshop/thumb.jpg").read_bytes(), "abc", Limits())
    paths = [e.path for e in res.manifest.entries]
    assert "Scripts/Client/Thing.cs" in paths and "thumb.jpg" in paths
    assert not any(x.startswith(("Tests", "docs", "Source", ".git")) for x in paths)
    origins = {e.path: e.origin for e in res.manifest.entries}
    assert origins["Scripts/Client/Thing.cs"] == "git:HEAD" and origins["thumb.jpg"] == "thumbnail"
    assert res.manifest.total_bytes == sum(f.stat().st_size for f in stage.rglob("*") if f.is_file())
    assert len(res.manifest.digest()) == 64
    assert (tmp_path / "manifest.json").exists()


def test_finish_stage_restores_users_vrgproj(mod, tmp_path):
    p = load_project(mod)
    stage = fake_builder_output(mod, tmp_path / "stage")
    (stage / "TestMod.vrgproj").write_text('{"patched": "Z:\\\\standin"}')
    finish_stage(p, stage, None, "abc", Limits())
    assert (stage / "TestMod.vrgproj").read_bytes() == (mod / "TestMod.vrgproj").read_bytes()


@pytest.mark.parametrize("rel,content,why", [
    ("Tests/test_a.py", b"x", "folders must not ship"),
    ("Source/Model/a.json", b"{}", "folders must not ship"),
    ("docs/README.md", b"x", "folders must not ship"),
    ("Assets/x/.git/config", b"x", "folders must not ship"),
    ("Assets/Keen.Game2.dll", b"MZ\x90\x00", "'.dll' files must not ship"),
    ("Assets/blob.def", b"MZ\x90\x00", "Windows executable"),
    ("Assets/Decomp.cs", b"class A {}", "C# outside Scripts/"),
    ("Assets/preview_render.png", b"png", "preview renders"),
    ("Assets/a.def", b'{"k": "ghp_' + b"a" * 36 + b'"}', "secret"),
    ("Assets/key.def", b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----", "secret"),  # split: the repo guard scans this file
    ("Assets/model.fbx", b"version https://git-lfs.github.com/spec/v1\n", "git-lfs pointer"),
    ("NOTES.md", b"x", "unexpected top-level"),
    ("Assets/build.py", b"x", "'.py' files must not ship"),
])
def test_check_tree_refusals(mod, tmp_path, rel, content, why):
    p = load_project(mod)
    stage = fake_builder_output(mod, tmp_path / "stage")
    f = stage / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)
    _, errors = check_tree(stage, p, Limits(), {})
    assert any(why in e for e in errors), errors


def test_check_tree_size_limits_and_symlinks(mod, tmp_path):
    p = load_project(mod)
    stage = fake_builder_output(mod, tmp_path / "stage")
    (stage / "Assets/big.vrb").write_bytes(b"\0" * 2048)
    (stage / "Assets/link.def").symlink_to(stage / "Assets/Block.def")
    _, errors = check_tree(stage, p, Limits(max_file_bytes=1024, max_total_bytes=1500), {})
    assert any("per-file limit" in e for e in errors)
    assert any("over the limit 1500" in e for e in errors)
    assert any("symlink" in e for e in errors)


def test_check_tree_requires_builder_outputs(mod, tmp_path):
    p = load_project(mod)
    stage = fake_builder_output(mod, tmp_path / "stage")
    (stage / "Content/definitionsets.vrb").unlink()
    _, errors = check_tree(stage, p, Limits(), {})
    assert any("missing Content/definitionsets.vrb" in e for e in errors)


def test_finish_stage_refuses_on_any_error(mod, tmp_path):
    p = load_project(mod)
    stage = fake_builder_output(mod, tmp_path / "stage")
    (stage / "Assets/x.exe").write_bytes(b"MZ")
    with pytest.raises(Refusal, match="safety checks"):
        finish_stage(p, stage, None, "abc", Limits())


def test_dirty_shipping_inputs(mod):
    p = load_project(mod)
    assert dirty_shipping_inputs(p) == []
    (mod / "Assets/New.def").write_text("{}")
    (mod / "Tests/test_y.py").write_text("")       # not shipped: ignored
    assert dirty_shipping_inputs(p) == ["?? Assets/New.def"]


# --- limits ------------------------------------------------------------------------------------------------------

def test_title_limits():
    assert limits.check_title("Demo Mod by Rioku Labs") == []
    assert "50" in limits.check_title("x" * 51)[0]
    assert limits.check_title("  ")[0] == "title is empty"


def test_description_limits():
    assert limits.check_description("[b]ok[/b]") == ([], [])
    errs, _ = limits.check_description("a\n" * 2700)              # 5400 chars but 8100 bytes with CRLF
    assert errs and "CRLF" in errs[0]
    _, warns = limits.check_description("x" * 7600)
    assert any("close to" in w for w in warns)
    _, warns = limits.check_description("[b]unclosed")
    assert any("unclosed [b]" in w for w in warns)


def test_thumbnail_checks_and_reencode(tmp_path):
    assert "not found" in limits.check_thumbnail(tmp_path / "none.jpg")[0]
    png = tmp_path / "t.png"
    from PIL import Image
    import os
    Image.frombytes("RGB", (2400, 1600), os.urandom(2400 * 1600 * 3)).save(png)
    assert any("not a JPEG" in e for e in limits.check_thumbnail(png))
    big = tmp_path / "big.jpg"
    Image.open(png).save(big, "JPEG", quality=100)
    assert any("< 1000000" in e for e in limits.check_thumbnail(big))
    out = tmp_path / "thumb.jpg"
    q, n = limits.reencode_thumbnail(png, out)
    assert n < 1_000_000 and limits.check_thumbnail(out) == []
    assert limits.jpeg_size(out) == (1280, 720)


# --- canonical folder / config -----------------------------------------------------------------------------------

def test_canonical_ok_and_siblings_reported(mod):
    sib = mod.parent / "TestMod-copy"
    sib.mkdir()
    (sib / "TestMod.vrgproj").write_text(vrgproj("TestMod"))
    notes = check_canonical(load_project(mod))
    assert any("TestMod-copy/ shares this project GUID" in n for n in notes)


def test_worktree_copy_is_refused(mod):
    wt = mod.parent / "TestMod-wt"
    git(mod, "worktree", "add", "-q", str(wt))
    with pytest.raises(Refusal, match="worktree"):
        check_canonical(load_project(wt))


def test_renamed_copy_is_refused(mod, tmp_path):
    other = tmp_path / "mods" / "TestMod-dmg"
    shutil.copytree(mod, other)
    with pytest.raises(Refusal, match="must all match"):
        check_canonical(load_project(other))


def test_non_git_folder_is_refused(tmp_path):
    d = tmp_path / "Loose"
    d.mkdir()
    (d / "Loose.vrgproj").write_text(vrgproj("Loose"))
    with pytest.raises(Refusal, match="not a git repository"):
        check_canonical(load_project(d))


def test_config_tags_and_defaults(mod):
    cfg = load_config(load_project(mod))
    assert cfg.title == "Test Mod by Rioku Labs" and cfg.tags == ["ModTagBlock"]
    assert cfg.first_visibility == "Unlisted" and cfg.summary == ""
    (mod / "docs/workshop/publish.toml").write_text('tags = ["Block", "Weapons", "ModTagOther"]\nfoo = 1\n')
    cfg = load_config(load_project(mod))
    assert cfg.tags == ["ModTagBlock"] and any("uncommitted changes" in x for x in cfg.problems)  # HEAD wins
    git(mod, "commit", "-qam", "config")
    cfg = load_config(load_project(mod))
    assert cfg.tags == ["ModTagBlock", "ModTagOther"] and cfg.title == "TestMod"
    assert any("Weapons" in x for x in cfg.problems) and any("unknown key 'foo'" in x for x in cfg.problems)
