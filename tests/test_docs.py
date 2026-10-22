"""Docs stay in step with the code: every registered subcommand is mentioned, every relative link resolves."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from se2modkit.cli import commands

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
FENCE = re.compile(r"```.*?```", re.S)
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")


def _text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in DOCS)


@pytest.mark.parametrize("name", commands())
def test_subcommand_is_documented(name):
    assert re.search(rf"se2mod {re.escape(name)}\b", _text()), f"`se2mod {name}` is not mentioned in README.md or docs/"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_links_resolve(doc):
    bad = []
    for target in LINK.findall(FENCE.sub("", doc.read_text(encoding="utf-8"))):
        if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        if not (doc.parent / target.split("#", 1)[0]).exists():
            bad.append(target)
    assert not bad, f"{doc.name}: broken links {bad}"
