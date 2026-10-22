"""`motion.sweep`: exact convex distance (GJK), its cut-short lower bound, the branch-and-bound minimum and the swept
interval bookkeeping (the bound holds against dense sampling of a spring-eased swing)."""

import math

import pytest

from se2modkit.motion import springs as SP
from se2modkit.motion import sweep as K


def _box(lo, hi):
    return K.corners(lo, hi)


def test_convex_distance_boxes():
    a = _box((0, 0, 0), (1, 1, 1))
    assert K.convex_distance(a, _box((1.5, 0, 0), (2.5, 1, 1))) == pytest.approx(0.5)
    assert K.convex_distance(a, _box((1.3, 1.4, 0), (2, 2, 1))) == pytest.approx(0.5)      # corner to corner
    assert K.convex_distance(a, _box((0.5, 0.5, 0.5), (2, 2, 2))) == 0.0                     # overlapping
    assert K.convex_distance(a, _box((1, 0, 0), (2, 1, 1))) == 0.0                           # touching


def test_convex_distance_cut_short_is_lower_bound():
    """Cut short (max_iter) GJK returns the supporting-plane lower bound, never more than the true distance."""
    import random
    rng = random.Random(7)
    for _ in range(40):
        P = [(rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0, 1)) for _ in range(12)]
        Q = [(rng.uniform(1.2, 2.4), rng.uniform(-0.5, 1.5), rng.uniform(-0.5, 1.5)) for _ in range(12)]
        exact = K.convex_distance(P, Q)
        for it in (1, 2, 3):
            assert 0.0 <= K.convex_distance(P, Q, max_iter=it) <= exact + 1e-12


def test_min_distance_branch_and_bound():
    a = _box((0, 0, 0), (1, 1, 1))
    far, near = _box((3, 0, 0), (4, 1, 1)), _box((1.25, 0, 0), (2, 1, 1))
    cands = [K.candidate(a, far, 0.0, "far"), K.candidate(a, near, 0.05, "near")]
    best, tag = K.min_distance(cands)
    assert tag == "near" and best == pytest.approx(0.2)
    assert K.aabb_gap(K.aabb(a), K.aabb(far)) == pytest.approx(2.0)


def test_intervals_swept_and_instants():
    traj = [(0.0, 0.0, 0.0), (0.1, 0.5, 0.0), (0.2, 1.0, 0.0), (0.3, 1.2, 0.0)]
    sw = K.intervals(traj)
    assert len(sw) == 3 and all(sl[0] >= 0 for _, _, sl in sw)
    assert K.intervals(traj, swept=False) == [(p, p, (0.0, 0.0)) for p in traj]


def _leaf(theta):
    """A thin leaf hinged on the x axis, 1 m long, swung by theta about x."""
    c, s = math.cos(theta), math.sin(theta)
    return [(x, y * c - z * s, y * s + z * c) for x in (-0.5, 0.5) for y in (0.0, 1.0) for z in (-0.015, 0.015)]


def test_swept_bound_holds_against_dense_sampling():
    """An under-damped spring swing sampled at 24 Hz: the swept lower bound (intervals + slack + sagitta) never
    exceeds the clearance found by dense 1 kHz sampling of the true motion, so a colliding motion never passes."""
    theta_max = math.radians(90)
    R = math.sqrt(0.5 ** 2 + 1.0 ** 2 + 0.015 ** 2)

    def run(h, t_end=0.8):
        return [(t, x, 0.0) for t, x, _, _ in SP.trajectory(SP.Spring(22, 0.35, 0.0), SP.Axis(), 1.0, t_end, h)]
    coarse, dense = run(1 / 24), run(1 / 1000)
    assert max(x for _, x, _ in dense) > 1.3

    def swept(obstacle):
        best = math.inf
        for a, b, (sl, _) in K.intervals(coarse):
            lo, hi = sorted((a[1], b[1]))
            lo, hi = lo - sl, hi + sl
            d_theta = (hi - lo) * theta_max
            pad = R * (1 - math.cos(min(d_theta, math.pi) / 2))
            best = min(best, K.convex_distance(_leaf(lo * theta_max) + _leaf(hi * theta_max), obstacle) - pad)
        return best
    hits = 0
    for phi_deg in (60, 110, 118, 120, 124):
        for r0 in (0.3, 0.7):
            phi = math.radians(phi_deg)
            cy, cz = r0 * math.cos(phi), r0 * math.sin(phi)
            obstacle = _box((-0.1, cy - 0.04, cz - 0.04), (0.1, cy + 0.04, cz + 0.04))
            truth = min(K.convex_distance(_leaf(x * theta_max), obstacle) for _, x, _ in dense)
            bound = swept(obstacle)
            assert bound <= truth + 1e-9, (phi_deg, r0, bound, truth)
            hits += truth == 0.0
    assert hits > 0
