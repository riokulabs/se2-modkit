"""Geometry checks a look set must pass before anyone sees it: plain numpy on triangles and part tags."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from se2modkit.pipeline import geocheck as GC


def box(name, lo, hi, role=None, **kw):
    lo, hi = np.array(lo, float), np.array(hi, float)
    v = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    # outward-wound quads split in two (index = x*4 + y*2 + z)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    t = np.array([tri for a, b, c, d in quads for tri in ((a, b, c), (a, c, d))])
    return GC.Part(name, v, t, role=role, **kw)


def cyl(name, p0, p1, r, role=None, n=24, **kw):
    p0, p1 = np.array(p0, float), np.array(p1, float)
    ax = (p1 - p0) / np.linalg.norm(p1 - p0)
    u = np.cross(ax, [0, 0, 1] if abs(ax[2]) < 0.9 else [1, 0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(ax, u)
    ring = [r * (math.cos(2 * math.pi * i / n) * u + math.sin(2 * math.pi * i / n) * w) for i in range(n)]
    v = np.array([p0 + q for q in ring] + [p1 + q for q in ring] + [p0, p1])
    t = []
    for i in range(n):
        j = (i + 1) % n
        t += [(i, j, n + j), (i, n + j, n + i), (2 * n, j, i), (2 * n + 1, n + i, n + j)]
    return GC.Part(name, v, np.array(t), role=role, **kw)


def _checks(report, kind):
    return [f for f in report.failures + report.warnings if f["check"] == kind]


def test_load_path_finds_a_floating_part():
    mount = box("base", (-1, -1, 0), (1, 1, 0.2), role="mount")
    post = box("post", (-0.1, -0.1, 0.2), (0.1, 0.1, 1.0), role="strut")
    ok = GC.run([mount, post])
    assert not _checks(ok, "load_path")
    floating = box("bracket", (0.5, 0.5, 1.5), (0.7, 0.7, 1.7), role="strut")
    bad = GC.run([mount, post, floating])
    assert _checks(bad, "load_path")[0]["parts"] == ["bracket"]
    assert any(f["check"] == "load_path" for f in bad.failures)


def test_load_path_ignores_decals_and_needs_a_mount():
    mount = box("base", (-1, -1, 0), (1, 1, 0.2), role="mount")
    decal = box("label", (3, 3, 3), (3.1, 3.1, 3.001), role="decal")
    assert not _checks(GC.run([mount, decal]), "load_path")
    lone = GC.run([box("post", (0, 0, 0), (1, 1, 1), role="strut")])
    assert [f["check"] for f in lone.warnings] == ["no_mount"]


def test_pin_axis():
    lug = box("lug", (-0.05, -0.1, -0.2), (0.05, 0.1, 0.1), role="lug", axis=(1, 0, 0), point=(0, 0, 0))
    good = cyl("pin", (-0.15, 0, 0), (0.15, 0, 0), 0.04, role="pin", axis=(1, 0, 0), links=("lug",))
    assert not _checks(GC.run([lug, good]), "pin_axis")
    tilted = cyl("pin", (-0.15, 0, -0.01), (0.15, 0, 0.01), 0.04, role="pin",
                 axis=(0.15, 0, 0.0067), links=("lug",))
    assert _checks(GC.run([lug, tilted]), "pin_axis")[0]["parts"] == ["pin", "lug"]
    offset = cyl("pin", (-0.15, 0, 0.005), (0.15, 0, 0.005), 0.04, role="pin", axis=(1, 0, 0), links=("lug",))
    f = _checks(GC.run([lug, offset]), "pin_axis")[0]
    assert "5.0 mm" in f["detail"]


def test_coplanar_faces_between_parts():
    a = box("panel", (0, 0, 0), (1, 1, 0.1))
    b = box("trim", (0.2, 0.2, 0.0), (0.8, 0.8, 0.1))          # shares both of the panel's planes
    found = _checks(GC.run([a, b]), "coplanar")
    assert found and set(found[0]["parts"]) == {"panel", "trim"}
    lifted = box("trim", (0.2, 0.2, 0.0008), (0.8, 0.8, 0.1008))
    assert not _checks(GC.run([a, lifted]), "coplanar")
    decal = box("trim", (0.2, 0.2, 0.0), (0.8, 0.8, 0.1), role="decal")
    assert not _checks(GC.run([a, decal]), "coplanar")


def test_poke_through():
    plate = box("plate", (-1, -1, 0), (1, 1, 0.1))
    rod = cyl("rod", (0, 0, -0.5), (0, 0, 0.6), 0.05)            # passes clean through the plate
    assert _checks(GC.run([plate, rod]), "poke_through")[0]["parts"] == ["rod", "plate"]
    seated = cyl("rod", (0, 0, 0.1), (0, 0, 0.6), 0.05)           # stands on it
    assert not _checks(GC.run([plate, seated]), "poke_through")
    linked = cyl("rod", (0, 0, -0.5), (0, 0, 0.6), 0.05, links=("plate",))
    assert not _checks(GC.run([plate, linked]), "poke_through")


def _box_projected(part, scale=0.5):
    """Per-corner UVs as Blender's cube projection makes them: each face drops its normal's dominant axis."""
    P = part.verts[part.tris]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    k = np.argmax(np.abs(n), axis=1)
    return np.array([[np.delete(c, kk) * scale for c in tri] for tri, kk in zip(P, k)])


def test_box_uv_on_curved_parts():
    c = cyl("tube", (0, 0, 0), (0, 0, 1), 0.3)
    c.uvs = _box_projected(c)
    assert _checks(GC.run([c]), "box_uv")[0]["parts"] == ["tube"]
    P = c.verts[c.tris]
    c.uvs = np.array([[[math.atan2(q[1], q[0]) / (2 * math.pi), q[2]] for q in tri] for tri in P])   # an unwrap
    assert not _checks(GC.run([c]), "box_uv")
    flat = box("panel", (0, 0, 0), (1, 1, 0.1))
    flat.uvs = _box_projected(flat)
    assert not _checks(GC.run([flat]), "box_uv")                  # box projection is fine on flat panels


def test_cell_groups_over_100_fail():
    cells = [(x, 0, 0) for x in range(0, 404, 2)]                 # 202 separate cells
    assert _checks(GC.run([], cells=cells), "cell_groups")
    assert not _checks(GC.run([], cells=[(x, 0, 0) for x in range(50)]), "cell_groups")


def test_triangle_budget():
    a = box("a", (0, 0, 0), (1, 1, 1))
    assert _checks(GC.run([a], tris_budget=10), "tris")[0]["detail"].startswith("12 triangles")
    assert not _checks(GC.run([a], tris_budget=12), "tris")


def test_untagged_parts_warn_not_fail():
    r = GC.run([box("a", (0, 0, 0), (1, 1, 1)), box("b", (1, 0, 0), (2, 1, 1), role="mount")])
    assert [f["check"] for f in r.failures] == []
    assert any(f["check"] == "untagged" and f["parts"] == ["a"] for f in r.warnings)


def test_report_json_shape(tmp_path):
    r = GC.run([box("a", (0, 0, 0), (1, 1, 1), role="mount")], tris_budget=1)
    d = r.to_dict()
    assert set(d) == {"ok", "failures", "warnings", "tris", "parts"}
    assert d["ok"] is False and d["tris"] == 12 and d["parts"] == 1
    path = r.write(tmp_path / "geocheck.json")
    assert json.loads(path.read_text())["failures"][0]["check"] == "tris"


def test_part_role_falls_back_to_name_prefix():
    assert GC.Part("pin_main", np.zeros((3, 3)), np.array([[0, 1, 2]])).role == "pin"
    assert GC.Part("mount_plate.001", np.zeros((3, 3)), np.array([[0, 1, 2]])).role == "mount"
    assert GC.Part("Body", np.zeros((3, 3)), np.array([[0, 1, 2]])).role is None


def test_crossing_parts_touch():
    mount = box("base", (-3, -0.2, 0), (3, 0.2, 0.2), role="mount")
    rail_a = box("rail_a", (-3, -1, 0.2), (3, -0.8, 0.4))
    rail_b = box("rail_b", (-3, 0.8, 0.2), (3, 1.0, 0.4))
    post = box("post", (-0.1, -1.0, 0.0), (0.1, 1.0, 0.2))           # carries both rails off the base
    deck = box("deck", (-0.5, -1.2, 0.4), (0.5, 1.2, 0.5))           # laid across rails longer than it
    assert not _checks(GC.run([mount, post, rail_a, rail_b, deck]), "load_path")
    beam_x = box("beam_x", (-1, -0.1, 0.2), (1, 0.1, 0.4))           # rests on the base
    beam_y = box("beam_y", (-0.1, -1, 0.25), (0.1, 1, 0.35))         # lapped through it, a "+" joint
    assert not _checks(GC.run([mount, beam_x, beam_y]), "load_path")


def _rotated(part, deg):
    a = math.radians(deg)
    R = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    part.local = part.verts.copy()
    part.verts = part.verts @ R.T
    return part


def test_box_uv_reads_the_local_frame():
    c = cyl("tube", (0, 0, 0), (0, 0, 1), 0.3)
    c.uvs = _box_projected(c)                                      # projected upright, in its own frame
    assert _checks(GC.run([_rotated(c, 30)]), "box_uv")


def prism(name, poly, z0, z1):
    """A closed prism over a convex 2D polygon (counter-clockwise)."""
    n = len(poly)
    v = np.array([(x, y, z0) for x, y in poly] + [(x, y, z1) for x, y in poly] + [(0, 0, z0), (0, 0, z1)], float)
    t = []
    for i in range(n):
        j = (i + 1) % n
        t += [(i, j, n + j), (i, n + j, n + i), (2 * n, j, i), (2 * n + 1, n + i, n + j)]
    return GC.Part(name, v, np.array(t))


def test_chamfered_box_is_not_curved():
    a, b = 0.5, 0.45                                               # a 1 m square with 5 cm chamfered corners
    octo = [(a, -b), (a, b), (b, a), (-b, a), (-a, b), (-a, -b), (-b, -a), (b, -a)]
    part = prism("chamfered", octo, 0.0, 1.0)
    part.uvs = _box_projected(part)
    assert not _checks(GC.run([part]), "box_uv")


def test_pin_axis_is_inferred_without_tags():
    lug = box("lug_eye", (-0.05, -0.1, -0.2), (0.05, 0.1, 0.1))
    tilted = cyl("pin_main", (-0.15, 0, -0.015), (0.15, 0, 0.015), 0.04)    # 5.7 degrees off, no se2_axis
    report = GC.run([lug, tilted])
    assert any(f["check"] == "pin_axis" and "degrees off" in f["detail"] for f in report.failures)
    square = cyl("pin_main", (-0.15, 0, 0.02), (0.15, 0, 0.02), 0.04)
    report = GC.run([lug, square])
    assert not [f for f in report.failures if f["check"] == "pin_axis"]
    assert any(f["check"] == "pin_axis" and "se2_point" in f["detail"] for f in report.warnings)


def test_untagged_clevis_and_stubby_pin_warn_instead_of_failing():
    cheeks = [box("clevis_1", (-0.07, -0.1, -0.1), (-0.06, 0.1, 0.1)), box("clevis_1b", (0.06, -0.1, -0.1), (0.07, 0.1, 0.1))]
    clevis = GC.Part("clevis_1", np.vstack([c.verts for c in cheeks]),
                     np.vstack([cheeks[0].tris, cheeks[1].tris + len(cheeks[0].verts)]))
    pin = cyl("pin_1", (-0.09, 0, 0), (0.09, 0, 0), 0.02)              # square in the clevis, nothing tagged
    report = GC.run([clevis, pin])
    assert not [f for f in report.failures if f["check"] == "pin_axis"]
    assert any(f["check"] == "pin_axis" and "se2_axis" in f["detail"] for f in report.warnings)
    lug = box("lug_eye", (-0.05, -0.1, -0.2), (0.05, 0.1, 0.1))
    stubby = cyl("pin_short", (-0.015, 0, 0), (0.015, 0, 0), 0.04)     # shorter than it is wide
    report = GC.run([lug, stubby])
    assert not [f for f in report.failures if f["check"] == "pin_axis"]


def test_a_tagged_eye_is_not_called_untagged():
    lug = box("lug_eye", (-0.05, -0.1, -0.2), (0.05, 0.1, 0.1), point=(0, 0, 0))
    stubby = cyl("pin_short", (-0.015, 0, 0), (0.015, 0, 0), 0.04)
    warnings = [f["detail"] for f in GC.run([lug, stubby]).warnings if f["check"] == "pin_axis"]
    assert warnings and not any("se2_point" in w for w in warnings)


def test_box_uv_on_a_short_disc():
    d = cyl("disc", (0, 0, 0), (0, 0, 0.05), 0.3)                 # its caps outweigh its rim
    d.uvs = _box_projected(d)
    assert _checks(GC.run([d]), "box_uv")[0]["parts"] == ["disc"]
    P = d.verts[d.tris]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    cap = np.abs(n[:, 2]) > 0.99 * np.linalg.norm(n, axis=1)
    d.uvs = np.array([[[q[0], q[1]] if c else [math.atan2(q[1], q[0]) / (2 * math.pi), q[2]] for q in tri]
                      for tri, c in zip(P, cap)])                  # planar caps, unwrapped rim
    assert not _checks(GC.run([d]), "box_uv")
