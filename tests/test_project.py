import pytest

from se2modkit.project import ProjectError, load_project, portable_errors


def _mod(root, name):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.vrgproj").write_text("<Project/>", encoding="utf-8")


def test_single_mod_project(tmp_path):
    _mod(tmp_path, "DemoMod")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.se2modkit]\nmod_name = "DemoMod"\nprefix = "XX_"\nprofile = "p"\ngates = ["a", "b"]\n',
        encoding="utf-8",
    )
    (tmp_path / "sub").mkdir()
    p = load_project(tmp_path / "sub")
    m = p.mod(None)
    assert m.name == "DemoMod" and m.prefix == "XX_" and m.gates == ("a", "b")
    assert m.paths.report == tmp_path / "Source/Model/build_report.json"
    assert m.paths.staged == tmp_path / "Source/Model/integration/Assets"
    assert m.paths.golden_states == tmp_path / "Tests/golden/states_baseline.json"


MULTI = """
[tool.se2modkit]
prefix = "X_"
profile = "base"
gates = ["g"]

[tool.se2modkit.mods.Core]
path = "mods/Core"

[tool.se2modkit.mods.Drives]
path = "mods/Drives"
profile = "special"
"""


@pytest.fixture
def multi(tmp_path):
    (tmp_path / "pyproject.toml").write_text(MULTI, encoding="utf-8")
    _mod(tmp_path / "mods/Core", "Core")
    _mod(tmp_path / "mods/Drives", "Drives")
    return tmp_path


def test_multi_mod_project(multi):
    p = load_project(multi)
    assert set(p.mods) == {"Core", "Drives"}
    assert p.mod("Core").profile == "base" and p.mod("Core").prefix == "X_"
    assert p.mod("Drives").profile == "special" and p.mod("Drives").gates == ("g",)
    assert p.mod("Drives").root == multi / "mods/Drives"
    assert p.mod("Drives").paths.models == multi / "mods/Drives/Source/Model/Models"


def test_mod_selected_by_cwd(multi):
    p = load_project(multi)
    inner = multi / "mods/Drives/Source"
    inner.mkdir()
    assert p.mod(None, cwd=inner).name == "Drives"


def test_ambiguous_mod_lists_names(multi):
    p = load_project(multi)
    for cwd in (None, multi):
        with pytest.raises(ProjectError) as e:
            p.mod(None, cwd=cwd)
        assert "Core" in str(e.value) and "Drives" in str(e.value)
    with pytest.raises(ProjectError, match="Core"):
        p.mod("Nope")


def test_portable_rejects_absolute_and_drive_letters():
    for bad in ("/home/x", "C:\\x", "~/x"):
        errs = portable_errors({"path": bad}, "tool.se2modkit")
        assert len(errs) == 1 and "tool.se2modkit.path" in errs[0]
    assert portable_errors({"path": "mods/x"}, "w") == []


def test_non_portable_value_fails_load(tmp_path):
    _mod(tmp_path, "A")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.se2modkit]\nmod_name = "A"\nprefix = "/abs"\n', encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="prefix"):
        load_project(tmp_path)


def test_mod_without_vrgproj_errors(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "A"\n', encoding="utf-8")
    with pytest.raises(ProjectError, match="vrgproj"):
        load_project(tmp_path)


def test_default_profile_is_vanilla(tmp_path):
    _mod(tmp_path, "A")
    (tmp_path / "pyproject.toml").write_text('[tool.se2modkit]\nmod_name = "A"\n', encoding="utf-8")
    assert load_project(tmp_path).mod(None).profile == "vanilla"


def test_malformed_pyproject_is_a_plain_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.se2modkit\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="pyproject.toml"):
        load_project(tmp_path)


def test_mod_paths_root_assets_and_guid_store(tmp_path):
    _mod(tmp_path, "XxMod")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.se2modkit]\nmod_name = "XxMod"\nprefix = "XX_"\n[tool.se2modkit.paths]\nasset_guids = "g.json"\n'
        'root = "ignored"\n', encoding="utf-8")
    m = load_project(tmp_path).mod(None)
    assert m.paths.root == tmp_path and m.paths.assets == tmp_path / "Assets"
    assert m.paths.clone_map == tmp_path / ".clonemap.json" and m.paths.asset_guids == tmp_path / "g.json"
    from dataclasses import replace
    with pytest.raises(ProjectError, match="no root"):
        _ = replace(m.paths, root=None).assets


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", " lead", "trail.", "x\\y"])
def test_mod_name_must_be_plain(tmp_path, bad):
    _mod(tmp_path, "Mod")
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.se2modkit]\nmod_name = "{bad.replace(chr(92), chr(92) * 2)}"\nprefix = "XX_"\n', encoding="utf-8")
    with pytest.raises(ProjectError, match="Mod name") as e:
        load_project(tmp_path)
    assert "\n" not in str(e.value)
