"""bmesh building blocks. Every primitive writes into a Part (a bmesh plus a local material list).

Coordinates are Blender world space (Z up). Block axes: X width, +Y the working face, Z height.
"""
import math

import bmesh
from mathutils import Matrix, Vector


class Part:
    """A bmesh plus a local material list. Faces use material_index into self.mats.

    Attributes the exporter reads:
      bevel   (width, segments) crisp bevel applied as a modifier (limit by angle 30 deg)
      section SECTION custom property (conveyor highlight sections)
      decal   True: open decal quads, keep the authored winding (no normal recalculation)
      zone    point used to assign the part to a fracture group when it is not cut
      cut     True: big shell, gets boolean-cut into fracture pieces in the Fractured variant
    """

    def __init__(self, name, section=None, zone=None, cut=False):
        self.name, self.section, self.zone, self.cut = name, section, zone, cut
        self.bm = bmesh.new()
        self.mats = []
        self.bevel = (0.0, 0)
        self.decal = False

    def mi(self, mat):
        if mat not in self.mats:
            self.mats.append(mat)
        return self.mats.index(mat)

    def empty(self):
        return len(self.bm.verts) == 0


LOD_ERR = (0.001, 0.003, 0.008, 0.025)     # allowed silhouette (sagitta) error per LOD (realism guide 4.1)
SEG_STEPS = (4, 6, 8, 12, 16, 20, 24, 32, 48)
SEG_FLOOR = {0: 8, 1: 6, 2: 4, 3: 4}


_ROUND = [0]


def loop_normal_layer(bm):
    """The bmesh loop float-vector layer of the per-loop normal hook (normals.py, objects.apply_loop_normals): write a
    face corner's exact shading normal there; corners left at zero keep the exported default."""
    from .normals import LOOP_NORMAL_LAYER
    return bm.loops.layers.float_vector.get(LOOP_NORMAL_LAYER) or bm.loops.layers.float_vector.new(LOOP_NORMAL_LAYER)


def round_id(bm):
    """New id for a band of round side faces (face int layer "sb_round"): edges between two faces with the same id
    are never marked sharp or bevelled (objects.finalize_mesh), so round parts shade smooth at any segment count."""
    _ROUND[0] += 1
    lay = bm.faces.layers.int.get("sb_round") or bm.faces.layers.int.new("sb_round")
    return lay, _ROUND[0]


def nseg(r, lod=0, chrome=False, floor=None):
    """Segment count for a round part of radius r: N = ceil(pi / acos(1 - e/r)) for the LOD's error budget,
    x1.5 for Chrome (reflections expose the facets), rounded up to 6/8/12/16/20/24/32/48."""
    e = LOD_ERR[min(lod, 3)]
    n = 4 if e >= r else math.ceil(math.pi / math.acos(1 - e / r))
    if chrome:
        n = math.ceil(n * 1.5)
    n = next((s for s in SEG_STEPS if s >= n), SEG_STEPS[-1])
    return max(n, SEG_FLOOR[min(lod, 3)] if floor is None else floor)


def _faces_of(verts):
    return list({f for v in verts for f in v.link_faces})


def box(p, lo, hi, mat, matrix=None):
    bm = p.bm
    lo, hi = Vector(lo), Vector(hi)
    vs = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.scale(bm, vec=hi - lo, verts=vs)
    bmesh.ops.translate(bm, vec=(lo + hi) / 2, verts=vs)
    if matrix is not None:
        bmesh.ops.transform(bm, matrix=matrix, verts=vs)
    fs = _faces_of(vs)
    idx = p.mi(mat)
    for f in fs:
        f.material_index = idx
    bm.normal_update()
    return fs


def chamfer_box(p, lo, hi, mat, c):
    """Box with 45-degree chamfers on all 12 edges (the vanilla block-edge language)."""
    fs = box(p, lo, hi, mat)
    edges = list({e for f in fs for e in f.edges})
    bmesh.ops.bevel(p.bm, geom=edges, offset=c, segments=1, profile=0.5, affect="EDGES", clamp_overlap=True)
    p.bm.normal_update()


def rot_about(center, angle, axis):
    c = Vector(center)
    return Matrix.Translation(c) @ Matrix.Rotation(angle, 4, axis) @ Matrix.Translation(-c)


def frame_between(p0, p1, up=(0, 0, 1)):
    """Matrix whose local Y runs from p0 to p1 (origin at p0)."""
    p0, p1 = Vector(p0), Vector(p1)
    d = (p1 - p0).normalized()
    upv = Vector(up)
    if abs(d.dot(upv)) > 0.95:
        upv = Vector((1, 0, 0))
    x = d.cross(upv).normalized()
    z = x.cross(d).normalized()
    m = Matrix((x, d, z)).transposed().to_4x4()
    m.translation = p0
    return m


def beam(p, p0, p1, w, h, mat, chamfer=0.0):
    p0, p1 = Vector(p0), Vector(p1)
    L = (p1 - p0).length
    pts = clipped_rect(w / 2, h / 2, chamfer)
    m = frame_between(p0, p1)
    _extrude_loop(p, [(x, 0.0, y) for x, y in pts], L, mat, m)


def _extrude_loop(p, loop_xz, length, mat, m, cap_mat=None):
    bm = p.bm
    a = [bm.verts.new(m @ Vector((x, 0.0, z))) for x, _, z in loop_xz]
    b = [bm.verts.new(m @ Vector((x, length, z))) for x, _, z in loop_xz]
    idx = p.mi(mat)
    for i in range(len(a)):
        j = (i + 1) % len(a)
        bm.faces.new((a[i], a[j], b[j], b[i])).material_index = idx
    bm.faces.new(list(reversed(a))).material_index = p.mi(cap_mat or mat)
    bm.faces.new(b).material_index = p.mi(cap_mat or mat)


def lathe(p, profile, sides, mats, cap0=None, cap1=None, rot=0.0, matrix=None, faceted=False):
    """Revolve a (radius, y) profile around the local Y axis. mats[k] = material of band k.
    sides=6/8 gives hexagonal/octagonal prisms. Returns the faces per band."""
    bm = p.bm
    rings = []
    for r, y in profile:
        rings.append([bm.verts.new((r * math.cos(rot + 2 * math.pi * i / sides), y,
                                    r * math.sin(rot + 2 * math.pi * i / sides))) for i in range(sides)])
    bands = []
    for k in range(len(rings) - 1):
        a, b = rings[k], rings[k + 1]
        idx = p.mi(mats[k])
        band = []
        lay, rid = round_id(bm) if sides >= 8 and not faceted else (None, 0)
        for i in range(sides):
            j = (i + 1) % sides
            f = bm.faces.new((a[i], a[j], b[j], b[i]))
            f.material_index = idx
            if lay:
                f[lay] = rid
            band.append(f)
        bands.append(band)
    if cap0:
        bm.faces.new(list(reversed(rings[0]))).material_index = p.mi(cap0)
    if cap1:
        bm.faces.new(rings[-1]).material_index = p.mi(cap1)
    if matrix is not None:
        bmesh.ops.transform(bm, matrix=matrix, verts=[v for r in rings for v in r])
    bm.normal_update()
    # NB: faces are wound inward; Part export recalculates normals. Recalculate before using face normals.
    return bands


def cylinder(p, p0, p1, r, sides, mat, cap=True, cap_mat=None):
    """Cylinder from p0 to p1."""
    L = (Vector(p1) - Vector(p0)).length
    return lathe(p, [(r, 0.0), (r, L)], sides, [mat], cap0=(cap_mat or mat) if cap else None,
                 cap1=(cap_mat or mat) if cap else None, matrix=frame_between(p0, p1))


def fillet_path(pts, bend, steps_per_90=3):
    """Polyline -> path with true circular-arc fillets of radius `bend` at every corner. Where a leg is too short
    the radius shrinks to fit (half of each adjacent leg). Returns (path, effective radii per corner)."""
    pts = [Vector(q) for q in pts]
    path, radii = [pts[0]], []
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        d0, d1 = (b - a).normalized(), (c - b).normalized()
        th = d0.angle(d1, 0.0)
        if th < 1e-4:
            path.append(b)
            continue
        la = (b - a).length if i == 1 else (b - a).length / 2           # end legs are not shared with a bend
        lc = (c - b).length if i == len(pts) - 2 else (c - b).length / 2
        t = min(bend * math.tan(th / 2), la, lc)
        R = t / math.tan(th / 2)
        radii.append(R)
        s, e = b - d0 * t, b + d1 * t
        ctr = s + (d1 - d0 * d0.dot(d1)).normalized() * R
        v0, v1 = s - ctr, e - ctr
        n = max(2, int(math.ceil(th / (math.pi / 2) * steps_per_90)))
        axis = v0.cross(v1).normalized()
        for k in range(n + 1):
            path.append(ctr + Matrix.Rotation(th * k / n, 3, axis) @ v0)
    path.append(pts[-1])
    out = [path[0]]
    for q in path[1:]:
        if (q - out[-1]).length > 1e-5:
            out.append(q)
    return out, radii


def pipe_path(p, pts, r, sides, mat, bend=0.12, bend_steps=3, cap=True):
    """A tube following a polyline with circular-arc bends of radius `bend` (cables, conduits, hoses)."""
    path, _ = fillet_path(pts, bend, bend_steps)
    # sweep a ring along the path with parallel-transport frames
    bm = p.bm
    idx = p.mi(mat)
    rings = []
    tangent = (path[1] - path[0]).normalized()
    normal = tangent.orthogonal().normalized()
    for i, q in enumerate(path):
        if 0 < i < len(path) - 1:
            t_new = ((path[i + 1] - path[i]).normalized() + (path[i] - path[i - 1]).normalized()).normalized()
        elif i == len(path) - 1:
            t_new = (path[i] - path[i - 1]).normalized()
        else:
            t_new = tangent
        axis = tangent.cross(t_new)
        if axis.length > 1e-6:
            ang = tangent.angle(t_new)
            normal = Matrix.Rotation(ang, 3, axis.normalized()) @ normal
        tangent = t_new
        binorm = tangent.cross(normal).normalized()
        rings.append([bm.verts.new(q + (normal * math.cos(2 * math.pi * k / sides) +
                                        binorm * math.sin(2 * math.pi * k / sides)) * r) for k in range(sides)])
    lay, rid = round_id(bm) if sides >= 6 else (None, 0)
    for a, b in zip(rings, rings[1:]):
        for k in range(sides):
            j = (k + 1) % sides
            f = bm.faces.new((a[k], a[j], b[j], b[k]))
            f.material_index = idx
            if lay:
                f[lay] = rid
    if cap:
        bm.faces.new(list(reversed(rings[0]))).material_index = idx
        bm.faces.new(rings[-1]).material_index = idx
    return path


def recess(p, face, inset, depth, floor_mat=None, wall_mat=None):
    """Inset a face, then push it in by depth."""
    bm = p.bm
    bmesh.ops.inset_region(bm, faces=[face], thickness=inset, depth=0.0, use_even_offset=True)
    if depth:
        walls = bmesh.ops.inset_region(bm, faces=[face], thickness=0.0, depth=-depth)["faces"]
        if wall_mat:
            for f in walls:
                f.material_index = p.mi(wall_mat)
    if floor_mat:
        face.material_index = p.mi(floor_mat)
    bm.normal_update()
    return face


def toward(faces, n):
    n = Vector(n)
    return max(faces, key=lambda f: f.normal.dot(n))


class Face:
    """A 2D frame on a surface: a along u, b along v, d along the outward normal n = u x v."""

    def __init__(self, origin, u, v):
        self.o, self.u, self.v = Vector(origin), Vector(u).normalized(), Vector(v).normalized()
        self.n = self.u.cross(self.v)

    def __call__(self, a, b, d=0.0):
        return self.o + self.u * a + self.v * b + self.n * d

    def matrix(self, a, b, d=0.0):
        """Local frame at (a, b, d) with local Y along the normal (for lathe() parts such as bolts)."""
        r = Matrix((self.u, self.n, self.v)).transposed().to_4x4()
        return Matrix.Translation(self(a, b, d)) @ r

    def sub(self, a, b, d=0.0):
        """A Face with the same axes, re-centred at (a, b, d)."""
        return Face(self(a, b, d), self.u, self.v)


def clipped_rect(hw, hh, c):
    """Rectangle with 45-degree clipped corners (the vanilla panel shape), counter-clockwise."""
    if c <= 0:
        return [(hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)]
    return [(hw - c, -hh), (hw, -hh + c), (hw, hh - c), (hw - c, hh),
            (-hw + c, hh), (-hw, hh - c), (-hw, -hh + c), (-hw + c, -hh)]


def ngon(r, n, rot=0.0):
    return [(r * math.cos(rot + 2 * math.pi * i / n), r * math.sin(rot + 2 * math.pi * i / n)) for i in range(n)]


def prism(p, F, pts, d0, d1, mat, ca=0.0, cb=0.0, side_mat=None):
    """Extrude a 2D polygon on face F from depth d0 to d1."""
    bm = p.bm
    lo = [bm.verts.new(F(ca + a, cb + b, d0)) for a, b in pts]
    hi = [bm.verts.new(F(ca + a, cb + b, d1)) for a, b in pts]
    top = bm.faces.new(hi)
    top.material_index = p.mi(mat)
    bm.faces.new(list(reversed(lo))).material_index = p.mi(side_mat or mat)
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        bm.faces.new((lo[i], lo[j], hi[j], hi[i])).material_index = p.mi(side_mat or mat)
    return top


def ring(p, F, outer, inner, d0, d1, mat, ca=0.0, cb=0.0, inner_mat=None):
    """A closed frame between two loops with the same vertex count (port borders, rims)."""
    bm = p.bm
    L = [[bm.verts.new(F(ca + a, cb + b, d)) for a, b in loop] for loop, d in
         ((outer, d0), (outer, d1), (inner, d0), (inner, d1))]
    o0, o1, i0, i1 = L
    idx, iidx = p.mi(mat), p.mi(inner_mat or mat)
    for i in range(len(outer)):
        j = (i + 1) % len(outer)
        bm.faces.new((o1[i], o1[j], i1[j], i1[i])).material_index = idx
        bm.faces.new((o0[j], o0[i], i0[i], i0[j])).material_index = idx
        bm.faces.new((o0[i], o0[j], o1[j], o1[i])).material_index = idx
        bm.faces.new((i0[j], i0[i], i1[i], i1[j])).material_index = iidx


def framed_recess(p, face, inset, clip, depth, floor_mat, wall_mat=None):
    """Vanilla face treatment: frame border, then a recessed panel with clipped corners. Returns the floor face."""
    bm = p.bm
    c, n = face.calc_center_median(), face.normal.copy()
    bmesh.ops.inset_region(bm, faces=[face], thickness=inset, depth=0.0, use_even_offset=True)
    if clip > 0:
        bmesh.ops.bevel(bm, geom=list(face.verts), offset=clip, segments=1, profile=0.5, affect="VERTICES")
        bm.normal_update()
        face = min((f for f in bm.faces if f.normal.dot(n) > 0.999),
                   key=lambda f: (f.calc_center_median() - c).length)
    walls = bmesh.ops.inset_region(bm, faces=[face], thickness=0.0, depth=-depth)["faces"]
    face.material_index = p.mi(floor_mat)
    if wall_mat:
        for w in walls:
            w.material_index = p.mi(wall_mat)
    bm.normal_update()
    return face


def biggest_face(p, n):
    n = Vector(n)
    return max((f for f in p.bm.faces if f.normal.dot(n) > 0.999), key=lambda f: f.calc_area())


# ---------------------------------------------------------------- cut-in detail (layered plates with holes)

def _ccw(loop):
    s = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(loop, loop[1:] + loop[:1]))
    return loop if s > 0 else list(reversed(loop))


def plate(p, F, outer, holes, d0, d1, mat, wall_mat=None, bottom=True):
    """A closed plate on face F between depths d0 < d1 whose outline is `outer` with through-`holes` (lists of
    (a, b) points). Holes are real openings with walls (slots, pockets, louvre openings, notches): Keen's fine
    detail is cut in, not added. Top/bottom are triangle-filled around the holes. Returns the top faces."""
    bm = p.bm
    loops = [_ccw(list(outer))] + [list(reversed(_ccw(list(h)))) for h in holes]
    top, bot = [], []
    for lp in loops:
        top.append([bm.verts.new(F(a, b, d1)) for a, b in lp])
        bot.append([bm.verts.new(F(a, b, d0)) for a, b in lp])

    def fill(rings, flip):
        edges = []
        for r in rings:
            for i in range(len(r)):
                edges.append(bm.edges.new((r[i], r[(i + 1) % len(r)])))
        res = bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=edges,
                                      normal=-F.n if flip else F.n)
        fs = [g for g in res["geom"] if isinstance(g, bmesh.types.BMFace)]
        for f in fs:
            f.material_index = p.mi(mat)
            f.normal_update()
            if (f.normal.dot(F.n) < 0) != flip:
                f.normal_flip()
        return fs

    tops = fill(top, False)
    if bottom:
        fill(bot, True)
    wm = p.mi(wall_mat or mat)
    for lt, lb in zip(top, bot):
        for i in range(len(lt)):
            j = (i + 1) % len(lt)
            f = bm.faces.new((lb[i], lb[j], lt[j], lt[i]))
            f.material_index = wm
    bm.normal_update()
    return tops


def rect(a0, a1, b0, b1):
    return [(a0, b0), (a1, b0), (a1, b1), (a0, b1)]


def slot(ac, bc, length, width, along_b=False, n=0):
    """A slot outline centred at (ac, bc); with n > 1 the ends are rounded with n segments each."""
    if along_b:
        length, width = width, length
    if n <= 1:
        return rect(ac - length / 2, ac + length / 2, bc - width / 2, bc + width / 2)
    pts = []
    r = min(length, width) / 2
    if length >= width:
        for side, c0 in ((1, ac + length / 2 - r), (-1, ac - length / 2 + r)):
            for k in range(n + 1):
                t = -math.pi / 2 + math.pi * k / n
                pts.append((c0 + side * r * math.cos(t), bc + side * r * math.sin(t)))
    else:
        for side, c0 in ((1, bc + width / 2 - r), (-1, bc - width / 2 + r)):
            for k in range(n + 1):
                t = math.pi * k / n
                pts.append((ac + side * r * math.cos(t), c0 + side * r * math.sin(t)))
    return pts


def poly_prism_y(p, pts_xz, y0, y1, mat, cap_mat=None):
    """Extrude an (x, z) polygon along +Y from y0 to y1 (long members: posts, longerons, bumpers)."""
    bm = p.bm
    pts = _ccw(list(pts_xz))
    a = [bm.verts.new((x, y0, z)) for x, z in pts]
    b = [bm.verts.new((x, y1, z)) for x, z in pts]
    idx = p.mi(mat)
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        bm.faces.new((a[i], a[j], b[j], b[i])).material_index = idx
    bm.faces.new(list(reversed(a))).material_index = p.mi(cap_mat or mat)
    bm.faces.new(b).material_index = p.mi(cap_mat or mat)
    return a, b


def louvre(p, F, a0, a1, b0, b1, d, n, mat, angle=38, t=0.006, along_b=False):
    """n angled slats filling the opening [a0, a1] x [b0, b1] (slats run along a; along b with along_b), centred
    at depth d below the opening. Each slat overlaps its neighbour in view, so the pocket reads as a louvre."""
    from mathutils import Matrix as _M
    if along_b:
        L, span, c_other = b1 - b0, (a0, a1), (b0 + b1) / 2
    else:
        L, span, c_other = a1 - a0, (b0, b1), (a0 + a1) / 2
    pitch = (span[1] - span[0]) / n
    w = pitch * 1.25
    for i in range(n):
        c = span[0] + pitch * (i + 0.5)
        if along_b:
            m = F.matrix(c, c_other, d) @ _M.Rotation(math.pi / 2, 4, "Y") @ _M.Rotation(math.radians(angle), 4, "X")
        else:
            m = F.matrix(c_other, c, d) @ _M.Rotation(math.radians(angle), 4, "X")
        box(p, (-L / 2, -t / 2, -w / 2), (L / 2, t / 2, w / 2), mat, m)
