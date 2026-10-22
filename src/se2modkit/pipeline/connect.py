"""Mechanical connectivity check (run on a build that asks for it; fails it on errors).

Two checks on the LOD0 parts, before they become objects:
 1. Declared joints: builders register named components (vertex spans of a Part's bmesh) and declare
    child -> parent joints. A joint passes if the two components overlap (BVH triangle overlap) or their
    nearest surfaces are within `tol` (default 0.5 mm). Pins also declare the holes they must pass through:
    every hole centre must lie on the pin axis (within 1 mm) and within the pin's length.
 2. Loose islands: every connected mesh island of every part must reach a structural root (`hooks.connect_roots`,
    part names) through islands that touch or overlap. Decals may float up to 2.5 mm (they sit 2 mm proud by design).
    No root registered: every island is reported, never a pass.

Registration (`comp`, `joint`, `role`, `hose` and the rest) is live only inside `check_build`: the builders call
these module functions while they build, so the registry (REG) is run state of this module, reset around every
check; the configuration (the roots, the manual realism verdicts) comes from the hooks.

Blender only (bmesh, mathutils).
"""

from __future__ import annotations

import math
from contextlib import contextmanager

import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

TOL = 0.0005
DECAL_TOL = 0.0025
REG = {"comps": {}, "joints": [], "axes": [], "fasteners": [], "decal_fasteners": [], "hoses": [], "cylinders": [],
       "kinematic": [], "roles": {}, "bolts": [], "rounds": [], "cables": [], "pipes": [], "labels": [], "hazards": []}
FASTENER_FINISHES = {"BrushedMetal", "MetalDull"}     # zinc-plated / black-oxide look; never the paint they hold
HOSE_MIN_BEND_X_OD = 4.0                              # see realism checks: min bend radius as a multiple of hose OD
HOSE_ALONG_MAX = 0.10                                 # m of hose allowed to run parallel along a cylinder body
ACTIVE = False   # registration only happens inside check_build(); other builds are unaffected


def reset():
    for v in REG.values():
        v.clear()


@contextmanager
def comp(p, name):
    """Register everything built inside the block as component `name` of part p."""
    start = len(p.bm.verts)
    yield
    if ACTIVE:
        REG["comps"][name] = (p, start, len(p.bm.verts))


def whole(p, name):
    if ACTIVE:
        REG["comps"][name] = (p, 0, None)


def joint(child, parent, tol=TOL):
    if ACTIVE:
        REG["joints"].append((child, parent, tol))


def coaxial(pin, p0, p1, holes, tol=0.001):
    if ACTIVE:
        REG["axes"].append((pin, Vector(p0), Vector(p1), list(holes), tol))


def role(name, role_, **attrs):
    """Role tag (and attributes) for a registered component, used by the realism checks (pipeline.realism)."""
    if ACTIVE:
        REG["roles"][name] = dict(attrs, role=role_)


def fastener(name):
    """Component `name` is a fastener set (bolts, nuts, washers): its finish must be a bare-metal finish and must
    differ from the material of the surface it is fastened to (found automatically: the nearest other face)."""
    if ACTIVE:
        REG["fasteners"].append(name)


def decal_fastener(part_name, key, variant):
    if ACTIVE:
        REG["decal_fasteners"].append((part_name, key, variant))


def hose(name, control_pts, r, bend, ends, chord=None, straight=None, sides=None, steps=4, kind="hose"):
    """A flexible hose (or cable): control polyline as built, radius, bend radius, the two components it joins,
    the straight distance between its fittings (for slack), the straight run after each ferrule, ring count."""
    if ACTIVE:
        REG["hoses"].append({"name": name, "pts": [Vector(q) for q in control_pts], "r": r, "bend": bend,
                             "ends": list(ends), "chord": chord, "straight": straight, "sides": sides,
                             "steps": steps, "kind": kind})


def bolt(origin, axis, r):
    if ACTIVE:
        REG["bolts"].append((Vector(origin), Vector(axis), r))


def bolt_pattern(name, kind, **attrs):
    if ACTIVE:
        REG["roles"].setdefault(name, {}).update(dict(attrs, pattern=kind))


def round_part(name, r, sides, mat):
    if ACTIVE:
        REG["rounds"].append((name, r, sides, mat))


def cylinder_body(name, p0, p1, r):
    """A hydraulic cylinder body axis (hoses may not run along it; both ports of one body may not share a hose)."""
    if ACTIVE:
        REG["cylinders"].append((name, Vector(p0), Vector(p1), r))


def kinematic(name, moving, vec, stroke, engaged=(), clear=(), clearance=0.005, min_engage=0.0):
    """Moving components translate by vec * s for s in [0, stroke]. At s = 0, stroke/2 and stroke: every (moving,
    static) pair in `engaged` must still overlap (also at stroke + min_engage), and every pair in `clear` must keep
    `clearance` (m) between them."""
    if ACTIVE:
        REG["kinematic"].append({"name": name, "moving": list(moving), "vec": Vector(vec).normalized(),
                                 "stroke": stroke, "engaged": list(engaged), "clear": list(clear),
                                 "clearance": clearance, "min_engage": min_engage})


# ------------------------------------------------------------------------------------------ geometry
def _span_geom(p, start, end, with_mats=False, exclude=False):
    """Vertices/polygons of a vertex span (or, with exclude=True, of everything in the part except the span)."""
    bm = p.bm
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    n = len(bm.verts)
    end = n if end is None else end
    if exclude:
        keep = [i for i in range(n) if not (start <= i < end)]
    else:
        keep = list(range(start, end))
    remap = {o: i for i, o in enumerate(keep)}
    verts = [bm.verts[i].co.copy() for i in keep]
    polys, mats = [], []
    for f in bm.faces:
        idx = [v.index for v in f.verts]
        if all(i in remap for i in idx):
            polys.append([remap[i] for i in idx])
            mats.append(p.mats[f.material_index] if f.material_index < len(p.mats) else None)
    return (verts, polys, mats) if with_mats else (verts, polys)


def _tree(verts, polys):
    if not polys:
        return None
    return BVHTree.FromPolygons(verts, polys, all_triangles=False, epsilon=0.0)


def _bbox(verts):
    lo = Vector(map(min, *verts)) if len(verts) > 1 else verts[0].copy()
    hi = Vector(map(max, *verts)) if len(verts) > 1 else verts[0].copy()
    return lo, hi


def _bbox_gap(a, b):
    (alo, ahi), (blo, bhi) = a, b
    gap = 0.0
    for k in range(3):
        d = max(blo[k] - ahi[k], alo[k] - bhi[k], 0.0)
        gap = max(gap, d)
    return gap


def _distance(ga, gb, ta, tb, tol):
    """0 if overlapping, else the nearest vertex-to-surface distance (both ways, early exit at tol)."""
    if ta and tb and ta.overlap(tb):
        return 0.0
    best = math.inf
    for verts, tree in ((ga[0], tb), (gb[0], ta)):
        if tree is None:
            continue
        for v in verts:
            hit = tree.find_nearest(v, best)
            if hit[0] is not None:
                best = min(best, hit[3])
                if best <= tol:
                    return best
    return best


def _islands(p):
    bm = p.bm
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    bm.verts.index_update()
    seen, out = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, isl = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            isl.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        out.append(isl)
    return out


# ------------------------------------------------------------------------------------------ the check
def run(hooks, parts, roots=None):
    """Both checks and the realism checks on the registered components of `parts`; the islands reach the
    structural roots `roots` (part names; default `hooks.connect_roots`)."""
    res = {"joints_checked": 0, "joint_failures": [], "axes_checked": 0, "axis_failures": [],
           "islands_checked": 0, "loose_islands": []}
    geo = {}
    for name, (p, s, e) in REG["comps"].items():
        if p.bm.is_valid:
            v, f = _span_geom(p, s, e)
            if v:
                geo[name] = (v, f, _tree(v, f), _bbox(v))
    for child, parent, tol in REG["joints"]:
        res["joints_checked"] += 1
        if child not in geo or parent not in geo:
            res["joint_failures"].append({"child": child, "parent": parent, "error": "component missing"})
            continue
        a, b = geo[child], geo[parent]
        if _bbox_gap(a[3], b[3]) > tol:
            d = _bbox_gap(a[3], b[3])
        else:
            d = _distance(a, b, a[2], b[2], tol)
        if d > tol:
            res["joint_failures"].append({"child": child, "parent": parent, "gap_mm": round(d * 1000, 2)})
    for pin, p0, p1, holes, tol in REG["axes"]:
        axis = p1 - p0
        L = axis.length
        ax = axis / L
        for h in holes:
            res["axes_checked"] += 1
            if h not in geo:
                res["axis_failures"].append({"pin": pin, "hole": h, "error": "component missing"})
                continue
            c = sum(geo[h][0], Vector()) / len(geo[h][0])
            t = (c - p0).dot(ax)
            off = (c - (p0 + ax * t)).length
            if off > tol or t < -tol or t > L + tol:
                res["axis_failures"].append({"pin": pin, "hole": h, "offset_mm": round(off * 1000, 2),
                                             "along_mm": round(t * 1000, 1), "pin_len_mm": round(L * 1000, 1)})
    # loose islands
    trees = []
    for p in parts:
        if p.empty():
            continue
        for k, isl in enumerate(_islands(p)):
            vids = sorted({v.index for f in isl for v in f.verts})
            remap = {o: i for i, o in enumerate(vids)}
            verts = [p.bm.verts[i].co.copy() for i in vids]
            polys = [[remap[v.index] for v in f.verts] for f in isl]
            # per-part touch tolerance: decals float 2 mm by design; a part may set island_tol (reused vanilla kit
            # parts keep their authored ~2 mm standoffs, e.g. a handle over its door)
            ptol = DECAL_TOL if p.decal else getattr(p, "island_tol", TOL)
            trees.append((p.name, k, (p.decal, ptol), verts, polys, _tree(verts, polys), _bbox(verts)))
    # touch graph between islands; every island must reach a structural root through touching islands
    n = len(trees)
    adj = [[] for _ in range(n)]
    nearest = [math.inf] * n
    for i in range(n):
        pn, k, dec, verts, polys, tr, bb = trees[i]
        for j in range(i + 1, n):
            pn2, k2, dec2, v2, f2, tr2, bb2 = trees[j]
            if dec[0] and dec2[0]:
                continue
            tol = max(dec[1], dec2[1])
            if _bbox_gap(bb, bb2) > tol:
                continue
            d = _distance((verts, polys), (v2, f2), tr, tr2, tol)
            nearest[i] = min(nearest[i], d)
            nearest[j] = min(nearest[j], d)
            if d <= tol:
                adj[i].append(j)
                adj[j].append(i)
    res["islands_checked"] = n
    roots = set(hooks.connect_roots if roots is None else roots)
    seen = set(i for i in range(n) if trees[i][0] in roots)
    stack = list(seen)
    while stack:
        i = stack.pop()
        for j in adj[i]:
            if j not in seen:
                seen.add(j)
                stack.append(j)
    for i in range(n):
        if i in seen:
            continue
        pn, k, dec, verts, polys, tr, bb = trees[i]
        c = sum(verts, Vector()) / len(verts)
        kind = "loose" if not adj[i] else "floating_cluster"
        res["loose_islands"].append({"part": pn, "island": k, "kind": kind, "centre": [round(x, 3) for x in c],
                                     "nearest_mm": None if nearest[i] == math.inf else round(nearest[i] * 1000, 2)})
    from . import realism
    res["realism"] = realism.run(hooks, REG, geo, parts)
    res["ok"] = not (res["joint_failures"] or res["axis_failures"] or res["loose_islands"] or
                     res["realism"]["errors"])
    return res


def label(kind, pos, normal):
    if ACTIVE:
        REG["labels"].append((kind, Vector(pos), Vector(normal)))


def hazard(kind, comp_name):
    if ACTIVE:
        REG["hazards"].append((kind, comp_name))


def check_build(hooks, build_parts_fn, roots=None):
    """Build the assembled LOD0 (main parts + subparts placed at their dummies) with registration on, and check:
    build_parts_fn() -> [model Part]; roots overrides `hooks.connect_roots` for this check (e.g. per subpart)."""
    global ACTIVE
    reset()
    ACTIVE = True
    try:
        parts = build_parts_fn()
        res = run(hooks, parts, roots)
    finally:
        ACTIVE = False
        for p in parts if 'parts' in locals() else []:
            p.bm.free()
        reset()
    return res
