"""Markdown -> BBCode (fixtures checked against how Steam renders BBCode), validation, trimming, change notes."""
from se2modkit.publish import gitutil
from se2modkit.publish.bbcode import (CHANGE_NOTE, DESCRIPTION, MarkdownToBBCode, steam_bytes, trim_to_bytes, validate)
from se2modkit.publish.changelog import build_change_note, changelog_sections, find_section, git_summary

from pubfix import git

D = MarkdownToBBCode(DESCRIPTION)


def test_f1_soft_and_hard_breaks():
    assert D.convert("Line one\nline two  \nline three") == "Line one line two\nline three"


def test_f2_heading_spacing():
    md = "# Title\nIntro text.\n\n## Features\n- A\n- B\n\nOutro."
    assert D.convert(md) == "[h1]Title[/h1]\nIntro text.\n\n[h2]Features[/h2]\n[list]\n[*]A\n[*]B\n[/list]\n\nOutro."


def test_f3_nested_mixed_list():
    md = "1. Install\n   - Subscribe\n   - Restart\n2. Play"
    assert D.convert(md) == "[olist]\n[*]Install\n[list]\n[*]Subscribe\n[*]Restart\n[/list]\n[*]Play\n[/olist]"


def test_f4_literal_known_tags_are_noparsed():
    assert D.convert("Look for [b] and [DemoMod] in the log") == \
        "Look for [noparse][b][/noparse] and [DemoMod] in the log"


def test_f5_inline_code():
    assert D.convert("Use `-loadScripts` or `[url]`") == "Use -loadScripts or [noparse][url][/noparse]"


def test_f6_code_block_with_bbcode():
    md = '```xml\n<Block id="[b]x[/b]"/>\n```'
    assert D.convert(md) == '[code][noparse]<Block id="[b]x[/b]"/>[/noparse][/code]'


def test_f7_link_with_brackets_in_url():
    assert D.convert("[wiki](https://example.com/a[1])") == "[url=https://example.com/a%5B1%5D]wiki[/url]"


def test_emphasis_strike_and_hr():
    assert D.convert("**b** *i* ~~s~~ ***bi***") == "[b]b[/b] [i]i[/i] [strike]s[/strike] [b][i]bi[/i][/b]"
    assert D.convert("a\n\n---\n\nb") == "a\n\n[hr][/hr]\nb"


def test_change_note_profile_limits_tags():
    c = MarkdownToBBCode(CHANGE_NOTE)
    out = c.convert("# Big\n![shot](https://x/y.png)")
    assert out == "[h2]Big[/h2]\n[url=https://x/y.png]shot[/url]"
    assert validate(out, CHANGE_NOTE) == []
    assert validate("[img]x[/img]", CHANGE_NOTE) == ["[img] is not allowed in a change-note"]


def test_validate_balance():
    assert validate("[b][i]x[/b][/i]", DESCRIPTION)[0].startswith("[/b] closes [i]")
    assert "unclosed [quote]" in validate("[quote]x", DESCRIPTION)
    assert validate("[noparse][b][/noparse] [Unknown] [list]\n[*]a\n[/list]", DESCRIPTION) == []


def test_steam_bytes_counts_crlf():
    assert steam_bytes("a\nb") == 4
    assert steam_bytes("é") == 2


def test_trim_closes_open_list_and_warns():
    text = "[list]\n" + "\n".join(f"[*]item {i}" for i in range(400)) + "\n[/list]"
    out, trimmed = trim_to_bytes(text, 300)
    assert trimmed and steam_bytes(out) <= 300
    assert out.rstrip().endswith("(trimmed - see the full changelog)[/i]")
    assert "[/list]" in out and validate(out, CHANGE_NOTE) == []
    assert trim_to_bytes("short", 300) == ("short", False)


CHANGELOG = ("# Changelog\n\n## [Unreleased]\n- wip\n\n## [1.1.0] - 2026-10-20\n### Added\n- New rack\n\n"
             "## v1.0.0 (2026-10-01)\n- First\n\n[1.1.0]: https://example.com/compare\n")


def test_changelog_sections_and_selection():
    secs = changelog_sections(CHANGELOG)
    assert [(v, d) for v, d, _ in secs] == [("Unreleased", None), ("1.1.0", "2026-10-20"), ("1.0.0", "2026-10-01")]
    assert find_section(CHANGELOG, None)[0] == "1.1.0"          # newest released, not Unreleased
    assert find_section(CHANGELOG, "v1.0.0")[2] == "- First"
    assert find_section(CHANGELOG, "9.9.9") is None


def test_change_note_from_changelog(mod):
    note = build_change_note(mod, mod / "CHANGELOG.md", "1.1.0", None, (mod / "CHANGELOG.md").read_text())
    assert note.source == "changelog:CHANGELOG.md#1.1.0"
    assert note.text == ("v1.1.0 - 2026-10-20\n[h3]Added[/h3]\n[list]\n[*]New [b]rack[/b] block\n"
                         "[*]Fix for [noparse][b][/noparse] tags in [noparse][noparse][/noparse] text\n[/list]")
    assert note.warnings == []


def test_change_note_git_fallback_since_last_publish(mod):
    first = gitutil.head_sha(mod)
    (mod / "Scripts/Client/Thing.cs").write_text("namespace TestMod { class Thing { int x; } }\n")
    git(mod, "commit", "-qam", "Thing: add x")
    (mod / "Assets/Block.def").write_text('{"a": 1}')
    git(mod, "commit", "-qam", "Block: tweak")
    note = build_change_note(mod, mod / "CHANGELOG.md", "2.0.0", first, (mod / "CHANGELOG.md").read_text())
    assert any("no section for 2.0.0" in w for w in note.warnings)
    assert note.source.startswith(f"git:{first[:8]}..")
    assert note.text == f"v2.0.0\nChanges since {first[:8]}:\n\n[list]\n[*]Block: tweak\n[*]Thing: add x\n[/list]"


def test_git_summary_first_publish_and_no_changes(mod):
    head = gitutil.head_sha(mod)
    md, src = git_summary(mod, None)
    assert md == f"Initial Workshop release (commit {head[:8]})." and src == f"git:initial@{head[:8]}"
    md, _ = git_summary(mod, head)
    assert "no source changes" in md


def test_change_note_missing_changelog_and_override(mod, tmp_path):
    note = build_change_note(mod, mod / "NOPE.md", None, None)
    assert any("no committed NOPE.md" in w for w in note.warnings) and note.text.startswith("Initial Workshop release")
    f = tmp_path / "note.bbcode"
    f.write_text("[b]hand written[/b]")
    assert build_change_note(mod, mod / "CHANGELOG.md", None, None, override_file=f).text == "[b]hand written[/b]"


def test_change_note_trimmed_with_warning(mod):
    big = "## [3.0.0]\n" + "\n".join(f"- change number {i} " + "x" * 80 for i in range(200))
    (mod / "CHANGELOG.md").write_text(big)
    note = build_change_note(mod, mod / "CHANGELOG.md", "3.0.0", None, big)
    assert steam_bytes(note.text) <= 8000
    assert any("trimmed" in w for w in note.warnings)


SKIPPY = ("# Changelog\n\n## [1.2.0] - 2026-10-21\n### Added\n- New rack\n\n### For modders\n- API hook\n\n"
          "### documentation\n- Docs\n\n### Fixed\n- Crash\n\n### Development\n- CI\n")


def _note(mod, **kw):
    return build_change_note(mod, mod / "CHANGELOG.md", "1.2.0", None, SKIPPY, **kw)


def test_change_note_skips_non_mod_sections_by_default(mod):
    note = _note(mod)
    assert note.text == ("v1.2.0 - 2026-10-21\n[h3]Added[/h3]\n[list]\n[*]New rack\n[/list]\n\n"
                         "[h3]Fixed[/h3]\n[list]\n[*]Crash\n[/list]")
    assert note.warnings == ["change note leaves out section(s): For modders, documentation, Development"]


def test_change_note_custom_skip_list(mod):
    note = _note(mod, skip_sections=["fixed"])
    assert "Crash" not in note.text and "API hook" in note.text and "CI" in note.text
    assert note.warnings == ["change note leaves out section(s): Fixed"]


def test_change_note_empty_skip_list_keeps_everything(mod):
    note = _note(mod, skip_sections=[])
    assert all(s in note.text for s in ("New rack", "API hook", "Docs", "Crash", "CI"))
    assert note.warnings == []


def test_change_note_everything_filtered_leaves_header_only(mod):
    md = "## [1.2.0] - 2026-10-21\n### For modders\n- API\n\n### Development\n- CI\n"
    note = build_change_note(mod, mod / "CHANGELOG.md", "1.2.0", None, md)
    assert note.text == "v1.2.0 - 2026-10-21"


def test_publish_toml_skip_sections_key(mod):
    from se2modkit.publish.project import load_config, load_project
    assert load_config(load_project(mod)).change_note_skip_sections == ["For modders", "Documentation", "Development"]
    toml = mod / "docs/workshop/publish.toml"
    toml.write_text(toml.read_text() + 'change_note_skip_sections = []\n')
    git(mod, "commit", "-qam", "cfg")
    cfg = load_config(load_project(mod))
    assert cfg.change_note_skip_sections == [] and not any("unknown key" in x for x in cfg.problems)
