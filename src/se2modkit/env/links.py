"""Directory links that work without admin rights: symlinks on POSIX, directory junctions on Windows."""

from __future__ import annotations

import os
import stat
from pathlib import Path

__all__ = ["is_junction", "is_link", "make_link", "remove_link", "same_target"]


def is_junction(p: Path) -> bool:
    if hasattr(os.path, "isjunction"):  # Python 3.12+
        return os.path.isjunction(p)
    try:
        st = os.lstat(p)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)) and not stat.S_ISLNK(st.st_mode)


def is_link(p: Path | None) -> bool:
    """A symlink or (Windows) directory junction; never follows it."""
    return p is not None and (Path(p).is_symlink() or is_junction(Path(p)))


def same_target(a: Path, b: Path) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def make_link(link: Path, target: Path, *, platform: str) -> None:
    """`link` -> `target` (a directory): a junction on Windows, a symlink elsewhere."""
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    if platform.startswith("win"):
        import _winapi  # Windows only
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_link(link: Path) -> None:
    """Remove the link itself, never what it points at."""
    if is_junction(link):
        os.rmdir(link)
    else:
        Path(link).unlink()
