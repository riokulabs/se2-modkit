"""security: config-supplied paths must stay inside the mod repo and be read from git HEAD."""
import os

import pytest

from se2modkit.publish.project import Refusal, load_config, load_project, read_tracked
from se2modkit.publish.publish import assemble

from pubfix import git
from test_publish_flow import BUNDLES


def set_config(mod, body, commit=True):
    (mod / "docs/workshop/publish.toml").write_text(body)
    if commit:
        git(mod, "commit", "-qam", "config")


@pytest.mark.parametrize("key", ["description", "changelog", "thumbnail"])
def test_dotdot_is_refused(mod, key):
    set_config(mod, f'{key} = "../../secret.txt"\n')
    with pytest.raises(Refusal, match=r"'\.\.'"):
        load_config(load_project(mod))


@pytest.mark.parametrize("value", ["/etc/passwd", "~/.ssh/id_rsa", "C:/Users/x/a.txt", "C:\\\\Users\\\\x"])
def test_absolute_paths_are_refused(mod, value):
    set_config(mod, f'description = "{value}"\n')
    with pytest.raises(Refusal, match="absolute"):
        load_config(load_project(mod))


def test_symlink_outside_repo_is_refused(mod, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("private")
    os.symlink(secret, mod / "docs/workshop/leak.bbcode")
    set_config(mod, 'description = "docs/workshop/leak.bbcode"\n')
    with pytest.raises(Refusal, match="outside the mod repo"):
        load_config(load_project(mod))


def test_symlinked_directory_outside_repo_is_refused(mod, tmp_path):
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext/d.bbcode").write_text("x")
    os.symlink(tmp_path / "ext", mod / "docs/ext")
    set_config(mod, 'description = "docs/ext/d.bbcode"\n')
    with pytest.raises(Refusal, match="outside the mod repo"):
        load_config(load_project(mod))


def test_committed_symlink_inside_repo_is_not_followed(mod):
    os.symlink("description.bbcode", mod / "docs/workshop/alias.bbcode")
    git(mod, "add", "-A")
    set_config(mod, 'description = "docs/workshop/alias.bbcode"\n')
    p = load_project(mod)
    with pytest.raises(Refusal, match="symlink in git"):
        read_tracked(p, load_config(p).description)


def test_dot_git_is_refused(mod):
    set_config(mod, 'description = ".git/config"\n')
    with pytest.raises(Refusal, match=".git"):
        load_config(load_project(mod))


def plan(mod, strict=True):
    p = load_project(mod)
    return assemble(p, load_config(p), version=None, visibility=None, adopt_existing=False, change_note_file=None,
                    keep_web_description=False, stage=None, bundles=BUNDLES, strict_git=strict)


def test_untracked_description_and_thumbnail_are_blockers(mod):
    (mod / "docs/workshop/new.bbcode").write_text("[b]untracked[/b]")
    (mod / "docs/workshop/new.jpg").write_bytes((mod / "docs/workshop/thumb.jpg").read_bytes())
    set_config(mod, 'description = "docs/workshop/new.bbcode"\nthumbnail = "docs/workshop/new.jpg"\n')
    pl = plan(mod)
    assert any("new.bbcode is not committed" in b for b in pl.blockers)
    assert any("new.jpg is not committed" in b for b in pl.blockers)
    assert pl.description is None


def test_untracked_changelog_falls_back_to_git(mod):
    (mod / "docs/CHANGES.md").write_text("## [9.0.0]\n- secret local notes\n")
    set_config(mod, 'changelog = "docs/CHANGES.md"\n')
    pl = plan(mod)
    assert "secret local notes" not in pl.change_note.text
    assert pl.change_note.source.startswith("git:")


def test_working_tree_edits_are_not_uploaded(mod):
    (mod / "docs/workshop/description.bbcode").write_text("LOCAL EDIT")
    pl = plan(mod, strict=False)
    assert pl.description == "[h1]Test Mod[/h1]\nA test.\n"
    assert any("uncommitted changes" in w for w in pl.warnings)
    assert any("uncommitted changes" in b for b in plan(mod, strict=True).blockers)


def test_uncommitted_config_blocks_a_real_upload(mod):
    git(mod, "rm", "-q", "--cached", "docs/workshop/publish.toml")
    git(mod, "commit", "-qm", "untrack config")
    assert any("not committed" in b for b in plan(mod, strict=True).blockers)
    assert not any("not committed" in b for b in plan(mod, strict=False).blockers)
