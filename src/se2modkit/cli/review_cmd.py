"""`se2mod review-pack REPO BASE HEAD --out FILE`: one reviewable text file for a range of commits.

The pack holds the commit list, the diff stat, and a `-U10` diff of the source files. Generated paths (assets, models,
lock files, ...) are collapsed to a single stat line each, so a reviewer reads code, not megabytes of output. Works on
any git repository.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

from . import register

__all__ = ["DEFAULT_EXCLUDES", "build_pack", "is_generated"]

DEFAULT_EXCLUDES = ("Assets/**", "**/*.fbx", "**/*.gltf", "**/*.bin", "**/Models/**", "Content/**", "*.lock")


class PackError(Exception):
    """A problem the user can fix; reported as one sentence."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
                           capture_output=True, check=False)
    except FileNotFoundError:
        raise PackError("git is not installed or not on PATH.") from None
    if check and r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise PackError(msg[-1] if msg else f"git {args[0]} failed.")
    return r


def _text(r: subprocess.CompletedProcess) -> str:
    return r.stdout.decode("utf-8", "replace")


def is_generated(path: str, patterns) -> bool:
    """True if the repo-relative POSIX `path` matches one of the fnmatch-style `patterns` (`*` crosses `/`; a leading
    `**/` also matches at the repo root)."""
    for pat in patterns:
        pats = [pat, pat[3:]] if pat.startswith("**/") else [pat]
        if any(fnmatch.fnmatchcase(path, p) for p in pats):
            return True
    return False


def _resolve(repo: Path, ref: str, what: str) -> str:
    r = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if r.returncode != 0:
        raise PackError(f"{what} '{ref}' is not a commit in {repo}.")
    return _text(r).strip()


def build_pack(repo: Path, base: str, head: str, excludes=DEFAULT_EXCLUDES) -> str:
    repo = Path(repo)
    if not repo.is_dir() or _git(repo, "rev-parse", "--git-dir", check=False).returncode != 0:
        raise PackError(f"{repo} is not a git repository.")
    b, h = _resolve(repo, base, "BASE"), _resolve(repo, head, "HEAD")
    if _git(repo, "merge-base", "--is-ancestor", b, h, check=False).returncode != 0:
        raise PackError(f"BASE '{base}' is not an ancestor of HEAD '{head}'; pick a BASE on HEAD's history.")

    plain = ("diff", "--no-ext-diff", "--no-renames", "--no-color")
    log = _text(_git(repo, "log", "--reverse", "--format=%h %s (%an)", f"{b}..{h}")).rstrip()
    stat = _text(_git(repo, *plain, "--stat=120", b, h)).rstrip()
    numstat = _git(repo, *plain, "--numstat", "-z", b, h).stdout
    source: list[str] = []
    collapsed: list[str] = []
    for rec in filter(None, numstat.split(b"\0")):
        added, deleted, path = rec.decode("utf-8", "replace").split("\t", 2)
        if is_generated(path, excludes):
            what = "binary" if added == "-" else f"+{added} -{deleted}"
            collapsed.append(f"{path} | {what} (generated, diff omitted)")
        else:
            source.append(path)

    out = [f"# Review pack: {base}..{head}", f"repo: {repo}", "", "## Commits", log or "(none)", "",
           "## Stat", stat or "(no changes)", ""]
    if collapsed:
        out += ["## Generated paths (collapsed)", *collapsed, ""]
    out.append("## Diff (-U10, source files)")
    if source:  # never run with an empty pathspec: that would diff everything
        diff = _git(repo, *plain, "-U10", b, h, "--", *(f":(literal){p}" for p in source))
        out.append(_text(diff).rstrip())
    else:
        out.append("(no source changes)")
    return "\n".join(out) + "\n"


def _add_args(p) -> None:
    p.add_argument("repo", help="path to any git repository")
    p.add_argument("base", help="the commit the review starts after (must be an ancestor of HEAD)")
    p.add_argument("head", help="the last commit to review")
    p.add_argument("--out", required=True, metavar="FILE", help="file to write the pack to")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                   help="extra generated-path glob to collapse (repeatable; adds to the defaults)")


@register("review-pack", "Write commits, stat and diff of a git range to one file, generated paths collapsed",
          add_args=_add_args, check_game_version=False)
def cmd_review_pack(args, env) -> int:
    try:
        pack = build_pack(Path(args.repo), args.base, args.head, (*DEFAULT_EXCLUDES, *args.exclude))
    except PackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(pack)
    except OSError as e:
        print(f"error: cannot write {out}: {e.strerror or e}.", file=sys.stderr)
        return 1
    print(f"{out} {len(pack.splitlines())}")
    return 0
