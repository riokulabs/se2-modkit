import os
from pathlib import Path

import pytest

from se2modkit.env.core import EnvError
from se2modkit.env.proton import (
    game_mods_dir_proton,
    prefix_for,
    proton_build,
    to_wine_path,
    wine_drive_map,
)

pytestmark = pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="Wine prefixes are Linux-only")


def _prefix(tmp_path: Path, drives: dict[str, Path | str]) -> Path:
    pfx = tmp_path / "compatdata" / "1133870" / "pfx"
    dd = pfx / "dosdevices"
    dd.mkdir(parents=True)
    (pfx / "drive_c").mkdir()
    for letter, target in drives.items():
        os.symlink(str(target), dd / letter)
    os.symlink("/dev/ttyS0", dd / "com1")  # not a drive
    os.symlink("/dev/sr0", dd / "d::")  # raw device, not a drive
    return pfx


def test_prefix_for(tmp_path):
    assert prefix_for(tmp_path / "steamapps") == tmp_path / "steamapps/compatdata/1133870/pfx"


def test_wine_drive_map_and_path(tmp_path):
    steamapps = tmp_path / "steamapps"
    steamapps.mkdir()
    pfx = _prefix(tmp_path, {"c:": "../drive_c", "z:": "/", "s:": steamapps})
    drives = wine_drive_map(pfx)
    assert set(drives) == {"C:", "S:", "Z:"}
    assert drives["Z:"] == Path("/")
    assert drives["C:"] == (pfx / "drive_c").resolve()
    assert to_wine_path(steamapps / "common/X", drives) == "S:\\common\\X"
    assert to_wine_path(steamapps, drives) == "S:\\"
    elsewhere = tmp_path / "other" / "dir"
    assert to_wine_path(elsewhere, drives) == "Z:" + str(elsewhere.resolve()).replace("/", "\\")
    assert to_wine_path(pfx / "drive_c/users", drives) == "C:\\users"


def test_wine_path_non_ascii(tmp_path):
    lib = tmp_path / "Jörg" / "Spiele Bibliothek"
    lib.mkdir(parents=True)
    drives = wine_drive_map(_prefix(tmp_path, {"z:": "/", "s:": lib}))
    p = lib / "steamapps/common/Space Engineers 2 - Mod SDK/Mods/Größe Ü"
    assert to_wine_path(p, drives) == "S:\\steamapps\\common\\Space Engineers 2 - Mod SDK\\Mods\\Größe Ü"
    outside = tmp_path / "Jörg" / "Mods mit Leerzeichen"
    assert to_wine_path(outside, drives) == "Z:" + str(outside.resolve()).replace("/", "\\")


def test_wine_path_without_covering_drive_is_plain_error(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    drives = wine_drive_map(_prefix(tmp_path, {"s:": lib}))
    with pytest.raises(EnvError, match="no Wine drive"):
        to_wine_path(tmp_path / "elsewhere", drives)


def test_wine_drive_map_missing_prefix(tmp_path):
    assert wine_drive_map(tmp_path / "nope") == {}


def test_game_mods_dir_proton(tmp_path):
    assert game_mods_dir_proton(tmp_path) == tmp_path / "drive_c/users/steamuser/Documents/SpaceEngineers2/Mods"


def _tool(root: Path, dirname: str, internal: str | None = None) -> Path:
    d = root / "compatibilitytools.d" / dirname
    d.mkdir(parents=True)
    (d / "proton").write_text("#!/bin/sh\n", encoding="utf-8")
    name = internal or dirname
    (d / "compatibilitytool.vdf").write_text(
        f'"compatibilitytools"\n{{\n  "compat_tools"\n  {{\n    "{name}" // internal name\n'
        '    {\n      "install_path" "."\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    return d


def _compat_mapping(root: Path, mapping: dict[str, str]) -> None:
    entries = "".join(f'\t\t\t\t\t"{app}"\n\t\t\t\t\t{{\n\t\t\t\t\t\t"name"\t\t"{name}"\n\t\t\t\t\t}}\n'
                      for app, name in mapping.items())
    text = ('"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
            f'\t\t\t\t"CompatToolMapping"\n\t\t\t\t{{\n{entries}\t\t\t\t}}\n\t\t\t}}\n\t\t}}\n\t}}\n}}\n')
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "config.vdf").write_text(text, encoding="utf-8")


def test_proton_from_compat_mapping(tmp_path):
    root = tmp_path / "Steam"
    _tool(root, "GE-Proton9-1")
    picked = _tool(root, "my-custom-dir", internal="GE-Proton8-5")
    _tool(root, "GE-Proton10-2")
    _compat_mapping(root, {"0": "GE-Proton10-2", "1133870": "GE-Proton8-5"})
    assert proton_build(root, configured=None) == picked


def test_proton_newest_fallback(tmp_path):
    root = tmp_path / "Steam"
    _tool(root, "GE-Proton9-27")
    newest = _tool(root, "GE-Proton10-3")
    _tool(root, "GE-Proton10-1")
    (root / "compatibilitytools.d" / "not-a-tool").mkdir()
    # a mapping for another app or an unresolvable name does not win
    _compat_mapping(root, {"0": "GE-Proton9-27", "1133870": "proton_unknown"})
    assert proton_build(root, configured=None) == newest


def test_proton_configured_wins(tmp_path):
    root = tmp_path / "Steam"
    _tool(root, "GE-Proton10-3")
    mine = _tool(root, "GE-Proton9-1")
    assert proton_build(root, configured="GE-Proton9-1") == mine
    absolute = tmp_path / "elsewhere" / "Proton Jörg"
    assert proton_build(root, configured=str(absolute)) == absolute


def test_proton_none_when_nothing_installed(tmp_path):
    assert proton_build(tmp_path, configured=None) is None


def _official(lib: Path, dirname: str) -> Path:
    d = lib / "steamapps" / "common" / dirname
    d.mkdir(parents=True)
    (d / "proton").write_text("#!/bin/sh\n", encoding="utf-8")
    (d / "toolmanifest.vdf").write_text('"manifest"\n{\n  "commandline" "/proton %verb%"\n}\n', encoding="utf-8")
    return d


def test_proton_mapping_to_official_in_second_library_wins(tmp_path):
    root, lib2 = tmp_path / "Steam", tmp_path / "Second Library"
    _tool(root, "GE-Proton10-3")
    _official(root, "Proton 8.0")
    nine = _official(lib2, "Proton 9.0")
    _compat_mapping(root, {"1133870": "proton_9"})
    assert proton_build(root, configured=None, libraries=[root, lib2]) == nine
    exp = _official(lib2, "Proton - Experimental")
    _compat_mapping(root, {"1133870": "proton_experimental"})
    assert proton_build(root, configured=None, libraries=[root, lib2]) == exp
    hot = _official(root, "Proton Hotfix")
    assert proton_build(root, configured="proton_hotfix", libraries=[root, lib2]) == hot


def test_proton_point_release_naming(tmp_path):
    root = tmp_path / "Steam"
    six = _official(root, "Proton 6.3")
    _compat_mapping(root, {"1133870": "proton_63"})
    assert proton_build(root, configured=None) == six


def test_proton_only_official_install_resolves(tmp_path):
    root, lib2 = tmp_path / "Steam", tmp_path / "Lib2"
    exp = _official(lib2, "Proton - Experimental")
    assert proton_build(root, configured=None, libraries=[root, lib2]) == exp
    nine = _official(root, "Proton 9.0")
    _official(root, "Proton 8.0")
    assert proton_build(root, configured=None, libraries=[root, lib2]) == nine


def test_wine_drive_map_skips_symlink_loop(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    pfx = _prefix(tmp_path, {"z:": "/", "s:": lib})
    dd = pfx / "dosdevices"
    os.symlink("f:", dd / "e:")
    os.symlink("e:", dd / "f:")
    drives = wine_drive_map(pfx)
    assert set(drives) == {"S:", "Z:"}
