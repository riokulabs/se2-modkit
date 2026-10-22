"""Offline placement checker: predicts SE2's generated occupancy and mount points for a block model.

It follows the game's rules for generating a block's placement data, so the result can be checked before the game
builds it:

* **Occupancy**: a 0.25 m cell is occupied when a box of half-extent 0.124 m at the cell centre overlaps any
  *regular* collider (the collision glTF minus `mountpoints*` / `occupancy*` nodes) or any `occupancy*` hint
  collider.
* **Surface cells**: occupied cells whose neighbour in direction d is not occupied. These are NOT limited to the
  bounding box: a step inside the box is a surface too.
* **Sampler**: 8 rays per surface cell, on a circle of radius 0.66 x 0.125 m around the face centre, from 0.0625 m
  outside the cell face, 0.125 m inward, against the *precise* collider (a mesh collider built from the LOD0 render
  mesh) plus the `mountpoints*` hint colliders. A ray is valid when its closest hit lies within 0.02 m of the cell
  face plane; the cell mounts when >= 4 rays are valid and >= 4 of those lie on their average plane (within 2 mm)
  or face within 5 deg of d.
* **Groups**: the mount cells of each face are split by fracture (the cell's box query against the breakable
  collider = Fracture_NN hulls) and greedily merged into boxes. The game's merge order is not reproduced, so group
  COUNTS can differ from the game by a few; the cell coverage numbers are exact up to float noise.

Frames: everything here is in the collision glTF frame (Y up, -Z = Forward = the tool face). The .model applies a
+0.125 m translation (vanilla Welder400 does the same), so cell i has its centre at 0.25 i - 0.125 in this frame and
cell faces lie on multiples of 0.25 m. Blender -> glTF: (x, y, z) -> (x, z, -y).
"""
import json
import math
import os
import struct

import numpy as np

CELL = 0.25
HALF_Q = 0.125 - 0.001          # occupancy / fracture query box half extent
SHIFT = 0.125                   # .model Transformations.Translation (even-sized axes; see shift_for_bounds)
RAY_LEN = 0.125
RAY_START = 0.125 + 0.5 * RAY_LEN
TOL = 0.02
N_TOL = math.cos(math.radians(5))
SAMPLES = [(0.66 * 0.125 * math.cos(math.radians(22.5 + 45 * k)), 0.66 * 0.125 * math.sin(math.radians(22.5 + 45 * k)))
           for k in range(8)]
DIRS = {"Forward": (0, 0, -1), "Backward": (0, 0, 1), "Left": (-1, 0, 0), "Right": (1, 0, 0),
        "Up": (0, 1, 0), "Down": (0, -1, 0)}


def blender_to_gltf(v):
    return (v[0], v[2], -v[1])


# --------------------------------------------------------------------------------------------- inputs

def gltf_hulls(path):
    """{node name: (N,3) vertex array} from a collision glTF (vertices are world-space, identity nodes)."""
    g = json.load(open(path))
    d = os.path.dirname(path)
    bufs = [open(os.path.join(d, b["uri"]), "rb").read() for b in g["buffers"]]
    out = {}
    for n in g["nodes"]:
        if "mesh" not in n:
            continue
        a = g["accessors"][g["meshes"][n["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
        bv = g["bufferViews"][a["bufferView"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", 12)
        v = [struct.unpack_from("<fff", bufs[bv["buffer"]], off + k * stride) for k in range(a["count"])]
        v = np.array(v, dtype=float)
        if "translation" in n:
            v += np.array(n["translation"])
        out[n["name"]] = v
    return out


def split_hulls(hulls):
    """Regular / occupancy / mountpoints colliders by node-name prefix (case-insensitive, as the importer)."""
    reg, occ, mnt = [], [], []
    for name, v in hulls.items():
        low = name.lower()
        (occ if low.startswith("occupancy") else mnt if low.startswith("mountpoints") else reg).append((name, v))
    return reg, occ, mnt


# --------------------------------------------------------------------------------------------- geometry

def _hull_faces(v):
    """Outward face planes (normal, offset), triangles and unique edge directions of a convex point set.
    Uses bmesh inside Blender; a numpy implementation (same outputs) elsewhere."""
    try:
        import bmesh
        from mathutils import Vector
    except ImportError:
        return _hull_faces_np(v)
    bm = bmesh.new()
    for p in v:
        bm.verts.new(Vector(p))
    res = bmesh.ops.convex_hull(bm, input=bm.verts)
    bmesh.ops.delete(bm, geom=res["geom_interior"] + res["geom_unused"], context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    planes, tris, edges = [], [], set()
    c = np.mean(v, axis=0)
    for f in bm.faces:
        n = np.array(f.normal)
        if np.linalg.norm(n) < 1e-9:
            continue
        p0 = np.array(f.verts[0].co)
        if np.dot(n, p0 - c) < 0:
            n = -n
        planes.append((n, float(np.dot(n, p0))))
        vs = [np.array(x.co) for x in f.verts]
        for i in range(1, len(vs) - 1):
            tris.append((vs[0], vs[i], vs[i + 1]))
    for e in bm.edges:
        d = np.array(e.verts[1].co) - np.array(e.verts[0].co)
        L = np.linalg.norm(d)
        if L > 1e-9:
            edges.add(tuple(np.round(d / L * (1 if d[np.argmax(np.abs(d))] > 0 else -1), 5)))
    bm.free()
    return planes, tris, [np.array(e) for e in edges]


def _hull_faces_np(v, eps=1e-7):
    """bmesh-free equivalent of _hull_faces: brute-force facets (fine for collision hulls of <= ~200 points)."""
    v = np.asarray(v, float)
    n = len(v)
    c = v.mean(0)
    scale = max(float(np.ptp(v, axis=0).max()), 1.0)
    found = {}
    for i in range(n):
        for j in range(i + 1, n):
            nrm = np.cross(v[j] - v[i], v - v[i])                 # (n,3), row k = plane through i, j, k
            ln = np.linalg.norm(nrm, axis=1)
            for k in np.where(ln > eps * scale * scale)[0]:
                if k <= j:
                    continue
                u = nrm[k] / ln[k]
                d = (v - v[i]) @ u
                if (d > eps * scale).any() and (d < -eps * scale).any():
                    continue
                if (d > eps * scale).any():
                    u = -u                                       # make "all points behind" the outward convention
                off = float(u @ v[i])
                key = tuple(np.round(np.append(u, off), 5))
                if key not in found:
                    found[key] = (u, off)
    planes, tris, edges = [], [], set()
    for u, off in found.values():
        if float(u @ c) > off:
            u, off = -u, -off
        planes.append((u, off))
        on = np.where(np.abs(v @ u - off) < eps * scale * 10)[0]
        pts = v[on]
        cen = pts.mean(0)
        a = np.cross(u, [0, 1, 0] if abs(u[1]) < 0.9 else [1, 0, 0])
        a /= np.linalg.norm(a)
        b = np.cross(u, a)
        order = np.argsort(np.arctan2((pts - cen) @ b, (pts - cen) @ a))
        ring = pts[order]
        for t in range(1, len(ring) - 1):
            tris.append((ring[0], ring[t], ring[t + 1]))
        for t in range(len(ring)):
            d = ring[(t + 1) % len(ring)] - ring[t]
            L = np.linalg.norm(d)
            if L > 1e-9:
                edges.add(tuple(np.round(d / L * (1 if d[np.argmax(np.abs(d))] > 0 else -1), 5)))
    return planes, tris, [np.array(e) for e in edges]


class Hull:
    def __init__(self, name, v):
        self.name = name
        self.v = np.asarray(v, dtype=float)
        self.planes, self.tris, self.edges = _hull_faces(self.v)
        self.lo, self.hi = self.v.min(0), self.v.max(0)
        axes = [np.array(a, float) for a in ((1, 0, 0), (0, 1, 0), (0, 0, 1))] + [n for n, _ in self.planes]
        for e in self.edges:
            for b in np.eye(3):
                c = np.cross(e, b)
                if np.linalg.norm(c) > 1e-6:
                    axes.append(c / np.linalg.norm(c))
        self.axes = np.array(axes)
        proj = self.v @ self.axes.T                              # (nv, na)
        self.pmin, self.pmax = proj.min(0), proj.max(0)

    def overlaps_boxes(self, centres, half):
        """SAT, vectorised over many axis-aligned boxes of equal half extent."""
        c = centres @ self.axes.T                               # (nc, na)
        r = half * np.abs(self.axes).sum(1)                     # (na,)
        sep = (c + r < self.pmin) | (c - r > self.pmax)
        return ~sep.any(1)


def shift_for_bounds(bounds):
    """Per-axis .model translation for a model centred on the origin: the game's grid-align step shifts an axis with
    an EVEN cell count by 0.125 m and leaves an ODD one at 0 (vanilla Timer50, 2x2x2: 0.125 on every axis;
    ControlPanel25, 1x1 cells across: X = Y = 0). bounds: inclusive cell indices ((x0,y0,z0),(x1,y1,z1))."""
    lo, hi = bounds
    return tuple(SHIFT if (hi[i] - lo[i] + 1) % 2 == 0 else 0.0 for i in range(3))


def cell_centres(idx, shift=SHIFT):
    """Centre of cell idx in the input (collision glTF) frame: 0.25 i - shift (shift: scalar or per-axis)."""
    return np.asarray(idx, float) * CELL - np.asarray(shift, float)


# --------------------------------------------------------------------------------------------- generators

def occupancy(hulls, rng=(-9, 10), shift=SHIFT):
    ii = np.arange(*rng)
    grid = np.array(np.meshgrid(ii, ii, ii, indexing="ij")).reshape(3, -1).T
    cen = cell_centres(grid, shift)
    occ = np.zeros(len(grid), bool)
    for h in hulls:
        m = np.all((cen + HALF_Q > h.lo) & (cen - HALF_Q < h.hi), axis=1)
        if m.any():
            sub = np.where(m)[0]
            occ[sub[h.overlaps_boxes(cen[sub], HALF_Q)]] = True
    return {tuple(int(x) for x in c) for c in grid[occ]}


def surface_cells(occ):
    out = {}
    for d, v in DIRS.items():
        out[d] = [c for c in sorted(occ, key=lambda c: (c[2], c[1], c[0]))
                  if (c[0] + v[0], c[1] + v[1], c[2] + v[2]) not in occ]
    return out


class NumpyBVH:
    """bmesh/mathutils-free stand-in for BVHTree: ray_cast(origin, direction, dist) -> (location, normal, index,
    distance) of the closest hit within dist, or (None, None, None, None). Moller-Trumbore over all triangles."""

    def __init__(self, tris):
        t = np.asarray(list(tris), float).reshape(-1, 3, 3)
        self.p0, self.e1, self.e2 = t[:, 0], t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        n = np.cross(self.e1, self.e2)
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-12
        self.p0, self.e1, self.e2 = self.p0[keep], self.e1[keep], self.e2[keep]
        self.n = n[keep] / ln[keep][:, None]

    def ray_cast(self, origin, direction, dist=1e9):
        o, d = np.asarray(origin, float), np.asarray(direction, float)
        h = np.cross(d, self.e2)
        a = np.einsum("ij,ij->i", self.e1, h)
        ok = np.abs(a) > 1e-12
        f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
        s = o - self.p0
        u = f * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, self.e1)
        w = f * (q @ d)
        t = f * np.einsum("ij,ij->i", self.e2, q)
        hit = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t >= 0) & (t <= dist)
        if not hit.any():
            return None, None, None, None
        i = int(np.where(hit)[0][np.argmin(t[hit])])
        return o + d * t[i], self.n[i], i, float(t[i])


def build_bvh(tris):
    try:
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree
    except ImportError:
        return NumpyBVH(tris)
    verts, polys = [], []
    for t in tris:
        b = len(verts)
        verts += [Vector(p) for p in t]
        polys.append((b, b + 1, b + 2))
    return BVHTree.FromPolygons(verts, polys, epsilon=0.0)


def _perp(v):
    v = np.array(v, float)
    a = np.array((0, 1, 0)) if abs(v[1]) < 0.9 else np.array((1, 0, 0))
    r = np.cross(a, v)
    r /= np.linalg.norm(r)
    return r, np.cross(v, r)


def sample(cell, d, bvh, shift=SHIFT):
    try:
        from mathutils import Vector
    except ImportError:
        def Vector(x):                       # noqa: N802  plain tuples are fine for NumpyBVH
            return np.asarray(x, float)
    f = np.array(DIRS[d], float)
    right, up = _perp(f)
    c = cell_centres(cell, shift)
    valid = []
    for sx, sy in SAMPLES:
        o = c + RAY_START * f + sx * right + sy * up
        loc, nrm, _, dist = bvh.ray_cast(Vector(o), Vector(-f), RAY_LEN)
        if loc is None:
            continue
        frac = dist / RAY_LEN
        n = np.array(nrm)
        if np.dot(n, f) < 0:                 # report the normal facing the ray (as the physics engine does)
            n = -n
        if abs(frac - 0.5) < TOL / RAY_LEN:
            valid.append((frac, float(np.dot(n, f)) > N_TOL))
    if len(valid) < 4:
        return False
    avg = sum(v[0] for v in valid) / len(valid)
    ok = sum(1 for fr, nok in valid if abs(fr - avg) / (TOL / RAY_LEN) < 0.1 or nok)
    return ok >= 4


def build_groups(cells):
    """Merge cells into boxes: grow a box from the first remaining cell, one layer at a time on each side in turn
    (x+, x-, y+, y-, z+, z-), until no side can grow; a side stops for good once its next layer is not all free."""
    free = dict.fromkeys(cells)              # insertion-ordered set: the seed is always the oldest remaining cell
    groups = []
    while free:
        seed = next(iter(free))
        del free[seed]
        lo, hi = list(seed), list(seed)
        growing = [(axis, step) for axis in range(3) for step in (1, -1)]
        while growing:
            for side in list(growing):
                axis, step = side
                layer = _layer(lo, hi, axis, hi[axis] + 1 if step > 0 else lo[axis] - 1)
                if all(c in free for c in layer):
                    for c in layer:
                        del free[c]
                    if step > 0:
                        hi[axis] += 1
                    else:
                        lo[axis] -= 1
                else:
                    growing.remove(side)
        groups.append((tuple(lo), tuple(hi)))
    return groups


def _layer(lo, hi, axis, at):
    """The cells of the box (lo, hi) widened to `at` on `axis`: the slab one cell thick at that index."""
    a, b = (axis + 1) % 3, (axis + 2) % 3
    out = []
    for u in range(lo[a], hi[a] + 1):
        for v in range(lo[b], hi[b] + 1):
            c = [0, 0, 0]
            c[axis], c[a], c[b] = at, u, v
            out.append(tuple(c))
    return out


def fracture_of(cell, fracs, shift=SHIFT):
    c = cell_centres([cell], shift)
    for h in fracs:
        if h.overlaps_boxes(c, HALF_Q)[0]:
            return h.name
    return None


# --------------------------------------------------------------------------------------------- driver

def analyse(render_tris, regular, occupancy_hints=(), mount_hints=(), fracture_hulls=(), bounds=((-4, -4, -7), (5, 5, 8)),
            spans=None, shift=None):
    """All inputs in the collision-glTF frame. render_tris: iterable of 3-point triangles (LOD0 render mesh);
    hull lists: (name, verts) pairs. spans: optional {name: (z0, z1)} cell index ranges along the long axis (+z = the
    back end) for a per-span row coverage in the report (a mod names its own). bounds: the definition BoundingBox in cells (x, y, z; game frame where
    cell i has centre 0.25 i), default Welder400's -4..5 x -4..5 x -7..8.
    shift: the .model translation, scalar or per axis. None keeps the old uniform 0.125 (right for even-sized
    boxes such as the Welder400); pass "bounds" (or shift_for_bounds(bounds)) for a model centred on the origin
    with odd-sized axes. The result also lists free_cells: the box cells left unoccupied."""
    if shift is None:
        shift = SHIFT
    elif isinstance(shift, str):
        assert shift == "bounds", shift
        shift = shift_for_bounds(bounds)
    reg = [Hull(n, v) for n, v in regular]
    occ_h = [Hull(n, v) for n, v in occupancy_hints]
    mnt = [Hull(n, v) for n, v in mount_hints]
    frs = [Hull(n, v) for n, v in fracture_hulls]
    occ = occupancy(reg + occ_h, shift=shift)
    tris = list(render_tris)
    for h in mnt:
        tris += h.tris
    bvh = build_bvh(tris)
    surf = surface_cells(occ)
    lo, hi = bounds
    box_cells = {(x, y, z) for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1) for z in range(lo[2], hi[2] + 1)}
    res = {"occupied_cells": len(occ), "box_cells": len(box_cells),
           "occupied_in_box": len(occ & box_cells), "occupied_outside_box": len(occ - box_cells),
           "empty_in_box": len(box_cells - occ), "faces": {},
           "free_cells": sorted(box_cells - occ), "shift": [float(v) for v in np.broadcast_to(shift, 3)]}
    occ_groups = build_groups(sorted(occ, key=lambda c: (c[2], c[1], c[0])))
    res["occupancy_groups"] = len(occ_groups)
    for d, cells in surf.items():
        ax = [i for i in range(3) if DIRS[d][i]][0]
        sgn = DIRS[d][ax]
        plane = hi[ax] if sgn > 0 else lo[ax]
        mount = [c for c in cells if sample(c, d, bvh, shift)]
        on_plane = [c for c in mount if c[ax] == plane]
        by_frac = {}
        for c in mount:
            by_frac.setdefault(fracture_of(c, frs, shift) if frs else None, []).append(c)
        groups = []
        for fr, cs in by_frac.items():
            groups += [(fr, g) for g in build_groups(cs)]
        # face cells on the bounding plane (the cells another block could attach to), and how many mount
        other = [i for i in range(3) if i != ax]
        n_face = (hi[other[0]] - lo[other[0]] + 1) * (hi[other[1]] - lo[other[1]] + 1)
        # coverage along the block's long axis (z): which z rows of this face have at least one mount cell
        rows = sorted({c[2] for c in on_plane}) if ax != 2 else []
        span_rep = {}
        if ax != 2:
            across = (hi[other[0]] - lo[other[0]] + 1) if other[0] != 2 else (hi[other[1]] - lo[other[1]] + 1)
            for sn, (z0, z1) in (spans or {}).items():
                cells_ = [c for c in on_plane if z0 <= c[2] <= z1]
                n_rows = z1 - z0 + 1
                span_rep[sn] = {"mount_cells": len(cells_), "rows_covered": f"{len({c[2] for c in cells_})}/{n_rows}",
                                "coverage_pct": round(100 * len(cells_) / (n_rows * across), 1)}
        res["faces"][d] = {
            "spans": span_rep,
            "surface_cells": len(cells), "mount_cells": len(mount), "mount_cells_on_box_plane": len(on_plane),
            "mount_cells_inside_box": len(mount) - len(on_plane), "box_face_cells": n_face,
            "inner_mount_cells": sorted(c for c in mount if c[ax] != plane),
            "mount_cell_list": sorted(mount),
            "box_face_coverage_pct": round(100 * len(on_plane) / n_face, 1), "groups": len(groups),
            "long_axis_rows_covered": f"{len(rows)}/{hi[2] - lo[2] + 1}" if ax != 2 else None,
            "long_axis_span": [rows[0], rows[-1]] if rows else None,
        }
    return res


def summary_line(name, r):
    f = r["faces"]
    g = " / ".join(str(f[d]["groups"]) for d in DIRS)
    cov = " / ".join(f"{f[d]['box_face_coverage_pct']:.0f}%" for d in DIRS)
    sp = ""
    spans = f["Left"].get("spans")
    if spans:
        sp = "; long sides rows (" + "|".join(spans) + ") " + " ".join(
            f"{d[0]}:" + "|".join(f[d]["spans"][k]["rows_covered"] for k in spans) for d in ("Left", "Right", "Up", "Down"))
    return (f"{name}: groups F/B/L/R/U/D = {g}; plane coverage = {cov}; occupied {r['occupied_in_box']}/{r['box_cells']} "
            f"box cells, {r['empty_in_box']} empty, {r['occupied_outside_box']} outside{sp}")


# --------------------------------------------------------------------------------------------- Blender adapters

def tris_from_objects(objs):
    """LOD0 render triangles (Blender objects, evaluated) in the glTF frame."""
    import bpy
    out = []
    dg = bpy.context.evaluated_depsgraph_get()
    for o in objs:
        if o.type != "MESH":
            continue
        oe = o.evaluated_get(dg)
        me = oe.to_mesh()
        me.calc_loop_triangles()
        mw = o.matrix_world
        vs = [blender_to_gltf(mw @ v.co) for v in me.vertices]
        for t in me.loop_triangles:
            out.append(tuple(vs[i] for i in t.vertices))
        oe.to_mesh_clear()
    return out

