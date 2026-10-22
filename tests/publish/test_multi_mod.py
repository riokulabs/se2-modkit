"""Repos holding several mods (`[tool.se2modkit.mods.<name>]`): picking the mod, per-mod changelogs, release tags
`<mod>-vX.Y.Z`, Workshop dependencies in the metadata, and the approval gate."""
import json
import uuid
from pathlib import Path

import pytest
from pubfix import git, make_jpeg, vrgproj
from test_publish_flow import BUNDLES

from se2modkit.publish import cli, steam
from se2modkit.publish.metadata import item_id_to_guid
from se2modkit.publish.project import Refusal, check_canonical, load_config, select_mod
from se2modkit.publish.publish import assemble, metadata_for, render_plan

GUIDS = {"Core": "21111111-2222-4333-8444-555555555555", "Extras": "31111111-2222-4333-8444-555555555555"}


def _mod(folder: Path, name: str, changelog: str, publish_toml: str = "") -> None:
    (folder / "Assets").mkdir(parents=True)
    (folder / "Assets/Block.def").write_text("{}")
    (folder / "Assets.meta").write_text("{}")
    (folder / f"{name}.vrgproj").write_text(vrgproj(name, GUIDS[name]))
    (folder / "Scripts").mkdir()
    (folder / f"Scripts/{name}.cs").write_text(f"namespace {name} {{ class A {{}} }}\n")
    (folder / "docs/workshop").mkdir(parents=True)
    (folder / "docs/workshop/description.bbcode").write_text(f"[h1]{name}[/h1]\n")
    (folder / "docs/workshop/publish.toml").write_text(f'title = "{name} by Rioku Labs"\ntags = ["Block"]\n'
                                                       + publish_toml)
    make_jpeg(folder / "docs/workshop/thumb.jpg")
    (folder / "CHANGELOG.md").write_text(changelog)


@pytest.fixture
def pack(tmp_path):
    """A multi-mod repo: mods/Core and mods/Extras, each with its own changelog, plus a root changelog."""
    root = tmp_path / "mods" / "Pack"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pack"\nversion = "0"\n\n[tool.se2modkit]\nprofile = "vanilla"\n\n'
        '[tool.se2modkit.mods.Core]\npath = "mods/Core"\n\n[tool.se2modkit.mods.Extras]\npath = "mods/Extras"\n')
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0] - 2026-01-01\n- root entry, never a mod's note\n")
    _mod(root / "mods/Core", "Core",
         "# Changelog\n\n## [1.2.0] - 2026-10-20\n- Core **pipes**\n\n## [1.1.0] - 2026-10-01\n- Core first\n",
         "dependencies = [3900000002, 3900000003]\n")
    _mod(root / "mods/Extras", "Extras", "# Changelog\n\n## [1.2.0] - 2026-10-21\n- Extras lamps\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


def _plan(p, version=None):
    return assemble(p, load_config(p), version=version, visibility=None, adopt_existing=False,
                    change_note_file=None, keep_web_description=False, stage=None, bundles=BUNDLES,
                    strict_git=False)


def test_multi_mod_changelog_path(pack):
    p = select_mod(pack, "Core")
    assert p.folder == (pack / "mods/Core").resolve() and p.repo == pack.resolve() and p.multi
    assert check_canonical(p) == []
    cfg = load_config(p)
    assert cfg.changelog == p.folder / "CHANGELOG.md"
    note = _plan(p, "1.2.0").change_note
    assert note.source == "changelog:CHANGELOG.md#1.2.0"
    assert "Core [b]pipes[/b]" in note.text and "root entry" not in note.text
    assert "Extras" not in note.text


def test_single_mod_keeps_root_changelog(mod):
    p = select_mod(mod)
    assert not p.multi and load_config(p).changelog == mod.resolve() / "CHANGELOG.md"


def test_multi_mod_tag_pattern(pack, mod):
    p = select_mod(pack, None, "Core-v1.2.0")
    assert p.name == "Core"
    note = _plan(p, "Core-v1.2.0").change_note
    assert note.version == "1.2.0" and note.text.startswith("v1.2.0 - 2026-10-20")
    assert "Core [b]pipes[/b]" in note.text
    assert p.release_tag("1.2.0") == "Core-v1.2.0"
    lines = []
    render_plan(_plan(p, "Core-v1.2.0"), out=lines.append)
    assert any("release tag Core-v1.2.0" in ln for ln in lines)
    extras = select_mod(pack, None, "Extras-v1.2.0")
    assert "Extras lamps" in _plan(extras, "Extras-v1.2.0").change_note.text
    with pytest.raises(Refusal, match="different mods"):
        select_mod(pack, "Core", "Extras-v1.2.0")
    with pytest.raises(Refusal, match="for mod 'Core', not 'Extras'"):
        _plan(extras, "Core-v1.2.0")
    single = select_mod(mod)
    assert single.release_tag("1.2.0") == "v1.2.0" and single.release_tag("v1.2.0") == "v1.2.0"
    with pytest.raises(Refusal, match="vX.Y.Z"):
        _plan(single, "TestMod-v1.1.0")


def test_ambiguous_mod_requires_flag(pack, capsys):
    with pytest.raises(Refusal, match="which mod.*Core, Extras"):
        select_mod(pack)
    assert cli.main(["publish", str(pack), "--no-stage"]) == 3
    err = capsys.readouterr().err
    assert "which mod" in err and "Core, Extras" in err and "--mod" in err
    with pytest.raises(Refusal, match="no mod 'Nope'.*Core, Extras"):
        select_mod(pack, "Nope")
    # pointing at a mod folder picks that mod; asking for another one from inside it is refused
    assert select_mod(pack / "mods/Extras" / "Scripts").name == "Extras"
    with pytest.raises(Refusal, match="inside mod 'Extras'"):
        select_mod(pack / "mods/Extras", "Core")


def test_mod_config_is_read_from_head(pack):
    (pack / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "Core"\n')   # uncommitted: ignored
    assert select_mod(pack, "Extras").name == "Extras"


def test_mod_path_outside_repo_is_refused(pack):
    (pack / "pyproject.toml").write_text('[tool.se2modkit.mods.Core]\npath = "../Elsewhere"\n')
    git(pack, "commit", "-qam", "escape")
    with pytest.raises(Refusal, match="inside the repo"):
        select_mod(pack, "Core")


def test_dependencies_in_metadata(pack, mod):
    p = select_mod(pack, "Core")
    plan = _plan(p)
    assert plan.config.dependencies == [3900000002, 3900000003]
    meta = json.loads(metadata_for(plan, 3900000001, 0, "Unlisted", now="2026-10-21T00:00:00.0000000+00:00"))
    deps = meta["$Value"]["FullMetadata"]["BaseMetadata"]["Dependencies"]
    assert deps == [{"Id": item_id_to_guid(3900000002), "ServiceName": "Steam"},
                    {"Id": item_id_to_guid(3900000003), "ServiceName": "Steam"}]
    assert all(uuid.UUID(d["Id"]) for d in deps)
    # no dependencies: null, byte for byte what the HUB writes
    single = select_mod(mod)
    meta = json.loads(metadata_for(_plan(single), 3900000001, 0, "Unlisted", now="x"))
    assert meta["$Value"]["FullMetadata"]["BaseMetadata"]["Dependencies"] is None
    toml = mod / "docs/workshop/publish.toml"
    toml.write_text(toml.read_text() + 'dependencies = ["3900000002"]\n')
    git(mod, "commit", "-qam", "bad dep")
    with pytest.raises(Refusal, match="not a Workshop item id"):
        load_config(single)


class Boom(Exception):
    pass


def test_no_upload_without_approval_multi_mod(pack, monkeypatch, capsys):
    def explode(*a, **k):
        raise Boom("the real Steam backend must not be constructed")
    monkeypatch.setattr(steam, "FlatApiBackend", explode)
    monkeypatch.setattr(cli, "find_libsteam_api", explode)
    assert cli.main(["publish", str(pack), "--mod", "Core", "--no-stage", "--version", "Core-v1.2.0"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "nothing was sent to Steam" in out and "Core [b]pipes[/b]" in out
    assert "mod Core in multi-mod repo" in out
    # the approval flag alone is not enough: no stage -> refused before any confirmation or Steam call
    assert cli.main(["publish", str(pack), "--mod", "Core", "--no-stage", "--i-have-user-approval"]) == 2
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", explode)
    from se2modkit.publish.stage import Limits, finish_stage

    def fake_do_stage(args, p, cfg, env):
        st = pack.parent / "stage"
        (st / "Content").mkdir(parents=True)
        for f in ("Content/contentcache.vrb", "Content/definitionsets.vrb"):
            (st / f).write_bytes(b"\0" * 10)
        (st / p.vrgproj.name).write_bytes(p.vrgproj.read_bytes())
        return finish_stage(p, st, None, git(pack, "rev-parse", "HEAD").strip(), Limits())
    monkeypatch.setattr(cli, "do_stage", fake_do_stage)
    capsys.readouterr()
    rc = cli.main(["publish", str(pack), "--mod", "Core", "--i-have-user-approval"])
    cap = capsys.readouterr()
    assert rc == 2 and "interactive terminal" in cap.err and "Scripts/Core.cs" in cap.out
    # nothing written into the repo
    assert git(pack, "status", "--porcelain") == ""
