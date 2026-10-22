"""Markdown -> Steam BBCode: a deliberately small converter + validator behind an interface the description builder
can share or replace.

It follows Steam's BBCode as it renders on Workshop pages: a fixed Markdown-to-tag mapping, Steam's whitespace
handling, the change-note tag set, and validation of size, allowed tags, balanced tags and literal brackets.
Supported Markdown subset: ATX headings, paragraphs (soft breaks join with a space,
hard breaks = two trailing spaces or a trailing backslash), bullet/numbered lists (nested by indentation), bold,
italic, ~~strike~~, inline code (plain text / [noparse]), fenced code blocks, links, images, > quotes, --- rules,
HTML comments (stripped). Tables and raw HTML are passed through as text (the description builder handles them).

Change notes use the same renderer as descriptions (checked on public change-note pages); this module's
CHANGE_NOTE profile restricts output to [b] [i] [u] [strike] [url] [list] [olist] [*] [h2] [h3] [code] [noparse].
Valve documents no change-note size limit (k_cchPublishedDocumentChangeDescriptionMax = 8000 is marked "Unused"):
warn above 2000 bytes, trim to 8000.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

CHANGE_NOTE_MAX_BYTES = 8000
CHANGE_NOTE_WARN_BYTES = 2000
DESCRIPTION_MAX_BYTES = 8000
DESCRIPTION_WARN_BYTES = 7500

KNOWN_TAGS = {"h1", "h2", "h3", "b", "i", "u", "strike", "spoiler", "noparse", "hr", "url", "list", "olist", "*",
              "quote", "code", "table", "tr", "th", "td", "img", "previewyoutube", "p"}


@dataclass(frozen=True)
class Profile:
    name: str
    allowed: frozenset[str]
    heading_tags: tuple[str, str, str]      # tags for #, ##, ### (#### and deeper -> [b])
    images: bool


DESCRIPTION = Profile("description", frozenset({"h1", "h2", "h3", "b", "i", "u", "strike", "spoiler", "noparse", "hr",
                                                "url", "list", "olist", "*", "quote", "code", "img", "table", "tr", "th",
                                                "td", "previewyoutube"}),
                      ("h1", "h2", "h3"), images=True)
CHANGE_NOTE = Profile("change-note", frozenset({"b", "i", "u", "strike", "url", "list", "olist", "*", "h2", "h3",
                                                "code", "noparse"}),
                      ("h2", "h2", "h3"), images=False)


class MarkupConverter(Protocol):
    """Interface shared with the (separate) description builder."""

    def convert(self, markdown: str) -> str: ...


def steam_bytes(text: str) -> int:
    """UTF-8 size counting every newline as CRLF (2 bytes), the conservative reading of Steam's limits."""
    return len(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


# --- inline ------------------------------------------------------------------------------------------------------

_IMG = re.compile(r"!\[([^\]]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\((\S+?)(?:\s+\"[^\"]*\")?\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_CODE = re.compile(r"`([^`]+)`")
_BOLDITAL = re.compile(r"(\*\*\*|___)(?=\S)(.+?)(?<=\S)\1")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITAL = re.compile(r"(?<![\w*\\])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_BRACKET_TAG = re.compile(r"\[/?([A-Za-z0-9*]+)(?:[= ][^\]]*)?\]")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _url_escape(url: str) -> str:
    return url.replace("[", "%5B").replace("]", "%5D")


def noparse_known_tags(text: str) -> str:
    """Wrap literal [x] whose name is a real Steam tag in [noparse] so Steam does not parse them; unknown names print as-is."""
    return _BRACKET_TAG.sub(lambda m: f"[noparse]{m.group(0)}[/noparse]" if m.group(1).lower() in KNOWN_TAGS
                            else m.group(0), text)


def inline(text: str, profile: Profile = DESCRIPTION) -> str:
    slots: list[str] = []

    def hold(s: str) -> str:
        slots.append(s)
        return f"\0{len(slots) - 1}\0"

    text = _CODE.sub(lambda m: hold(noparse_known_tags(m.group(1))), text)

    def img(m):
        alt, url = m.group(1), _url_escape(m.group(2))
        if profile.images:
            return hold(f"[img]{url}[/img]")
        return hold(f"[url={url}]{alt or url}[/url]")

    text = _IMG.sub(img, text)
    text = _LINK.sub(lambda m: hold(f"[url={_url_escape(m.group(2))}]"
                                    + (m.group(1) if m.group(1).startswith("\0") else noparse_known_tags(m.group(1)))
                                    + "[/url]"), text)
    text = _AUTOLINK.sub(lambda m: hold(m.group(1)), text)
    text = noparse_known_tags(text)
    text = _BOLDITAL.sub(lambda m: f"[b][i]{m.group(2)}[/i][/b]", text)
    text = _BOLD.sub(lambda m: f"[b]{m.group(2)}[/b]", text)
    text = _ITAL.sub(lambda m: f"[i]{m.group(2)}[/i]", text)
    text = _STRIKE.sub(lambda m: f"[strike]{m.group(1)}[/strike]", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!~|>])", r"\1", text)      # Markdown escapes
    for _ in range(3):  # links may hold images
        text = re.sub("\0(\\d+)\0", lambda m: slots[int(m.group(1))], text)
    return text


# --- blocks ------------------------------------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+\S+")


class MarkdownToBBCode:
    """The converter. `MinimalMarkdownToBBCode` is kept as an alias for callers of the first version."""

    def __init__(self, profile: Profile = CHANGE_NOTE):
        self.profile = profile

    def _heading(self, level: int, text: str) -> str:
        tag = self.profile.heading_tags[level - 1] if level <= 3 else "b"
        return f"[{tag}]{inline(text, self.profile)}[/{tag}]"

    def convert(self, markdown: str) -> str:
        src = _COMMENT.sub("", markdown.replace("\r\n", "\n")).replace("\t", "    ")
        lines = src.split("\n")
        blocks: list[tuple[str, str]] = []   # (kind, text): kind in para, heading, list, code, quote, hr
        para: list[str] = []

        def flush_para():
            if para:
                out, buf = [], ""
                for ln in para:
                    hard = ln.endswith("  ") or ln.endswith("\\")
                    piece = ln.rstrip().removesuffix("\\").strip()
                    buf = f"{buf} {piece}".strip() if buf else piece
                    if hard:
                        out.append(buf)
                        buf = ""
                if buf:
                    out.append(buf)
                blocks.append(("para", "\n".join(inline(x, self.profile) for x in out)))
                para.clear()

        i = 0
        while i < len(lines):
            line = lines[i]
            if _FENCE.match(line):
                flush_para()
                fence = _FENCE.match(line).group(1)
                body = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith(fence):
                    body.append(lines[i])
                    i += 1
                i += 1
                code = "\n".join(body)
                if "[" in code:
                    code = f"[noparse]{code}[/noparse]"
                blocks.append(("code", f"[code]{code}[/code]"))
                continue
            if not line.strip() or _REFDEF.match(line):
                flush_para()
                i += 1
                continue
            h = _HEADING.match(line)
            if h:
                flush_para()
                blocks.append(("heading", self._heading(len(h.group(1)), h.group(2))))
                i += 1
                continue
            if _HR.match(line) and not para:
                flush_para()
                blocks.append(("hr", "[hr][/hr]"))
                i += 1
                continue
            if _ITEM.match(line):
                flush_para()
                items = []
                while i < len(lines) and (_ITEM.match(lines[i]) or (lines[i].startswith("  ") and lines[i].strip()
                                                                     and items) or (not lines[i].strip() and i + 1 < len(lines) and _ITEM.match(lines[i + 1]))):
                    m = _ITEM.match(lines[i])
                    if m:
                        items.append([len(m.group(1)), "olist" if m.group(2)[0].isdigit() else "list", m.group(3)])
                    elif lines[i].strip():
                        items[-1][2] += " " + lines[i].strip()
                    i += 1
                blocks.append(("list", self._list(items)))
                continue
            if line.lstrip().startswith(">"):
                flush_para()
                q = []
                while i < len(lines) and lines[i].lstrip().startswith(">"):
                    q.append(lines[i].lstrip()[1:].removeprefix(" "))
                    i += 1
                inner = MarkdownToBBCode(self.profile).convert("\n".join(q))
                blocks.append(("quote", f"[quote]{inner}[/quote]" if "quote" in self.profile.allowed else inner))
                continue
            para.append(line)
            i += 1
        flush_para()
        # Join the way Steam renders whitespace: after a heading a single newline (it is swallowed); after other block closers a blank
        # line; after [hr][/hr] a single newline; between paragraphs a blank line.
        out = ""
        prev = None
        for kind, text in blocks:
            if prev is None:
                out = text
            elif prev in ("heading", "hr"):
                out += "\n" + text
            else:
                out += "\n\n" + text
            prev = kind
        return out

    def _list(self, items: list[list]) -> str:
        """items: [indent, kind, text]; deeper indentation nests inside the previous item."""
        out: list[str] = []
        stack: list[tuple[int, str]] = []
        for indent, kind, text in items:
            while stack and indent < stack[-1][0]:
                out.append(f"[/{stack.pop()[1]}]")
            if not stack or indent > stack[-1][0]:
                stack.append((indent, kind))
                out.append(f"[{kind}]")
            out.append("[*]" + inline(text, self.profile))
        while stack:
            out.append(f"[/{stack.pop()[1]}]")
        return "\n".join(out)


MinimalMarkdownToBBCode = MarkdownToBBCode


# --- validation & trimming ---------------------------------------------------------------------------------------

_TAG = re.compile(r"\[(/?)([a-z0-9*]+)(?:[= ]([^\]]*))?\]", re.I)


def validate(bbcode: str, profile: Profile) -> list[str]:
    """The tag allow-list and balance (LIFO) checks for the tags this module emits. Returns error strings."""
    errors: list[str] = []
    stack: list[str] = []
    pos = 0
    text = bbcode
    while True:
        m = _TAG.search(text, pos)
        if not m:
            break
        pos = m.end()
        closing, name = m.group(1) == "/", m.group(2).lower()
        if name not in KNOWN_TAGS:
            continue  # unknown tags print literally
        if name == "noparse" and not closing:
            end = text.lower().find("[/noparse]", pos)
            if end < 0:
                errors.append("unclosed [noparse]")
                break
            pos = end + len("[/noparse]")
            continue
        if name not in profile.allowed and not closing:
            errors.append(f"[{name}] is not allowed in a {profile.name}")
        if name in ("*", "hr") and not closing:
            continue
        if closing:
            if name == "hr":
                continue
            if not stack or stack[-1] != name:
                errors.append(f"[/{name}] closes {'[' + stack[-1] + ']' if stack else 'nothing'}")
            else:
                stack.pop()
        else:
            stack.append(name)
    errors += [f"unclosed [{t}]" for t in stack]
    return errors


def trim_to_bytes(bbcode: str, max_bytes: int = CHANGE_NOTE_MAX_BYTES,
                  marker: str = "[i](trimmed - see the full changelog)[/i]") -> tuple[str, bool]:
    """Cut at a line boundary so steam_bytes() fits, close tags left open, append a marker. Returns (text, trimmed)."""
    if steam_bytes(bbcode) <= max_bytes:
        return bbcode, False
    lines = bbcode.split("\n")
    while lines:
        lines.pop()
        text = "\n".join(lines)
        closers = _open_tags(text)
        candidate = "\n".join([text] + [f"[/{t}]" for t in closers] + [marker]) if text else marker
        if steam_bytes(candidate) <= max_bytes:
            return candidate, True
    return marker, True


def _open_tags(text: str) -> list[str]:
    stack: list[str] = []
    for m in _TAG.finditer(text):
        name = m.group(2).lower()
        if name not in KNOWN_TAGS or name in ("*", "hr", "noparse"):
            continue
        if m.group(1):
            if stack and stack[-1] == name:
                stack.pop()
        else:
            stack.append(name)
    return list(reversed(stack))
