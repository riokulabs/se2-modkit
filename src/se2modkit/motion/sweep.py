"""Swept clearance: exact convex-polytope distance (GJK) and the interval bookkeeping that bounds a moving part's
clearance from below. Pure Python.

How a sweep is bounded: within a sample interval a translating vertex sweeps the segment between its endpoints, a
translating and rotating one stays inside the parallelogram of its translation and its rotation chord, a rotation's
arc leaves its chord by at most its sagitta r (1 - cos(dtheta / 2)), and the true motion leaves the linear
interpolation of the samples by at most |second difference| / 8 (`interp_slack`). So the convex hull of a part's
interval vertices, inflated by those slacks, holds every pose the part takes in the interval, and hull-to-hull
distances minus the slacks are LOWER bounds on the true clearance (clearance, not penetration depth:
`convex_distance` is 0 for touching or overlapping hulls). The sagitta is exact; the interpolation slack is a
second-difference ESTIMATE (exact for smooth motion, over at kinks), so the swept value brackets the clearance from
below rather than proving it: the tests check it against dense sampling of a spring-eased rotation whose overshoot
falls between samples.

`intervals` / `interp_slack` read trajectories as [(t, a, b)] (two channels, e.g. a slide and a fold).
"""

from __future__ import annotations

import math

__all__ = ["aabb", "aabb_gap", "candidate", "convex_distance", "corners", "interp_slack", "intervals", "min_distance"]


def interp_slack(traj):
    """Per interval: (slide, fold) bounds on how far the true motion leaves the linear interpolation of the samples.
    For a C2 curve the chord error is at most h^2 max|x''| / 8; the second differences around the interval estimate
    h^2 x'' (a hard-stop kink only inflates it, which stays conservative)."""
    n = len(traj)
    d2 = [0.0] * n
    for i in range(1, n - 1):
        d2[i] = (abs(traj[i + 1][1] - 2 * traj[i][1] + traj[i - 1][1]),
                 abs(traj[i + 1][2] - 2 * traj[i][2] + traj[i - 1][2]))
    d2[0] = d2[1] if n > 2 else (0.0, 0.0)
    d2[-1] = d2[-2] if n > 2 else (0.0, 0.0)
    return [(max(d2[i][0], d2[i + 1][0]) / 8.0, max(d2[i][1], d2[i + 1][1]) / 8.0) for i in range(n - 1)]


def intervals(traj, swept=True):
    """[(a, b, slack)] the checks run over. swept: every sample interval with its interpolation slack (the bounds are
    lower bounds on clearances, upper bounds on intrusions); not swept: every sample as a zero-length interval with no
    slack (the poses at the instants only: the other side of the bracket)."""
    if not swept:
        return [(p, p, (0.0, 0.0)) for p in traj]
    return [(a, b, sl) for (a, b), sl in zip(zip(traj, traj[1:]), interp_slack(traj))]


def corners(lo, hi):
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _support(P, d):
    return max(P, key=lambda p: p[0] * d[0] + p[1] * d[1] + p[2] * d[2])


def _solve(G, r):
    """Gaussian elimination with partial pivoting for a k x k system (k <= 3); None if singular."""
    k = len(r)
    M = [list(G[i]) + [r[i]] for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda i: abs(M[i][c]))
        if abs(M[p][c]) < 1e-14:
            return None
        M[c], M[p] = M[p], M[c]
        for i in range(k):
            if i != c:
                f = M[i][c] / M[c][c]
                for j in range(c, k + 1):
                    M[i][j] -= f * M[c][j]
    return [M[i][k] / M[i][i] for i in range(k)]


def _closest_on_simplex(W):
    """The point of hull(W) (|W| <= 4) nearest the origin and the smallest subset of W it lies on. The nearest point
    lies in the relative interior of one face, where it is that face's affine projection of the origin; so the answer
    is the shortest projection whose barycentric weights are all non-negative (brute force over the <= 15 faces)."""
    best = None
    n = len(W)
    for mask in range(1, 1 << n):
        Y = [W[i] for i in range(n) if mask >> i & 1]
        y0 = Y[0]
        E = [_sub(y, y0) for y in Y[1:]]
        if E:
            mu = _solve([[_dot(a, b) for b in E] for a in E], [-_dot(a, y0) for a in E])
            if mu is None:
                continue
            lam = [1.0 - sum(mu)] + mu
            if min(lam) < -1e-12:
                continue
            v = tuple(y0[j] + sum(m * e[j] for m, e in zip(mu, E)) for j in range(3))
        else:
            v = y0
        d = _dot(v, v)
        if best is None or d < best[0] - 1e-18 or (abs(d - best[0]) <= 1e-18 and len(Y) < len(best[2])):
            best = (d, v, Y)
    return best[1], best[2]


def convex_distance(P, Q, max_iter=64):
    """Euclidean distance between hull(P) and hull(Q) (3D point lists); 0.0 when they touch or overlap. GJK on the
    Minkowski difference P - Q with the exhaustive sub-simplex solver above (exact up to float round-off). Converged,
    it returns |v|; cut short (max_iter, or a support point already in the simplex) it returns the supporting-plane
    LOWER bound max(0, v . w / |v|) (w = the support point toward -v: every point of P - Q lies on the far side of that
    plane), never the upper bound |v|, so a clearance check stays on the safe side."""
    v = _sub(P[0], Q[0])
    W = []
    for _ in range(max_iter):
        vv = _dot(v, v)
        if vv < 1e-24:
            return 0.0
        w = _sub(_support(P, (-v[0], -v[1], -v[2])), _support(Q, v))
        if vv - _dot(v, w) <= 1e-12 * max(vv, 1e-12):
            return math.sqrt(vv)                      # converged: |v| is the distance
        if w in W:
            break
        v, W = _closest_on_simplex(W + [w])
        if len(W) == 4:
            return 0.0
    vv = _dot(v, v)
    if vv < 1e-24:
        return 0.0
    w = _sub(_support(P, (-v[0], -v[1], -v[2])), _support(Q, v))
    return max(0.0, _dot(v, w) / math.sqrt(vv))


def aabb(P):
    return (tuple(min(p[j] for p in P) for j in range(3)), tuple(max(p[j] for p in P) for j in range(3)))


def aabb_gap(A, B):
    """Distance between two AABBs ((lo, hi) each): a cheap lower bound on the hull distance."""
    return math.sqrt(sum(max(0.0, A[0][j] - B[1][j], B[0][j] - A[1][j]) ** 2 for j in range(3)))


def min_distance(candidates):
    """Best-first branch and bound over [(lower bound, pad, P, Q, tag)]: the smallest hull(P)-to-hull(Q) distance less
    its pad, and its tag. A cheap lower bound (an AABB gap less the pad) orders the search and ends it as soon as no
    remaining candidate can beat the best found, so only the few near pairs ever run GJK."""
    best, tag = math.inf, None
    for bound, pad, P, Q, t in sorted(candidates, key=lambda c: c[0]):
        if bound >= best:
            break
        d = convex_distance(P, Q) - pad
        if d < best:
            best, tag = d, t
    return best, tag


def candidate(P, Q, pad, tag, boxes=None):
    """A min_distance candidate; `boxes` = the AABBs of P and Q when the caller already has them."""
    bp, bq = boxes or (aabb(P), aabb(Q))
    return (aabb_gap(bp, bq) - pad, pad, P, Q, tag)


# the legacy private names (a mod moving onto modkit keeps its call sites)
_interp_slack, _corners, _aabb, _candidate = interp_slack, corners, aabb, candidate
