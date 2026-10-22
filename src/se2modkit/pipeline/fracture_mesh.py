"""The Blender half of `fracture`: cut a LOD along the plan's seams, close every cut cross-section with a break face
(FracturedMaterial01), and dent a copy for the Damaged model. Used by the exporter; no file I/O. Blender only.

cut_lod(hooks, objs, plan, lod, coll, lod_prefix, name) -> (whole, caps)
    whole  one object, every face tagged with its zone (face attribute se2_zone, 1-based), its island
           (se2_island) and its construction role (se2_role, fracture.role_of); the cut vertices carry the seam they
           lie on (point attribute se2_seam, 1-based).
    caps   {zone index: object} the break faces of that zone, in FracturedMaterial01, UVs projected on the seam
           plane at vanilla's density (fracture.CUT_UV_PER_M); a cap face of a hidden part (role hide: a cable)
           carries se2_role = hide (else 0), so the exporter puts it in the zone's _Hide group with its part.
           The faces of the family's core parts (`hooks.core_parts`: the solid inside, Keen's way) join their zone's
           caps, FracturedMaterial01 with box UVs at the same density (_as_core, _core_to_caps).
How a LOD object is cut, per seam (fracture.Seam):
  1. islands (connected face sets) of a Part listed in plan.keep_whole are not cut: a strut, a screw, a handle goes
     whole to the zone holding its bounding-box centre; where the plan sets long_cut, a hardware island reaching that
     far past the plane on both sides is cut after all (a long slide rail, not a screw);
  2. any other island that reaches fracture.CUT_MIN past the plane on both sides is bisected, only where its faces
     lie in one of the seam's two zones (a seam between a hub and an arm does not slice the next arm);
  3. the long cut edges are split every `hooks.jag[family]["step"]`; then the cut islands' vertices near the plane
     move along the seam normal by a smooth noise of their plane position (+- seam.jag), faded out over seam.reach:
     one shared ragged break, the intact model stays closed and nothing folds (seam.jag < seam.reach);
  4. a face of a cut island goes to the zone of its centre; an uncut island goes whole to the zone of its centre.
A cap is made per (zone, seam, island) from the boundary that zone gets along the cut: its cut edges are closed
loops on a solid (a strand, a rod, a wall, a tub) and are filled (bmesh triangle_fill: nested loops become holes); an
open polyline (a single-sided sheet) gets no cap, as vanilla leaves sheet edges open.

densify(objs, max_edge, budget) / dent(hooks, objs, family, lo, hi, seed): the Damaged model (`hooks.dent`).
strip_attributes removes the se2_* bookkeeping (and the CAPS_PROP / SCAFFOLD_PROP properties) before an FBX export.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector, noise

from ..model import objects as OB
from ..model.materials import get_mat
from . import fracture as FR

ZONE, ISLAND, SEAM, CUTISL = "se2_zone", "se2_island", "se2_seam", "se2_cutisl"
ROLE = "se2_role"           # face int: the construction role (1-based fracture.ROLE_IDS index), fracture.role_of
CAPS_PROP = "se2_caps"      # object property on a cut LOD: [closed, open] cut boundary counts (build_caps)
SCAFFOLD_PROP = "se2_scaffold"   # object property: the construction scaffold (left out of the Damaged copy)


def part_name(ob, lod_prefix):
    n = ob.name.split(".")[0]
    return n[len(lod_prefix):] if n.startswith(lod_prefix) else n


def islands(bm):
    """Connected face sets (faces sharing a vertex), as lists of BMFace."""
    bm.faces.ensure_lookup_table()
    seen = set()
    out = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, comp = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            comp.append(g)
            for v in g.verts:
                for h in v.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        out.append(comp)
    return out


def _d(co, s):
    return (co[0] - s.o[0]) * s.n[0] + (co[1] - s.o[1]) * s.n[1] + (co[2] - s.o[2]) * s.n[2]


def _centre(faces):
    vs = {v for f in faces for v in f.verts}
    lo = [min(v.co[i] for v in vs) for i in range(3)]
    hi = [max(v.co[i] for v in vs) for i in range(3)]
    return tuple((a + b) / 2 for a, b in zip(lo, hi))


# ---------------------------------------------------------------------------------------------------- seams
def snap_seams(hooks, plan, objs, lod_prefix):
    """Move each snappable seam (axis-aligned, seam.snap > 0) into the widest vertex-free gap within +-snap of its
    nominal place (measured on LOD0, all cuttable Parts in the seam's two zones), then set its break: amplitude
    jag amp, faded out over jag reach x amp, the reach kept inside the model's bounding faces across the seam (ports
    and mount faces never move); a reach shorter than the amplitude lowers the amplitude (no fold)."""
    jag = hooks.jag[plan.family]
    comps = []                      # (vertex positions) of every cuttable island of LOD0
    for ob in objs:
        if ob.type != "MESH" or part_name(ob, lod_prefix) in plan.keep_whole:
            continue
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bm.transform(ob.matrix_world)
        for comp in islands(bm):
            comps.append([Vector(v.co) for v in {v for f in comp for v in f.verts}])
        bm.free()
    pts = [w for c in comps for w in c]
    allv_lo = [min(w[i] for w in pts) for i in range(3)] if pts else [0, 0, 0]
    allv_hi = [max(w[i] for w in pts) for i in range(3)] if pts else [0, 0, 0]
    for k, s in enumerate(plan.seams):
        ax = s.axis()
        cos = [w for c in comps for w in c if plan.zone_index(w) in s.A + s.B]
        if s.snap and ax is not None and cos:
            p0 = s.o[ax]
            xs = sorted({round(w[ax], 6) for w in cos if abs(w[ax] - p0) <= s.snap + 0.05})
            best = None
            for a, b in zip(xs, xs[1:]):
                lo, hi = max(a, p0 - s.snap), min(b, p0 + s.snap)
                if hi <= lo:
                    continue
                g = b - a
                mid = min(max((a + b) / 2, p0 - s.snap), p0 + s.snap)
                score = (min(mid - a, b - mid), -abs(mid - p0))
                if best is None or score > best[0]:
                    best = (score, mid, g)
            if best:
                plan.move_seam(k, (best[1] - p0) / s.n[ax])
        # the break's reach: jag["reach"] x its amplitude, but never to the model's bounding faces (ports, mounts)
        ax = s.axis()
        to_box = min(abs(allv_lo[ax] - s.o[ax]), abs(allv_hi[ax] - s.o[ax])) if (ax is not None and pts) else 1.0
        s.jag = jag["amp"]
        s.reach = min(jag["reach"] * jag["amp"], 0.8 * to_box)
        if s.reach <= s.jag * 1.05:
            s.jag = s.reach / 1.25
    return plan


def _straddles(f, s, eps=1e-7):
    ds = [_d(v.co, s) for v in f.verts]
    return min(ds) < -eps and max(ds) > eps


def cut_object(hooks, ob, plan, part, gid0, lod=0, ragged=True):
    """Cut one LOD object in place (see module doc). Returns the next free island id. ragged=False: a straight cut
    on the seam planes, no jag (standins.cut_standin: a baked stand-in's faces then stay in their zone, and a face
    lying on a seam plane keeps the tie rule's zone instead of being lifted into the next one)."""
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.transform(ob.matrix_world)
    ob.matrix_world = Matrix.Identity(4)
    zl = bm.faces.layers.int.get(ZONE) or bm.faces.layers.int.new(ZONE)
    il = bm.faces.layers.int.get(ISLAND) or bm.faces.layers.int.new(ISLAND)
    cl = bm.faces.layers.int.get(CUTISL) or bm.faces.layers.int.new(CUTISL)
    sl = bm.verts.layers.int.get(SEAM) or bm.verts.layers.int.new(SEAM)
    whole = part in plan.keep_whole
    jag = hooks.jag[plan.family] if ragged else dict(hooks.jag[plan.family], step=0)
    if whole and not plan.long_cut:
        lim = None
    else:     # hardware is cut only where one long piece really spans the break (a drawer's slide members)
        lim = plan.long_cut if whole else FR.CUT_MIN
    for k, s in enumerate(plan.seams):
        if lim is None:
            break
        sel = set()
        for comp in islands(bm):
            ds = [_d(v.co, s) for f in comp for v in f.verts]
            if not (min(ds) <= -lim and max(ds) >= lim):
                continue
            zs = {plan.zone_index(f.calc_center_median()) for f in comp}
            if not zs & set(s.A + s.B):
                continue
            for f in comp:         # split per face even when it already parts on the plane (segments meeting
                f[cl] = 1          # exactly there: nothing to bisect, but it is not one piece)
            sel.update(f for f in comp if _straddles(f, s) and plan.zone_index(f.calc_center_median()) in s.A + s.B)
        if not sel or (plan.edge_split and lod == 0):
            continue
        geom = list(sel) + list({e for f in sel for e in f.edges}) + list({v for f in sel for v in f.verts})
        res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-7, plane_co=Vector(s.o), plane_no=Vector(s.n))
        for g in res["geom_cut"]:
            if isinstance(g, bmesh.types.BMVert):
                g[sl] = k + 1
        # a sliver n-gon (a plate's bridge face round its holes) can keep both new cut vertices without being split:
        # split it between them, or it stays one face across the break (and goes whole to one zone)
        for f in [f for f in bm.faces if _straddles(f, s, 1e-5)]:
            on = [v for v in f.verts if v[sl] == k + 1 and abs(_d(v.co, s)) < 1e-6]
            if len(on) == 2:
                bmesh.ops.connect_verts(bm, verts=on)
        step = jag["step"] and lod <= 1 and jag["step"] * (1 + 2 * lod)   # (LOD1: a coarser break; LOD2+: none)
        if step and s.jag > 0:   # split the long cut edges so the break line can be jagged
            long_ = [e for e in bm.edges if e.verts[0][sl] == k + 1 and e.verts[1][sl] == k + 1
                     and e.calc_length() > 1.5 * step and len(e.link_faces) == 2]
            by_cuts = {}
            for e in long_:
                by_cuts.setdefault(int(math.ceil(e.calc_length() / step)) - 1, []).append(e)
            for cuts, es in sorted(by_cuts.items()):
                r = bmesh.ops.subdivide_edges(bm, edges=es, cuts=cuts, use_grid_fill=False)
                for g in r["geom_inner"]:
                    if isinstance(g, bmesh.types.BMVert):
                        g[sl] = k + 1
    gid = gid0
    for comp in islands(bm):
        if any(f[cl] for f in comp):
            for f in comp:
                f[zl] = plan.zone_index(f.calc_center_median()) + 1
        else:
            z = plan.zone_index(_centre(comp)) + 1
            for f in comp:
                f[zl] = z
        for f in comp:
            f[il] = gid
        gid += 1
    edge = plan.edge_split and lod == 0      # (LOD0 decides Keen's LOD count; the lower LODs keep the bisect)
    if edge:              # the zones part along existing edges: mark them as the seam's cut line (caps follow)
        pair = {frozenset((a, b)): k for k, s in enumerate(plan.seams) for a in s.A for b in s.B}
        for e in bm.edges:
            fs = e.link_faces
            if len(fs) == 2 and fs[0][zl] != fs[1][zl] and fs[0][cl]:
                k = pair.get(frozenset((fs[0][zl] - 1, fs[1][zl] - 1)))
                if k is not None:
                    for v in e.verts:
                        v[sl] = v[sl] or k + 1
    rl = bm.faces.layers.int.get(ROLE) or bm.faces.layers.int.new(ROLE)
    mats = [m.name if m else "" for m in me.materials]
    for f in bm.faces:        # (after the bisects: the new faces keep their material, so their role is the same)
        f[rl] = FR.ROLE_IDS.index(FR.role_of(hooks, plan.family, part, mats[f.material_index])) + 1
    cut_verts = {v for f in bm.faces if f[cl] for v in f.verts} if not edge else set()
    for k, s in enumerate(plan.seams):   # the ragged break: a smooth field along the normal, faded with the distance
        if s.jag <= 0 or not ragged:     # from the plane (no fold: |J| < reach); the cut vertices are shared, so the
            continue                     # two zones keep matching and the intact model stays closed
        n = Vector(s.n)
        for v in cut_verts:
            d = _d(v.co, s)
            if abs(d) < s.reach:
                v.co += n * (jag_offset(Vector(v.co) - n * d, s, k, jag) * (1.0 - abs(d) / s.reach))
    bm.to_mesh(me)
    bm.free()
    return gid


def jag_offset(q, s, k, jag):
    """The break's offset along the seam normal at plane point q: two octaves of Perlin noise, +- s.jag."""
    a = noise.noise(q * jag["freq"] + Vector((17.3 * (k + 1), 5.1, -9.7)))
    b = noise.noise(q * jag["freq"] * 3.1 + Vector((-3.3, 11.9 * (k + 1), 2.2)))
    return s.jag * max(-1.0, min(1.0, 1.7 * (0.72 * a + 0.28 * b)))


def cut_lod(hooks, objs, plan, lod, coll, lod_prefix, name, caps_min=None):
    """Copies of a LOD's objects, cut and tagged, joined into one object; plus the per-zone caps. caps_min: cap only
    cut loops at least this long (None: the plan's rule, every loop but thin wire ends on a light LOD0)."""
    pieces, gid = [], 1
    core = hooks.core_parts.get(plan.family, ())
    for ob in objs:
        if ob.type != "MESH" or not len(ob.data.polygons):
            continue
        n = ob.copy()
        n.data = ob.data.copy()
        for k in list(n.keys()):
            del n[k]
        coll.objects.link(n)
        if part_name(ob, lod_prefix) in core:
            _as_core(n)
        gid = cut_object(hooks, n, plan, part_name(ob, lod_prefix), gid, lod)
        pieces.append(n)
    whole = OB.join(pieces, name)
    caps = build_caps(whole, plan, coll, name, thin_caps=plan.thin_caps or lod > 0, min_len=caps_min)
    if core:
        caps = _core_to_caps(whole, caps, coll, name)
    for o in [whole] + list(caps.values()):
        drop_degenerate(o)
    return whole, caps


def _as_core(ob):
    """A core-part copy as the broken core: every face FracturedMaterial01, box-projected UVs at vanilla's cut
    density (fracture.CUT_UV_PER_M, as the caps)."""
    me = ob.data
    me.materials.clear()
    me.materials.append(get_mat(FR.CUT_MATERIAL))
    for p in me.polygons:
        p.material_index = 0
    while me.uv_layers:
        me.uv_layers.remove(me.uv_layers[0])
    OB.world_box_uv(me, FR.CUT_UV_PER_M)


def _core_to_caps(whole, caps, coll, name):
    """Move the core faces (FracturedMaterial01 in `whole`: _as_core) into their zone's break-face object, so they
    carry the cut faces' group and construction props and count as break faces; a zone with core faces but no cap
    gets a new one. Returns caps."""
    me = whole.data
    idx = {i for i, m in enumerate(me.materials) if m and m.name.split(".")[0] == FR.CUT_MATERIAL}
    if not idx:
        return caps
    bm = bmesh.new()
    bm.from_mesh(me)
    zl = bm.faces.layers.int[ZONE]
    uv = bm.loops.layers.uv.get("UVMap") or bm.loops.layers.uv.active
    core = [f for f in bm.faces if f.material_index in idx]
    by_zone = {}
    for f in core:
        by_zone.setdefault(f[zl] - 1, []).append(f)
    mat = get_mat(FR.CUT_MATERIAL)
    for zi, faces in sorted(by_zone.items()):
        cb = bmesh.new()
        if zi in caps:
            cb.from_mesh(caps[zi].data)
        sl = cb.faces.layers.int.get(SEAM) or cb.faces.layers.int.new(SEAM)
        cr = cb.faces.layers.int.get(ROLE) or cb.faces.layers.int.new(ROLE)
        cu = cb.loops.layers.uv.get("UVMap") or cb.loops.layers.uv.new("UVMap")
        vmap = {}
        for f in faces:
            vs = []
            for v in f.verts:
                if v.index not in vmap:
                    vmap[v.index] = cb.verts.new(v.co)
                vs.append(vmap[v.index])
            try:
                g = cb.faces.new(vs)
            except ValueError:      # (a duplicate face: nothing to add)
                continue
            g.material_index, g.smooth = 0, False
            g[sl], g[cr] = 0, 0     # (a visible break face: no seam, not a hidden part's)
            for a, b in zip(g.loops, f.loops):
                a[cu].uv = b[uv].uv
        if zi in caps:
            cb.to_mesh(caps[zi].data)
        else:
            m2 = bpy.data.meshes.new(f"{name}_cut{zi + 1}")
            cb.to_mesh(m2)
            m2.materials.append(mat)
            caps[zi] = bpy.data.objects.new(m2.name, m2)
            coll.objects.link(caps[zi])
        cb.free()
    bmesh.ops.delete(bm, geom=core, context="FACES")
    bm.to_mesh(me)
    bm.free()
    return caps


def drop_degenerate(ob):
    """Triangulate the n-gons and delete the zero-area triangles: a bisect leaves a collinear extra vertex on the
    faces next to a cut, whose triangulation gives slivers Keen's importer drops; our triangle counts must be the
    importer's, since the LOD count is keyed on them."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    ng = [f for f in bm.faces if len(f.verts) > 4]
    if ng:
        bmesh.ops.triangulate(bm, faces=ng, quad_method="BEAUTY", ngon_method="BEAUTY")
    bad = [f for f in bm.faces if f.calc_area() < 1e-9]
    if bad:
        bmesh.ops.delete(bm, geom=bad, context="FACES_ONLY")
    bm.to_mesh(ob.data)
    bm.free()


def plan_for(hooks, spec, lods, boxes):
    """The block's fracture plan with its seams snapped on the built LOD0 and its colliders fitted (None for an
    unfractured block). lods: [(collection, objects)] per LOD."""
    plan = FR.plan(hooks, spec, boxes)
    if plan is None:
        return None
    snap_seams(hooks, plan, lods[0][1], f"{spec.name}_LOD0_")
    plan.fit_hulls(boxes)
    return plan


def lod_tris(res):
    """Triangles of a cut LOD (cut_lod's (whole, caps)): the joined object plus its caps."""
    whole, caps = res
    return tris(whole) + sum(tris(c) for c in caps.values())


_total = lod_tris   # the legacy name


def zone_points(whole, caps, n_zones):
    """[[(x, y, z)] per zone]: the vertices of a cut LOD's faces of each zone (skin, decals, lamps) and of its break
    faces, in the block frame (what fracture.shaped_hull fits the zone's collider to)."""
    out = [set() for _ in range(n_zones)]
    me = whole.data
    za = me.attributes[ZONE].data
    mw = whole.matrix_world
    co = [tuple(mw @ v.co) for v in me.vertices]
    for poly in me.polygons:
        zi = za[poly.index].value - 1
        if 0 <= zi < n_zones:
            out[zi].update(co[i] for i in poly.vertices)
    for zi, c in caps.items():
        out[zi].update(tuple(c.matrix_world @ v.co) for v in c.data.vertices)
    return [sorted(s) for s in out]


def hull_object(name, verts, coll):
    """A convex mesh object through verts (the zone collider; glTF node name = the fracture Group)."""
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old)
    bm = bmesh.new()
    for v in verts:
        bm.verts.new(v)
    bmesh.ops.convex_hull(bm, input=list(bm.verts))
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    if ob.name != name:
        raise RuntimeError(f"collider {name}: Blender renamed it {ob.name} (a name clash in the scene)")
    return ob


# ---------------------------------------------------------------------------------------------------- caps
def _loops_ok(edges):
    deg = {}
    for a, b in edges:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    return bool(deg) and all(d == 2 for d in deg.values())


def _components(edges):
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, out = set(), []
    for v in adj:
        if v in seen:
            continue
        stack, comp = [v], set()
        while stack:
            u = stack.pop()
            if u in comp:
                continue
            comp.add(u)
            stack += adj[u]
        seen |= comp
        out.append(comp)
    return out


def build_caps(whole, plan, coll, name, thin_caps=True, min_len=None):
    """{zone index: cap object} (see module doc). Also records whole[CAPS_PROP] = closed / open boundary counts."""
    bm = bmesh.new()
    bm.from_mesh(whole.data)
    zl, il = bm.faces.layers.int[ZONE], bm.faces.layers.int[ISLAND]
    rl = bm.faces.layers.int.get(ROLE)
    sl = bm.verts.layers.int[SEAM]
    hide = FR.ROLE_IDS.index("hide") + 1
    buckets, hidden = {}, set()     # hidden: the buckets whose part is hidden under construction (role hide)
    for e in bm.edges:
        k = e.verts[0][sl] - 1
        if k < 0 or e.verts[1][sl] - 1 != k or len(e.link_faces) != 2:
            continue
        f1, f2 = e.link_faces
        if f1[zl] == f2[zl] or f1[il] != f2[il]:
            continue
        for f in (f1, f2):
            key = (f[zl] - 1, k, f[il])
            buckets.setdefault(key, []).append((e.verts[0].index, e.verts[1].index))
            if rl is not None and f[rl] == hide:
                hidden.add(key)
    bm.verts.ensure_lookup_table()
    pos = {v.index: Vector(v.co) for v in bm.verts}
    bm.free()
    caps, stats = {}, {"closed": 0, "open": 0}
    out = {}
    for (zi, k, isl), edges in sorted(buckets.items()):
        s = plan.seams[k]
        want = Vector(s.n) * (-1.0 if zi in s.A else 1.0)     # the cap faces out of its zone, toward the gone one
        good = [c for c in _components(edges) if _loops_ok([e for e in edges if e[0] in c])]
        lim = max(FR.THIN_CAP if not thin_caps else 0.0, min_len or 0.0)
        if lim:
            good = [c for c in good if sum((pos[a] - pos[b]).length for a, b in edges if a in c) >= lim]
        stats["closed"] += len(good)
        stats["open"] += len(_components(edges)) - len(good)
        if not good:
            continue
        keep = set().union(*good)
        if zi not in caps:
            caps[zi] = bmesh.new()
            caps[zi].faces.layers.int.new(SEAM)
            caps[zi].faces.layers.int.new(ROLE)
        cb = caps[zi]
        kl, cr = cb.faces.layers.int[SEAM], cb.faces.layers.int[ROLE]
        vmap = {i: cb.verts.new(pos[i]) for i in keep}
        es = [cb.edges.new((vmap[a], vmap[b])) for a, b in edges if a in keep and b in keep]
        r = bmesh.ops.triangle_fill(cb, use_beauty=True, use_dissolve=False, edges=es, normal=want)
        for f in r["geom"]:
            if isinstance(f, bmesh.types.BMFace):
                f.normal_update()
                if f.normal.dot(want) < 0:
                    f.normal_flip()
                f.smooth = False
                f[kl] = k + 1
                f[cr] = hide if (zi, k, isl) in hidden else 0   # (the exporter: hidden caps -> _Hide)
    mat = get_mat(FR.CUT_MATERIAL)
    for zi, cb in caps.items():
        loose = [e for e in cb.edges if not e.link_faces]
        if loose:
            bmesh.ops.delete(cb, geom=loose, context="EDGES")
        if not cb.faces:
            cb.free()
            continue
        uv = cb.loops.layers.uv.new("UVMap")
        kl = cb.faces.layers.int[SEAM]
        for f in cb.faces:
            s = plan.seams[f[kl] - 1]
            # projected on the seam plane; a cap face tilted more than 15 deg off it (a jagged cut's flank, the cap
            # of a thin member cut at an angle) on its own plane, so every cap keeps vanilla's density
            n = Vector(s.n) if abs(f.normal.dot(Vector(s.n))) >= 0.966 else f.normal
            t1 = n.orthogonal().normalized()
            t2 = n.cross(t1).normalized()
            off = 0.173 * (zi + 1) + 0.311 * f[kl]
            for lp in f.loops:
                q = lp.vert.co
                lp[uv].uv = (q.dot(t1) * FR.CUT_UV_PER_M + off, q.dot(t2) * FR.CUT_UV_PER_M + 0.5 * off)
        me = bpy.data.meshes.new(f"{name}_cut{zi + 1}")
        cb.to_mesh(me)
        cb.free()
        me.materials.append(mat)
        ob = bpy.data.objects.new(me.name, me)
        coll.objects.link(ob)
        out[zi] = ob
    whole[CAPS_PROP] = [stats["closed"], stats["open"]]
    return out


# ---------------------------------------------------------------------------------------------------- damaged
def tris(ob):
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


def densify(objs, max_edge, budget):
    """Split the longest edges (> max_edge) of the given objects' faces, longest first, while the objects' total
    triangle count stays within budget: big flat panels get vertices a dent can move. Triangulates what it touches."""
    total = sum(tris(o) for o in objs)
    if total >= budget:
        return total
    bms = []
    for o in objs:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bms.append((o, bm))
    for _ in range(12):
        cands = []
        for oi, (o, bm) in enumerate(bms):
            for e in bm.edges:
                L = e.calc_length()
                if L > max_edge and e.link_faces:
                    cands.append((L, oi, e))
        if not cands:
            break
        cands.sort(key=lambda c: -c[0])
        changed = False
        per = {}
        for L, oi, e in cands:
            # each split adds about one triangle per adjacent face (+ the triangulation of n-gons it touches)
            cost = 2 * len(e.link_faces)
            if total + cost > budget:
                break
            per.setdefault(oi, []).append(e)
            total += cost
        for oi, es in per.items():
            bm = bms[oi][1]
            es = list({e for e in es if e.is_valid})
            if not es:
                continue
            bmesh.ops.subdivide_edges(bm, edges=es, cuts=1, use_grid_fill=False)
            ng = [f for f in bm.faces if len(f.verts) > 4]
            bmesh.ops.triangulate(bm, faces=ng, quad_method="BEAUTY", ngon_method="BEAUTY")
            changed = True
        total = sum(sum(len(f.verts) - 2 for f in bm.faces) for _, bm in bms)
        if not changed or total >= budget:
            break
    for o, bm in bms:
        bm.to_mesh(o.data)
        bm.free()
    return sum(tris(o) for o in objs)


def dent_offset(co, p, lo, hi, centre):
    """The Damaged model's displacement at co (Blender frame): a smooth two-octave vector noise (coincident vertices
    move identically, so nothing tears), outward components x 0.25 (dents, not bulges), every component faded to 0
    within p["pin"] (8 mm) of the model's bounding box faces (render bounds, ports and mount faces stay put). A face
    listed in p["unpin"] (e.g. "+z": a walking deck) keeps only its outward component pinned: there the surface may
    dent inward, still never past the bounding box."""
    q = Vector(co)
    d = noise.noise_vector(q * p["freq"] + Vector((3.7, -1.3, 8.1))) * p["amp"]
    d += noise.noise_vector(q * p["fine_freq"] + Vector((-6.1, 2.9, 0.4))) * p["fine"]
    pin, unpin = p.get("pin", 0.008), p.get("unpin", ())
    out = []
    for i in range(3):
        c = d[i]
        if (q[i] - centre[i]) * c > 0:
            c *= 0.25
        a = "xyz"[i]
        dl, dh = q[i] - lo[i], hi[i] - q[i]
        if dh <= dl:   # the nearer face is +axis: free when unpinned there and the dent goes inward (c < 0)
            edge = pin if (f"+{a}" in unpin and c < 0) else dh
        else:
            edge = pin if (f"-{a}" in unpin and c > 0) else dl
        f = max(0.0, min(1.0, edge / pin))
        out.append(c * f * f * (3 - 2 * f))
    return Vector(out)


def clear_custom_normals(me):
    """Drop a mesh's custom split normals (Blender 4.1+ stores them as the "custom_normal" attribute)."""
    att = me.attributes.get("custom_normal")
    if att is not None:
        me.attributes.remove(att)
    elif getattr(me, "has_custom_normals", False):
        try:
            me.free_normals_split()
        except AttributeError:
            pass


def strip_attributes(ob):
    """Remove the se2_* bookkeeping attributes (and the CAPS_PROP / SCAFFOLD_PROP properties) before an FBX export."""
    for k in (CAPS_PROP, SCAFFOLD_PROP):
        if k in ob.keys():
            del ob[k]
    me = ob.data
    for n in (ZONE, ISLAND, SEAM, CUTISL, ROLE):
        a = me.attributes.get(n)
        if a is not None:
            me.attributes.remove(a)


def dent(hooks, objs, family, lo, hi, seed):
    """Displace every vertex of objs by dent_offset with the family's `hooks.dent` parameters, then clear custom
    normals and shade by angle, so the dents catch the light (vanilla Deformed meshes are smooth-shaded with their own
    normals). seed (fracture.dent_seed of the block) fixes the noise: every export of a block, and each of its LODs,
    dents alike. Returns {mean_mm, p90_mm, max_mm} of the displacement ({} without vertices)."""
    noise.seed_set(seed)
    p = hooks.dent[family]
    centre = [(a + b) / 2 for a, b in zip(lo, hi)]
    stats = []
    for o in objs:
        me = o.data
        mw = o.matrix_world
        inv = mw.inverted()
        for v in me.vertices:
            w = mw @ v.co
            off = dent_offset(w, p, lo, hi, centre)
            stats.append(off.length)
            v.co = inv @ (w + off)
        clear_custom_normals(me)
        for poly in me.polygons:
            poly.use_smooth = True
        me.set_sharp_from_angle(angle=math.radians(40))
        me.update()
    stats.sort()
    if not stats:
        return {}
    return {"mean_mm": round(1000 * sum(stats) / len(stats), 2), "p90_mm": round(1000 * stats[int(0.9 * len(stats))], 2),
            "max_mm": round(1000 * stats[-1], 2)}


# ---------------------------------------------------------------------------------------------------- scaffold
def scaffold_object(sp, lod, coll, name):
    """The construction scaffold of one Fractured LOD (scaffold.ScaffoldPlan): its members as open
    model.geom.cylinder tubes (no caps: every end meets a crossing member) in BrushedMetal, ungrouped (vanilla: the
    cage stays when zones die, hence the stage-1.0 damaged-only hide), with the Scaffold / AlwaysVisible /
    Scaffolding props; ob[SCAFFOLD_PROP] = 1 marks it for the Damaged copy (which leaves it out). None when the plan
    has no cage at this LOD."""
    from ..model.geom import Part, cylinder
    from . import scaffold as SC
    members = sp.lod_members.get(lod) if sp else None
    if not members:
        return None
    p = Part(name)
    for a, b in members:
        cylinder(p, a, b, sp.radius, sp.sides[lod], SC.MATERIAL, cap=False)
    ob = OB.part_to_object(p, coll, name)
    for k, v in zip(("ConstructionMeshType", "ConstructionMeshVisibility", "ConstructionMeshPreset"), SC.PROPS):
        ob[k] = v
    ob[SCAFFOLD_PROP] = 1
    return ob
