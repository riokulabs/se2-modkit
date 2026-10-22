"""Realism checks on the assembled LOD0, run with the connectivity check (`connect.check_build`): the registry-based
items of the hard-surface realism checklist. Each result is {"id", "severity", "checked", "failures": [...]}; errors
fail the build, warnings are reported only.

Implemented here: M1 M2 M3 H1 H2 H3 H5 H6 H7 H8 H9 P1 P3 B1 B2 B3 C1 C2 S1 S3 D2 L1, plus K1 (kinematic sweep of a
registered stroke). The checks a mod reviews by hand are reported as "manual" with the reason
(`hooks.realism_manual`: {check id: why}).

Blender only (mathutils).
"""

from __future__ import annotations

import math

from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..model.geom import fillet_path, nseg

BM, MD, CH, RB, DK, C, YL = ("BrushedMetal", "MetalDull", "Chrome", "Rubber", "PaintedMetalDarker",
                             "PaintedMetalColorable", "PaintedMetalYellow")
FASTENER_FINISHES = {BM, MD}
ROLE_MATS = {"hose": {RB}, "cable": {RB}, "ferrule": {BM}, "cylinder_port": {BM}, "valve_port": {BM},
             "tube": {BM}, "bulkhead": {BM}}
BEND_X_OD = {"hose": 7.0, "cable": 8.0, "tube": 3.0}
HAZARD_RANGE = 0.6


def _res(id_, severity):
    return {"id": id_, "severity": severity, "checked": 0, "failures": []}


def _tree(verts, polys):
    return BVHTree.FromPolygons(verts, polys, all_triangles=False, epsilon=0.0) if polys else None


def _structure_trees(reg, parts):
    """Per part: BVH of everything except fastener components, with face materials."""
    spans = {}
    for name in reg["fasteners"]:
        if name in reg["comps"]:
            p, s, e = reg["comps"][name]
            spans.setdefault(p.name, []).append((s, len(p.bm.verts) if e is None else e))
    out = []
    for p in parts:
        if p.empty() or p.decal:
            continue
        bm = p.bm
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        ex = spans.get(p.name, [])
        keep = [i for i in range(len(bm.verts)) if not any(s <= i < e for s, e in ex)]
        remap = {o: i for i, o in enumerate(keep)}
        verts = [bm.verts[i].co.copy() for i in keep]
        polys, mats = [], []
        for f in bm.faces:
            idx = [v.index for v in f.verts]
            if all(i in remap for i in idx):
                polys.append([remap[i] for i in idx])
                mats.append(p.mats[f.material_index] if f.material_index < len(p.mats) else None)
        t = _tree(verts, polys)
        if t:
            out.append((t, mats))
    return out


def _nearest(trees, v, maxd=0.02):
    best = (math.inf, None, None)
    for t, mats in trees:
        hit = t.find_nearest(v, maxd)
        if hit[0] is not None and hit[3] < best[0]:
            best = (hit[3], mats[hit[2]], hit[1])
    return best


def _comp_mats(reg, name):
    p, s, e = reg["comps"][name]
    bm = p.bm
    bm.verts.index_update()
    e = len(bm.verts) if e is None else e
    return {p.mats[f.material_index] for f in bm.faces if all(s <= v.index < e for v in f.verts)}


def _fasteners(reg, parts, R):
    m1, m2, b3 = R["M1"], R["M2"], R["B3"]
    trees = _structure_trees(reg, parts)
    for name in reg["fasteners"]:
        m1["checked"] += 1
        if name not in reg["comps"]:
            m1["failures"].append({"fastener": name, "error": "component missing"})
            continue
        p, s, e = reg["comps"][name]
        p.bm.verts.ensure_lookup_table()
        finish = _comp_mats(reg, name)
        best = (math.inf, None, None)
        for v in p.bm.verts[s:e]:
            hit = _nearest(trees, v.co)
            if hit[0] < best[0]:
                best = hit
        bad = sorted(m for m in finish if m not in FASTENER_FINISHES)
        if bad or best[1] in finish:
            m1["failures"].append({"fastener": name, "finish": sorted(finish), "surface": best[1],
                                   "error": "not bare metal" if bad else "same material as the surface"})
        m2["checked"] += 1
        if best[1] == DK and MD in finish:
            m2["failures"].append({"fastener": name, "warning": "MetalDull on Darker: use BrushedMetal for contrast"})
    for part, key, variant in reg["decal_fasteners"]:
        m1["checked"] += 1
        if variant != "Chrome":
            m1["failures"].append({"fastener": f"{part}:{key}", "finish": variant,
                                   "error": "bolt decal in a paint quadrant (use the Chrome quadrant)"})
    for origin, axis, r in reg["bolts"]:
        b3["checked"] += 1
        d, mat, nrm = _nearest(trees, origin, 0.01)
        if nrm is None:
            b3["failures"].append({"at": [round(x, 3) for x in origin], "error": "no seat surface within 10 mm"})
            continue
        ang = math.degrees(axis.angle(nrm, 0.0))
        if ang > 2.0 and abs(ang - 180) > 2.0:
            b3["failures"].append({"at": [round(x, 3) for x in origin], "error": "axis not normal to the seat",
                                   "deg": round(ang, 1)})
        elif d > 0.001:   # the bolt origin is its seat point on the surface (washer embedded below it)
            b3["failures"].append({"at": [round(x, 3) for x in origin], "error": "not seated", "gap_mm": round(d * 1000, 2)})


def _role_mats(reg, R):
    m3 = R["M3"]
    for name, r in reg["roles"].items():
        allowed = ROLE_MATS.get(r.get("role"))
        if not allowed or name not in reg["comps"]:
            continue
        m3["checked"] += 1
        mats = _comp_mats(reg, name)
        if not mats <= allowed:
            m3["failures"].append({"comp": name, "role": r["role"], "materials": sorted(mats), "allowed": sorted(allowed)})


def _run_along(path, a0, a1, rad, dist, cos_par):
    ax = a1 - a0
    L = ax.length
    ax = ax / L
    covered = 0.0
    for q0, q1 in zip(path, path[1:]):
        sg = q1 - q0
        if sg.length < 1e-6:
            continue
        mid = (q0 + q1) / 2
        t = (mid - a0).dot(ax)
        off = (mid - (a0 + ax * t)).length
        if 0 <= t <= L and off <= dist and abs(sg.normalized().dot(ax)) >= cos_par:
            covered += abs(sg.dot(ax))
    return covered, L


def _hoses(reg, geo, R):
    for h in reg["hoses"]:
        kind = h["kind"]
        path, radii = fillet_path(h["pts"], h["bend"], h.get("steps") or 4)
        od = 2 * h["r"]
        # H5 bend radius
        R["H5"]["checked"] += 1
        need = BEND_X_OD[kind] * od
        rmin = min(radii) if radii else math.inf
        if rmin < need - 1e-4:
            R["H5"]["failures"].append({"line": h["name"], "kind": kind, "min_bend_mm": round(rmin * 1000, 1),
                                        "required_mm": round(need * 1000, 1)})
        # S3 angular step per ring
        R["S3"]["checked"] += 1
        step = 90.0 / (h.get("steps") or 4)
        if step > 22.5 + 1e-6:
            R["S3"]["failures"].append({"line": h["name"], "deg_per_ring": step})
        # H1 / C1 ends exist and are joined (the joint check verifies contact)
        chk = "C1" if kind == "cable" else "H1"
        R[chk]["checked"] += 1
        if len(h["ends"]) != 2 or any(e not in geo for e in h["ends"]):
            R[chk]["failures"].append({"line": h["name"], "error": "needs two registered end fittings", "ends": h["ends"]})
        if kind == "tube":
            R["H4t"]["checked"] += 1
            roles = sorted(reg["roles"].get(e, {}).get("role") for e in h["ends"])
            if roles != ["bulkhead", "valve_port"]:
                R["H4t"]["failures"].append({"tube": h["name"], "ends": h["ends"], "roles": roles,
                                             "error": "a supply tube must run from a valve port to a bulkhead"})
            r = reg["roles"].get(h["name"], {})
            R["P1"]["checked"] += 1
            sup = sorted(r.get("supports", []))
            span = max((b - a for a, b in zip(sup, sup[1:])), default=0)
            if span > 0.6 or len(sup) < 3:
                R["P1"]["failures"].append({"tube": h["name"], "max_span_m": round(span, 2),
                                            "error": "rigid tube needs a clamp at least every 0.6 m"})
            continue
        if kind == "cable":
            R["C2"]["checked"] += 1
            r = reg["roles"].get(h["name"], {})
            stops = sorted(list(r.get("ends", [])) + list(r.get("clips", [])))
            gap = max((b - a for a, b in zip(stops, stops[1:])), default=0)
            if gap > 0.5:
                R["C2"]["failures"].append({"cable": h["name"], "max_span_m": round(gap, 2)})
            continue
        # H2 port topology
        R["H2"]["checked"] += 1
        roles = [reg["roles"].get(e, {}).get("role") for e in h["ends"]]
        owners = [e.rsplit(".", 1)[0] for e in h["ends"]]
        if len(set(owners)) < 2 or sorted(roles) != ["cylinder_port", "valve_port"]:
            R["H2"]["failures"].append({"hose": h["name"], "ends": h["ends"], "roles": roles,
                                        "error": "a line must join one cylinder port to a valve/manifold port"})
        # H8 never to a rod
        R["H8"]["checked"] += 1
        if "cylinder_rod" in roles:
            R["H8"]["failures"].append({"hose": h["name"], "error": "hose joined to a sliding rod"})
        # H3 along a barrel
        for cname, a0, a1, rad in reg["cylinders"]:
            R["H3"]["checked"] += 1
            cov, L = _run_along(path, a0, a1, rad, 2 * rad, math.cos(math.radians(20)))
            if cov > 0.5 * L:
                R["H3"]["failures"].append({"hose": h["name"], "cylinder": cname, "covered_pct": round(100 * cov / L)})
        # H6 straight after the ferrule
        R["H6"]["checked"] += 1
        if h.get("straight") is not None and h["straight"] < 1.5 * od - 1e-6:
            R["H6"]["failures"].append({"hose": h["name"], "straight_mm": round(h["straight"] * 1000, 1)})
        # H7 slack
        R["H7"]["checked"] += 1
        length = sum((b - a).length for a, b in zip(path, path[1:]))
        if h.get("chord") and length < 1.03 * h["chord"]:
            R["H7"]["failures"].append({"hose": h["name"], "length_over_chord": round(length / h["chord"], 3)})
        # H9 clamps on long runs
        R["H9"]["checked"] += 1
        if length > 0.6:
            R["H9"]["failures"].append({"hose": h["name"], "length_m": round(length, 2), "warning": "needs a clamp"})


def _pipes(reg, R):
    def max_span(od):
        return 1.0 if od <= 0.033 else 1.2 if od <= 0.06 else 1.5 if od <= 0.114 else 2.0
    for name, r in reg["roles"].items():
        if r.get("role") != "pipe":
            continue
        R["P1"]["checked"] += 1
        sup = sorted(r.get("supports", []))
        if len(sup) < 2:
            R["P1"]["failures"].append({"pipe": name, "error": "needs a support at each end"})
            continue
        span = max(b - a for a, b in zip(sup, sup[1:]))
        if span > max_span(r["od"]):
            R["P1"]["failures"].append({"pipe": name, "max_span_m": round(span, 2)})
        for fpos in r.get("fittings", []):
            near = min(abs(fpos - s) for s in sup)
            if near > max(2 * r["od"], 0.15):
                R["P1"]["failures"].append({"pipe": name, "fitting_at": fpos, "nearest_support_m": round(near, 2)})
    for name, r in reg["roles"].items():
        if r.get("pattern") != "flange":
            continue
        n, d, Rb, hub, outer = r["count"], r["d"], r["R"], r["hub"], r["outer"]
        R["P3"]["checked"] += 1
        if n % 4 or not (hub + 1.5 * d <= Rb <= outer - 1.5 * d):
            R["P3"]["failures"].append({"flange": name, "bolts": n, "bolt_circle": round(Rb, 4),
                                        "window": [round(hub + 1.5 * d, 4), round(outer - 1.5 * d, 4)]})
        R["B1"]["checked"] += 1
        pitch = 2 * Rb * math.sin(math.pi / n)
        edge = outer - Rb
        if pitch < 2.67 * d or edge < 1.25 * d:
            R["B1"]["failures"].append({"pattern": name, "pitch_d": round(pitch / d, 2), "edge_d": round(edge / d, 2)})
        elif pitch < 3 * d or edge < 1.5 * d:
            R["B1w"]["failures"].append({"pattern": name, "pitch_d": round(pitch / d, 2), "edge_d": round(edge / d, 2)})
        R["B2"]["checked"] += 1
        if n < 2:
            R["B2"]["failures"].append({"pattern": name, "bolts": n})


def _rounds(reg, R):
    for name, r, sides, mat in reg["rounds"]:
        R["S1"]["checked"] += 1
        need = nseg(r, 0, mat == CH)
        if sides < need:
            R["S1"]["failures"].append({"comp": name, "radius_mm": round(r * 1000, 1), "sides": sides,
                                        "required": need, "chrome": mat == CH})
        R["D2"]["checked"] += 1
        if 2 * r < 0.010:
            R["D2"]["failures"].append({"comp": name, "diameter_mm": round(2 * r * 1000, 1)})


def _labels(reg, geo, R):
    pts = {}
    for kind, comp in reg["hazards"]:
        if comp in geo:
            pts.setdefault(kind, []).extend(geo[comp][0])
    for kind, pos, nrm in reg["labels"]:
        R["L1"]["checked"] += 1
        cand = pts.get(kind)
        if not cand:
            R["L1"]["failures"].append({"label": kind, "at": [round(x, 2) for x in pos], "error": "no registered hazard"})
            continue
        d = min((v - pos).length for v in cand)
        if d > HAZARD_RANGE:
            R["L1"]["failures"].append({"label": kind, "at": [round(x, 2) for x in pos], "nearest_hazard_m": round(d, 2)})


def _kinematics(reg, geo, R):
    from . import connect as K
    k1 = R["K1"]
    for k in reg["kinematic"]:
        stroke, vec = k["stroke"], k["vec"]
        for s in (0.0, stroke / 2, stroke, stroke + k["min_engage"]):
            moved = {}
            for m in k["moving"]:
                if m not in geo:
                    k1["failures"].append({"set": k["name"], "error": "component missing", "comp": m})
                    continue
                v = [q + vec * s for q in geo[m][0]]
                moved[m] = (v, geo[m][1], K._tree(v, geo[m][1]), K._bbox(v))
            for a, b in k["engaged"]:
                k1["checked"] += 1
                if a in moved and b in geo:
                    d = K._distance(moved[a], geo[b], moved[a][2], geo[b][2], K.TOL)
                    if d > K.TOL:
                        k1["failures"].append({"set": k["name"], "error": "disengaged", "moving": a, "static": b,
                                               "stroke_mm": round(s * 1000), "gap_mm": round(d * 1000, 1)})
            if s > stroke:
                continue
            for a, b in k["clear"]:
                if a not in moved or b not in geo:
                    continue
                k1["checked"] += 1
                g1, g2 = moved[a], geo[b]
                if K._bbox_gap(g1[3], g2[3]) > k["clearance"]:
                    continue
                d = K._distance(g1, g2, g1[2], g2[2], 0.0)
                if d < k["clearance"]:
                    k1["failures"].append({"set": k["name"], "error": "clearance", "moving": a, "static": b,
                                           "stroke_mm": round(s * 1000), "distance_mm": round(d * 1000, 1)})


def run(hooks, reg, geo, parts):
    """Every check on the registry `reg` (connect.REG), the component geometry `geo` and the parts."""
    sev = {"M1": "error", "M2": "warning", "M3": "error", "H1": "error", "H2": "error", "H3": "error",
           "H5": "error", "H6": "warning", "H7": "warning", "H8": "error", "H9": "warning", "P1": "error",
           "P3": "error", "B1": "error", "B1w": "warning", "B2": "error", "B3": "error", "C1": "error",
           "C2": "warning", "S1": "error", "S3": "warning", "D2": "warning", "L1": "error",
           "W2": "warning", "K1": "error", "H4t": "error"}
    R = {k: _res(k, v) for k, v in sev.items()}
    _fasteners(reg, parts, R)
    _role_mats(reg, R)
    _hoses(reg, geo, R)
    _pipes(reg, R)
    _rounds(reg, R)
    _labels(reg, geo, R)
    _kinematics(reg, geo, R)
    # W2 (gussets): no registry check here: stays 0
    R["B1w"]["checked"] = R["B1"]["checked"]
    errors = sum(len(r["failures"]) for r in R.values() if r["severity"] == "error")
    warnings = sum(len(r["failures"]) for r in R.values() if r["severity"] == "warning")
    return {"errors": errors, "warnings": warnings, "checks": R, "manual": dict(hooks.realism_manual)}
