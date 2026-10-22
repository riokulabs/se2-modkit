"""`style-waivers.toml`: one reasoned, dated waiver per block + key; waivers stay visible in the output."""

import pytest

from se2modkit.style import check, profile, waivers
from se2modkit.style.waivers import WaiverError

VANILLA = profile.load("vanilla")
REPORT = {"blocks": {"B": {"lods": [{"area_pct": {"PaintedMetalColorable": 30.0, "PaintedMetalDarker": 70.0},
                                     "material_names": ["PaintedMetalColorable", "PaintedMetalDarker"],
                                     "materials": 2, "tris": 10, "max_tris": 100, "max_materials": 18}]}}}


def _write(tmp_path, text):
    p = tmp_path / "style-waivers.toml"
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def test_waiver_marks_finding(tmp_path):
    w = waivers.load(_write(tmp_path, """
[[waiver]]
block = "B"
key = "paint.colorable_min_share"
reason = "dark equipment cabinet: the vanilla analogue is mostly Darker"
date = 2026-10-21
"""))
    f, = check.check(REPORT, VANILLA, w)
    assert f.level == "fail"
    assert f.waived_by == "dark equipment cabinet: the vanilla analogue is mostly Darker (2026-10-21)"
    res = check.run(REPORT, VANILLA, w)
    assert res.unwaived_failures == []
    assert res.unused_waivers == []


def test_waiver_needs_reason(tmp_path):
    p = _write(tmp_path, '[[waiver]]\nblock = "B"\nkey = "paint.colorable_min_share"\ndate = 2026-10-21\n')
    with pytest.raises(WaiverError, match="reason"):
        waivers.load(p)


def test_waiver_needs_date(tmp_path):
    p = _write(tmp_path, '[[waiver]]\nblock = "B"\nkey = "paint.colorable_min_share"\nreason = "why"\n')
    with pytest.raises(WaiverError, match="date"):
        waivers.load(p)


def test_waiver_date_must_be_a_date(tmp_path):
    p = _write(tmp_path, '[[waiver]]\nblock = "B"\nkey = "k.k"\nreason = "why"\ndate = "last week"\n')
    with pytest.raises(WaiverError, match="date"):
        waivers.load(p)


def test_one_waiver_per_block_and_key(tmp_path):
    one = '[[waiver]]\nblock = "B"\nkey = "paint.colorable_min_share"\nreason = "why"\ndate = 2026-10-21\n'
    with pytest.raises(WaiverError, match="B.*paint.colorable_min_share"):
        waivers.load(_write(tmp_path, one + "\n" + one))


def test_unused_waiver_is_listed(tmp_path):
    w = waivers.load(_write(tmp_path, '[[waiver]]\nblock = "Gone"\nkey = "emissive.max_share"\nreason = "r"\n'
                                      'date = 2026-10-21\n'))
    res = check.run(REPORT, VANILLA, w)
    assert [(u.block, u.key) for u in res.unused_waivers] == [("Gone", "emissive.max_share")]


def test_missing_file_is_no_waivers(tmp_path):
    assert waivers.load(tmp_path / "style-waivers.toml").items == ()


def test_bad_toml_is_a_waiver_error(tmp_path):
    with pytest.raises(WaiverError, match="style-waivers.toml"):
        waivers.load(_write(tmp_path, "[[waiver]\n"))
