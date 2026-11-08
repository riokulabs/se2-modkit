"""The plain-Python half of a look set's measured views: part numbers and colors, the envelope, section cuts, the
gameplay-distance sizes, the sheets drawn from the Blender side's `diag.json`, and the reference silhouette compare."""

from __future__ import annotations

import json
import math

import pytest

from se2modkit.preview import PreviewError, diag, lookdev

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402


def test_part_color_is_stable_and_spread():
    a = diag.part_color("Nozzle")
    assert a == diag.part_color("Nozzle") and all(0 <= c <= 255 for c in a)
    assert a != diag.part_color("Body")
    assert diag.part_color("Nozzle.001") != diag.part_color("Nozzle")   # each object is its own part, as in describe
    colors = {diag.part_color(f"part_{i}") for i in range(40)}
    assert len(colors) == 40


def test_parts_are_numbered_the_way_describe_numbers_them():
    parts = diag.number_parts([{"name": "b_strut", "collection": "Block"}, {"name": "A_plate", "collection": "Block"},
                               {"name": "zz", "collection": "Aux"}])
    assert [(p["id"], p["name"]) for p in parts] == [(1, "zz"), (2, "A_plate"), (3, "b_strut")]
    assert parts[0]["color"] == list(diag.part_color("zz"))


def test_placed_size_follows_the_mount_rotation():
    assert diag.placed_size((1.0, 2.0, 3.0), "-Z") == pytest.approx((1.0, 2.0, 3.0))
    # a block mounted by +Y lies with its Y axis vertical
    assert diag.placed_size((1.0, 2.0, 3.0), "+Y") == pytest.approx((1.0, 3.0, 2.0))
    assert diag.placed_size((1.0, 2.0, 3.0), "+X") == pytest.approx((3.0, 2.0, 1.0))


def test_envelope_from_the_flag_is_centered_on_the_block_origin():
    env = diag.envelope_box((1.0, 1.0, 1.0), envelope=(2.5, 2.5, 5.0), mount_face="+Y", origin=(0.0, 0.1, 1.2))
    assert env["from"] == "--envelope"
    assert env["size_m"] == pytest.approx([2.5, 5.0, 2.5])
    assert env["lo"] == pytest.approx([-1.25, -2.4, -0.05]) and env["hi"] == pytest.approx([1.25, 2.6, 2.45])


def test_envelope_without_the_flag_is_whole_grid_cells_around_the_bounds():
    env = diag.envelope_box((1.0, 1.3, 0.6), grid="large")
    assert env["from"] == "grid"
    assert env["cells"] == [1, 2, 1] and env["size_m"] == pytest.approx([2.5, 5.0, 2.5])
    assert env["lo"] == pytest.approx([-1.25, -2.5, 0.0]) and env["hi"] == pytest.approx([1.25, 2.5, 2.5])
    assert "1 x 2 x 1 large-grid cells" in env["label"]
    small = diag.envelope_box((0.25, 0.25, 0.25), grid="small")
    assert small["cells"] == [1, 1, 1] and small["size_m"] == pytest.approx([0.5, 0.5, 0.5])


def test_envelope_must_be_three_sizes():
    with pytest.raises(PreviewError):
        diag.envelope_box((1, 1, 1), envelope=(1.0, 0.0, 2.0))


def test_section_cuts_contain_the_long_axis():
    cuts = diag.section_cuts((0.5, 2.0, 1.0))           # long along Y
    assert [c["normal"] for c in cuts] == ["z", "x"]
    assert {c["view"] for c in cuts} == {"top", "right"}
    cuts = diag.section_cuts((0.5, 0.6, 3.0))           # tall: long along Z
    assert [c["normal"] for c in cuts] == ["y", "x"]
    assert all("cut" in c["label"] for c in cuts)


def test_apparent_size_matches_a_pinhole_camera():
    # a 2.5 m cube seen face on at 10 m in a 1080p view with the game's field of view
    w, h = diag.apparent_px((1.25, 1.25, 1.25), (0.0, 1.0, 0.0), 10.0)
    near = 1080 * 2.5 / (2 * (10.0 - 1.25) * math.tan(lookdev.GAME_FOV_V / 2))
    assert h == pytest.approx(near, rel=0.01) and w == pytest.approx(h, rel=0.01)
    far = diag.apparent_px((1.25, 1.25, 1.25), (0.0, 1.0, 0.0), 50.0)
    assert far[1] < h / 4


def test_distance_plan_shrinks_with_range():
    plan = diag.distance_plan((1.25, 1.25, 1.25), (-0.6, 0.8, 0.45))
    assert [p["m"] for p in plan] == list(diag.DISTANCES_M)
    assert plan[0]["px"][0] > plan[1]["px"][0] > plan[2]["px"][0] > 0


# --------------------------------------------------------------------------- sheets


def _tile(path, *, alpha=True, color=(150, 150, 150), box=(300, 120, 500, 330), size=(800, 450)):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", size, (0, 0, 0, 0) if alpha else (255, 255, 255, 255))
    ImageDraw.Draw(im).rectangle(box, fill=(*color, 255))
    im.save(path)
    return path


def _info(out):
    d = out / "diag"
    for name in ("clay", "parts", "wire", "section_z", "section_x"):
        _tile(d / f"{name}.png", color=(255, 255, 255) if name == "wire" else (150, 150, 150))
    for v in ("front", "right", "top"):
        _tile(d / f"ortho_{v}.png", size=(1280, 720), box=(500, 200, 780, 600))
    shot = _tile(out / "XX_look_r1_3q_front.png", alpha=False, color=(90, 110, 130), size=(800, 450))
    info = {
        "tile": [800, 450],
        "tiles": {"clay": "diag/clay.png", "parts": "diag/parts.png", "wire": "diag/wire.png"},
        "sections": [{"png": "diag/section_z.png", "label": "cut z = 1250 mm, from above"},
                     {"png": "diag/section_x.png", "label": "cut x = 0 mm, from +X"}],
        "parts": [{"id": 1, "name": "Body", "color": [200, 40, 40], "xy": [400, 220]},
                  {"id": 2, "name": "Nozzle", "color": [40, 200, 40], "xy": None}],
        "distance": {"shot": shot.name, "rect": [300, 120, 500, 330],
                     "targets": [{"m": 10, "px": [150, 160]}, {"m": 25, "px": [60, 64]}, {"m": 50, "px": [30, 32]}]},
        "size_mm": [2500.0, 2500.0, 2500.0], "size_mm_source": [2500.0, 2500.0, 2500.0],
        "envelope": {"size_mm": [2500.0, 2500.0, 5000.0], "from": "grid", "label": "1 x 1 x 2 large-grid cells"},
        "ortho": {v: {"png": f"diag/ortho_{v}.png", "bbox": [500, 200, 780, 600], "envelope": [480, 100, 800, 600],
                      "h_axis": 0 if v != "right" else 1, "v_axis": 2 if v != "top" else 1}
                  for v in ("front", "right", "top")},
        "seconds": 2.5,
    }
    (d / "diag.json").write_text(json.dumps(info), encoding="utf-8")
    return info


def test_compose_writes_the_diagnostic_and_measured_sheets(tmp_path):
    _info(tmp_path)
    files = diag.compose(tmp_path)
    assert files["sheet"] == "diag.png" and files["ortho"] == "ortho.png"
    sheet = Image.open(tmp_path / "diag.png")
    assert sheet.width > 3 * 800 and sheet.height > 2 * 450
    ortho = Image.open(tmp_path / "ortho.png")
    assert ortho.width > 2 * ortho.height   # front, side and top in one row
    assert "diag/clay.png" in files["files"] and "diag/diag.json" in files["files"]


def test_compose_without_a_distance_shot_still_writes_the_sheet(tmp_path):
    info = _info(tmp_path)
    info["distance"] = None
    (tmp_path / "diag" / "diag.json").write_text(json.dumps(info), encoding="utf-8")
    assert diag.compose(tmp_path)["sheet"] == "diag.png"


def test_silhouette_tile_is_black_on_white(tmp_path):
    _tile(tmp_path / "clay.png")
    sil = diag.silhouette(Image.open(tmp_path / "clay.png"))
    assert sil.getpixel((400, 200)) == (0, 0, 0) and sil.getpixel((10, 10)) == (255, 255, 255)


def test_distance_thumbs_are_drawn_at_their_game_size(tmp_path):
    info = _info(tmp_path)
    thumbs = diag.distance_thumbs(tmp_path, info["distance"])
    assert [t["m"] for t in thumbs] == [10, 25, 50]
    # the block's rect (200 px wide) shrinks to about the planned width, with the crop margin around it
    assert thumbs[2]["image"].width < thumbs[1]["image"].width < thumbs[0]["image"].width
    assert 30 <= thumbs[2]["image"].width <= 45


def test_a_block_bigger_than_the_screen_is_drawn_capped_in_the_strip(tmp_path):
    info = _info(tmp_path)
    info["distance"]["targets"] = [{"m": 10, "px": [2000, 2200]}, {"m": 50, "px": [40, 44]}]
    from PIL import ImageFont
    strip = diag._distance_strip(tmp_path, info["distance"], diag._font(ImageFont, 14))
    assert strip.height <= diag.STRIP_CAP + 40


def test_a_block_bigger_than_the_screen_shows_only_the_screen_sized_part(tmp_path):
    info = _info(tmp_path)
    info["distance"]["targets"] = [{"m": 10, "px": [2000, 2200]}, {"m": 50, "px": [40, 44]}]
    near, far = diag.distance_thumbs(tmp_path, info["distance"])
    assert near["screen"] and not far["screen"]
    w, h = near["image"].size
    assert w <= diag.SCREEN[0] + 2 and h <= diag.SCREEN[1] + 2
    assert abs(w / h - diag.SCREEN[0] / diag.SCREEN[1]) < 0.05   # a screen-shaped window, not the whole block


def test_a_block_taller_than_the_screen_keeps_the_window_screen_shaped(tmp_path):
    info = _info(tmp_path)
    info["distance"]["targets"] = [{"m": 10, "px": [150, 1200]}]   # overflows the screen on one axis only
    (near,) = diag.distance_thumbs(tmp_path, info["distance"])
    w, h = near["image"].size
    assert near["screen"]
    assert w <= diag.SCREEN[0] + 2 and h <= diag.SCREEN[1] + 2
    assert abs(w / h - diag.SCREEN[0] / diag.SCREEN[1]) < 0.05   # still a screen-shaped window, not a block sliver


# --------------------------------------------------------------------------- reference


def _mask_png(path, box, size=(400, 300), alpha=True, flip=False):
    im = Image.new("RGBA", size, (0, 0, 0, 0) if alpha else (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle(box, fill=(20, 20, 20, 255))
    x0, y0, x1, y1 = box
    nw, nh = (x1 - x0) * 3 // 10, (y1 - y0) // 5
    notch = (x1 - nw, y0, x1, y0 + nh) if not flip else (x0, y0, x0 + nw, y0 + nh)
    d.rectangle(notch, fill=(0, 0, 0, 0) if alpha else (255, 255, 255, 255))
    im.save(path)
    return path


def test_reference_on_white_matches_our_silhouette(tmp_path):
    ours = _mask_png(tmp_path / "ours.png", (100, 50, 300, 250))
    ref = _mask_png(tmp_path / "ref.png", (40, 20, 140, 120), size=(200, 150), alpha=False)   # same shape, half size
    r = diag.compare_reference(ours, ref, tmp_path / "refdiff.png")
    assert r["iou"] > 0.95 and r["mirrored"] is False
    assert (tmp_path / "refdiff.png").is_file()


def test_a_mirrored_reference_is_turned_round(tmp_path):
    ours = _mask_png(tmp_path / "ours.png", (100, 50, 300, 250))
    ref = _mask_png(tmp_path / "ref.png", (100, 50, 300, 250), alpha=False, flip=True)
    r = diag.compare_reference(ours, ref, tmp_path / "refdiff.png")
    assert r["mirrored"] is True and r["iou"] > 0.95


def test_a_wider_reference_shows_missing_area(tmp_path):
    ours = _mask_png(tmp_path / "ours.png", (100, 50, 200, 250))
    ref = _mask_png(tmp_path / "ref.png", (100, 50, 300, 250), alpha=False)
    r = diag.compare_reference(ours, ref, tmp_path / "refdiff.png")
    assert r["iou"] < 0.7 and r["missing_share"] > r["extra_share"]
    assert r["aspect_reference"] > r["aspect_ours"]
    diff = Image.open(tmp_path / "refdiff.png").convert("RGB")
    assert any(c[2] > 150 and c[0] < 100 for _n, c in diff.getcolors(1 << 20))     # blue: in the reference, not in ours


def test_an_empty_reference_is_one_plain_error(tmp_path):
    ours = _mask_png(tmp_path / "ours.png", (100, 50, 300, 250))
    blank = tmp_path / "blank.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(blank)
    with pytest.raises(PreviewError, match="no shape"):
        diag.compare_reference(ours, blank, tmp_path / "refdiff.png")


def test_an_envelope_left_undrawn_is_named(tmp_path):
    info = _info(tmp_path)
    for o in info["ortho"].values():
        o["envelope"] = None
    info["envelope"]["drawn"] = False
    (tmp_path / "diag" / "diag.json").write_text(json.dumps(info), encoding="utf-8")
    assert diag.compose(tmp_path)["ortho"] == "ortho.png"

