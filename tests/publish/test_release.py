"""The git tag and GitHub release made after a successful upload (git and gh are faked; nothing is pushed)."""
import pytest

from se2modkit.publish import cli, release, steam
from se2modkit.publish.project import load_config, load_project, read_ledger, read_tracked
from se2modkit.publish.publish import render_plan
from se2modkit.publish.stage import Limits, finish_stage
from se2modkit.publish.steam import FakeBackend

from pubfix import git
from test_publish_flow import make_plan
from test_stage_rules import fake_builder_output

SHA = "a" * 40
ITEM = 3812345678


class FakeRunner:
    """Answers git/gh calls from a table of (argv prefix -> (rc, stdout, stderr)); records every call."""

    def __init__(self, **over):
        self.calls: list[list[str]] = []
        self.notes: list[str] = []
        self.table = {
            ("git", "rev-parse"): (1, "", ""),                 # no such tag yet
            ("git", "tag"): (0, "", ""),
            ("git", "push"): (0, "", ""),
            ("git", "remote"): (0, "git@github.com:riokulabs/se2-test-mod.git\n", ""),
            ("gh", "auth"): (0, "", ""),
            ("gh", "release", "view"): (1, "", "release not found"),
            ("gh", "release", "create"): (0, "", ""),
            ("gh", "release", "edit"): (0, "", ""),
        }
        for k, v in over.items():
            self.table[tuple(k.split("_"))] = v

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if "--notes-file" in argv:
            with open(argv[argv.index("--notes-file") + 1], encoding="utf-8") as f:
                self.notes.append(f.read())
        for n in (3, 2):
            if tuple(argv[:n]) in self.table:
                return self.table[tuple(argv[:n])]
        raise AssertionError(f"unexpected command {argv}")

    def ran(self, *prefix):
        return [c for c in self.calls if tuple(c[:len(prefix)]) == prefix]


@pytest.fixture
def released_mod(mod):
    (mod / "docs/workshop/publish.toml").write_text(
        'title = "Test Mod by Rioku Labs"\ntags = ["Block"]\nrelease_title = "Test Mod"\n')
    (mod / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.1.0] - 2026-10-20\n### Added\n- New **rack** block\n\n### For modders\n"
        "- New API\n\n## [1.0.0] - 2026-10-01\n- First release\n")
    git(mod, "commit", "-qam", "release config")
    return mod


def after(mod, version="1.1.0", runner=None, commit=SHA):
    p = load_project(mod)
    lines: list[str] = []
    ok = release.after_upload(p, load_config(p), version, commit, ITEM, runner=runner, out=lines.append)
    return ok, "\n".join(lines)


def test_config_defaults(mod):
    cfg = load_config(load_project(mod))
    assert cfg.release_title == "TestMod" and cfg.github_release is True
    assert not [x for x in cfg.problems if "unknown key" in x]


def test_new_tag_and_new_release(released_mod):
    r = FakeRunner()
    ok, out = after(released_mod, runner=r)
    assert ok, out
    assert r.ran("git", "tag") == [["git", "tag", "-a", "v1.1.0", "-m", "Test Mod by Rioku Labs 1.1.0", SHA]]
    assert r.ran("git", "push") == [["git", "push", "origin", "refs/tags/v1.1.0"]]
    create = r.ran("gh", "release", "create")[0]
    assert create[4:6] == ["-R", "riokulabs/se2-test-mod"]
    assert create[create.index("--title") + 1] == "Test Mod 1.1.0" and "--latest" in create
    notes = r.notes[0]
    assert "New **rack** block" in notes and "### For modders" in notes and "New API" in notes
    assert "First release" not in notes
    assert notes.rstrip().endswith(f"**Steam Workshop:** [Test Mod by Rioku Labs]"
                                   f"(https://steamcommunity.com/sharedfiles/filedetails/?id={ITEM})")
    assert not r.ran("gh", "release", "edit")


def test_existing_draft_is_edited_and_published(released_mod):
    r = FakeRunner(gh_release_view=(0, '{"isDraft":true}', ""))
    ok, out = after(released_mod, runner=r)
    assert ok, out
    edit = r.ran("gh", "release", "edit")[0]
    assert "--draft=false" in edit and "--latest" in edit and edit[edit.index("--title") + 1] == "Test Mod 1.1.0"
    assert not r.ran("gh", "release", "create")


def test_tag_at_same_commit_is_reused(released_mod):
    r = FakeRunner()
    r.table[("git", "rev-parse")] = (0, SHA + "\n", "")
    ok, out = after(released_mod, runner=r)
    assert ok and "reusing" in out and not r.ran("git", "tag") and r.ran("gh", "release", "create")


def test_tag_at_another_commit_is_left_alone(released_mod):
    r = FakeRunner()
    r.table[("git", "rev-parse")] = (0, "b" * 40 + "\n", "")
    ok, out = after(released_mod, runner=r)
    assert not ok and "left alone" in out
    assert not r.ran("git", "tag") and not r.ran("git", "push") and not [c for c in r.calls if c[0] == "gh"]


def test_no_version_skips(released_mod):
    r = FakeRunner()
    ok, out = after(released_mod, version=None, runner=r)
    assert not ok and "no --version" in out and r.calls == []


def test_github_release_false_skips(released_mod):
    (released_mod / "docs/workshop/publish.toml").write_text('title = "T"\ngithub_release = false\n')
    git(released_mod, "commit", "-qam", "opt out")
    r = FakeRunner()
    ok, out = after(released_mod, runner=r)
    assert not ok and "github_release = false" in out and r.calls == []


def test_gh_failure_warns_with_manual_commands(released_mod):
    r = FakeRunner(gh_auth=(1, "", "You are not logged into any GitHub hosts"))
    ok, out = after(released_mod, runner=r)
    assert not ok and "WARNING" in out and "not signed in" in out
    assert "gh release create v1.1.0" in out and "--draft=false" in out
    assert "git -C" not in out          # the tag was made and pushed; only the release is left


def test_tag_push_failure_lists_tag_commands_too(released_mod):
    r = FakeRunner(git_push=(1, "", "permission denied"))
    ok, out = after(released_mod, runner=r)
    assert not ok and "permission denied" in out and "tag -a v1.1.0" in out and "push origin" in out


def test_dry_run_lists_the_tag_and_release(released_mod, tmp_path):
    plan = make_plan(released_mod, tmp_path, version="1.1.0")
    lines: list[str] = []
    render_plan(plan, out=lines.append)
    text = "\n".join(lines)
    assert f"then: tag v1.1.0 at {plan.stage.head[:12]} and push" in text
    assert 'then: publish GitHub release v1.1.0 "Test Mod 1.1.0"' in text


def test_cli_upload_succeeds_when_gh_fails(released_mod, tmp_path, monkeypatch, capsys):
    fake = FakeBackend()
    fake.close = lambda: None
    monkeypatch.setattr(steam, "FlatApiBackend", lambda *a, **k: fake)
    monkeypatch.setattr(cli, "find_libsteam_api", lambda *a: tmp_path / "libsteam_api.so")
    monkeypatch.setattr(cli, "preflight_live", lambda *a, **k: None)
    monkeypatch.setattr(cli, "discover", lambda *a, **k: None)
    monkeypatch.setattr(cli, "publish_check", lambda *a, **k: (None, None))
    monkeypatch.setattr(cli, "hub_bundles", lambda *a: {"VRage": "4.0.0.3151"})

    def fake_do_stage(args, p, cfg, env):
        return finish_stage(p, fake_builder_output(released_mod, tmp_path / "stage"), read_tracked(p, cfg.thumbnail),
                            git(released_mod, "rev-parse", "HEAD").strip(), Limits())
    monkeypatch.setattr(cli, "do_stage", fake_do_stage)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "TestMod")
    r = FakeRunner(gh_auth=(127, "", "gh: command not found"))
    monkeypatch.setattr(release, "run", r)
    rc = cli.main(["publish", str(released_mod), "--version", "1.1.0", "--i-have-user-approval"])
    out = capsys.readouterr().out
    assert rc == 0 and "published item" in out and "WARNING" in out and "gh release create v1.1.0" in out
    commit = read_ledger(load_project(released_mod))[-1]["commit"]
    assert r.ran("git", "tag")[0][-1] == commit
