"""Thin, read-only git helpers (subprocess, explicit timeouts)."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, binary: bool = False, check: bool = True, timeout: int = 120):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=timeout)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout if binary else p.stdout.decode("utf-8", errors="replace")


def head_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def toplevel(repo: Path) -> Path | None:
    try:
        return Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    except GitError:
        return None


def is_linked_worktree(repo: Path) -> bool:
    gd = Path(git(repo, "rev-parse", "--absolute-git-dir").strip()).resolve()
    common = git(repo, "rev-parse", "--git-common-dir").strip()
    common_p = Path(common) if Path(common).is_absolute() else (repo / common)
    return gd != common_p.resolve() or not (repo / ".git").is_dir()


def prefix(repo: Path) -> str:
    """`repo`'s path inside its git work tree ("" at the top, "mods/Core/" in a multi-mod repo's mod folder)."""
    return git(repo, "rev-parse", "--show-prefix").strip()


def _strip(pre: str, path: str) -> str | None:
    return path[len(pre):] if path.startswith(pre) else None


def ls_tree(repo: Path, rev: str = "HEAD", path: str | None = None) -> list[tuple[str, str, str, str]]:
    """[(mode, type, sha, path)] for every blob under path at rev (recursive). `path` and the returned paths are
    relative to `repo`, which may be a folder inside the work tree (a mod in a multi-mod repo)."""
    pre = prefix(repo)
    args = ["ls-tree", "-r", "-z", "--full-tree", rev]
    if path or pre:
        args += ["--", (pre + path) if path else pre]
    out = git(repo, *args)
    rows = []
    for entry in filter(None, out.split("\0")):
        meta, p = entry.split("\t", 1)
        mode, typ, sha = meta.split()
        rel = _strip(pre, p)
        if rel is not None:
            rows.append((mode, typ, sha, rel))
    return rows


def cat_blob(repo: Path, sha: str) -> bytes:
    return git(repo, "cat-file", "blob", sha, binary=True)


def status_porcelain(repo: Path, paths: list[str] | None = None) -> list[tuple[str, str]]:
    """[(XY, path)] including untracked files, for the given pathspecs (or the whole repo); paths relative to
    `repo` like `ls_tree` (git itself reports them from the top of the work tree)."""
    pre = prefix(repo)
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
    if paths:
        args += ["--", *paths]
    elif pre:
        args += ["--", "."]
    out = git(repo, *args)
    rows = []
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if not e:
            continue
        xy, p = e[:2], e[3:]
        if xy[0] in "RC":
            i += 1  # rename source follows
        rows.append((xy, _strip(pre, p) or p))
    return rows


def commit_exists(repo: Path, sha: str) -> bool:
    return git(repo, "cat-file", "-t", sha, check=False).strip() == "commit"


def log_subjects(repo: Path, since: str | None, limit: int = 50) -> list[str]:
    """Commit subjects since `since`; inside a multi-mod repo only commits touching `repo`'s folder."""
    rng = f"{since}..HEAD" if since else "HEAD"
    args = ["log", "--no-merges", f"--max-count={limit}", "--format=%s", rng]
    if prefix(repo):
        args += ["--", "."]
    out = git(repo, *args)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]
