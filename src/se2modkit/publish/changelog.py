"""Change note for an update: the CHANGELOG.md section for the version, else a summary of git commits."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from collections.abc import Sequence
from pathlib import Path

from . import gitutil
from .bbcode import (CHANGE_NOTE, CHANGE_NOTE_MAX_BYTES, CHANGE_NOTE_WARN_BYTES, MarkdownToBBCode, MarkupConverter,
                     steam_bytes, trim_to_bytes, validate)

# Subsections of a CHANGELOG section that are not about the mod itself; left out of the Steam change note.
DEFAULT_SKIP_SECTIONS: tuple[str, ...] = ("For modders", "Documentation", "Development")

# Keep a Changelog headings: "## [1.2.0] - 2026-10-20", "## 1.2.0", "## v1.2.0 (2026-10-20)", "## [Unreleased]"
_SECTION = re.compile(r"^##\s+\[?v?(?P<ver>[^\]\s]+)\]?(?:\s*[-–(]\s*(?P<date>[^)]*?)\)?)?\s*$")


@dataclass
class ChangeNote:
    text: str                     # final BBCode sent to SubmitItemUpdate
    source: str                   # "changelog:<file>#<version>", "git:<a>..<b>", "file:<path>", "none"
    version: str | None
    warnings: list[str] = field(default_factory=list)


def changelog_sections(md: str) -> list[tuple[str, str | None, str]]:
    """[(version, date, body_markdown)] in file order."""
    out: list[tuple[str, str | None, str]] = []
    cur: tuple[str, str | None] | None = None
    body: list[str] = []
    for line in md.replace("\r\n", "\n").split("\n"):
        m = _SECTION.match(line) if line.startswith("## ") else None
        if m:
            if cur:
                out.append((cur[0], cur[1], "\n".join(body).strip("\n")))
            cur = (m.group("ver"), (m.group("date") or None))
            body = []
        elif line.startswith("# ") and cur is None:
            continue
        elif cur and not re.match(r"^\s{0,3}\[[^\]]+\]:\s+\S+", line):   # drop link reference definitions
            body.append(line)
    if cur:
        out.append((cur[0], cur[1], "\n".join(body).strip("\n")))
    return out


def filter_subsections(body: str, skip: Sequence[str]) -> tuple[str, list[str]]:
    """Drop every `###` subsection whose heading equals (case-insensitively) an entry of `skip`.

    Returns (remaining markdown, headings dropped in file order)."""
    names = {s.strip().casefold() for s in skip}
    if not names:
        return body, []
    out: list[str] = []
    dropped: list[str] = []
    skipping = False
    fenced = False
    for line in body.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        m = None if fenced else re.match(r"^#{1,3}[ \t]+(.*?)[ \t#]*$", line)
        if m:
            skipping = len(line) - len(line.lstrip("#")) == 3 and m.group(1).strip().casefold() in names
            if skipping:
                dropped.append(m.group(1).strip())
        if not skipping:
            out.append(line)
    return "\n".join(out).strip("\n"), dropped


def find_section(md: str, version: str | None) -> tuple[str, str | None, str] | None:
    """The section for version (leading 'v' ignored); with no version, the newest released (non-Unreleased) one."""
    secs = changelog_sections(md)
    if version:
        want = version.lstrip("vV")
        for s in secs:
            if s[0].lstrip("vV") == want:
                return s
        return None
    for s in secs:
        if s[0].lower() != "unreleased":
            return s
    return None


def git_summary(repo: Path, since: str | None, limit: int = 40) -> tuple[str, str]:
    """Markdown bullet list of commit subjects since `since` (exclusive) and a source label."""
    head = gitutil.head_sha(repo)
    if since and not gitutil.commit_exists(repo, since):
        since = None
    if since is None:
        return f"Initial Workshop release (commit {head[:8]}).", f"git:initial@{head[:8]}"
    subjects = gitutil.log_subjects(repo, since, limit + 1)
    if not subjects:
        return f"Rebuild of commit {head[:8]}; no source changes since the last publish.", f"git:{since[:8]}..{head[:8]}"
    more = len(subjects) > limit
    lines = [f"Changes since {since[:8]}:"] + [f"- {s}" for s in subjects[:limit]]
    if more:
        lines.append("- ... and more (see the repository history)")
    return "\n".join(lines), f"git:{since[:8]}..{head[:8]}"


def build_change_note(repo: Path, changelog: Path, version: str | None, last_commit: str | None,
                      changelog_text: str | None = None,
                      converter: MarkupConverter | None = None, override_file: Path | None = None,
                      max_bytes: int = CHANGE_NOTE_MAX_BYTES,
                      skip_sections: Sequence[str] = DEFAULT_SKIP_SECTIONS) -> ChangeNote:
    """First line is plain text "v1.2.0 - 2026-10-20", then the converted section.

    changelog_text is the committed CHANGELOG (the caller reads it from git HEAD); None = not in git."""
    conv = converter or MarkdownToBBCode(CHANGE_NOTE)
    warnings: list[str] = []
    if override_file is not None:
        text = override_file.read_text(encoding="utf-8")
        bb = text if override_file.suffix == ".bbcode" else conv.convert(text)
        src, ver = f"file:{override_file.name}", version
    else:
        sec = None
        if changelog_text is not None:
            sec = find_section(changelog_text, version)
            if sec is None:
                warnings.append(f"{changelog.name} has no section for "
                                f"{version or 'a released version'}; falling back to git history")
        else:
            warnings.append(f"no committed {changelog.name}; change note falls back to git history")
        if sec is not None:
            ver, date, body = sec
            first = " - ".join(x for x in (f"v{ver.lstrip('vV')}", date) if x)
            body, dropped = filter_subsections(body, skip_sections)
            if dropped:
                warnings.append("change note leaves out section(s): " + ", ".join(dropped))
            bb = first + "\n" + conv.convert(body) if body.strip() else first
            src = f"changelog:{changelog.name}#{ver}"
        else:
            md, src = git_summary(repo, last_commit)
            ver = version
            first = f"v{ver.lstrip('vV')}" if ver else ""
            bb = (first + "\n" if first else "") + conv.convert(md)
    bb, trimmed = trim_to_bytes(bb, max_bytes)
    if trimmed:
        warnings.append(f"change note trimmed to {max_bytes} bytes (counting newlines as CRLF)")
    elif steam_bytes(bb) > CHANGE_NOTE_WARN_BYTES:
        warnings.append(f"change note is {steam_bytes(bb)} bytes; keep notes under {CHANGE_NOTE_WARN_BYTES}")
    warnings += [f"change note markup: {e}" for e in validate(bb, CHANGE_NOTE)]
    if not bb.strip():
        warnings.append("change note is empty")
    return ChangeNote(bb, src, ver, warnings)
