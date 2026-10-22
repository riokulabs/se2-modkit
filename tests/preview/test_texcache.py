"""The preview texture cache is private (CWE-377 / CWE-59): never a shared, predictable temp dir or fixed temp names."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from se2modkit.preview import texcache as TC

posix = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX modes and owners")


@posix
def test_texcache_private_mode(tmp_path):
    d = tmp_path / "cache"
    old = os.umask(0)
    try:
        assert TC.cache_dir({"SE2MODKIT_TEXCACHE": str(d)}) == d
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.lstat(d).st_mode) == 0o700


@posix
def test_world_or_group_writable_refused(tmp_path):
    d = tmp_path / "shared"
    d.mkdir()
    for mode in (0o777, 0o775):
        os.chmod(d, mode)
        with pytest.raises(TC.TexcacheError, match="writable"):
            TC.ensure(d)
    os.chmod(d, 0o755)
    assert TC.ensure(d) == d


@posix
def test_symlink_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(TC.TexcacheError, match="not a directory"):
        TC.ensure(link)


@posix
def test_foreign_owner_refused(tmp_path, monkeypatch):
    d = tmp_path / "c"
    d.mkdir(mode=0o700)
    monkeypatch.setattr(TC.os, "getuid", lambda: os.stat(d).st_uid + 1)
    with pytest.raises(TC.TexcacheError, match="owned by"):
        TC.ensure(d)


def test_texcache_alias_env(tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    assert TC.cache_path({"XX_TEXCACHE": str(old)}, legacy_prefix="XX_") == old
    assert TC.cache_path({"SE2MODKIT_TEXCACHE": str(new), "XX_TEXCACHE": str(old)}, legacy_prefix="XX_") == new
    assert TC.cache_path({"XX_TEXCACHE": str(old)}) != old          # no alias without the mod's prefix


def test_default_is_the_user_cache(tmp_path):
    env = {"XDG_CACHE_HOME": str(tmp_path / "xdg"), "LOCALAPPDATA": str(tmp_path / "local")}
    p = TC.cache_path(env)
    assert p.name == "texcache" and str(tmp_path) in str(p)
    assert "/tmp/" not in Path(TC.__file__).read_text(encoding="utf-8")


def test_mkstemp_is_unique_and_inside(tmp_path):
    d = TC.ensure(tmp_path / "c")
    (fa, a), (fb, b) = TC.mkstemp(d, ".png"), TC.mkstemp(d, ".png")
    os.close(fa)
    os.close(fb)
    assert a != b and a.parent == d and b.parent == d and a.suffix == ".png"
    if not sys.platform.startswith("win"):
        assert stat.S_IMODE(os.stat(a).st_mode) == 0o600


def test_windows_skips_owner_check(tmp_path, monkeypatch):
    monkeypatch.setattr(TC, "_POSIX", False)
    d = tmp_path / "w"
    assert TC.ensure(d) == d and d.is_dir()
