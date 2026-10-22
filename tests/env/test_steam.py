import fake_steam

from se2modkit.env.steam import (
    GAME_APP_ID,
    SDK_APP_ID,
    app_dir,
    app_manifest,
    find_steam_root,
    libraries,
    parse_vdf,
    steam_root_candidates,
)

SDK_DIR_NAME = "Space Engineers 2 - Mod SDK"


def test_parse_vdf_nested_and_comments():
    text = """
// leading comment
"Outer"
{
    "Key"   "Value"   // trailing comment
    "Inner"
    {
        "Path"  "C:\\\\Games\\\\Jörg \\"x\\""
        "Deep" { "a" "1" }
    }
}
"""
    d = parse_vdf(text)
    assert d["Outer"]["Key"] == "Value"
    assert d["Outer"]["Inner"]["Path"] == 'C:\\Games\\Jörg "x"'
    assert d["Outer"]["Inner"]["Deep"] == {"a": "1"}


def test_root_order_linux(tmp_path):
    for rel in (".steam/steam", ".local/share/Steam"):
        (tmp_path / rel / "steamapps").mkdir(parents=True)
    assert find_steam_root(home=tmp_path, platform="linux", environ={}) == tmp_path / ".steam/steam"
    cands = steam_root_candidates(home=tmp_path, platform="linux", environ={})
    assert cands[:3] == [
        tmp_path / ".steam/steam",
        tmp_path / ".steam/debian-installation",
        tmp_path / ".local/share/Steam",
    ]


def test_flatpak_steam_root(tmp_path):
    root = tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    (root / "steamapps").mkdir(parents=True)
    assert find_steam_root(home=tmp_path, platform="linux", environ={}) == root


def test_windows_registry_root(tmp_path):
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    got = find_steam_root(
        home=tmp_path, platform="win32", environ={}, read_registry=lambda: str(root)
    )
    assert got == root


def test_windows_programfiles_fallback(tmp_path):
    pf = tmp_path / "Program Files (x86)"
    (pf / "Steam" / "steamapps").mkdir(parents=True)
    env = {"ProgramFiles(x86)": str(pf)}
    assert find_steam_root(home=tmp_path, platform="win32", environ=env, read_registry=lambda: None) == pf / "Steam"


def test_game_and_sdk_in_different_libraries(tmp_path):
    root = tmp_path / "Steam"
    lib2 = tmp_path / "second library"
    fake_steam.make(
        root,
        libraries=[lib2],
        apps={
            GAME_APP_ID: {"installdir": "SpaceEngineers2", "buildid": 111},
            SDK_APP_ID: {"library": lib2, "installdir": SDK_DIR_NAME, "buildid": 222},
        },
    )
    libs = libraries(root)
    assert libs == [root, lib2]
    assert app_dir(libs, GAME_APP_ID) == root / "steamapps/common/SpaceEngineers2"
    assert app_dir(libs, SDK_APP_ID) == lib2 / "steamapps/common" / SDK_DIR_NAME


def test_sdk_dir_with_spaces(tmp_path):
    root = tmp_path / "Steam"
    fake_steam.make(root, apps={SDK_APP_ID: {"installdir": SDK_DIR_NAME}})
    assert app_dir(libraries(root), SDK_APP_ID).name == "Space Engineers 2 - Mod SDK"


def test_non_ascii_library_path(tmp_path):
    root = tmp_path / "Steam"
    lib2 = tmp_path / "Jörg" / "Spiele Bibliothek"
    fake_steam.make(root, libraries=[lib2], apps={GAME_APP_ID: {"library": lib2, "installdir": "SpaceEngineers2"}})
    libs = libraries(root)
    assert lib2 in libs
    assert app_dir(libs, GAME_APP_ID) == lib2 / "steamapps/common/SpaceEngineers2"


def test_buildid_from_manifest(tmp_path):
    root = tmp_path / "Steam"
    fake_steam.make(root, apps={SDK_APP_ID: {"installdir": SDK_DIR_NAME, "buildid": 26049505}})
    m = app_manifest(root, SDK_APP_ID)
    assert m == {"installdir": SDK_DIR_NAME, "buildid": "26049505"}


def test_missing_steam_returns_none(tmp_path):
    assert find_steam_root(home=tmp_path, platform="linux", environ={}) is None
    assert find_steam_root(home=tmp_path, platform="win32", environ={}, read_registry=lambda: None) is None
    assert libraries(tmp_path / "nope") == []
    assert app_manifest(tmp_path / "nope", GAME_APP_ID) is None
    assert app_dir([tmp_path / "nope"], GAME_APP_ID) is None
