"""The construction scaffold: the cage the welders work from while a block is below 30 %.

Vanilla: an ungrouped lattice of thin BrushedMetal members around the block's bounding box (Timer50 Main_Scaffold,
Reactor250 Scaffold_LOD0), ConstructionMeshType Scaffold / AlwaysVisible / Scaffolding preset: shown from the first
weld in the see-through BrushedMetal_Scaffold (4050183d), hidden at 0.3, never in the Damaged model, and hidden by the
stage-1.0 ApplyOnlyWhenDamaged entry so a finished block that is shot does not show it (construction.scaffold_parts).
A median 19 % of the Fractured LOD0's triangles.

The cage is the edges of a union of boxes plus every face's grid lines at a chosen pitch, as straight open tubes:
  boxes   the render envelope by default; a family listed in `hooks.scaffold_zone_boxes` gets its zone hulls' boxes,
          so a branching or bent block is caged along its own outline and a doorway stays clear, instead of a square
          round it (the family's value is the thinnest hull that gets a box: a thin sill strip gets none, or its tubes
          would lie across the threshold);
  members a member is one straight tube (its two end points); the cage is the surface of the boxes' union: lines on a
          face two boxes share inside the union are dropped, collinear pieces are merged into one tube, and every
          member is pulled inside the union by its radius; where the block fills its cell the box face is set
          OUTSET (4 mm) proud of the cell face (reach_cells), so the tubes' outer surface sits there and the cage reads
          outside the skin, as vanilla's do;
  boxes   are first snapped per axis (faces within a tube diameter become one), so two zone hulls meeting a few mm
          apart share one row of members instead of a doubled bar;
  pitch   the block's natural bay (natural_pitch: a cell from 1 m up, half a cell below), else the smallest coarser
          one whose LOD0 triangles fit the budget (each face split into equal bays of about the pitch); a finer bay
          only when the natural one keeps the cage under MIN_SHARE and the budget allows it;
  tubes   round, 8 sides (a coarser pitch before fewer sides); square (4) only when no 8-sided cage fits. Both have a
          vertex on each perpendicular axis, so the outer surface reaches exactly the box face (a hexagon reaches only
          0.87 r on one axis); radius 10 mm on a block of 1 m or more, else 6 mm;
  LOD1    the same cage at 4 sides, at the smallest pitch >= LOD0's that fits 90 % of the LOD1 budget (or none).

The budget rule: the scaffold may never push the Fractured model over its LOD row, so it gets at most min(20 % of FR
LOD0, FR budget[0] - FR LOD0 - 10 % of budget[0], the LOD-row margin). Stand-in triangles (the parked subparts baked
into FR) count against the same budget: pass them as `reserve` (per LOD) and the scaffold plans around them.

Pure Python (no bpy): fracture_mesh.scaffold_object builds the tubes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..model import limits

__all__ = ["EDGES_ONLY", "MATERIAL", "PROPS", "ScaffoldPlan", "boxes_for", "budget", "cage", "natural_pitch", "plan",
           "radius_for", "reach_cells", "row_margin", "tris_of"]

MATERIAL = PART = "BrushedMetal"
PROPS = ("Scaffold", "AlwaysVisible", "Scaffolding")
MIN_SHARE, MAX_SHARE = 0.05, 0.20
PITCHES = (0.125, 0.25, 0.5, 0.75, 1.0, 1.25, 2.5)   # 0.125: half a cell, a small block's bay (vanilla
#                                                     Timer50's lattice is about that dense on a 0.5 m block)
OUTSET = 0.004                                        # a cage face where the block fills its cell stands this far
#                                                     proud of the cell face (tube outer surface; vanilla: Timer50
#                                                     +4.0 mm, Gyroscope250 +6.6, WallInsertPanel +4.3, LCD50 +8.0,
#                                                     batteries +0.4..2.5): it reads outside the skin, not behind it
FILLS = 0.002                                         # "fills its cell": the envelope within this of the cell face
EDGES_ONLY = math.inf                                 # the last resort: the boxes' edges alone
SIDE_TIERS = ((8,), (4,))                             # LOD0 tube sides, preferred first: round 8-sided tubes, at
#                                                     a coarser pitch if need be; square ones only when no 8-sided
#                                                     cage fits at any pitch
SIDES = {0: SIDE_TIERS[0] + SIDE_TIERS[1], 1: (4,)}
LOD1_FILL = 0.9                                       # LOD1 carries the cage only within 90 % of its budget
SNAP = 1e-3                                           # members are deduplicated to 1 mm

Member = tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass
class ScaffoldPlan:
    boxes: list
    pitch: float
    radius: float
    sides: dict[int, int]
    members: list[Member]
    tris: dict[int, int]
    pitches: dict[int, float] = field(default_factory=dict)     # per LOD (LOD0 == pitch)
    proud: list = field(default_factory=list)                   # axes whose filled faces stand OUTSET proud
    lod_members: dict[int, list] = field(default_factory=dict)  # per LOD (LOD0 == members)

    def manifest(self):
        return {"pitch": _pitch_json(self.pitch), "radius": self.radius, "sides": dict(self.sides),
                "boxes": [[list(map(_r5, lo)), list(map(_r5, hi))] for lo, hi in self.boxes],
                "members": len(self.members), "proud": list(self.proud),
                "pitches": {str(k): _pitch_json(v) for k, v in self.pitches.items()},
                "tris": {str(k): v for k, v in self.tris.items()}}


def _r5(v):
    return round(v, 5)


def _pitch_json(p):
    return None if p == EDGES_ONLY else p


def tris_of(members, sides):
    """Open tubes (no caps): one quad per side, two triangles each."""
    return 2 * sides * len(members)


# ---------------------------------------------------------------------------------------------------- the cage
def _snap_boxes(boxes, tol):
    """Per axis, box faces closer than tol are snapped to one value (their mean; the outermost value for the group at
    the union's outline): two zone hulls meeting at a seam a few mm apart share one row of members instead of a
    doubled bar."""
    out = [[list(lo), list(hi)] for lo, hi in boxes]
    for a in range(3):
        vals = sorted({b[k][a] for b in out for k in (0, 1)})
        groups, cur = [], [vals[0]]
        for v in vals[1:]:
            if v - cur[-1] <= tol:
                cur.append(v)
            else:
                groups.append(cur)
                cur = [v]
        groups.append(cur)
        # the outer faces keep their extreme value (the cage's outline, a proud face stays proud), others the mean
        to = {v: (vals[0] if g[0] == vals[0] else vals[-1] if g[-1] == vals[-1] else sum(g) / len(g))
              for g in groups for v in g}
        for b in out:
            for k in (0, 1):
                b[k][a] = to[b[k][a]]
    return [(tuple(lo), tuple(hi)) for lo, hi in out]


def _inside(p, boxes, eps=1e-9):
    return any(all(lo[i] - eps <= p[i] <= hi[i] + eps for i in range(3)) for lo, hi in boxes)


def _splits(a, b, pitch):
    """Interior grid positions of [a, b]: equal bays of about `pitch` (none for edges only)."""
    if pitch == EDGES_ONLY:
        return []
    n = max(1, round((b - a) / pitch))
    return [a + (b - a) * k / n for k in range(1, n)]


def _lines(boxes, pitch):
    """{(axis, (c1, c2)): [(t0, t1), ...]}: every box's 12 edges and its faces' grid lines, by line (the two fixed
    coordinates: lines within SNAP of each other are one line, at the first one's exact coordinates) and interval
    along the axis."""
    lines, exact = {}, {}

    def add(axis, fixed, t0, t1):
        key = (axis, tuple(round(c / SNAP) for c in fixed))
        lines.setdefault(exact.setdefault(key, (axis, fixed)), []).append((t0, t1))

    for lo, hi in boxes:
        for ax in range(3):
            u, v = [i for i in range(3) if i != ax]
            us = [lo[u], hi[u]]
            vs = [lo[v], hi[v]]
            # the 4 edges along ax
            for cu in us:
                for cv in vs:
                    add(ax, _fixed(ax, u, v, cu, cv), lo[ax], hi[ax])
            # grid lines along ax on the 4 faces parallel to ax: faces u = const (lines at v splits) and v = const
            for cu in us:
                for cv in _splits(lo[v], hi[v], pitch):
                    add(ax, _fixed(ax, u, v, cu, cv), lo[ax], hi[ax])
            for cv in vs:
                for cu in _splits(lo[u], hi[u], pitch):
                    add(ax, _fixed(ax, u, v, cu, cv), lo[ax], hi[ax])
    return lines


def _fixed(ax, u, v, cu, cv):
    f = [0.0, 0.0, 0.0]
    f[u], f[v] = cu, cv
    return (f[u], f[v]) if u < v else (f[v], f[u])


def _point(ax, fixed, t):
    u, v = [i for i in range(3) if i != ax]
    p = [0.0, 0.0, 0.0]
    p[u], p[v], p[ax] = fixed[0], fixed[1], t
    return p


def _quadrants(ax, fixed, t, boxes, eps):
    """Which of the 4 quadrants around the line (ax, fixed) at t lie inside the union: {(su, sv): bool}."""
    u, v = [i for i in range(3) if i != ax]
    out = {}
    for su in (-1, 1):
        for sv in (-1, 1):
            p = _point(ax, fixed, t)
            p[u] += su * eps
            p[v] += sv * eps
            out[(su, sv)] = _inside(p, boxes)
    return out


def _shift(q):
    """The inward pull of a member per perpendicular axis (-1, 0, +1): toward the side the union lies on, none where
    the union lies on both sides (a concave edge, where the tube already sits within the cells)."""
    out = []
    for k in (0, 1):
        plus = any(ins for s, ins in q.items() if s[k] > 0)
        minus = any(ins for s, ins in q.items() if s[k] < 0)
        out.append(0 if plus == minus else (1 if plus else -1))
    return out


def cage(boxes, pitch: float, inset: float) -> list[Member]:
    """The scaffold members of a union of axis-aligned boxes [(lo, hi)]: every box's 12 edges plus its faces' grid
    lines at `pitch`, deduplicated to 1 mm; pieces lying inside the union (a face two boxes share) are dropped,
    collinear pieces merged into one member, and each member pulled `inset` into the union (ends included)."""
    boxes = [(tuple(lo), tuple(hi)) for lo, hi in boxes]
    eps = 1e-4
    out = []
    for (ax, fixed), ivs in sorted(_lines(boxes, pitch).items()):
        # split at every box boundary along the axis, keep the surface pieces, then merge what touches
        cuts = sorted({round(t, 6) for iv in ivs for t in iv} | {round(b[k][ax], 6) for b in boxes for k in (0, 1)})
        pieces = []
        for t0, t1 in zip(cuts, cuts[1:]):
            if t1 - t0 < SNAP or not any(a - 1e-6 <= t0 and t1 <= b + 1e-6 for a, b in ivs):
                continue
            q = _quadrants(ax, fixed, (t0 + t1) / 2, boxes, eps)
            if not any(q.values()) or all(q.values()):       # outside the union, or inside it (a shared face)
                continue
            sh = _shift(q)
            if pieces and abs(pieces[-1][1] - t0) < 1e-6 and pieces[-1][2] == sh:
                pieces[-1][1] = t1
            else:
                pieces.append([t0, t1, sh])
        for t0, t1, sh in pieces:
            f = (fixed[0] + sh[0] * inset, fixed[1] + sh[1] * inset)
            # an end where the union stops along the axis is pulled in too (it meets the perpendicular member there)
            if not any(_quadrants(ax, fixed, t0 - eps, boxes, eps).values()):
                t0 += inset
            if not any(_quadrants(ax, fixed, t1 + eps, boxes, eps).values()):
                t1 -= inset
            if t1 - t0 > SNAP:
                out.append((tuple(_point(ax, f, t0)), tuple(_point(ax, f, t1))))
    return out


# ---------------------------------------------------------------------------------------------------- the budget
def row_margin(size, t0: int, limit: int) -> int:
    """The largest k <= limit with optimal_lods(size, t0 + k) == optimal_lods(size, t0) (0 when limit <= 0)."""
    if limit <= 0:
        return 0
    want = limits.optimal_lods(size, t0)
    lo, hi = 0, limit
    while lo < hi:
        m = (lo + hi + 1) // 2
        if limits.optimal_lods(size, t0 + m) == want:
            lo = m
        else:
            hi = m - 1
    return lo


def budget(size, fr_t0: int, budget0: int) -> int:
    """The scaffold's LOD0 triangle ceiling (the budget rule); negative when the FR LOD0 already sits in the
    headroom."""
    return min(int(MAX_SHARE * fr_t0), budget0 - fr_t0 - int(0.10 * budget0),
               row_margin(size, fr_t0, budget0 - fr_t0))


def boxes_for(hooks, family: str, zones: list[dict], lo, hi) -> list[tuple]:
    """[(lo, hi)]: the envelope (lo, hi: the FR LOD0's render bounds within the cells), except for a family in
    `hooks.scaffold_zone_boxes`: each zone hull's box clipped to the envelope (a hull's few % of shaping gain must not
    grow the model), so the cage follows the block's outline instead of a square round it; a hull thinner than the
    family's value gets no box."""
    if family in hooks.scaffold_zone_boxes:
        thinnest = hooks.scaffold_zone_boxes[family]
        out = [(tuple(max(a, b) for a, b in zip(z["lo"], lo)), tuple(min(a, b) for a, b in zip(z["hi"], hi)))
               for z in zones]
        return [(a, b) for a, b in out if min(y - x for x, y in zip(a, b)) >= thinnest]
    return [(tuple(lo), tuple(hi))]


def reach_cells(lo, hi, half, axes=(0, 1, 2)):
    """(lo, hi) with every face that comes within FILLS of the cell face (cells: +-half) moved to the cell face +
    OUTSET, on the given axes only (the cage is inset by its radius from these faces, so its outer surface lands
    OUTSET past the cell). The cage reaches 4 mm into a neighbour cell, as vanilla's; it has no collider, and a
    finished neighbour's skin hides it."""
    nlo = [-h - OUTSET if k in axes and a <= -h + FILLS else a for k, (a, h) in enumerate(zip(lo, half))]
    nhi = [h + OUTSET if k in axes and b >= h - FILLS else b for k, (b, h) in enumerate(zip(hi, half))]
    return tuple(nlo), tuple(nhi)


def natural_pitch(lo, hi):
    """The bay a scaffold of this size is built at: one cell (0.25 m) on a block of 1 m or more, half a cell below
    (a 12 cm lattice over a 2.5 m block reads as wire mesh, not scaffolding)."""
    return 0.25 if max(b - a for a, b in zip(lo, hi)) >= 1.0 else 0.125


def radius_for(lo, hi):
    return 0.010 if max(b - a for a, b in zip(lo, hi)) >= 1.0 else 0.006


def plan(hooks, family, zones, lo, hi, size, fr_tris: list[int], budget_lods: list[int], reserve=(0, 0), cap=None):
    """The block's ScaffoldPlan, or None when not even the edges-only cage fits the budget. fr_tris / budget_lods: the
    cut Fractured LODs' triangles and fracture.fracture_budget; reserve: triangles still to be added per LOD (the
    stand-ins), counted as already there; cap: a lower LOD0 ceiling (the exporter's retry when the cage would move the
    FR LOD0 across a LOD row: a coarser pitch)."""
    r = radius_for(lo, hi)
    boxes = _snap_boxes(boxes_for(hooks, family, zones, lo, hi), 2 * r)
    t0 = fr_tris[0] + reserve[0]
    b = budget(size, t0, budget_lods[0])
    if cap is not None:
        b = min(b, cap)
    pitches = PITCHES + (EDGES_ONLY,)
    cages = {}

    def fits(p, tier):
        if p not in cages:
            cages[p] = cage(boxes, p, r)
        return next((n for n in tier if tris_of(cages[p], n) <= b), None)

    # the block's natural bay first (a cell on a big block, half a cell on a small one); the smallest pitch from there
    # that fits; only when that stays under MIN_SHARE of the FR LOD0 a finer bay, if the budget allows it. Square
    # tubes only when no round cage fits at any pitch.
    nat = natural_pitch(lo, hi)
    for tier in SIDE_TIERS:
        p, fit = next(((q, n) for q in pitches if q >= nat for n in [fits(q, tier)] if n), (None, None))
        if fit and tris_of(cages[p], fit) < MIN_SHARE * t0:
            finer = [(q, n) for q in reversed([q for q in pitches if q < nat]) for n in [fits(q, tier)]
                     if n and tris_of(cages[q], n) >= MIN_SHARE * t0]
            if finer:
                p, fit = finer[0]
        if fit:
            break
    if not fit:
        return None
    members = cages[p]
    sp = ScaffoldPlan(boxes=boxes, pitch=p, radius=r, sides={0: fit}, members=members,
                      tris={0: tris_of(members, fit)}, pitches={0: p}, lod_members={0: members})
    if len(fr_tris) > 2:          # LOD1 exists and is not the LastLOD box
        room = LOD1_FILL * budget_lods[1] - fr_tris[1] - (reserve[1] if len(reserve) > 1 else 0)
        for p1 in [q for q in pitches if q >= p]:
            m1 = cages.get(p1) or cage(boxes, p1, r)
            n1 = SIDES[1][0]
            if tris_of(m1, n1) <= room:
                sp.sides[1], sp.pitches[1], sp.lod_members[1], sp.tris[1] = n1, p1, m1, tris_of(m1, n1)
                break
    return sp
