"""After a successful Workshop upload: tag the published commit and publish its GitHub release.

The upload is already done when this runs, so nothing here may fail the publish. Every problem becomes a warning
plus the exact commands to finish by hand.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import progress
from .changelog import find_section
from .project import ModProject, PublishConfig, read_tracked, split_release_tag

# runner(argv, cwd) -> (returncode, stdout, stderr)
Runner = Callable[[list[str], Path], tuple[int, str, str]]


def run(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]}: timed out"
    return p.returncode, p.stdout, p.stderr


def workshop_url(item: int | None) -> str:
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={item if item is not None else '<item id>'}"


@dataclass
class Release:
    repo: Path              # the git work tree that owns the tag
    tag: str
    commit: str
    tag_message: str        # "<Workshop title> <version>"
    title: str              # "<release_title> <version>"
    notes: str              # the CHANGELOG section in full, then the Workshop link


def tag_name(p: ModProject, version: str) -> str:
    """`vX.Y.Z`, or `<mod>-vX.Y.Z` for a mod in a multi-mod repo."""
    return p.release_tag(version)


def plan_release(p: ModProject, cfg: PublishConfig, version: str | None, commit: str,
                 item: int | None) -> tuple[Release | None, str]:
    """(release, "") or (None, why it is skipped)."""
    if not cfg.github_release:
        return None, "github_release = false in publish.toml"
    if not version:
        return None, "no --version given, so there is no release to tag"
    ver = (split_release_tag(version)[1] or version).lstrip("vV")
    data = read_tracked(p, cfg.changelog)
    sec = find_section(data.decode("utf-8"), ver) if data is not None else None
    body = sec[2].strip() if sec and sec[0].lstrip("vV") == ver else ""
    link = f"**Steam Workshop:** [{cfg.title}]({workshop_url(item)})"
    notes = (body + "\n\n" + link if body else link) + "\n"
    return Release(p.repo or p.folder, tag_name(p, ver), commit, f"{cfg.title} {ver}", f"{cfg.release_title} {ver}",
                   notes), ""


def plan_lines(rel: Release | None, why: str) -> list[str]:
    """What a dry run prints for this step."""
    if rel is None:
        return [f"then: no git tag or GitHub release ({why})"]
    return [f"then: tag {rel.tag} at {rel.commit[:12]} and push it to origin",
            f"then: publish GitHub release {rel.tag} \"{rel.title}\" with its CHANGELOG section and the Workshop "
            "link (edits the release if one already exists for the tag)"]


class _Failed(Exception):
    pass


def _gh_repo(rel: Release, runner: Runner) -> list[str]:
    """`-R owner/name` from origin when it is a GitHub URL, so gh never has to guess between remotes."""
    rc, out, _ = runner(["git", "remote", "get-url", "origin"], rel.repo)
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", out.strip()) if rc == 0 else None
    return ["-R", f"{m.group(1)}/{m.group(2)}"] if m else []


def _manual(rel: Release, notes_file: Path, with_tag: bool) -> list[str]:
    q = shlex.quote
    cmds = []
    if with_tag:
        cmds += [f"git -C {q(str(rel.repo))} tag -a {q(rel.tag)} -m {q(rel.tag_message)} {rel.commit}",
                 f"git -C {q(str(rel.repo))} push origin refs/tags/{q(rel.tag)}"]
    cmds += [f"cd {q(str(rel.repo))}",
             f"gh release view {q(rel.tag)} && gh release edit {q(rel.tag)} --title {q(rel.title)} "
             f"--notes-file {q(str(notes_file))} --draft=false --latest "
             f"|| gh release create {q(rel.tag)} --verify-tag --title {q(rel.title)} "
             f"--notes-file {q(str(notes_file))} --latest"]
    return cmds


def _tag(rel: Release, runner: Runner, out) -> bool:
    """True when the tag is at the published commit and pushed; False = skip the release (tag elsewhere)."""
    rc, sha, _ = runner(["git", "rev-parse", "-q", "--verify", f"refs/tags/{rel.tag}^{{commit}}"], rel.repo)
    if rc == 0:
        if sha.strip() != rel.commit:
            out(f"WARNING: tag {rel.tag} already exists at {sha.strip()[:12]}, not at the published commit "
                f"{rel.commit[:12]}. It is left alone, and no GitHub release is published.")
            return False
        out(f"tag {rel.tag} already exists at {rel.commit[:12]}; reusing it")
    else:
        rc, _, err = runner(["git", "tag", "-a", rel.tag, "-m", rel.tag_message, rel.commit], rel.repo)
        if rc != 0:
            raise _Failed(f"git tag failed: {err.strip()}")
        out(f"tagged {rel.tag} at {rel.commit[:12]}")
    rc, _, err = runner(["git", "push", "origin", f"refs/tags/{rel.tag}"], rel.repo)
    if rc != 0:
        raise _Failed(f"git push of {rel.tag} failed: {err.strip()}")
    return True


def _github(rel: Release, notes_file: Path, runner: Runner, out) -> None:
    rc, _, err = runner(["gh", "auth", "status"], rel.repo)
    if rc != 0:
        raise _Failed("the gh CLI is missing or not signed in" + (f" ({err.strip().splitlines()[0]})"
                                                                     if err.strip() else ""))
    repo = _gh_repo(rel, runner)
    rc, _, _ = runner(["gh", "release", "view", rel.tag, *repo, "--json", "isDraft"], rel.repo)
    if rc == 0:
        argv = ["gh", "release", "edit", rel.tag, *repo, "--title", rel.title, "--notes-file", str(notes_file),
                "--draft=false", "--latest"]
        verb = "updated"
    else:
        argv = ["gh", "release", "create", rel.tag, *repo, "--verify-tag", "--title", rel.title,
                "--notes-file", str(notes_file), "--latest"]
        verb = "published"
    rc, _, err = runner(argv, rel.repo)
    if rc != 0:
        raise _Failed(f"gh release {argv[2]} failed: {err.strip()}")
    out(f"{verb} GitHub release {rel.tag}: {rel.title}")


def finish(rel: Release, runner: Runner | None = None, out=print) -> bool:
    """Tag and release. Never raises; on a problem prints a warning and the commands to finish by hand."""
    runner = runner or run
    fd, name = tempfile.mkstemp(prefix=f"release-{rel.tag}-", suffix=".md")
    notes_file = Path(name)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(rel.notes)
    tagged = False
    try:
        with progress.step(f"tagging {rel.tag}"):
            if not _tag(rel, runner, out):
                notes_file.unlink(missing_ok=True)
                return False
        tagged = True
        with progress.step("publishing the GitHub release"):
            _github(rel, notes_file, runner, out)
    except Exception as e:                       # the upload is done; this must not fail the publish
        out(f"WARNING: the Workshop upload succeeded, but the {'GitHub release' if tagged else 'git tag'} step "
            f"did not: {e}")
        out(f"Finish by hand (release notes saved in {notes_file}):")
        for c in _manual(rel, notes_file, with_tag=not tagged):
            out(f"  {c}")
        return False
    notes_file.unlink(missing_ok=True)
    return True


def after_upload(p: ModProject, cfg: PublishConfig, version: str | None, commit: str, item: int,
                 runner: Runner | None = None, out=print) -> bool:
    try:
        rel, why = plan_release(p, cfg, version, commit, item)
    except Exception as e:
        out(f"WARNING: the Workshop upload succeeded, but the release step could not start: {e}")
        return False
    if rel is None:
        out(f"no git tag or GitHub release: {why}")
        return False
    return finish(rel, runner, out)
