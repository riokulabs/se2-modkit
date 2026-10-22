import itertools

import numpy as np

from se2modkit.model import placement as PL


def box_hull(lo, hi):
    return np.array(list(itertools.product(*zip(lo, hi))), float)


def box_tris(lo, hi):
    c = box_hull(lo, hi)
    tris = []
    for ax in range(3):
        for side in (0, 1):
            pts = [p for p in c if p[ax] == (hi if side else lo)[ax]]
            a, b, d, e = (pts[0], pts[1], pts[3], pts[2])
            tris += [(a, b, d), (a, d, e)]
    return tris


def test_solid_box_fills_bounds():
    # collision-glTF frame: cell i covers [0.25 (i-1), 0.25 i]. A 0.75 x 2.5 x 1.25 m box is 3 x 10 x 5 cells:
    # x cells -1..1, y cells -5..4, z cells -2..2. bounds are inclusive cell indices (as in sb).
    lo, hi = (-0.5, -1.5, -0.75), (0.25, 1.0, 0.5)
    bounds = ((-1, -5, -2), (1, 4, 2))
    r = PL.analyse(box_tris(lo, hi), [("box", box_hull(lo, hi))], bounds=bounds, spans=None)
    assert r["box_cells"] == 150
    assert r["occupied_cells"] == 150
    assert r["occupied_in_box"] == 150
    assert r["empty_in_box"] == 0 and r["occupied_outside_box"] == 0
    for d in PL.DIRS:
        assert r["faces"][d]["box_face_coverage_pct"] == 100.0, d
    assert "box" in PL.summary_line("box", r)


def test_shift_for_bounds_parity():
    # even cell counts shift 0.125 (vanilla Timer50 2x2x2), odd counts 0 (vanilla ControlPanel25 1x1 across)
    assert PL.shift_for_bounds(((0, 0, 0), (1, 1, 1))) == (0.125, 0.125, 0.125)
    assert PL.shift_for_bounds(((0, 0, 0), (0, 0, 1))) == (0.0, 0.0, 0.125)
    assert PL.shift_for_bounds(((-1, -4, -2), (2, 5, 2))) == (0.125, 0.125, 0.0)


def test_odd_axes_centred_box_fills_bounds():
    # a model centred on the origin with odd x (3) and z (5) and even y (10): 0.75 x 2.5 x 1.25 m, x +-0.375,
    # y +-1.25, z +-0.625. Inclusive game-frame bounds x -1..1, y -4..5, z -2..2 (dc.catalog.bounds).
    lo, hi = (-0.375, -1.25, -0.625), (0.375, 1.25, 0.625)
    bounds = ((-1, -4, -2), (1, 5, 2))
    r = PL.analyse(box_tris(lo, hi), [("box", box_hull(lo, hi))], bounds=bounds, shift="bounds")
    assert r["occupied_in_box"] == 150 and r["occupied_outside_box"] == 0 and r["free_cells"] == []
    for d in PL.DIRS:
        assert r["faces"][d]["box_face_coverage_pct"] == 100.0, d
    # the old uniform shift misreads the same centred model (half-cells spill out on the odd axes)
    r_old = PL.analyse(box_tris(lo, hi), [("box", box_hull(lo, hi))], bounds=bounds)
    assert r_old["occupied_outside_box"] > 0


def test_mount_cells_count_and_list():
    """The face's mount_cells stays the int count; the cells themselves are a separate key (mount_cell_list)."""
    lo, hi = (-0.5, -1.5, -0.75), (0.25, 1.0, 0.5)
    bounds = ((-1, -5, -2), (1, 4, 2))
    r = PL.analyse(box_tris(lo, hi), [("box", box_hull(lo, hi))], bounds=bounds, spans=None)
    for d, f in r["faces"].items():
        assert isinstance(f["mount_cells"], int), d
        assert isinstance(f["mount_cell_list"], list) and len(f["mount_cell_list"]) == f["mount_cells"], d
        assert all(len(c) == 3 for c in f["mount_cell_list"])
