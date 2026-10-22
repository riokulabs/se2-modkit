"""Leak gate: modkit is a public, mod-neutral library. No file of the repository (`git ls-files`, so src/, tests/,
docs/, the profiles and kit under src/, the top-level files, .github/ and scripts/) may name a sibling mod's blocks,
a machine-specific path or a local tool config.

Every text file is scanned line by line. A hit fails the test with `file:line: pattern: text`, unless an ALLOW entry
names that file and that pattern with a reason: test data that must hold the very thing (a Wine drive letter, a Proton
build name, the Steam discovery rule) or a gate that bans it.

Further private terms (names not yet announced) live outside the repository: `tests/.leak-terms.local` (git-ignored),
one regular expression per line (case-insensitive unless the pattern says `(?-i:...)`; `#` starts a comment), is
read when present. Keep such names out of this file.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
LOCAL_TERMS = ROOT / "tests" / ".leak-terms.local"
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".dds", ".fbx", ".glb", ".zip"}

# Split so this file does not hold the words it bans (other gates scan every file for some of them).
PATTERNS = {   # label -> compiled pattern
    "DC_": re.compile(r"\bDC" + "_"),
    "data-center": re.compile(r"\bdata ?" + r"center\b", re.I),
    "blueprint": re.compile(r"blue" + r"print", re.I),
    "home": re.compile(r"/ho" + r"me/"),
    "debian-steam": re.compile(r"debian-" + r"installation", re.I),
    "ge-proton": re.compile(r"ge-" + r"proton", re.I),
    "mcp-config": re.compile(r"semcp2\." + r"cfg", re.I),
    "wine-drive": re.compile(r"\b[SZ]:" + r"\\"),
}

# (repo-relative path, pattern label) -> why the hit is legitimate.
ALLOW = {
    # .repo-guard.toml is itself a gate: its `forbidden` list names the local tool config file so that it is never
    # committed (org-managed file, read by scripts/check_repo_guard.py and CI).
    (".repo-guard.toml", "mcp-config"): "the repo guard's forbidden-file list (a gate)",
    ("src/se2modkit/env/steam.py", "debian-steam"):
        "the Steam discovery rule list: Debian's Steam package installs under ~/.steam/<this name>",
    ("tests/env/test_steam.py", "debian-steam"): "tests that discovery rule",
    ("tests/model/test_config_discovery.py", "debian-steam"): "asserts the model config holds no such default",
    ("tests/test_style_docs.py", "debian-steam"): "a gate banning machine paths in the style docs",
    ("tests/test_style_docs.py", "ge-proton"): "same gate",
    ("tests/test_style_docs.py", "home"): "same gate",
    ("tests/diag/test_diag_paths.py", "debian-steam"): "a gate banning machine paths in the diag docs",
    ("tests/diag/test_diag_paths.py", "ge-proton"): "same gate",
    ("tests/diag/test_diag_paths.py", "home"): "same gate",
    ("tests/scaffold/test_new.py", "debian-steam"): "a gate banning machine values in the scaffolded files",
    ("tests/scaffold/test_new.py", "ge-proton"): "same gate",
    ("tests/scaffold/test_new.py", "home"): "same gate",
    ("tests/scaffold/test_new.py", "wine-drive"): "same gate",
    ("tests/test_project.py", "home"): "an absolute path that the portable-config check must refuse",
    ("tests/env/test_proton.py", "ge-proton"): "Proton build discovery: synthetic compatibility-tool names",
    ("tests/env/test_proton.py", "wine-drive"): "Wine path translation: synthetic drive mappings",
    ("tests/cli/test_env_cmd.py", "ge-proton"): "a synthetic unresolvable Proton tool name",
    ("tests/env/test_procs.py", "wine-drive"): "process matching on synthetic Wine command lines",
    ("tests/cli/test_play_cmd.py", "wine-drive"): "the launch argv in Wine form",
    ("tests/publish/test_stage_rules.py", "wine-drive"): "a patched .vrgproj path the publish stage must refuse",
}


def _local_terms() -> list[re.Pattern]:
    if not LOCAL_TERMS.is_file():
        return []
    out = []
    for line in LOCAL_TERMS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(re.compile(line, re.I))
    return out


def _files():
    """Every file of the checkout: `git ls-files` (tracked plus untracked, not ignored); outside a git checkout every
    file under the repo, skipping VCS, virtualenv, cache and build folders."""
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, check=True)
        rels = sorted({x for x in r.stdout.decode("utf-8", "replace").split("\0") if x})
        paths = [ROOT / x for x in rels]
    except (OSError, subprocess.CalledProcessError):
        paths = [f for f in sorted(ROOT.rglob("*"))
                 if not SKIP_DIRS & set(f.relative_to(ROOT).parts) and ".egg-info" not in str(f)]
    for f in paths:
        if f.is_file() and f.resolve() != SELF and f != LOCAL_TERMS and f.suffix.lower() not in SKIP_SUFFIXES:
            yield f


def _text(f: Path) -> str | None:
    data = f.read_bytes()
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def scan(extra: list[re.Pattern] | None = None) -> list[str]:
    pats = list(PATTERNS.items()) + [(f"local-term {i + 1}", rx) for i, rx in enumerate(extra or [])]
    hits = []
    for f in _files():
        text = _text(f)
        if text is None:
            continue
        rel = f.relative_to(ROOT).as_posix()
        for n, line in enumerate(text.splitlines(), 1):
            for label, rx in pats:
                if rx.search(line) and (rel, label) not in ALLOW:
                    hits.append(f"{rel}:{n}: {label}: {line.strip()[:110]}")
    return hits


def test_no_leaks():
    hits = scan()
    assert not hits, "leaks (fix them, or add an ALLOW entry with a reason):\n" + "\n".join(hits)


def test_no_private_terms_from_local_list():
    extra = _local_terms()
    if not extra:
        return
    hits = [h for h in scan(extra) if ": local-term " in h]
    assert not hits, "private terms:\n" + "\n".join(hits)


def test_every_allow_entry_is_used():
    """A stale ALLOW entry would let a later leak in silently: each one must still match its file."""
    unused = []
    for rel, label in ALLOW:
        f = ROOT / rel
        text = _text(f) if f.is_file() else None
        if text is None or not PATTERNS[label].search(text):
            unused.append(f"{rel}: {label}")
    assert not unused, "ALLOW entries that no longer match:\n" + "\n".join(unused)


def test_gate_catches_each_pattern(tmp_path):
    samples = {"DC_": "DC" + "_Rack42", "data-center": "Data " + "Center",
               "blueprint": "Blue" + "print", "home": "/ho" + "me/x", "debian-steam": "debian-" + "installation",
               "ge-proton": "GE-" + "Proton9", "mcp-config": "SeMcp2." + "cfg", "wine-drive": "Z:" + "\\games"}
    assert set(samples) == set(PATTERNS)
    for label, s in samples.items():
        assert PATTERNS[label].search(s), label
    for ok in ("ls_tree", "calls.append", "checks:\\n", "tools.py"):
        assert not any(rx.search(ok) for rx in PATTERNS.values()), ok
