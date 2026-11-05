"""`pipeline.fracture_check`: the fail lines and the summary of a block's fracture check (plain Python)."""

from se2modkit.pipeline import fracture_check as FC


def _report(**kw):
    r = {"block": "XX_Box", "family": "box", "status": "checked", "error": None, "cell_groups": 3, "loose": {},
         "tris": 1200,
         "zones": [{"id": "Fracture_01", "label": "mount", "critical": True, "share": 0.18, "lifted": False},
                   {"id": "Fracture_02", "label": "body", "critical": False, "share": 0.82, "lifted": False}],
         "seams": [{"zones": ["Fracture_01", "Fracture_02"], "snap_mm": 50.0, "moved_mm": 12.0, "clear_mm": 8.0}]}
    r.update(kw)
    return r


def test_a_clean_block_has_no_problems():
    assert FC.problems(_report()) == []


def test_a_key_zone_over_the_default_share_only_warns():
    r = _report()
    r["zones"][0]["share"] = 0.43
    assert FC.problems(r) == []
    (line,) = FC.warnings(r)
    assert "Fracture_01" in line and "43.0%" in line and "20%" in line
    assert any(ln.startswith("WARN") for ln in FC.summary(r))


def test_a_given_key_share_limit_fails():
    r = _report()
    r["zones"][0]["share"] = 0.25
    assert FC.problems(r, max_key_share=0.3) == []
    (line,) = FC.problems(r, max_key_share=0.2)
    assert "25.0%" in line
    assert FC.warnings(r, max_key_share=0.2) == []          # a limit given is a FAIL, never also a WARN


def test_a_large_plain_zone_does_not_fail():
    assert FC.problems(_report()) == []      # Fracture_02 holds 82 % but is not critical


def test_loose_geometry_fails_by_part():
    (line,) = FC.problems(_report(loose={"pipe": {"verts": 22199, "edges": 3}}))
    assert "pipe" in line and "22199" in line and "3" in line


def test_an_error_is_a_fail_line():
    (line,) = FC.problems(_report(status="error", error="zone Fracture_03 has no LOD0 geometry", zones=[]))
    assert "XX_Box" in line and "Fracture_03" in line


def test_too_many_cell_groups_fail():
    (line,) = FC.problems(_report(cell_groups=200))
    assert "200" in line and "groups" in line


def test_an_unfractured_block_passes_with_a_note():
    r = _report(status="unfractured", zones=[], seams=[], error="one physics box")
    assert FC.problems(r) == []
    assert any("not fractured" in ln for ln in FC.summary(r))


def test_summary_lists_zones_seams_and_fails():
    r = _report()
    r["zones"][0]["share"] = 0.5
    r["zones"][1]["share"] = 0.5
    r["zones"][1]["lifted"] = True
    lines = FC.summary(r, max_key_share=0.2)
    text = "\n".join(lines)
    assert lines[0].startswith("XX_Box")
    assert "Fracture_01" in text and "50.0%" in text and "critical" in text
    assert "lifted" in text
    assert "moved 12.0 mm" in text and "clear 8.0 mm" in text
    assert lines[-1].startswith("FAIL")


def test_a_fixed_seam_says_so():
    r = _report(seams=[{"zones": ["Fracture_01", "Fracture_02"], "snap_mm": 0.0, "moved_mm": 0.0, "clear_mm": 3.0}])
    assert any("fixed, clear 3.0 mm" in ln for ln in FC.summary(r))


def test_unknown_cell_groups_print_as_a_question_mark():
    assert "? cell groups" in FC.summary(_report(cell_groups=None))[0]
