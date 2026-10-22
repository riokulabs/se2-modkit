#!/usr/bin/env python3
# managed by riokulabs/.github - edit there (templates/), not here
"""Repo guard: forbidden files, oversized files, obvious secrets, commit-message rules.

    scripts/check_repo_guard.py                 check every tracked file
    scripts/check_repo_guard.py --staged        check what is staged (pre-commit)
    scripts/check_repo_guard.py --commit-msg F  check one commit message file (commit-msg hook)
    scripts/check_repo_guard.py --range A..B    check the messages of a commit range (CI)

Settings live in .repo-guard.toml. Standard library only. Exit 1 on any finding.
"""
import argparse
import fnmatch
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
CFG = tomllib.loads((ROOT / ".repo-guard.toml").read_text())

SECRET_PATTERNS = [
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SeMcp2 token", re.compile(r"(?i)semcp2[^\n]{0,60}(?:token|bearer)[^\n]{0,10}[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}")),
    ("quoted secret", re.compile(r"(?i)\b(?:api[_-]?key|secret|passwd|password|auth[_-]?token)\b['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-/+]{24,}['\"]")),
]
CONVENTIONAL = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([a-z0-9._/-]+\))?!?: \S.*")
SKIP_SUBJECT = re.compile(r"^(Merge |Revert \"|fixup! |squash! )")
AI_TRAILER = re.compile(r"(?i)^\s*co-authored-by:.*(claude|anthropic|copilot|chatgpt|openai|codex|cursor|gemini|\bai\b|\bllm\b)")
AI_NOTE = re.compile(r"(?i)(generated (with|by|using)\s+\[?(claude|chatgpt|copilot|codex|cursor|gemini|an? ai|ai)\b|\U0001F916)")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def files(staged):
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACM") if staged else git("ls-files")
    return [f for f in out.split("\n") if f]


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def check_files(paths):
    bad = []
    forbidden = CFG.get("forbidden", [])
    allow = CFG.get("allow", [])
    max_kb = CFG.get("max_kb", 1024)
    allow_large = CFG.get("allow_large", [])
    for rel in paths:
        p = ROOT / rel
        if not p.is_file():
            continue
        if matches(rel, forbidden) and not matches(rel, allow):
            bad.append(f"{rel}: forbidden path (see .repo-guard.toml)")
            continue
        size = p.stat().st_size
        if size > max_kb * 1024 and not matches(rel, allow_large):
            bad.append(f"{rel}: {size // 1024} KB is over the {max_kb} KB limit (use Git LFS or add to allow_large)")
            continue
        if size < 1_000_000 and not matches(rel, CFG.get("secret_scan_skip", [])):
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if text.startswith("version https://git-lfs"):
                continue
            for name, rx in SECRET_PATTERNS:
                m = rx.search(text)
                if m:
                    line = text.count("\n", 0, m.start()) + 1
                    bad.append(f"{rel}:{line}: looks like a {name}")
    return bad


def check_message(msg, label):
    bad = []
    lines = msg.split("\n")
    subject = next((l for l in lines if l.strip() and not l.startswith("#")), "")
    for l in lines:
        if l.startswith("#"):
            continue
        if AI_TRAILER.search(l) or AI_NOTE.search(l):
            bad.append(f"{label}: AI attribution is not allowed in commits: {l.strip()!r}")
    if subject and not SKIP_SUBJECT.match(subject) and not CONVENTIONAL.match(subject):
        bad.append(f"{label}: subject is not a Conventional Commit (type(scope): summary): {subject!r}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--commit-msg")
    ap.add_argument("--range")
    a = ap.parse_args()
    bad = []
    if a.commit_msg:
        bad += check_message(Path(a.commit_msg).read_text(), "commit message")
    elif a.range:
        for sha in git("rev-list", "--no-merges", a.range).split():
            bad += check_message(git("log", "-1", "--format=%B", sha), sha[:10])
    else:
        bad += check_files(files(a.staged))
    for b in bad:
        print(b, file=sys.stderr)
    if bad:
        print(f"{len(bad)} problem(s)", file=sys.stderr)
        sys.exit(1)
    print("repo guard: ok")


if __name__ == "__main__":
    main()
