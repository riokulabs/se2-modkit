import functools
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import fake_sdk
import fake_steam
import pytest

from se2modkit.cli import mods_cmd
from se2modkit.compat import load_compat
from se2modkit.env import core
from se2modkit.env.steam import GAME_APP_ID, SDK_APP_ID
from se2modkit.project import ProjectError, load_project

SDK_NAME = "Space Engineers 2 - Mod SDK"

# Synthetic GUIDs (hand-written, not from any game file).
G_INV = "11111111-2222-4333-8444-555555555555"
G_A = "aaaaaaaa-0000-4000-8000-000000000001"
G_B = "bbbbbbbb-0000-4000-8000-000000000002"
G_OUT = "cccccccc-0000-4000-8000-000000000003"

# Read before the autouse fixture clears it: the workspace whose tools/scripts/se2mod.py wrapper to check.
_WORKSPACE = os.environ.get("SE2_WORKSPACE")


def _wrapper() -> Path:
    """The workspace's `tools/scripts/se2mod.py`, which must be the modkit wrapper; skips without SE2_WORKSPACE."""
    if not _WORKSPACE:
        pytest.skip("workspace integration: set SE2_WORKSPACE")
    p = Path(_WORKSPACE) / "tools" / "scripts" / "se2mod.py"
    assert p.is_file(), f"{p} not found"
    assert "from se2modkit import cli" in p.read_text(encoding="utf-8"), f"{p} is not the se2modkit wrapper"
    return p


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _steam(tmp_path, version="4.0.0.3155", sdk_version=None):
    root = tmp_path / "home" / ".steam" / "steam"
    fake_steam.make(root, apps={GAME_APP_ID: {"installdir": "SpaceEngineers2", "buildid": 5},
                                SDK_APP_ID: {"installdir": SDK_NAME, "buildid": 7}})
    game = fake_sdk.make_game(root / "steamapps/common/SpaceEngineers2", version)
    sdk = fake_sdk.make_sdk(root / "steamapps/common" / SDK_NAME, sdk_version or version)
    return root, game, sdk


def _vanilla_def(game: Path):
    p = game / "GameData/Vanilla/Content/Inventories/SmallCrate.def"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"$Bundles": {}, "$Type": "Game2:Keen.Game2.Test.InventoryDefinitionObjectBuilder",
                             "$Value": {"Guid": G_INV, "MaxVolume": 4.0}}), encoding="utf-8")
    # A decoy that mentions the GUID without declaring it.
    q = game / "GameData/Vanilla/Content/Other/Refs.def"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(json.dumps({"$Type": "X", "$Value": {"Guid": G_OUT, "Ref": G_INV}}), encoding="utf-8")
    return p


def _mod(parent: Path, name: str) -> Path:
    d = parent / name
    (d / "Assets").mkdir(parents=True)
    (d / f"{name}.vrgproj").write_text("{}\n", encoding="utf-8")
    return d


def test_override_stamps_from_sdk(tmp_path, run):
    root, game, _sdk = _steam(tmp_path, version="4.0.0.3155", sdk_version="4.0.0.3199")
    _vanilla_def(game)
    mod = _mod(tmp_path, "MyMod")
    code, out, err = run("override", str(mod), G_INV.upper(), "--steam-root", str(root))
    assert code == 0, err
    written = mod / "Assets" / "Override of SmallCrate.partialdef"
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["$Bundles"] == {"Game2": "4.0.0.3199", "System.Runtime": "1.0.0.0", "VRage": "4.0.0.3199"}
    assert data["$Type"] == "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff"
    assert data["$Value"] == {"BaseDefinition": G_INV, "PartialDefinitionKind": "Override", "Manipulator": {},
                              "DefinitionType": "Game2:Keen.Game2.Test.InventoryDefinition",
                              "PriorityOverride": False}
    assert '"MaxVolume": 4.0' in out
    code, _out, err = run("override", str(mod), G_INV, "--steam-root", str(root))
    assert code == 1 and "already exists" in err


def test_show_prints_vanilla_def(tmp_path, run):
    root, game, _ = _steam(tmp_path)
    _vanilla_def(game)
    code, out, _ = run("show", G_INV, "--steam-root", str(root))
    assert code == 0
    assert out.splitlines()[0] == f"# {Path('Inventories/SmallCrate.def')}"
    assert json.loads(out.split("\n", 1)[1])["$Value"]["Guid"] == G_INV


def test_show_unknown_guid_is_one_sentence(tmp_path, run):
    root, game, _ = _steam(tmp_path)
    _vanilla_def(game)
    code, out, err = run("show", G_OUT[:-1] + "9", "--steam-root", str(root))
    assert code == 1 and "is not declared by any vanilla .def" in err and "Traceback" not in err


def test_show_without_game_names_how_to_set_it(run):
    code, _, err = run("show", G_INV)
    assert code == 1 and "Space Engineers 2 not found" in err


def test_new_scaffold_files(tmp_path, run):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    code, out, err = run("new", "My Mod")
    assert code == 0, err
    d = tmp_path / "My Mod"
    names = {p.name for p in d.iterdir()}
    assert names >= {"My Mod.vrgproj", "Assets.meta", "Content.meta", "ModProject.modhub-metadata", "Assets",
                     "Content", ".gitignore", ".gitattributes", "README.md", ".git"}  # full layout: tests/scaffold
    assert (d / ".gitignore").read_bytes() == mods_cmd.GITIGNORE.encode()
    assert (d / ".gitattributes").read_bytes() == mods_cmd.GITATTRIBUTES.encode()
    proj = json.loads((d / "My Mod.vrgproj").read_text(encoding="utf-8"))
    hub = json.loads((d / "ModProject.modhub-metadata").read_text(encoding="utf-8"))
    newest = load_compat().game_versions_tested[-1]
    assert proj["$Bundles"] == {"System.Runtime": "1.0.0.0", "VRage": newest}  # no SDK: newest tested build
    assert proj["$Value"]["ProjectID"]["Guid"] == hub["$Value"]["ProjectID"]["Guid"]
    assert hub["$Value"]["ProjectFile"] == "My Mod.vrgproj"
    assert (d / ".git").is_dir()
    code, _, err = run("new", "My Mod")
    assert code == 1 and "already exists" in err
    code, _, err = run("new", "bad/name")
    assert code == 1 and "letters, digits" in err


def test_new_stamps_from_sdk(tmp_path, run):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    sdk = fake_sdk.make_sdk(tmp_path / "sdk", "4.0.0.3199")
    code, _, err = run("new", "M", "--sdk", str(sdk))
    assert code == 0, err
    proj = json.loads((tmp_path / "M" / "M.vrgproj").read_text(encoding="utf-8"))
    assert proj["$Bundles"]["VRage"] == "4.0.0.3199"


def _copy_source(sdk: Path) -> Path:
    src = sdk / "GameData/Vanilla/Assets/Blocks/Foo"
    (src / "Sub").mkdir(parents=True)
    (src / "Foo.def").write_text(json.dumps({"$Type": "Game2:T", "$Value": {"Guid": G_A, "Kind": G_B, "Ext": G_OUT}},
                                            indent=2), encoding="utf-8")
    (src / "FooKind.partialdef").write_text(json.dumps({"$Type": "VRage:P", "$Value": {
        "PartialDefinitionKind": "Copy", "BaseDefinition": G_OUT, "Manipulator": {"Guid": G_B, "Block": G_A}}},
        indent=2), encoding="utf-8")
    (src / "FooFix.partialdef").write_text(json.dumps({"$Type": "VRage:P", "$Value": {
        "PartialDefinitionKind": "Override", "BaseDefinition": G_OUT}}), encoding="utf-8")
    (src / "Sub" / "FooDeep.def").write_text(json.dumps({"$Value": {"Guid": G_OUT}}), encoding="utf-8")
    return src


def test_copy_rewires_guids(tmp_path, run):
    sdk = fake_sdk.make_sdk(tmp_path / "sdk")
    _copy_source(sdk)
    mod = _mod(tmp_path, "MyMod")
    code, out, err = run("copy", str(mod), "Blocks/Foo", "--from", "Foo", "--to", "Bar", "--sdk", str(sdk))
    assert code == 0, err
    dest = mod / "Assets" / "Bar"
    assert sorted(p.name for p in dest.iterdir()) == ["Bar.def", "BarKind.partialdef"]  # not recursive, no Override
    cmap = json.loads((mod / ".clonemap.json").read_text(encoding="utf-8"))
    assert set(cmap) == {G_A, G_B}
    new_a, new_b = cmap[G_A], cmap[G_B]
    block = json.loads((dest / "Bar.def").read_text(encoding="utf-8"))["$Value"]
    kind = json.loads((dest / "BarKind.partialdef").read_text(encoding="utf-8"))["$Value"]
    assert block == {"Guid": new_a, "Kind": new_b, "Ext": G_OUT}  # outside reference untouched
    assert kind["Manipulator"] == {"Guid": new_b, "Block": new_a} and kind["BaseDefinition"] == G_OUT
    assert "skip (Override, nothing to clone)" in out
    code, _, err = run("copy", str(mod), "Blocks/Foo", "--from", "Foo", "--to", "Bar", "--sdk", str(sdk))
    assert code == 1 and "--force" in err


def test_copy_keyed_map_recursive(tmp_path, run):
    sdk = fake_sdk.make_sdk(tmp_path / "sdk")
    _copy_source(sdk)
    mod = _mod(tmp_path, "MyMod")
    for key in ("one", "two"):
        code, _, err = run("copy", str(mod), "Blocks/Foo", "--from", "Foo", "--to", key, "-r", "--map-key", key,
                           "--sdk", str(sdk))
        assert code == 0, err
    raw = json.loads((mod / ".clonemap.json").read_text(encoding="utf-8"))
    assert raw["$keyed"] is True
    one, two = raw["maps"]["one"], raw["maps"]["two"]
    assert set(one["guids"]) == {G_A, G_B, G_OUT} and one["guids"][G_A] != two["guids"][G_A]
    assert one["copies"] == [{"src": "Blocks/Foo", "from": "Foo", "to": "one", "also": [], "dest": "one",
                              "recursive": True, "definitions": 3}]
    assert (mod / "Assets/two/Sub/twoDeep.def").is_file()
    code, _, err = run("copy", str(mod), "Blocks/Foo", "--from", "Foo", "--to", "x", "--sdk", str(sdk))
    assert code == 1 and "--map-key" in err


def _project(tmp_path):
    for name in ("Core", "Drives"):
        d = _mod(tmp_path / "mods", name)
        (d / "Assets" / "a.partialdef").write_text("{}", encoding="utf-8")
        (d / "Content").mkdir()
    (tmp_path / "mods/Drives/Content/x.def").write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.se2modkit]\n[tool.se2modkit.mods.Core]\npath = "mods/Core"\n'
        '[tool.se2modkit.mods.Drives]\npath = "mods/Drives"\n', encoding="utf-8")


def test_list_shows_project_mods(tmp_path, run):
    _project(tmp_path)
    code, out, err = run("list")
    assert code == 0, err
    assert out.splitlines() == [f"{'Core':30} 1 partialdef(s), 0 compiled def(s)",
                                f"{'Drives':30} 1 partialdef(s), 1 compiled def(s)"]


def test_list_workspace_mods_from_env(tmp_path, run, monkeypatch):
    _mod(tmp_path / "ws" / "mods", "Solo")
    monkeypatch.setenv("SE2_WORKSPACE", str(tmp_path / "ws"))
    code, out, _ = run("list")
    assert code == 0 and out.splitlines() == [f"{'Solo':30} 0 partialdef(s), 0 compiled def(s)"]


def test_resolve_mod_order(tmp_path, monkeypatch):
    _project(tmp_path)
    env = core.discover_env(environ={}, platform="linux", home=tmp_path / "home", config={}, sysroot=tmp_path / "s")
    project = load_project(tmp_path)
    assert mods_cmd.resolve_mod(env, project, "Core") == tmp_path / "mods/Core"
    assert mods_cmd.resolve_mod(env, None, str(tmp_path / "mods/Drives")) == tmp_path / "mods/Drives"
    other = _mod(tmp_path / "elsewhere", "Far")
    cfg = env.config_dir / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"workspace_mods = [{json.dumps(str(other.parent))}]\n", encoding="utf-8")
    assert mods_cmd.resolve_mod(env, project, "Far") == other
    with pytest.raises(ProjectError, match="Mod 'Nope' not found"):
        mods_cmd.resolve_mod(env, project, "Nope")


def _load_wrapper(path: Path):
    spec = importlib.util.spec_from_file_location("se2mod_wrapper_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wrapper_exposes_legacy_names(tmp_path, monkeypatch):
    wrapper = _wrapper()
    sdk = fake_sdk.make_sdk(tmp_path / "sdk", "4.0.0.3199")
    _copy_source(sdk)
    monkeypatch.setattr(core, "discover_env", functools.partial(
        core.discover_env, environ={"SE2_SDK_ASSETS": str(sdk)}, platform="linux", home=tmp_path / "home",
        config={}, sysroot=tmp_path / "sys"))
    ws = tmp_path / "ws"
    script = ws / "tools" / "scripts" / "se2mod.py"
    script.parent.mkdir(parents=True)
    shutil.copy(wrapper, script)
    monkeypatch.setenv("SE2_WORKSPACE", str(ws))
    se2mod = _load_wrapper(script)
    assert se2mod.GAME_BUNDLES["Game2"] == "4.0.0.3199"
    assert se2mod.VRAGE_BUNDLES == {"System.Runtime": "1.0.0.0", "VRage": "4.0.0.3199"}
    assert se2mod.SDK_ASSETS == sdk / "GameData/Vanilla/Assets"
    assert se2mod.MODS == ws / "mods"
    assert se2mod.envelope("T", {})["$Bundles"] == se2mod.VRAGE_BUNDLES
    assert se2mod.own_guid({"$Value": {"PartialDefinitionKind": "Copy", "Manipulator": {"Guid": G_A}}}) == G_A
    # clone_block.py / dc/clone.py: in-process cmd_copy with a bare Namespace and a patched mod_dir.
    import argparse
    target = _mod(tmp_path / "pinned", "Pinned")
    se2mod.mod_dir = lambda name: target
    se2mod.cmd_copy(argparse.Namespace(mod="Pinned", src="Blocks/Foo", from_="Foo", to="Baz", dest=None,
                                       recursive=False, force=False))
    assert (target / "Assets/Baz/Baz.def").is_file()
    with pytest.raises(SystemExit):
        se2mod.cmd_copy(argparse.Namespace(mod="Pinned", src="Nope", from_="a", to="b", dest=None,
                                           recursive=False, force=False))


def test_wrapper_runs_every_command_in_modkit(tmp_path, monkeypatch, capsys):
    wrapper = _wrapper()
    script = tmp_path / "ws" / "tools" / "scripts" / "se2mod.py"
    script.parent.mkdir(parents=True)
    shutil.copy(wrapper, script)
    monkeypatch.setenv("SE2_WORKSPACE", str(tmp_path / "ws"))
    se2mod = _load_wrapper(script)
    assert not script.with_name("se2mod_legacy.py").exists()
    assert se2mod.main(["build", "X"]) == 1  # modkit's own one-sentence error, no legacy script involved
    assert "Mod 'X' not found" in capsys.readouterr().err
