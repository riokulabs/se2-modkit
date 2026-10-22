"""Offline emulation of SE2's block placer: does a player who aims a block at a given cell of a
given block get the placement the mod intends, and does it validate?

Plain Python (no Blender). What the game's placer does each frame (game 4.0), in the steps emulated here:

1. Targeting: a physics ray from the eye; the first grid body hit sets the segment end at hit + 0.25 m. On that grid
   the FIRST OCCUPIED CELL the eye -> end segment crosses (every block's occupied cells, cell boxes +-0.125 m) is the
   hit cell; the normal is the axis-snapped vector from its centre to the entry point (the face the ray entered), or
   the snapped -ray when that is perpendicular. The placement position is hit cell + normal.
2. The mount point starts at the block AABB's centre cell.
3. Grid snap with snap size 1 (the default for a block whose smallest edge is 1 cell): across the normal, the block
   AABB is centred on the hit cell (Min + (Max - Min) / 2, integer division truncating toward zero), then its
   mount-face AABB (the block's mount cells facing the target) is centred the same way; along the normal nothing.
4. The mount point becomes the block's face cell toward the target (Min / pivot / Max per axis by the sign of
   -normal in block space); if that placement fails (occupied cells or no mount point), the placer scans from the
   pivot (the AABB centre, truncated) toward that face cell, else keeps the face cell.
5. The first snap is undone and redone with the new mount point. When the cells are not empty, the mount point
   moves to the nearest offset (perpendicular axes +-n, along +normal 0..n, n = the block's longest edge) whose
   cells are empty (ties: x fastest, then y, then z). The game also treats grid projections as occupied (not
   emulated: no projections here).
6. A block's primary mount point dummies (e.g. conveyor ports) snap the ghost onto the target's matching ones. Mod
   data decides which those are, so `emulate` takes the step as `snap(grid, prefab, R, result) -> result` from the
   mod (none: a no-op, as for a block without primary mount points).
Validation: every new cell empty, and ANY one mount cell of the new block faces a neighbour block's cell that has a
mount point in the opposite direction (the neighbour definition's mount groups, per cell).

The emulation is checked by its tests on synthetic grids (tests/pipeline/test_placer.py).

Frames: grid cells (x, y, z) as a block's cell bounds, -z = Forward (the block's front); metres in grid space, cell c
centred at 0.25 c. The target block usually sits at the grid origin, identity orientation.

Assumptions (stated in the report): SnapSize 1 (the default for a 1-cell edge; a player who raises the snap size
gets coarser in-plane steps), no symmetry, the placer's max build distance not limiting, no other grids. A target
block should carry all its generated mount cells, inner and on its outer faces (the mod builds it from its
build_report: `Block`); the physics bodies are its regular collision boxes plus a box per placed block (`placed`), not
the precise render collider.
"""

from __future__ import annotations

import math
from collections.abc import Callable

__all__ = ["CELL", "DIRS", "HALF", "ROT_ID", "ROT_YAW180", "VEC2DIR", "Block", "Grid", "Prefab", "aabb", "box_cells",
           "cs_div", "emulate", "mounted_for", "placed"]

DIRS = {"Forward": (0, 0, -1), "Backward": (0, 0, 1), "Left": (-1, 0, 0), "Right": (1, 0, 0),
        "Up": (0, 1, 0), "Down": (0, -1, 0)}
VEC2DIR = {v: k for k, v in DIRS.items()}
HALF = 0.125
CELL = 0.25
ROT_ID = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
ROT_YAW180 = ((-1, 0, 0), (0, 1, 0), (0, 0, -1))   # a block turned round (its face to the target's rear)


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def neg(a):
    return tuple(-x for x in a)


def mul(R, v):
    return tuple(sum(R[i][j] * v[j] for j in range(3)) for i in range(3))


def transpose(R):
    return tuple(tuple(R[j][i] for j in range(3)) for i in range(3))


def cs_div(a, b):
    """Integer division truncating toward zero (as the game's integer math does)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def box_cells(lo, hi):
    return {(x, y, z) for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1) for z in range(lo[2], hi[2] + 1)}


def aabb(cells):
    cs = list(cells)
    return (tuple(min(c[i] for c in cs) for i in range(3)), tuple(max(c[i] for c in cs) for i in range(3)))


def slab(origin, d, lo, hi):
    """Entry parameter t >= 0 of origin + t d into the box (lo, hi), or None (0 when the origin is inside)."""
    t0, t1 = 0.0, math.inf
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if origin[i] < lo[i] or origin[i] > hi[i]:
                return None
            continue
        a, b = (lo[i] - origin[i]) / d[i], (hi[i] - origin[i]) / d[i]
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return None
    return t0


def snap_axis(v):
    """The dominant component's unit vector."""
    i = max(range(3), key=lambda k: abs(v[k]))
    out = [0, 0, 0]
    out[i] = 1 if v[i] > 0 else -1
    return tuple(out)


class Block:
    """A placed block: cells, mount cells per GRID direction, physics boxes (grid metres)."""

    def __init__(self, name, cells, mounts, boxes):
        self.name, self.cells, self.mounts, self.boxes = name, set(cells), mounts, boxes


class Prefab:
    """A block to place: block-local cell bounds, mount directions (the definition's mount point generator directions) and the cells that
    mount per direction (block-local); by default its whole faces."""

    def __init__(self, name, lo, hi, directions, mount_cells=None):
        self.name, self.lo, self.hi = name, tuple(lo), tuple(hi)
        self.cells = box_cells(lo, hi)
        self.directions = tuple(directions)
        if mount_cells is None:
            mount_cells = {}
            for d in directions:
                v = DIRS[d]
                ax = [i for i in range(3) if v[i]][0]
                face = hi[ax] if v[ax] > 0 else lo[ax]
                mount_cells[d] = {c for c in self.cells if c[ax] == face}
        self.mount_cells = mount_cells
        # pivot: the AABB centre truncated toward zero; the first mount point: the floored centre
        self.pivot = tuple(cs_div(a + b, 2) for a, b in zip(lo, hi))
        self.centre = tuple(math.floor((a + b) / 2) for a, b in zip(lo, hi))


class Grid:
    def __init__(self, blocks=()):
        self.blocks = []
        self.at = {}
        for b in blocks:
            self.add(b)

    def add(self, b):
        self.blocks.append(b)
        for c in b.cells:
            assert c not in self.at, (b.name, c)
            self.at[c] = b

    def empty(self, cells):
        return all(c not in self.at for c in cells)

    def mount_ok(self, prefab, R, mounted):
        """Any of the new block's mount cells meets a neighbour's mount point in the opposite direction."""
        for d, cells in prefab.mount_cells.items():
            gd = mul(R, DIRS[d])
            opp = VEC2DIR[neg(gd)]
            for c in cells:
                g = add(mul(R, c), mounted)
                n = add(g, gd)
                b = self.at.get(n)
                if b is not None and n in b.mounts.get(opp, ()):
                    return True
        return False

    # ------------------------------------------------------------------------------------------- targeting
    def target(self, eye, d, max_dist=10.0):
        """Step 1, targeting: (hit cell, normal, placement position, distance) or None (no physics hit / no cell)."""
        n = math.sqrt(sum(c * c for c in d))
        d = tuple(c / n for c in d)
        tp = None
        for b in self.blocks:
            for lo, hi in b.boxes:
                t = slab(eye, d, lo, hi)
                if t is not None and t <= max_dist and (tp is None or t < tp):
                    tp = t
        if tp is None:
            return None
        end = tuple(e + c * min(tp + 0.25, max_dist) for e, c in zip(eye, d))
        seg = sub(end, eye)
        best, bt = None, math.inf
        for c in self.at:
            p = tuple(CELL * v for v in c)
            t = slab(eye, seg, tuple(v - HALF for v in p), tuple(v + HALF for v in p))
            if t is not None and t <= 1.0 and t < bt:
                best, bt = c, t
        if best is None:
            return None
        hp = tuple(e + s * bt for e, s in zip(eye, seg))
        nrm = snap_axis(sub(hp, tuple(CELL * v for v in best)))
        if sum(a * b for a, b in zip(nrm, seg)) == 0:
            nrm = snap_axis(neg(seg))
        return best, nrm, add(best, nrm), bt * math.sqrt(sum(c * c for c in seg))

    # ------------------------------------------------------------------------------------------- placement
    def place(self, prefab, R, pp, normal):
        """The ghost's final cells and whether it validates: (cells, valid, mount_ok)."""
        Ri = transpose(R)
        perp = tuple(0 if v else 1 for v in normal)

        def mounted(mp, off):
            return sub(add(pp, off), mul(R, mp))

        def cells_at(mp, off):
            m = mounted(mp, off)
            return {add(mul(R, c), m) for c in prefab.cells}

        def snap(mp, off):
            bp = add(pp, off)
            lo, hi = aabb(cells_at(mp, off))
            so = tuple(bp[i] - lo[i] - cs_div(hi[i] - lo[i], 2) for i in range(3))
            face = VEC2DIR[neg(mul(Ri, normal))]   # the mount face toward the target
            m = mounted(mp, off)
            mc = prefab.mount_cells.get(face)
            lo2, hi2 = aabb({add(mul(R, c), m) for c in mc}) if mc else (lo, hi)
            lo2, hi2 = add(lo2, so), add(hi2, so)
            mn = sub(lo2, bp)
            mx = tuple(bp[i] + 1 - hi2[i] for i in range(3))
            so = tuple(so[i] + cs_div(mx[i] - mn[i], 2) for i in range(3))
            return tuple(so[i] * perp[i] for i in range(3))

        def check(mp, off):
            cs = cells_at(mp, off)
            return self.empty(cs) and self.mount_ok(prefab, R, mounted(mp, off))

        off = (0, 0, 0)
        mp = prefab.centre
        s1 = snap(mp, off)
        off = add(off, s1)
        # step 4: the face cell toward the target, else a scan from the pivot
        nl = neg(mul(Ri, normal))
        subst = tuple(prefab.lo[i] if nl[i] < 0 else prefab.hi[i] if nl[i] > 0 else prefab.pivot[i] for i in range(3))
        chosen = subst if check(subst, off) else None
        if chosen is None:
            p = prefab.pivot
            v = sub(p, subst)
            if abs(v[0]) > abs(v[1]):          # the axis of the largest |component| (ties go to the later axis)
                ax = 0 if abs(v[0]) > abs(v[2]) else 2
            else:
                ax = 1 if abs(v[1]) > abs(v[2]) else 2
            step = [0, 0, 0]
            step[ax] = (v[ax] > 0) - (v[ax] < 0)
            k = 0
            while p != subst and k < 16:
                if check(p, off):
                    chosen = p
                    break
                p = sub(p, tuple(step))
                k += 1
        mp = chosen if chosen is not None else subst
        off = sub(off, s1)
        off = add(off, snap(mp, off))
        # step 5: the nearest free offset
        if not self.empty(cells_at(mp, off)):
            ax = [i for i in range(3) if normal[i]][0]
            p1, p2 = [tuple(1 if j == i else 0 for j in range(3)) for i in range(3) if i != ax]
            val = max(h - l + 1 for l, h in zip(prefab.lo, prefab.hi))
            a = neg(mul(Ri, tuple((p1[i] + p2[i]) * val for i in range(3))))
            b = mul(Ri, tuple((p1[i] + p2[i] + normal[i]) * val for i in range(3)))
            lo, hi = tuple(min(x, y) for x, y in zip(a, b)), tuple(max(x, y) for x, y in zip(a, b))
            best, bd = mp, math.inf
            for oz in range(lo[2], hi[2] + 1):
                for oy in range(lo[1], hi[1] + 1):
                    for ox in range(lo[0], hi[0] + 1):
                        o = (ox, oy, oz)
                        d2 = ox * ox + oy * oy + oz * oz
                        if d2 < bd and self.empty(cells_at(add(mp, o), off)):
                            best, bd = add(mp, o), d2
            mp = best
        cs = cells_at(mp, off)
        mok = self.mount_ok(prefab, R, mounted(mp, off))
        return cs, self.empty(cs) and mok, mok

    def aim(self, prefab, R, eye, look_at):
        """One frame: aim from eye at look_at. Returns None (nothing targeted) or a dict."""
        t = self.target(eye, sub(look_at, eye))
        if t is None:
            return None
        hit, nrm, pp, dist = t
        cells, valid, mok = self.place(prefab, R, pp, nrm)
        return {"hit": hit, "normal": nrm, "pp": pp, "cells": cells, "valid": valid, "mount": mok,
                "hit_block": self.at[hit].name, "dist": dist}


def placed(prefab, R, mounted, name=None):
    """A prefab placed at block-origin `mounted` with rotation R, as a grid Block (its body fills its cells)."""
    cells = {add(mul(R, c), mounted) for c in prefab.cells}
    mounts = {}
    for d, cs in prefab.mount_cells.items():
        gd = VEC2DIR[mul(R, DIRS[d])]
        mounts.setdefault(gd, set()).update(add(mul(R, c), mounted) for c in cs)
    lo, hi = aabb(cells)
    box = (tuple(CELL * v - HALF for v in lo), tuple(CELL * v + HALF for v in hi))
    return Block(name or prefab.name, cells, mounts, [box])


def mounted_for(prefab, R, cells):
    """The block origin that puts prefab (rotation R) exactly on `cells`."""
    lo, _ = aabb(cells)
    plo, _ = aabb({mul(R, c) for c in prefab.cells})
    return sub(lo, plo)


def emulate(grid: Grid, prefab: Prefab, R, eye, look_at, *, snap: Callable | None = None) -> dict | None:
    """One frame of the placer: aim `prefab` (rotation R) from `eye` at `look_at` (grid metres) on `grid`, then the
    mod's mount-snap step `snap(grid, prefab, R, result) -> result` (the primary mount point snap; None: no-op).
    Returns None (nothing targeted) or Grid.aim's dict (hit, normal, pp, cells, valid, mount, hit_block, dist)."""
    r = grid.aim(prefab, R, eye, look_at)
    if r is None or snap is None:
        return r
    return snap(grid, prefab, R, r)
