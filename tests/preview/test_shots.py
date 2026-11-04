from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from se2modkit.preview import PreviewError, plan, shots

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

CANON = ("front", "back", "left", "right", "top", "bottom", "3q_front", "3q_back", "3q_low", "mount", "inside")


def _png(path: Path, value: int, *, white_share: float = 0.0, size=(100, 100), alpha: bool = False) -> Path:
    im = Image.new("RGBA" if alpha else "RGB", size, (value, value, value, 255) if alpha else (value,) * 3)
    n = int(size[0] * size[1] * white_share)
    px = im.load()
    for i in range(n):
        px[i % size[0], i // size[0]] = (255, 255, 255, 255) if alpha else (255, 255, 255)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def _set(folder: Path, block: str, stage: str, rnd: int, views, value=120):
    for v in views:
        _png(folder / f"{shots.shot_name(block, stage, rnd, v)}.png", value)


def test_eleven_canonical_views():
    assert tuple(shots.VIEWS) == CANON
    for name in ("3q_front", "3q_back", "3q_low", "mount", "inside"):
        assert shots.VIEWS[name].eye
    for name in ("front", "back", "left", "right", "top", "bottom"):
        assert not shots.VIEWS[name].eye
    for v in shots.VIEWS.values():
        assert math.isclose(sum(c * c for c in v.direction), 1.0, rel_tol=1e-6)


def test_old_camera_names_still_parse():
    for old, new in (("3q_rear", "3q_back"), ("3q_front_low", "3q_low"), ("3q_front_high", "3q_front"),
                     ("iso", "3q_front"), ("three_q_back", "3q_back"), ("side", "right")):
        assert shots.canonical(old) == new
    sets = plan.parse_plan({"sets": {"s": {"blocks": ["XX_A"], "shots": [{"name": "x", "camera": "3q_rear"}]}}})
    assert sets["s"].shots[0].camera == "3q_back"
    assert set(CANON) <= set(plan.CAMERAS)


def test_unknown_view_raises():
    with pytest.raises(PreviewError):
        shots.canonical("diagonal")
    with pytest.raises(PreviewError):
        shots.shot_name("A", "look", 1, "diagonal")


def test_shot_name_round_trips():
    assert shots.shot_name("ARCS", "look", 3, "3q_front") == "ARCS_look_r3_3q_front"
    assert shots.shot_name("ARCS", "look", 3, "3q_front", "close-gimbal") == "ARCS_look_r3_3q_front_close-gimbal"
    for stem in ("ARCS_look_r3_3q_front", "ARCS_Cube250_look_r12_3q_low_close-gimbal", "A_fix_r1_top_before"):
        n = shots.parse_shot_name(stem)
        assert n is not None
        assert shots.shot_name(n.block, n.stage, n.rnd, n.view, n.extra) == stem
    n = shots.parse_shot_name("ARCS_Cube250_look_r12_3q_low_close-gimbal")
    assert (n.block, n.stage, n.rnd, n.view, n.extra) == ("ARCS_Cube250", "look", 12, "3q_low", "close-gimbal")
    assert shots.parse_shot_name("drives-contact-v2") is None


def test_fit_distance_fills_frame():
    half = (1.25, 1.25, 1.25)
    d = shots.fit_distance(half, (0, 1, 0), 25.0, 16 / 9, fill=0.8)
    sensor_h = 36.0 / (16 / 9)                      # Blender's 36 mm sensor fits the wider side
    half_fov_v = math.atan(sensor_h / 2 / 25.0)
    # the nearest face is 1.25 m closer than the centre; the cube's 2.5 m height fills 0.8 of the frame there
    seen = 2.5 / (2 * (d - 1.25) * math.tan(half_fov_v))
    assert seen == pytest.approx(0.8, rel=0.02)


def test_exposure_flags_dark_and_blown(tmp_path):
    assert shots.exposure(_png(tmp_path / "mid.png", 120)).ok
    dark = shots.exposure(_png(tmp_path / "dark.png", 15))
    assert not dark.ok and "dark" in dark.why
    blown = shots.exposure(_png(tmp_path / "blown.png", 120, white_share=0.10))
    assert not blown.ok and "highlights" in blown.why
    # transparent background pixels don't count
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(50):
        for y in range(100):
            im.putpixel((x, y), (120, 120, 120, 255))
    im.save(tmp_path / "alpha.png")
    assert shots.exposure(tmp_path / "alpha.png").ok


def test_check_set_lists_missing_views(tmp_path):
    _set(tmp_path, "A", "look", 1, [v for v in shots.REQUIRED["look"] if v != "top"])
    problems = shots.check_set(tmp_path, "A", "look", 1)
    assert problems == ["missing view: top (A_look_r1_top.png)"]
    _set(tmp_path, "A", "look", 1, ["top"])
    assert shots.check_set(tmp_path, "A", "look", 1) == []
    assert any("inside" in p for p in shots.check_set(tmp_path, "A", "look", 1, interior=True))


def test_check_set_reports_failed_exposure(tmp_path):
    _set(tmp_path, "A", "concept", 2, shots.REQUIRED["concept"])
    _png(tmp_path / "A_concept_r2_top.png", 10)
    assert any(p.startswith("exposure: A_concept_r2_top") for p in shots.check_set(tmp_path, "A", "concept", 2))


def test_check_set_wants_locator_on_close_ups(tmp_path):
    _set(tmp_path, "A", "fix", 1, [])
    close = _png(tmp_path / "A_fix_r1_3q_front_close-gimbal.png", 120)
    assert shots.check_set(tmp_path, "A", "fix", 1) == ["close-up without a locator: A_fix_r1_3q_front_close-gimbal"]
    shots.mark_locator(close)
    assert shots.check_set(tmp_path, "A", "fix", 1) == []


def test_after_needs_same_camera_before(tmp_path):
    after = _png(tmp_path / "A_fix_r1_top_after.png", 120)
    assert shots.check_set(tmp_path, "A", "fix", 1) == ["after without a before: A_fix_r1_top_after"]
    before = _png(tmp_path / "A_fix_r1_top_before.png", 120)
    shots.save_camera(before, [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 5], [0, 0, 0, 1]], 25.0)
    shots.save_camera(after, [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 6], [0, 0, 0, 1]], 25.0)
    assert shots.check_set(tmp_path, "A", "fix", 1) == ["before/after cameras differ: A_fix_r1_top"]
    shots.save_camera(after, [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 5], [0, 0, 0, 1]], 25.0)
    assert shots.check_set(tmp_path, "A", "fix", 1) == []
    assert shots.load_camera(after)["lens"] == 25.0


def test_locator_inset_drawn(tmp_path):
    close = _png(tmp_path / "A_fix_r1_3q_front_close-x.png", 120, size=(400, 300))
    over = _png(tmp_path / "over.png", 40, size=(400, 300))
    out = tmp_path / "out.png"
    shots.add_locator(close, over, (100, 100, 200, 180), out)
    im = Image.open(out).convert("RGB")
    assert im.size == (400, 300)
    assert im.getpixel((10, 10)) == (120, 120, 120)                  # the close-up itself is untouched up top
    corner = [im.getpixel((x, y)) for x in range(300, 400) for y in range(225, 300)]
    assert any(p[0] > 200 and p[1] < 80 for p in corner)              # the red rect is in the inset
    assert json.loads(Path(f"{out}.locator").read_text())["overview"] == "over.png"


def test_shots_check_cli_exit_codes(tmp_path, capsys, monkeypatch):
    from se2modkit import cli
    for name in ("SE2MODKIT_CONFIG", "SE2_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    _set(tmp_path, "A", "concept", 1, shots.REQUIRED["concept"])
    argv = ["shots", "check", str(tmp_path), "--block", "A", "--stage", "concept", "--round", "1"]
    assert cli.main(argv) == 0
    (tmp_path / "A_concept_r1_top.png").unlink()
    assert cli.main(argv) == 1
    assert "missing view: top" in capsys.readouterr().out
