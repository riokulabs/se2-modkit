"""`pipeline.interior`: what a broken block shows inside, Keen's way: a zone's inside is a solid core whose exposed
faces are FracturedMaterial01, plus a few large recognisable parts; no zone opens onto an empty hollow, and the inside
is not a modelled machine."""

import pytest

from se2modkit.pipeline import interior as IN


def _box_tris(lo, hi):
    """The 12 outward triangles of an axis-aligned box, [(p0, p1, p2)]."""
    out = []
    for ax in range(3):
        for v in (lo[ax], hi[ax]):
            u, w = [i for i in range(3) if i != ax]

            def P(a, b, v=v, u=u, w=w, ax=ax):
                p = [0.0, 0.0, 0.0]
                p[ax], p[u], p[w] = v, a, b
                return tuple(p)
            q = [P(lo[u], lo[w]), P(hi[u], lo[w]), P(hi[u], hi[w]), P(lo[u], hi[w])]
            out += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    return out


LO, HI = (-0.5, -0.5, -1.0), (0.5, 0.5, 1.0)
SEAMS = [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), ["Fracture_01"], ["Fracture_02"])]   # Fracture_01 on +x


def _walls(sx):
    """One half's outer skin: its side wall, and the front / back / top / bottom walls of its half."""
    x0, x1 = sorted((0.0, sx * 0.5))
    out = _box_tris(*sorted([(sx * 0.49, -0.5, -1.0), (sx * 0.5, 0.5, 1.0)], key=lambda p: p[0]))
    for lo, hi in (((x0, -0.5, -1.0), (x1, -0.49, 1.0)), ((x0, 0.49, -1.0), (x1, 0.5, 1.0)),
                   ((x0, -0.5, -1.0), (x1, 0.5, -0.99)), ((x0, -0.5, 0.99), (x1, 0.5, 1.0))):
        out += _box_tris(lo, hi)
    return out


def _core(sx):
    """One half's piece of a solid core: its body faces and its break cap on the seam (x = 0)."""
    x0, x1 = sorted((0.0, sx * 0.49))
    faces = _box_tris((x0, -0.49, -0.99), (x1, 0.49, 0.99))
    cap = [t for t in faces if all(abs(p[0]) < 1e-9 for p in t)]
    return [t for t in faces if t not in cap], cap


def test_inner_box_shrinks_and_vanishes_on_thin_blocks():
    assert IN.inner_box((-0.5, -0.6, -1.25), (0.5, 0.6, 1.25)) == pytest.approx(
        ([-0.4, -0.5, -1.15], [0.4, 0.5, 1.15]))
    assert IN.inner_box((-0.5, -0.5, -0.02), (0.5, 0.5, 0.02)) is None          # a tile: no interior


def test_survey_fails_an_empty_shell():
    """Two halves of a hollow box: either half's death opens onto the other's empty inside."""
    rows = IN.survey(LO, HI, {"Fracture_01": _walls(1), "Fracture_02": _walls(-1)}, {}, SEAMS)
    assert rows["Fracture_01"]["exposes"] and rows["Fracture_01"]["cover"] < 0.05
    assert {z for z, _ in IN.faults(rows)} == {"Fracture_01", "Fracture_02"}


def test_survey_fails_a_modelled_machine_in_a_hollow():
    """The hollow filled with modelled parts still opens onto a hollow between them, and its detail is over the
    cap."""
    def parts(sx):
        out = []
        for k in range(40):
            x = sx * (0.15 + 0.008 * (k % 5))
            z = -0.9 + 0.045 * k
            out += _box_tris(*sorted([(x, -0.3, z), (x + sx * 0.02, 0.3, z + 0.02)], key=lambda p: p[0]))
        return out
    rows = IN.survey(LO, HI, {"Fracture_01": _walls(1) + parts(1), "Fracture_02": _walls(-1) + parts(-1)}, {}, SEAMS)
    why = dict(IN.faults(rows))
    assert "empty hollow" in why["Fracture_01"] and "modelled interior" in why["*"]


def test_survey_passes_a_solid_core():
    """The same halves on a solid core cut at the seam: the dead half leaves the live half's FracturedMaterial01 cap,
    so nothing opens onto a hollow."""
    b1, c1 = _core(1)
    b2, c2 = _core(-1)
    rows = IN.survey(LO, HI, {"Fracture_01": _walls(1), "Fracture_02": _walls(-1)},
                     {"Fracture_01": c1 + b1, "Fracture_02": c2 + b2}, SEAMS)
    assert rows["Fracture_01"]["cover"] > 0.95 and rows["Fracture_01"]["core_m2"] > 1.0
    assert IN.faults(rows) == []


def test_survey_counts_a_part_just_behind_the_seam():
    """A recognisable face just behind the seam covers it; the same face DEPTH + 5 cm back does not."""
    def plate(x):
        return _box_tris((x - 0.004, -0.49, -0.99), (x, 0.49, 0.99))
    for x, want in ((-0.002, True), (-(IN.DEPTH + 0.05), False)):
        rows = IN.survey(LO, HI, {"Fracture_01": _walls(1), "Fracture_02": _walls(-1) + plate(x)}, {}, SEAMS)
        assert (rows["Fracture_01"]["cover"] > 0.95) is want, x
