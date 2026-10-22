import pytest
from pubfix import git, make_jpeg, vrgproj


@pytest.fixture
def mod(tmp_path):
    """A canonical mod repo: mods/TestMod with a committed script, a description, a changelog and a thumbnail."""
    root = tmp_path / "mods" / "TestMod"
    (root / "Assets").mkdir(parents=True)
    (root / "Assets/Block.def").write_text("{}")
    (root / "Assets.meta").write_text("{}")
    (root / "TestMod.vrgproj").write_text(vrgproj("TestMod"))
    (root / "Scripts/Client").mkdir(parents=True)
    (root / "Scripts/Client/Thing.cs").write_text("namespace TestMod { class Thing {} }\n")
    (root / "Scripts/notes.txt").write_text("not shipped\n")
    (root / "docs/workshop").mkdir(parents=True)
    (root / "docs/workshop/description.bbcode").write_text("[h1]Test Mod[/h1]\nA test.\n")
    (root / "docs/workshop/publish.toml").write_text('title = "Test Mod by Rioku Labs"\ntags = ["Block"]\n')
    make_jpeg(root / "docs/workshop/thumb.jpg")
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n- wip\n\n## [1.1.0] - 2026-10-20\n### Added\n- New **rack** block\n"
        "- Fix for [b] tags in `[noparse]` text\n\n## [1.0.0] - 2026-10-01\n- First release\n\n"
        "[1.1.0]: https://example.com/compare\n")
    (root / "Tests").mkdir()
    (root / "Tests/test_x.py").write_text("def test(): pass\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture(autouse=True)
def hermetic_env(tmp_path, monkeypatch):
    """`se2publish` CLI runs see a synthetic machine: no Steam, SDK, game, Pulsar or user config."""
    from se2modkit.env.core import discover_env
    from se2modkit.publish import cli

    monkeypatch.setattr(cli, "discover", lambda: discover_env(environ={}, platform="linux", home=tmp_path / "home",
                                                              config={}, sysroot=tmp_path / "sys"))
