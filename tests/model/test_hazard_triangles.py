"""Stickers02 warning triangles (se2modkit/model/decals.py HAZARD_TRIANGLES): the atlas interlocks point-up / point-down
triangles, so element() must draw them as exact triangles, not their bounding rectangles (whose corners show the
neighbours: the in-game "diagonal lines"). Runs without Blender (bmesh is stubbed; nothing here touches it)."""
import sys
import types

sys.modules.setdefault("bmesh", types.ModuleType("bmesh"))

from se2modkit.model import decals as D  # noqa: E402
from se2modkit.model.config import S2  # noqa: E402


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _atlas_pts(key):
    _, bl, br, apex = D.HAZARD_TRIANGLES[key]
    return [bl, br, apex]


def test_triangles_centred_on_their_elements():
    """Each triangle's centroid lies inside its STICKERS02 rectangle (1600 px preview -> 2048 px). Not containment:
    those rectangles were rough and clipped the signs themselves (tri_hot's top edge cut ~24 px off its apex)."""
    for key in D.HAZARD_TRIANGLES:
        x0, y0, x1, y1 = (v * 2048 / 1600 for v in D.STICKERS02[key])
        pts = _atlas_pts(key)
        cx, cy = sum(x for x, _ in pts) / 3, sum(y for _, y in pts) / 3
        assert x0 <= cx <= x1 and y0 <= cy <= y1, (key, (cx, cy), (x0, y0, x1, y1))


def test_drawn_point_up_as_a_rotation():
    """The UV triangle (base-left, base-right, apex) winds the same way as the drawn face (counter-clockwise): the
    stored point-down sign is turned 180 deg, never mirrored, and the apex is drawn above the base."""
    for key in D.HAZARD_TRIANGLES:
        uv, ratio = D.hazard_triangle_uv(key)
        assert _cross(*uv) > 0, key
        assert 0.8 < ratio < 0.9, (key, ratio)   # an equilateral sign with rounded corners


def test_neighbours_do_not_overlap():
    """The interlocked triangles are disjoint (separating axis on their edges): neither crop shows the other."""
    a, b = (_atlas_pts(k) for k in ("tri_electric", "tri_hot"))

    def separated(p, q):
        for i in range(3):
            e0, e1 = p[i], p[(i + 1) % 3]
            other = p[(i + 2) % 3]
            side = _cross(e0, e1, other)
            if all(_cross(e0, e1, v) * side < 0 for v in q):
                return True
        return False
    assert separated(a, b) or separated(b, a)


def test_element_draws_a_triangle(monkeypatch):
    """element() routes the triangle keys to one 3-corner face, point up, base = width, centred on (a, b)."""
    got = []
    monkeypatch.setattr(D, "_quad", lambda p, F, corners, d, mat, uvs: got.append((corners, uvs)) or "face")
    p = types.SimpleNamespace(name="t")
    for key in D.HAZARD_TRIANGLES:
        got.clear()
        assert D.element(p, None, 1.0, 2.0, 0.2, S2, key) == "face"
        (corners, uvs), = got
        assert len(corners) == 3 and len(uvs) == 3
        (xl, yl), (xr, yr), (xa, ya) = corners
        assert abs(xr - xl - 0.2) < 1e-9 and yl == yr and ya > yl and abs(xa - 1.0) < 1e-9
        assert abs((yl + ya) / 2 - 2.0) < 1e-9
