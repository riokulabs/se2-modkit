"""The preview texture cache (no Blender): where the preview materials write the PNGs they convert from the game's DDS
and the SDK's large textures. Per user, outside every repository, private.

  cache_path(environ)  $SE2MODKIT_TEXCACHE, else the mod's legacy `<PREFIX>TEXCACHE` (with `legacy_prefix`, accepted
                       for one release), else `user_cache_dir()/texcache`;
  ensure(d)            creates d at mode 0o700, or accepts an existing d only when it is a real directory (not a
                       symlink) owned by the current user and not group- or world-writable (POSIX; on Windows only the
                       directory is created); raises TexcacheError otherwise;
  cache_dir(environ)   ensure(cache_path(environ));
  mkstemp(d)           a fresh temp file in d (tempfile.mkstemp: exclusive, unpredictable): write there, then
                       os.replace onto the final name, so parallel renders can share the cache.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from ..env.config import user_cache_dir

__all__ = ["ENV", "TexcacheError", "cache_dir", "cache_path", "ensure", "mkstemp"]

ENV = "SE2MODKIT_TEXCACHE"
_POSIX = not sys.platform.startswith("win")


class TexcacheError(PermissionError):
    """The cache directory is not safe to use; the message is one plain sentence naming the fix."""


def cache_path(environ: Mapping[str, str] = os.environ, *, legacy_prefix: str | None = None) -> Path:
    for name in (ENV,) + ((f"{legacy_prefix}TEXCACHE",) if legacy_prefix else ()):
        if environ.get(name):
            return Path(environ[name]).expanduser()
    return user_cache_dir(environ=environ) / "texcache"


def ensure(d: str | os.PathLike) -> Path:
    """The cache directory d, created private or checked safe; returns it as a Path."""
    d = Path(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        d.mkdir(mode=0o700)
        if _POSIX:
            os.chmod(d, 0o700)         # (mkdir's mode is masked by the umask; never wider than 0o700 anyway)
    except FileExistsError:
        pass
    if not _POSIX:
        if not d.is_dir():
            raise TexcacheError(f"The texture cache {d} is not a directory: set {ENV} to a folder of your own.")
        return d
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode):
        raise TexcacheError(f"The texture cache {d} is not a directory (a symlink?): set {ENV} to a folder of your own.")
    if st.st_uid != os.getuid():
        raise TexcacheError(f"The texture cache {d} is owned by uid {st.st_uid}, not you: set {ENV} to a folder of "
                            "your own.")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise TexcacheError(f"The texture cache {d} is group- or world-writable ({oct(st.st_mode & 0o777)}): "
                            f"chmod 700 it or set {ENV}.")
    return d


def cache_dir(environ: Mapping[str, str] = os.environ, *, legacy_prefix: str | None = None) -> Path:
    """The checked private cache directory (created if missing)."""
    return ensure(cache_path(environ, legacy_prefix=legacy_prefix))


def mkstemp(d: str | os.PathLike, suffix: str = "") -> tuple[int, Path]:
    """(fd, path) of a new private temp file in the checked cache d."""
    fd, p = tempfile.mkstemp(dir=os.fspath(d), prefix=".tmp-", suffix=suffix)
    return fd, Path(p)
