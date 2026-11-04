"""The look-dev scene's plain-Python half: viewpoints, set layouts, and the [lookdev] table in previews.toml."""

from __future__ import annotations

import math

import pytest

from se2modkit.preview import lookdev as LD
from se2modkit.preview import plan, shots

CUBE = (1.25, 1.25, 1.25)       # one large-grid cell, half extents


def test_eye_view_stands_on_the_hull_at_eye_height():
    for view in ("3q_front", "3q_back"):
        loc, target, lens, ortho = LD.viewpoint(view, CUBE, center=(0, 0, 1.25), floor_z=0.0)
        assert loc[2] == pytest.approx(LD.EYE_HEIGHT_M)
        assert ortho is None
        assert target == pytest.approx((0, 0, 1.25))
        horiz = math.hypot(loc[0], loc[1])
        assert 1.25 + 2.0 <= horiz <= 1.25 + 6.0                 # 2-6 m from the block's side
        d = shots.VIEWS[view].direction
        assert (loc[0] * d[0] + loc[1] * d[1]) > 0                # on the view's side
    low = LD.viewpoint("3q_low", CUBE, center=(0, 0, 1.25), floor_z=0.0)[0]
    assert low[2] == pytest.approx(LD.CROUCH_HEIGHT_M)


def test_eye_view_lens_matches_game_fov():
    lens = LD.game_lens(16 / 9)
    assert lens == pytest.approx(14.49, abs=0.05)               # 1.22 rad vertical on a 36 mm-wide 16:9 sensor
    sensor_h = 36.0 / (16 / 9)
    assert 2 * math.atan(sensor_h / 2 / lens) == pytest.approx(LD.GAME_FOV_V, rel=1e-6)
    assert LD.viewpoint("3q_front", CUBE, center=(0, 0, 1.25))[2] == pytest.approx(lens)


def test_studio_view_fits_bounds():
    loc, target, lens, ortho = LD.viewpoint("front", CUBE, center=(0, 0, 1.25))
    assert ortho == pytest.approx(2.5 * 16 / 9 / 0.8)           # Blender's ortho scale spans the width: fit the height
    tall = LD.viewpoint("front", (8.75, 8.75, 11.25), center=(0, 0, 11.25))[3]
    assert tall == pytest.approx(22.5 * 16 / 9 / 0.8)
    assert loc[1] > 1.25 and loc[0] == pytest.approx(0) and loc[2] == pytest.approx(1.25)
    loc, _, _, ortho = LD.viewpoint("top", (2.5, 1.25, 1.25), center=(0, 0, 1.25))
    assert ortho == pytest.approx(5.0 / 0.8)
    assert loc[2] > 2.5


def test_hull_covers_the_footprint_with_margin():
    cells = LD.hull_cells((1.25, 1.25, 1.25), "large")
    assert (0.0, 0.0) in cells
    xs = sorted({x for x, _ in cells})
    assert xs[0] == pytest.approx(-5.0) and xs[-1] == pytest.approx(5.0)   # two free cells each side
    small = LD.hull_cells((0.25, 0.25, 0.25), "small")
    assert min(x for x, _ in small) == pytest.approx(-1.0)


def test_neighbor_slots_stay_off_the_block():
    half = (2.5, 1.25, 1.25)
    slots = LD.neighbor_slots(half, "large", 4)
    assert len(slots) == 4
    for x, y in slots:
        assert abs(x) > half[0] or abs(y) > half[1]
    fig = LD.figure_spot(half, "large")
    assert abs(fig[0]) > half[0] or abs(fig[1]) > half[1]
    assert fig not in slots


def test_neighbors_come_from_previews_toml():
    cfg = plan.parse_lookdev({"lookdev": {"neighbors": ["Thrusters/Hydrogen/250/Models/NonFractured/HydrogenThruster.fbx"],
                                          "materials": {"BellSteel": "PaintedMetalColorable"}}})
    assert cfg.neighbors == ("Thrusters/Hydrogen/250/Models/NonFractured/HydrogenThruster.fbx",)
    assert cfg.materials == {"BellSteel": "PaintedMetalColorable"}
    assert plan.parse_lookdev({}).neighbors == ()
    # a [lookdev] table no longer trips the top-level key check
    plan.parse_plan({"lookdev": {"neighbors": []}, "sets": {"s": {"blocks": ["XX_A"], "shots": [{"name": "x"}]}}})
    with pytest.raises(plan.PlanError):
        plan.parse_lookdev({"lookdev": {"neighbours": []}})
    with pytest.raises(plan.PlanError):
        plan.parse_lookdev({"lookdev": {"neighbors": ["../../etc/passwd"]}})


def test_space_set_is_not_default():
    assert LD.DEFAULT_SET == "neighbors"
    assert LD.SETS == ("hull", "neighbors", "interior", "space")
    assert LD.SET_SPECS["space"].mood and not LD.SET_SPECS["neighbors"].mood
    assert LD.SET_SPECS["neighbors"].neighbors and not LD.SET_SPECS["hull"].neighbors
    with pytest.raises(shots.PreviewError):
        LD.set_spec("void")


XL = (8.75, 11.25, 8.75)         # an XL drive lying along Y


def test_tall_blocks_are_seen_from_mid_height_and_whole():
    loc, target, lens, _ = LD.viewpoint("3q_front", XL, center=(0, 0, 8.75))
    assert loc[2] == pytest.approx(8.75)                       # hovering like a jetpack, not standing under it
    fit = shots.fit_distance(XL, (-0.6, 0.8, 0.0), lens, 16 / 9, LD.EYE_FILL)
    assert math.hypot(loc[0], loc[1]) == pytest.approx(fit)     # no 6 m cap when the block needs more room
    low = LD.viewpoint("3q_low", XL, center=(0, 0, 8.75))[0]
    assert low[2] == pytest.approx(LD.EYE_HEIGHT_M)             # the low view still stands on the hull


def test_mount_face_turns_that_face_down():
    for face, axis in (("-Z", (0, 0, -1)), ("+Z", (0, 0, 1)), ("+Y", (0, 1, 0)), ("-Y", (0, -1, 0)),
                       ("+X", (1, 0, 0)), ("-X", (-1, 0, 0))):
        r = LD.mount_rotation(face)
        down = tuple(sum(r[i][j] * axis[j] for j in range(3)) for i in range(3))
        assert down == pytest.approx((0, 0, -1)), face
    assert LD.rotated_half(XL, "+Y") == pytest.approx((8.75, 8.75, 11.25))
    with pytest.raises(shots.PreviewError):
        LD.mount_rotation("up")


@pytest.mark.parametrize("bad", ["C:/Windows/x.fbx", "C:foo.fbx", "Blocks\\..\\..\\x.fbx", "/abs/x.fbx",
                                 "\\\\server\\share\\x.fbx", "a/../b.fbx", "x.FBX.txt"])
def test_neighbor_paths_cannot_leave_the_vanilla_folder(bad):
    with pytest.raises(plan.PlanError):
        plan.parse_lookdev({"lookdev": {"neighbors": [bad]}})
