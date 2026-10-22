"""Publishing needs a release build: a stamp from `se2mod build-models MOD --release` of the very commit being
published, a clean tree and the model files it stamped. A dry run shows it as a blocker and a warning; a real upload
is refused before anything is staged. A mod that builds its models without se2modkit only gets a warning; a mod with
no model build is not affected."""
import pytest
from pubfix import git

from se2modkit import release as REL
from se2modkit.publish import cli, steam


class Boom(Exception):
    pass


@pytest.fixture
def modeled(mod, monkeypatch):
    """The publish test mod with a model build: a [tool.se2modkit] project, a build script and exported models."""
    (mod / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "TestMod"\nprefix = "TM_"\n', encoding="utf-8")
    (mod / "Source/Model/Models/TM_A").mkdir(parents=True)
    (mod / "Source/Model/build_testmod.py").write_text("# the mod's build script\n", encoding="utf-8")
    (mod / "Source/Model/Models/TM_A/TM_A.fbx").write_bytes(b"fbx")
    git(mod, "add", "-A")
    git(mod, "commit", "-q", "-m", "models")

    def explode(*a, **k):
        raise Boom("nothing may be staged or sent")
    monkeypatch.setattr(steam, "FlatApiBackend", explode)
    monkeypatch.setattr(cli, "do_stage", explode)
    return mod


def _stamp(folder, **over):
    files = REL.model_files(folder / "Source/Model/Models", folder)
    kw = dict(mod="TestMod", head_sha=git(folder, "rev-parse", "HEAD").strip(), blocks=["TM_A"], keys={"TM_A": "k"},
              files=files, shipped=REL.shipped_copies(folder / "Source/Model/Models", folder / "Assets", folder, "TestMod")[0],
              script="Source/Model/build_testmod.py")
    kw.update(over)
    REL.write_stamp(REL.stamp_path(folder / "Source/Model/build_report.json", "TestMod"), **kw)


def test_a_matching_release_stamp_passes(modeled):
    _stamp(modeled)
    assert REL.publish_check(modeled) == (None, None)


def test_no_stamp_is_refused_naming_the_command(modeled):
    why, _ = REL.publish_check(modeled)
    assert "no release build" in why and "se2mod build-models TestMod --release" in why


def test_a_stamp_of_another_commit_is_refused(modeled):
    _stamp(modeled)
    (modeled / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    git(modeled, "commit", "-q", "-am", "later")
    why, _ = REL.publish_check(modeled)
    assert "not HEAD" in why and "--release" in why


def test_a_dirty_tree_is_refused(modeled):
    _stamp(modeled)
    (modeled / "notes.txt").write_text("x", encoding="utf-8")
    why, _ = REL.publish_check(modeled)
    assert "uncommitted changes (notes.txt)" in why and "--release" in why


def test_model_files_changed_since_the_release_build_are_refused(modeled):
    _stamp(modeled, files={"Source/Model/Models/TM_A/TM_A.fbx": "0" * 64})
    why, _ = REL.publish_check(modeled)
    assert "model files changed" in why and "TM_A.fbx" in why


def _own_build(mod):
    (mod / "Source/Model").mkdir(parents=True)
    (mod / "Source/Model/build_testmod.py").write_text("# build\n", encoding="utf-8")
    git(mod, "add", "-A")
    git(mod, "commit", "-q", "-m", "own model build")


def test_a_model_build_without_a_project_warns_and_passes(mod):
    _own_build(mod)
    why, note = REL.publish_check(mod)
    assert why is None and "doesn't build through modkit" in note


def test_a_mod_without_a_model_build_is_not_affected(mod):
    assert REL.publish_check(mod) == (None, None)


def test_a_mod_without_a_project_publishes_with_a_loud_warning(mod, monkeypatch, capsys):
    """Not refused: the real upload goes on to the existing gates (here the no-terminal refusal at the prompt)."""
    _own_build(mod)
    monkeypatch.setattr(cli, "do_stage", lambda *a, **k: _stage(mod))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["publish", str(mod), "--i-have-user-approval"]) == 2
    cap = capsys.readouterr()
    assert "interactive terminal" in cap.err and "REFUSED" not in cap.err
    assert "WARNING: models not verified by a release build" in cap.err
    assert cli.main(["publish", str(mod)]) == 0
    cap = capsys.readouterr()
    assert "WARNING: release build: models not verified" in cap.out and "READY FOR UPLOAD: yes" in cap.out


def _stage(mod):
    from se2modkit.publish.project import load_config, load_project, read_tracked
    from se2modkit.publish.stage import Limits, finish_stage
    from test_stage_rules import fake_builder_output
    p = load_project(mod)
    return finish_stage(p, fake_builder_output(mod, mod.parent / "stage"), read_tracked(p, load_config(p).thumbnail),
                        git(mod, "rev-parse", "HEAD").strip(), Limits())


def test_dry_run_shows_the_missing_release_build_loudly(modeled, capsys):
    assert cli.main(["publish", str(modeled), "--no-stage"]) == 0
    cap = capsys.readouterr()
    assert "BLOCKER: release build: TestMod has no release build" in cap.out
    assert "READY FOR UPLOAD: no" in cap.out
    assert "WARNING: a real upload would be refused" in cap.err and "--release" in cap.err


def test_a_real_upload_is_refused_before_staging(modeled, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must refuse before asking"))
    assert cli.main(["publish", str(modeled), "--i-have-user-approval"]) == 3
    err = capsys.readouterr().err
    refusal = [ln for ln in err.splitlines() if not ln.startswith("[")]    # skip the progress lines
    assert refusal[0].startswith("REFUSED: TestMod has no release build")
    assert "se2mod build-models TestMod --release" in err
    assert "staging" not in err                                             # refused before staging began


def test_with_a_release_build_the_other_gates_still_apply(modeled, monkeypatch, capsys):
    """The release check passes, so the real upload goes on to the existing gates: staging (refused here, as the
    fixture's stand-in for anything that must not run) comes before any confirmation or Steam call."""
    _stamp(modeled)
    with pytest.raises(Boom):
        cli.main(["publish", str(modeled), "--i-have-user-approval"])


def _ship(mod, data=b"fbx"):
    dst = mod / "Assets/TestMod/TM_A/Models/TM_A.fbx"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    git(mod, "add", "-A")
    git(mod, "commit", "-q", "-m", "ship")


def test_the_shipped_copy_must_be_what_the_release_build_exported(modeled):
    _ship(modeled)
    _stamp(modeled)
    assert REL.publish_check(modeled) == (None, None)
    _ship(modeled, b"an older export")          # the copy publish stages from goes stale, even if stamped again
    _stamp(modeled)
    why, _ = REL.publish_check(modeled)
    assert "Assets/TestMod/TM_A/Models/TM_A.fbx" in why and "--release" in why


def test_a_symlinked_models_folder_is_refused(modeled):
    models = modeled / "Source/Model/Models"
    (models / "TM_B").symlink_to(models / "TM_A", target_is_directory=True)
    git(modeled, "add", "-A")
    git(modeled, "commit", "-q", "-m", "link")
    _stamp(modeled)
    why, _ = REL.publish_check(modeled)
    assert "symlink" in why and "TM_B" in why


def test_a_project_mod_with_models_but_no_build_star_script_is_gated(modeled):
    git(modeled, "mv", "Source/Model/build_testmod.py", "Source/Model/make.py")
    git(modeled, "commit", "-q", "-m", "renamed")
    why, _ = REL.publish_check(modeled)
    assert "no release build" in why
    _stamp(modeled, script="Source/Model/make.py")
    assert REL.publish_check(modeled) == (None, None)
    _stamp(modeled, script="Source/Model/gone.py")
    assert "build script" in REL.publish_check(modeled)[0]


def test_an_ignored_file_that_would_ship_is_refused(modeled):
    (modeled / ".gitignore").write_text("*.tmpx\n", encoding="utf-8")
    git(modeled, "add", "-A")
    git(modeled, "commit", "-q", "-m", "ignore")
    _stamp(modeled)
    (modeled / "Assets/stray.tmpx").write_text("x", encoding="utf-8")
    why, _ = REL.publish_check(modeled)
    assert "uncommitted changes (Assets/stray.tmpx)" in why
    (modeled / "Assets/stray.tmpx").unlink()
    (modeled / "notes.tmpx").write_text("x", encoding="utf-8")      # ignored and never shipped: fine
    assert REL.publish_check(modeled) == (None, None)


def test_a_stamp_made_for_another_mod_is_refused(modeled):
    _stamp(modeled, mod="OtherMod")
    why, _ = REL.publish_check(modeled)
    assert "'OtherMod'" in why


def test_a_shipped_model_of_a_block_the_export_no_longer_has_is_refused(modeled):
    """A renamed or removed block's old models still in Assets/ would ship: refused, naming the file, even with a
    stamp of this very commit."""
    _ship(modeled)
    old = modeled / "Assets/TestMod/TM_OLD/Models/TM_OLD.fbx"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    git(modeled, "add", "-A")
    git(modeled, "commit", "-q", "-m", "left behind")
    _stamp(modeled)
    why, _ = REL.publish_check(modeled)
    assert "Assets/TestMod/TM_OLD/Models/TM_OLD.fbx" in why and "--release" in why
