"""The style guide's numbers live in the profiles: every numeric profile key is cited in a doc table, and every number
in a doc table sits in a row that cites the profile key it comes from (so docs and checks cannot drift)."""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "style-guide"
PROFILES = ROOT / "src" / "se2modkit" / "profiles"
FILES = ["00-house-rules", "10-block-archetypes", "20-modeling", "30-materials", "40-decals-and-marks",
         "50-budgets-and-lods", "60-build-and-damage", "70-animation", "80-scripts-and-fallback",
         "90-naming-menus-texts", "95-previews-and-review", "checks", "profiles/vanilla", "profiles/industrial"]
# A number: digits not glued to a word or a dotted name (LOD0, G10, Fracture_01, 4.0.0.3155 stay out by the rules
# below), outside backticks.
NUMBER = re.compile(r"(?<![\w.§-])\d+(?:[.,]\d+)?(?![\w.]\d|\w)")
CODE = re.compile(r"`[^`]*`")
LINK = re.compile(r"\]\([^)]*\)")      # markdown link targets (file names like 60-build-and-damage.md)


def _numeric_keys(name: str) -> dict[str, object]:
    data = tomllib.loads((PROFILES / f"{name}.toml").read_text(encoding="utf-8"))
    out = {}

    def walk(t, prefix):
        for k, v in t.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                walk(v, key + ".")
            elif isinstance(v, bool):
                continue
            elif isinstance(v, int | float) or (isinstance(v, list) and v and all(
                    isinstance(x, int | float) and not isinstance(x, bool) for x in v)):
                out[key] = v
    walk(data, "")
    return out


def _all_keys() -> set[str]:
    keys = set()
    for p in PROFILES.glob("*.toml"):
        data = tomllib.loads(p.read_text(encoding="utf-8"))

        def walk(t, prefix):
            for k, v in t.items():
                if isinstance(v, dict):
                    walk(v, f"{prefix}{k}.")
                else:
                    keys.add(f"{prefix}{k}")
        walk(data, "")
    return keys


def _table_rows():
    for f in FILES:
        path = GUIDE / f"{f}.md"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("|") and not re.fullmatch(r"\|[\s:|-]+\|?", s):
                yield f"{f}.md:{i}", s


def test_every_guide_file_exists():
    missing = [f for f in FILES if not (GUIDE / f"{f}.md").is_file()]
    assert not missing


@pytest.mark.parametrize("profile", ["vanilla", "industrial"])
def test_style_docs_cite_profile_keys(profile):
    rows = [r for _, r in _table_rows()]
    uncited = [k for k in _numeric_keys(profile) if not any(f"`{k}`" in r for r in rows)]
    assert not uncited, f"numeric {profile} keys no doc table cites: {uncited}"


def test_no_table_number_without_a_key():
    from se2modkit.style import check
    keys = _all_keys() | check.FINDING_KEYS
    bad = []
    for where, row in _table_rows():
        cited = {c.strip("`") for c in CODE.findall(row)}
        if NUMBER.search(LINK.sub("]", CODE.sub("", row))) and not (cited & keys):
            bad.append(f"{where}: {row[:100]}")
    assert not bad, "table rows with a number but no profile key:\n" + "\n".join(bad)


def test_cited_profile_keys_exist():
    from se2modkit.style import check
    keys = _all_keys() | check.FINDING_KEYS
    sections = {k.split(".")[0] for k in keys}
    bad = []
    for where, row in _table_rows():
        for c in CODE.findall(row):
            c = c.strip("`")
            if re.fullmatch(r"[a-z_]+(\.[a-z_]+)+", c) and c.split(".")[0] in sections and c not in keys:
                bad.append(f"{where}: `{c}`")
    assert not bad, "doc tables cite keys no profile has:\n" + "\n".join(bad)


def test_believable_purpose_is_manual():
    text = (GUIDE / "checks.md").read_text(encoding="utf-8")
    manual = text.split("## Manual review", 1)
    assert len(manual) == 2, "checks.md needs a '## Manual review' section"
    assert "believable" in manual[1].split("\n## ", 1)[0].lower()
    from se2modkit.style import check
    assert not [k for k in check.REPORT_KEYS if "purpose" in k or "believ" in k]


def test_no_machine_paths_in_the_guide():
    banned = re.compile(r"/home/|debian-installation|GE-Proton|\b[A-Z]:\\|\.steam/")
    hits = []
    for p in GUIDE.rglob("*.md"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                hits.append(f"{p.relative_to(GUIDE)}:{i}: {line.strip()[:90]}")
    assert not hits, "\n".join(hits)


def test_every_profile_has_its_doc():
    profiles = {p.stem for p in PROFILES.glob("*.toml")}
    docs = {p.stem for p in (GUIDE / "profiles").glob("*.md")}
    assert profiles == docs


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _numbers(cell: str) -> list[float]:
    return [float(m.group(0).replace(",", "")) for m in NUMBER.finditer(LINK.sub("]", CODE.sub("", cell)))]


def test_doc_values_equal_profile_values():
    """In every table with a Key and a Value column, the numbers in the Value cell are the cited keys' values, in
    order (a list value contributes all its numbers)."""
    from se2modkit.style import profile
    checked, bad = 0, []
    for f in FILES:
        prof = profile.load("industrial" if f == "profiles/industrial" else "vanilla")
        header = None
        for i, line in enumerate((GUIDE / f"{f}.md").read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if not s.startswith("|"):
                header = None
                continue
            if re.fullmatch(r"\|[\s:|-]+\|?", s):
                continue
            cells = _cells(s)
            if header is None:
                header = [c.lower() for c in cells]
                continue
            if "key" not in header or "value" not in header:
                continue
            keys = [c.strip("`") for c in CODE.findall(cells[header.index("key")])]
            values = [prof.get(k, None) for k in keys]
            if not keys or any(v is None or isinstance(v, bool | str) or (isinstance(v, list) and (
                    not v or not all(isinstance(x, int | float) for x in v))) for v in values):
                continue
            want = [float(x) for v in values for x in (v if isinstance(v, list) else [v])]
            got = _numbers(cells[header.index("value")])
            checked += 1
            if got != pytest.approx(want):
                bad.append(f"{f}.md:{i}: {keys} value cell {got} != profile {want}")
    assert checked >= 40, checked
    assert not bad, "\n".join(bad)
