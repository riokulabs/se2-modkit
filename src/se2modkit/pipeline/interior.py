"""What a broken block shows inside. Plain Python: the exporter runs it on the cut Fractured LOD0 (export.json
fractured.interior).

The standard (Keen's way): a fracture zone's inside is a SOLID CORE whose exposed faces are vanilla
FracturedMaterial01 (the cut caps, and the faces of the family's core parts, `hooks.core_parts`), plus only a few
large, recognisable parts you would see through the hole (a fan wall, a coil face, busbars and breaker rows). No zone
may open onto an empty hollow, and the inside is not a modelled machine (pipes, valves, boards, looms).

The INTERIOR of a block is its model bounds shrunk by INSET (at most a quarter of the extent) on every side: an outer
skin, a cover stack or a liner lies within a few centimetres of the bounds and never counts; what lies deeper is
behind the skin.
A zone EXPOSES the interior when at least EXPOSE_SHARE of its box lies inside the interior: when it dies, the player
looks into that volume. For each zone the survey records
  interior_tris   triangles of the zone's own model (skin, hide, support; not its break faces) whose centroid lies in
                  the interior: the modelled detail behind the skin;
  core_m2         the zone's break-face area (caps and core faces, FracturedMaterial01) inside the interior;
  exposes         whether the zone exposes the interior (above);
  cover           what its death leaves to see: across each of its axis-aligned seams, inside the interior, the
                  share of a STEP grid on the seam plane from which a ray into the neighbours meets a face within
                  DEPTH (a break face, a core face or a part; the dead zone's own faces are gone). An empty hollow
                  scores near 0, a solid core near 1. None when no seam crosses the interior;
  seam_cover      the same per seam: [[the neighbours across it], cover];
  cap_plane_m2    the largest single break-face plane of the zone (informational: a big flat FracturedMaterial01
                  face is Keen's look, not a fault).
The rule (faults): every exposing zone has cover >= MIN_COVER, and the exposing zones together carry at most
MAX_DETAIL_TRIS interior triangles.
"""

import math

__all__ = ["DEPTH", "EXPOSE_SHARE", "INSET", "LEAD", "MAX_DETAIL_TRIS", "MIN_COVER", "STEP", "cap_plane_area",
           "faults", "inner_box", "survey", "zone_cover"]

INSET = 0.10            # m: deeper than any skin, liner or cover stack; at most a quarter of the block's extent on
#                         that axis
EXPOSE_SHARE = 0.5      # a zone whose box is at least half interior exposes the interior when it dies (a plinth or
#                         top band slab is not: its death shows the body's underside or roof)
STEP = 0.03             # m: the cover grid on a seam plane
DEPTH = 0.10            # m: a ray into the neighbour that meets nothing this deep looks into a hollow
LEAD = 0.03             # m: the rays start this far on the dead zone's side (the break is jagged about the plane)
MIN_COVER = 0.8         # an exposing zone's death must leave at least this much of its seams' interior covered
MAX_DETAIL_TRIS = 800   # interior triangles of all exposing zones together: a few large parts, not a machine


def inner_box(lo, hi, inset=INSET):
    """(lo, hi) of the interior, or None when the block is too thin to have one (trays, tiles, signs)."""
    e = [min(inset, (hi[i] - lo[i]) / 4) for i in range(3)]
    a = [lo[i] + e[i] for i in range(3)]
    b = [hi[i] - e[i] for i in range(3)]
    if any(b[i] - a[i] <= 0.02 for i in range(3)):
        return None
    return a, b


def _inside(p, box):
    return all(box[0][i] <= p[i] <= box[1][i] for i in range(3))


def _bounds(tris):
    pts = [p for t in tris for p in t]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def _vol(lo, hi):
    return max(0.0, hi[0] - lo[0]) * max(0.0, hi[1] - lo[1]) * max(0.0, hi[2] - lo[2])


def _area_normal(t):
    u = [t[1][i] - t[0][i] for i in range(3)]
    v = [t[2][i] - t[0][i] for i in range(3)]
    c = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    m = math.sqrt(sum(x * x for x in c))
    return m / 2, tuple(x / m for x in c) if m else (0.0, 0.0, 0.0)


def _centroid(t):
    return [sum(p[i] for p in t) / 3 for i in range(3)]


def cap_plane_area(cut_tris, q=0.005):
    """The largest total cap area in one plane (facing rounded to 0.01, depth to q)."""
    planes = {}
    for t in cut_tris:
        a, n = _area_normal(t)
        if not a:
            continue
        d = sum(n[i] * t[0][i] for i in range(3))
        k = (tuple(round(x, 2) for x in n), round(d / q))
        planes[k] = planes.get(k, 0.0) + a
    return max(planes.values(), default=0.0)


def _axis(n):
    for i in range(3):
        if abs(abs(n[i]) - 1.0) < 1e-6:
            return i
    return None


def _seam_cover(ax, c, into, rect, tris):
    """(hits, samples) on the plane x[ax] = c over rect ((u0, v0), (u1, v1)) on the other two axes: a ray from
    c - into * LEAD along `into` (+1 / -1 on ax) hits when a triangle of `tris` covers the sample point within
    DEPTH of the plane."""
    u, v = [i for i in range(3) if i != ax]
    (u0, v0), (u1, v1) = rect
    nu, nv = int((u1 - u0) / STEP), int((v1 - v0) / STEP)
    if nu < 1 or nv < 1:
        return 0, 0
    lo_d, hi_d = (c - LEAD, c + DEPTH) if into > 0 else (c - DEPTH, c + LEAD)
    cell = 4 * STEP
    grid = {}
    for t in tris:
        ds = [p[ax] for p in t]
        if max(ds) < lo_d or min(ds) > hi_d:
            continue
        pu, pv = [p[u] for p in t], [p[v] for p in t]
        if max(pu) < u0 or min(pu) > u1 or max(pv) < v0 or min(pv) > v1:
            continue
        det = (pu[1] - pu[0]) * (pv[2] - pv[0]) - (pu[2] - pu[0]) * (pv[1] - pv[0])
        if abs(det) < 1e-12:        # edge-on to the ray: it does not cover anything
            continue
        e = (pu, pv, ds, det)
        for i in range(int((min(pu) - u0) // cell), int((max(pu) - u0) // cell) + 1):
            for j in range(int((min(pv) - v0) // cell), int((max(pv) - v0) // cell) + 1):
                grid.setdefault((i, j), []).append(e)
    hits = 0
    for a in range(nu):
        su = u0 + (a + 0.5) * (u1 - u0) / nu
        for b in range(nv):
            sv = v0 + (b + 0.5) * (v1 - v0) / nv
            for pu, pv, ds, det in grid.get((int((su - u0) // cell), int((sv - v0) // cell)), ()):
                l1 = ((pv[2] - pv[0]) * (su - pu[0]) - (pu[2] - pu[0]) * (sv - pv[0])) / det
                l2 = ((pu[1] - pu[0]) * (sv - pv[0]) - (pv[1] - pv[0]) * (su - pu[0])) / det
                if l1 < -1e-9 or l2 < -1e-9 or l1 + l2 > 1 + 1e-9:
                    continue
                d = ds[0] + l1 * (ds[1] - ds[0]) + l2 * (ds[2] - ds[0])
                if lo_d <= d <= hi_d:
                    hits += 1
                    break
    return hits, nu * nv


def zone_cover(z, box, zbox, seams, others, nbox):
    """(cover, [[neighbour ids, cover] per seam]) of zone z (see module doc). zbox: z's (lo, hi); seams:
    [(o, n, A ids, B ids)] (A on the +n side, as fracture.Seam); others: every other zone's triangles; nbox: {zone:
    (lo, hi)}."""
    hits = samples = 0
    per = []
    for o, n, a_ids, b_ids in seams:
        ax = _axis(n)
        if ax is None or (z not in a_ids and z not in b_ids):
            continue
        c = o[ax]
        if not box[0][ax] < c < box[1][ax]:
            continue
        side = 1.0 if z in a_ids else -1.0          # z lies on the +n side when in A
        into = -side * (1.0 if n[ax] > 0 else -1.0)  # +1 / -1 along ax, from z into its neighbours
        ids = list(b_ids if z in a_ids else a_ids)
        sh = ss = 0
        for nlo, nhi in [nbox[k] for k in ids if k in nbox]:
            r = [[max(zbox[0][i], nlo[i], box[0][i]) for i in range(3)],
                 [min(zbox[1][i], nhi[i], box[1][i]) for i in range(3)]]
            u, v = [i for i in range(3) if i != ax]
            h, s_ = _seam_cover(ax, c, into, ((r[0][u], r[0][v]), (r[1][u], r[1][v])), others)
            sh, ss = sh + h, ss + s_
        if ss:
            per.append([ids, round(sh / ss, 3)])
        hits, samples = hits + sh, samples + ss
    return (round(hits / samples, 3) if samples else None), per


def survey(lo, hi, body, cuts, seams=()):
    """{zone: {interior_tris, core_m2, exposes, cover, seam_cover, cap_plane_m2}} for zone -> triangles (body: the
    zone's model without its break faces; cuts: its break faces, caps and core faces), lo / hi the model bounds, seams
    the plan's [(o, n, A zone ids, B zone ids)]."""
    box = inner_box(lo, hi)
    zones = sorted(set(body) | set(cuts))
    allt = {z: body.get(z, []) + cuts.get(z, []) for z in zones}
    zb = {z: _bounds(t) for z, t in allt.items() if t}
    out = {}
    for z in zones:
        tris = body.get(z, [])
        n, core, exposes, cover, per = 0, 0.0, False, None, []
        if box and allt[z]:
            n = sum(1 for t in tris if _inside(_centroid(t), box))
            core = sum(_area_normal(t)[0] for t in cuts.get(z, []) if _inside(_centroid(t), box))
            zl, zh = zb[z]
            ol = [max(zl[i], box[0][i]) for i in range(3)]
            oh = [min(zh[i], box[1][i]) for i in range(3)]
            v = _vol(zl, zh)
            exposes = bool(v) and _vol(ol, oh) / v >= EXPOSE_SHARE
            others = [t for k in zones if k != z for t in allt[k]]
            cover, per = zone_cover(z, box, zb[z], seams, others, {k: b for k, b in zb.items() if k != z})
        out[z] = {"interior_tris": n, "core_m2": round(core, 4), "exposes": exposes, "cover": cover,
                  "seam_cover": per, "cap_plane_m2": round(cap_plane_area(cuts.get(z, [])), 4)}
    return out


def faults(rows):
    """[(zone, reason)] breaking the rule for one block's survey rows."""
    bad = []
    for z, r in sorted(rows.items()):
        if not r["exposes"]:
            continue
        c = r.get("cover")
        if c is None or c < MIN_COVER:
            bad.append((z, f"opens onto an empty hollow: cover {c} (< {MIN_COVER})"))
    detail = sum(r["interior_tris"] for r in rows.values() if r["exposes"])
    if detail > MAX_DETAIL_TRIS:
        bad.append(("*", f"modelled interior: {detail} interior tris in the exposing zones (> {MAX_DETAIL_TRIS})"))
    return bad
