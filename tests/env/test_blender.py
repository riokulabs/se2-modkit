from pathlib import Path

from se2modkit.env.blender import find_blender
from se2modkit.env.config import Resolved


def _exe(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


def _no_which(name, path=None):
    return None


def _linux(tmp_path, **kw):
    args = dict(flag=None, platform="linux", environ={}, config={}, which=_no_which,
                home=tmp_path / "home", sysroot=tmp_path / "sys")
    args.update(kw)
    return find_blender(**args)


def test_blender_order_linux(tmp_path):
    sys_ = tmp_path / "sys"
    home = tmp_path / "home"
    flatpak_user = _exe(home / ".local/share/flatpak/exports/bin/org.blender.Blender")
    flatpak_sys = _exe(sys_ / "var/lib/flatpak/exports/bin/org.blender.Blender")
    assert _linux(tmp_path) == Resolved(flatpak_sys, "discovered")
    flatpak_sys.unlink()
    assert _linux(tmp_path) == Resolved(flatpak_user, "discovered")
    usr = _exe(sys_ / "usr/bin/blender")
    assert _linux(tmp_path) == Resolved(usr, "discovered")
    snap = _exe(sys_ / "snap/bin/blender")
    assert _linux(tmp_path) == Resolved(snap, "discovered")
    on_path = "/opt/blender-on-path/blender"
    which = lambda name, path=None: on_path if name == "blender" else None  # noqa: E731
    assert _linux(tmp_path, which=which) == Resolved(Path(on_path), "discovered")
    assert _linux(tmp_path, which=which, config={"blender": "/cfg/blender"}) == Resolved("/cfg/blender", "config")
    env = {"BLENDER": "/env2/blender", "SE2_BLENDER": "/env1/blender"}
    assert _linux(tmp_path, which=which, config={"blender": "/cfg"}, environ=env) == Resolved("/env1/blender", "env")
    assert _linux(tmp_path, environ={"BLENDER": "/env2/blender"}) == Resolved("/env2/blender", "env")
    assert _linux(tmp_path, flag="/flag/blender", environ=env) == Resolved("/flag/blender", "flag")


def test_blender_missing_linux(tmp_path):
    assert _linux(tmp_path) == Resolved(None, "missing")


def test_blender_path_lookup_uses_given_environ(tmp_path):
    seen = []

    def which(name, path=None):
        seen.append((name, path))
        return None

    _linux(tmp_path, which=which, environ={"PATH": "/only/here"})
    assert ("blender", "/only/here") in seen


def test_blender_windows_newest_first(tmp_path):
    pf = tmp_path / "Program Files"
    base = pf / "Blender Foundation"
    _exe(base / "Blender 4.2" / "blender.exe")
    newest = _exe(base / "Blender 4.10" / "blender.exe")
    _exe(base / "Blender 3.6" / "blender.exe")
    (base / "Blender 9.9").mkdir()  # no exe inside
    lib = tmp_path / "SteamLib"
    _exe(lib / "steamapps/common/Blender/blender.exe")
    got = find_blender(flag=None, platform="win32", environ={"ProgramFiles": str(pf)}, config={},
                       which=_no_which, steam_libraries=[lib])
    assert got == Resolved(newest, "discovered")


def test_blender_windows_steam_fallback(tmp_path):
    lib1, lib2 = tmp_path / "Lib1", tmp_path / "Lib Jörg"
    steam_blender = _exe(lib2 / "steamapps/common/Blender/blender.exe")
    got = find_blender(flag=None, platform="win32", environ={"ProgramFiles": str(tmp_path / "none")}, config={},
                       which=_no_which, steam_libraries=[lib1, lib2])
    assert got == Resolved(steam_blender, "discovered")


def test_blender_windows_path_uses_exe_name(tmp_path):
    which = lambda name, path=None: "C:/B/blender.exe" if name == "blender.exe" else None  # noqa: E731
    got = find_blender(flag=None, platform="win32", environ={}, config={}, which=which)
    assert got == Resolved(Path("C:/B/blender.exe"), "discovered")
