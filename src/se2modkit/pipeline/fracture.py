"""Fracture plans for the Fractured / Damaged models, vanilla-style (plain Python, no bpy).

A block's FRACTURE PLAN is a set of convex zones "Fracture_NN" (each an intersection of half spaces in the centred
Blender block frame: X width, +Y front, Z up) and the seams between them (Seam: one or several zones on each side). The
exporter and `fracture_mesh` turn it into the cut Fractured model (per zone the skin in Group Fracture_NN, decals /
stencils / lamps in Fracture_NN_Hide, break faces in vanilla FracturedMaterial01 with the Split /
HiddenUntilSecondSequenceStart / Fracture props, one convex collider per zone shaped to its geometry) and the dented
Damaged model (vanilla "Deformed": same mesh parts, densified and dented, lamp / screen / metal slots mapped to their
broken materials, `defs.material_guids.damaged_slot`).

A critical zone does NOT destroy the block: a dead critical zone makes BreakableBlockComponent.IsBroken true (the block
switches off and shows "Broken"); the block is destroyed only when its volume-weighted integrity falls below its
DestructionThreshold (0.3 by default). Zone health = collider volume share, x 2 for a critical zone (health_shares).

The family tables come from the mod's `ModHooks` (the first argument of every function that reads one): `plans`
{family: fn(spec, boxes) -> Plan}, `keep_whole`, `roles`, `core_parts`, `dent`, `jag`, `unfractured`,
`damaged_tris_x`, `standins`, `min_share_exempt`. `plan(hooks, spec, boxes)` dispatches on `hooks.plans[spec.family]`
and raises for a family with no plan that is not unfractured.

The SDK's limits (model.limits): a fractured model may hold
round(interp(max edge: 0.25 m -> 1, 0.5 -> 2, 1.5 -> 5, 2.5 -> 15)) fractures, 32 materials and 256 mesh parts per
LOD, and 1.83425 x the triangles; its LOD count is still keyed on its own LOD0 triangles (fracture_budget).
"""

from __future__ import annotations

import math
import zlib

from ..defs.material_guids import DAMAGED_SLOTS, SUBPART_SUFFIX
from ..defs.material_guids import damaged_slot as _damaged_slot
from ..model import limits
from .standins import STANDIN_PROPS, StandIn

__all__ = ["CUT_MATERIAL", "CUT_PROPS", "CUT_UV_PER_M", "DAMAGED_SLOTS", "FractureError", "GLASS_MATERIALS",
           "HIDE_PREFIXES", "Plan", "ROLE_IDS", "ROLE_PROPS", "ROLE_SUFFIX", "STANDIN_PROPS", "SUBPART_SUFFIX", "Seam",
           "StandIn", "TABLE_ROLES", "Zone", "box_hull", "check_role_tables", "clip_box", "damaged_slot",
           "damaged_tris_x", "dent_seed", "fracture_budget", "grid_zones", "health_shares", "is_hide",
           "lift_small_zones", "lod_row", "lod_side", "max_fractures", "min_share_box", "min_share_exempt", "plan",
           "polytope_vertices", "polytope_volume", "role_of", "role_props", "shaped_hull", "skin_carrier",
           "unfractured_reason"]


class FractureError(RuntimeError):
    """A block's fracture plan cannot be made; the message is one plain sentence."""


PREFIX = "Fracture_"
CUT_MATERIAL = "FracturedMaterial01"          # vanilla break-face material (Timer50, Battery50, EventController100)
CUT_UV_PER_M = 0.4                            # vanilla cut faces: ~0.4 UV units per metre (Timer50 / Battery50)
CUT_PROPS = dict(ConstructionMeshType="Split", ConstructionMeshVisibility="HiddenUntilSecondSequenceStart",
                 ConstructionMeshPreset="Fracture")
CUT_MIN = 0.012     # an island is cut by a seam only when it reaches this far past the plane on BOTH sides
THIN_CAP = 0.04     # a cut loop shorter than this is a thin wire's end (6 mm wire: 0.02 m)
LONG_CUT = 0.04     # a keep-whole island reaching this far past a seam on both sides is cut (a plan's long_cut)

# Construction roles per face. A face's role decides its object and Group in the Fractured model and so its
# construction mesh-part props:
#   skin     the zone's own group Fracture_NN: the panels that build up through both sequences;
#   hide     Fracture_NN_Hide, AlwaysHidden while under construction: decals, stencils, lamps, screens (HIDE_PREFIXES);
#   support  Fracture_NN_Support, the Support preset: the mounts that carry the block, built first;
#   frame    Fracture_NN_Frame, Split / HiddenUntilSecondSequenceStart: parts that appear in the second sequence;
#   glass    the zone's own group, a separate object with the WindowGlass preset: glass stays glass while it builds.
# Zone names never change, so a save's DamagedFractures stay valid. `hooks.roles` is per family {role: part names},
# matched by exact part name like `hooks.keep_whole`.
HIDE_PREFIXES = ("AtlasDecal", "AtlasFont", "Emissive", "Terminal", "LCDScreen")   # vanilla LCDs keep their screen in
#                                                            a _Hide group too: Fracture_16_Hide-LCDScreen_Off
ROLE_IDS = ("skin", "hide", "support", "frame", "glass")
ROLE_SUFFIX = {"skin": "", "hide": "_Hide", "support": "_Support", "frame": "_Frame", "glass": ""}
ROLE_PROPS = {"skin": ("Default", "AlwaysVisible", "Default"), "hide": ("Default", "AlwaysHidden", "Default"),
              "support": ("Default", "AlwaysVisible", "Support"),
              "frame": ("Split", "HiddenUntilSecondSequenceStart", "Default"),
              "glass": ("Default", "AlwaysVisible", "WindowGlass")}
GLASS_MATERIALS = ("FrostedGlass", "WindowGlass", "WindowGlassThin", "ClearGlass")
TABLE_ROLES = tuple(r for r in ROLE_IDS if ROLE_SUFFIX[r])   # the roles a part table may name (own suffixed group)


def check_role_tables(roles):
    """A part table names only suffixed roles. A "skin" entry is the default anyway, and a "glass" one would put a
    glass object and a skin object of the same material in one Group, i.e. one mesh-part name for two meshes (the
    exporter keeps the last object's props, the importer sees two meshes for one part). Glass is decided by material
    (GLASS_MATERIALS) only."""
    bad = {(fam, r) for fam, t in roles.items() for r in t if r not in TABLE_ROLES}
    if bad:
        raise ValueError(f"role tables may name only {TABLE_ROLES}: {sorted(bad)}")


def is_hide(material):
    """The one hide test: a decal / stencil / lamp / screen material, AlwaysHidden while building."""
    return material.startswith(HIDE_PREFIXES)


def role_of(hooks, family, part, material):
    """The construction role of a face: hide by material, then glass by material, then the first role whose
    `hooks.roles[family]` entry names the face's part (exact name, as keep_whole), else skin."""
    if is_hide(material):
        return "hide"
    if material in GLASS_MATERIALS:
        return "glass"
    table = hooks.roles.get(family, {})
    return next((r for r in TABLE_ROLES if part in table.get(r, ())), "skin")


def role_props(role):
    """{ConstructionMeshType, ConstructionMeshVisibility, ConstructionMeshPreset} of a role's objects."""
    return dict(zip(("ConstructionMeshType", "ConstructionMeshVisibility", "ConstructionMeshPreset"),
                    ROLE_PROPS[role]))


def skin_carrier(present, lod, block, zone):
    """The role whose piece carries a zone's Group and ColliderMeshGroups at this LOD: the skin when there is one; on
    a lower LOD the first present role in ROLE_IDS order (a decal-only or glass-only corner of a coarse LOD); None when
    the zone has nothing at that lower LOD. A zone with no LOD0 skin face fails here, by name: its group would carry no
    ColliderMeshGroups (the importer then drops every fracture) and the generator reads LOD0."""
    if "skin" in present:
        return "skin"
    if lod == 0:
        if present:
            raise RuntimeError(f"{block}: fracture zone {zone} has no LOD0 skin face (only {', '.join(sorted(present))}"
                               f"): give it a skin part or move its role parts to a neighbouring zone")
        raise RuntimeError(f"{block}: fracture zone {zone} has no LOD0 geometry")
    return next((r for r in ROLE_IDS if r in present), None)


def damaged_slot(hooks, slot):
    """The material a Damaged .model maps a slot to (`defs.material_guids.damaged_slot`: the generic remaps plus
    `hooks.damaged_slots`; a `<Mat>_Subpart` stand-in slot follows its base slot)."""
    return _damaged_slot(hooks, slot)


def dent_seed(hooks, name):
    """The Damaged model's noise seed for block `name`. Blender's mathutils.noise.noise_vector draws a random offset
    per process unless noise.seed_set is called (two exports of one block dented differently, up to ~15 mm apart), so
    fracture_mesh.dent seeds it with this stable per-block value: CRC-32 of "<mod name, lower case>/<block name>"
    (Python's hash() of a str is salted per process)."""
    return (zlib.crc32(f"{hooks.name.lower()}/{name}".encode()) & 0x7FFFFFFF) or 1   # (seed_set(0): the clock)


# the least mean dent (vertex displacement, fracture_mesh.dent) a Damaged model must keep to read as dented
MIN_DENT_MM = 1.5


def damaged_tris_x(hooks, family):
    """The Damaged / Fractured (scaffold excluded) triangle ratio cap of a family (`hooks.damaged_tris_x`)."""
    return hooks.tris_x(family)


def min_share_exempt(hooks, family):
    """The zone ids of `family` that keep their shaped hull under MIN_ZONE_SHARE (`hooks.min_share_exempt`)."""
    return {zid for fam, zid in hooks.min_share_exempt if fam == family}


class Zone:
    def __init__(self, zid, halfspaces, label, critical=False, hull_from="regular"):
        self.id = zid
        self.halfspaces = [(tuple(map(float, o)), _unit(n)) for o, n in halfspaces]
        self.label = label
        self.critical = critical
        self.hull_from = hull_from   # the collision boxes its plan box is fitted to: "regular" (physics),
        #                              "occupancy" (the occupancy hints) or "geometry" (no physics box under it);
        #                              the exported collider is shaped to the zone's geometry (shaped_hull)
        self.shape = None         # the exported collider: shaped_hull's dict (set by the exporter)
        self.hull = None          # (lo, hi): its collider, set by Plan.fit_hulls

    def violation(self, p):
        return max([0.0] + [-_dot(_sub(p, o), n) for o, n in self.halfspaces])

    def __repr__(self):
        return f"Zone({self.id}, {self.label!r}, critical={self.critical})"


class Seam:
    """The plane between zones a and b (indices into Plan.zones): (p - o) . n >= 0 on a's side. a / b may also be
    tuples of zones sharing that plane on each side (e.g. a base plate's top meeting two columns): self.A / self.B;
    self.a / self.b are their first zones."""

    def __init__(self, o, n, a, b, snap=0.0):
        self.o, self.n = tuple(map(float, o)), _unit(n)
        self.A = tuple(a) if isinstance(a, (tuple, list)) else (a,)
        self.B = tuple(b) if isinstance(b, (tuple, list)) else (b,)
        self.a, self.b = self.A[0], self.B[0]
        self.snap = snap          # +- window (m) in which build snaps the plane into the widest vertex-free gap
        self.jag = 0.0            # the break's amplitude along n (fracture_mesh.snap_seams)
        self.reach = 0.0          # how far from the plane the break field fades out (< the model's bounding faces)

    def axis(self):
        """The axis index when the plane is axis-aligned, else None."""
        for i in range(3):
            if abs(abs(self.n[i]) - 1.0) < 1e-9:
                return i
        return None

    def moved(self, d):
        """Shift the plane by d along its normal (the zones that use it follow: Plan.move_seam)."""
        return tuple(self.o[i] + d * self.n[i] for i in range(3))


class Plan:
    def __init__(self, spec, family, zones, seams, keep_whole=(), supporting=None):
        self.spec, self.family = spec, family
        self.zones, self.seams = zones, seams
        self.keep_whole = tuple(keep_whole)
        self.supporting = supporting or {}     # dummy name -> point (Blender frame): its zone supports it

    def ids(self):
        return [z.id for z in self.zones]

    # the cheaper LOD0 cuts a light block may take to stay clearly on one side of Keen's LOD-count row (the exporter
    # tries them in turn): no caps on thin wire ends (< THIN_CAP perimeter), then a split along the existing edges
    # instead of a bisect (a bend's arcs are faceted: its wires and strands part at a segment ring)
    edge_split = False
    thin_caps = True
    long_cut = None      # keep_whole islands reaching this far past a seam on both sides are cut (None: never)

    def cut_mode(self):
        return ("edges" if self.edge_split else "bisect") + ("" if self.thin_caps else ", no wire-end caps")

    def critical(self):
        return [z.id for z in self.zones if z.critical]

    def zone_index(self, p):
        """Index of the zone containing p (the least violated; ties to the lower index = the key zone)."""
        best, bi = None, 0
        for i, z in enumerate(self.zones):
            v = z.violation(p)
            if best is None or v < best - 1e-12:
                best, bi = v, i
        return bi

    @staticmethod
    def zone_at(zones, p):
        """The id of the zone holding p (Blender frame) among an export manifest's zones ("fractured" "zones": their
        half spaces), by Plan.zone_index's rule: the least violated, ties to the earlier (the key) zone. For dummies
        placed after the export (e.g. a use toggle) that a zone must support."""
        best, bid = None, None
        for z in zones:
            v = max([0.0] + [-_dot(_sub(p, o), n) for o, n in z["halfspaces"]])
            if best is None or v < best - 1e-12:
                best, bid = v, z["id"]
        return bid

    def move_seam(self, k, d):
        """Move seam k by d along its normal, and the matching half spaces of its two zones."""
        s = self.seams[k]
        old = s.o
        s.o = s.moved(d)
        for z in [self.zones[i] for i in s.A + s.B]:     # (the half spaces lying on the seam's plane move with it)
            z.halfspaces = [(tuple(c + d * m for c, m in zip(o, s.n))
                             if ((n == s.n or n == _neg(s.n)) and abs(_dot(_sub(o, old), s.n)) < 1e-9) else o, n)
                            for o, n in z.halfspaces]

    def fit_hulls(self, boxes):
        """Each zone's collider: the bounding box of the regular physics boxes (or, for a zone flagged so, the
        occupancy boxes) clipped to the zone: a box where the zone holds a box piece; the zones of an L bend overlap
        a little at the bend, as vanilla's hulls do. boxes: the collision dict."""
        for z in self.zones:
            if z.hull_from == "geometry":     # no physics box under it (a render-only part): its
                continue                      # collider comes from its geometry only (shaped_hull, at export)
            pts = []
            for _, lo, hi in boxes[z.hull_from]:
                pts += clip_box(lo, hi, z.halfspaces)
            if not pts:
                raise RuntimeError(f"{self.spec.name}: zone {z.id} holds no physics box")
            lo = tuple(round(min(p[i] for p in pts), 5) for i in range(3))
            hi = tuple(round(max(p[i] for p in pts), 5) for i in range(3))
            if min(h - l for l, h in zip(lo, hi)) < 0.004:  # noqa: E741
                raise RuntimeError(f"{self.spec.name}: zone {z.id} collider is degenerate {lo} {hi}")
            z.hull = (lo, hi)
        return [(z.id, z.hull[0], z.hull[1]) for z in self.zones if z.hull]

    def manifest(self):
        return {"zones": [{"id": z.id, "label": z.label, "critical": z.critical,
                           "lo": list(z.hull[0]) if z.hull else None, "hi": list(z.hull[1]) if z.hull else None,
                           "halfspaces": [[list(o), list(n)] for o, n in z.halfspaces]} for z in self.zones],
                "seams": [{"o": [round(v, 6) for v in s.o], "n": [round(v, 6) for v in s.n],
                           "zones": [self.zones[s.a].id, self.zones[s.b].id],
                           "sides": [[self.zones[i].id for i in s.A], [self.zones[i].id for i in s.B]],
                           "jag": round(s.jag, 6),
                           "reach": round(s.reach, 6)}
                          for s in self.seams],
                "supporting": {d: self.zones[self.zone_index(p)].id for d, p in sorted(self.supporting.items())}}


# ---------------------------------------------------------------------------------------------------- geometry
def _unit(n):
    n = tuple(map(float, n))
    m = math.sqrt(sum(c * c for c in n))
    return tuple(c / m for c in n)


def _neg(n):
    return tuple(-c for c in n)


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def clip_box(lo, hi, halfspaces):
    """Vertices of the AABB [lo, hi] clipped by the half spaces (every face polygon clipped Sutherland-Hodgman);
    [] when nothing is left."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [[c[i] for i in f] for f in ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6),
                                         (3, 0, 4, 7))]
    for o, n in halfspaces:
        out = []
        for poly in faces:
            res = []
            for i, p in enumerate(poly):
                q = poly[(i + 1) % len(poly)]
                dp, dq = _dot(_sub(p, o), n), _dot(_sub(q, o), n)
                if dp >= -1e-9:
                    res.append(p)
                if (dp >= -1e-9) != (dq >= -1e-9):
                    t = dp / (dp - dq)
                    res.append(tuple(p[k] + t * (q[k] - p[k]) for k in range(3)))
            if len(res) >= 3:
                out.append(res)
        faces = out
    return [p for f in faces for p in f]


# ---------------------------------------------------------------------------------------------------- shaped hulls
# A zone's collider follows its solid. Health is the block's MaxHealth x the collider's VOLUME share
# (BreakableComponentDefinition.PostProcessPrefabs), and the colliders become the block's physics once anything
# breaks, so a box over mostly empty space (a bend's inner corner) buys health and phantom collision. Each collider is
# a k-DOP of the zone's own LOD0 geometry (its skin, decals and break faces): the axis box clipped to the block's cells
# and the zone's seams, then greedily the planes (45 degree face / body diagonals, and DOP_FREE) that cut the most
# volume, while each cuts >= HULL_GAIN of it and the block's total hull vertices stay within the SDK's fractured collider
# budget (the SUM of all pieces' vertices <= 1.5 x interp(max edge:
# 0.25 -> 32, 0.5 -> 40, 1.5 -> 48, 2.5 -> 80, 4 -> 120)).
_R2, _R3 = 1 / math.sqrt(2), 1 / math.sqrt(3)
DOP_AXES = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
DOP_DIAGONALS = ([tuple(c * _R2 for c in v) for v in
                  [(a, b, 0) for a in (1, -1) for b in (1, -1)] + [(a, 0, b) for a in (1, -1) for b in (1, -1)] +
                  [(0, a, b) for a in (1, -1) for b in (1, -1)]] +
                 [tuple(c * _R3 for c in (a, b, e)) for a in (1, -1) for b in (1, -1) for e in (1, -1)])
HULL_GAIN = 0.03


def _spiral(n, turn=2.39996323):
    """n directions spread evenly over the sphere (a golden-angle spiral)."""
    out = []
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(max(0.0, 1 - z * z))
        out.append((r * math.cos(turn * i + 0.3), r * math.sin(turn * i + 0.3), z))
    return out


# a slanted bundle or a bend's arc has faces in no 45 degree direction: 48 more directions to choose from
DOP_FREE = _spiral(48)


def _solve3(planes):
    """The point on three planes (n . p = d), or None when they do not meet in one point (Cramer)."""
    (a, da), (b, db), (c, dc) = planes
    bc = _cross(b, c)
    det = _dot(a, bc)
    if abs(det) < 1e-12:
        return None
    ca, ab = _cross(c, a), _cross(a, b)
    return tuple((da * bc[i] + db * ca[i] + dc * ab[i]) / det for i in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dedupe(pts, q=1e-6):
    seen, out = set(), []
    for p in pts:
        k = tuple(round(c / q) for c in p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def polytope_vertices(planes, eps=1e-7):
    """Vertices of the bounded polytope {p: n . p <= d for every (n, d)} (every feasible triple intersection)."""
    out = []
    k = len(planes)
    for i in range(k):
        for j in range(i + 1, k):
            for m in range(j + 1, k):
                p = _solve3((planes[i], planes[j], planes[m]))
                if p is not None and all(_dot(n, p) <= d + eps for n, d in planes):
                    out.append(p)
    return _dedupe(out)


def _clip_vertices(verts, planes, new, eps=1e-7):
    """polytope_vertices(planes + [new]) from the polytope's verts: the kept ones and the new plane's corners."""
    n0, d0 = new
    out = [v for v in verts if _dot(n0, v) <= d0 + eps]
    k = len(planes)
    for i in range(k):
        for j in range(i + 1, k):
            p = _solve3((new, planes[i], planes[j]))
            if p is not None and all(_dot(n, p) <= d + eps for n, d in planes):
                out.append(p)
    return _dedupe(out)


def polytope_volume(planes, verts, eps=1e-6):
    """Volume of the convex polytope (planes, its vertices): a pyramid from the vertex centroid over each face."""
    if len(verts) < 4:
        return 0.0
    c = tuple(sum(v[i] for v in verts) / len(verts) for i in range(3))
    vol = 0.0
    for n, d in planes:
        f = [v for v in verts if abs(_dot(n, v) - d) < eps]
        if len(f) < 3:
            continue
        fc = tuple(sum(v[i] for v in f) / len(f) for i in range(3))
        t1 = _unit(_sub(f[0], fc)) if any(abs(x) > 1e-12 for x in _sub(f[0], fc)) else _unit(_cross(n, (1, 0, 0)))
        t2 = _cross(n, t1)
        f.sort(key=lambda v: math.atan2(_dot(_sub(v, fc), t2), _dot(_sub(v, fc), t1)))
        area = 0.0
        for a, b in zip(f, f[1:] + f[:1]):
            area += _dot(_cross(_sub(a, fc), _sub(b, fc)), n) / 2
        vol += abs(area) * (d - _dot(n, c)) / 3
    return vol


def shaped_hull(points, lo, hi, max_verts, gain=HULL_GAIN, halfspaces=()):
    """The zone collider fitted to points (its LOD0 geometry): {"verts", "planes", "volume", "box_volume"}; see the
    section comment. lo / hi: the block's cell box (the collider stays inside it). halfspaces: the zone's own
    [(o, n)] ((p - o) . n >= 0): the collider stops at its seams' planes, so the ragged break's few cm of spill do not
    carry it into the next zone's cells (a mount cell belongs to the FIRST collider overlapping it, and the seams
    lie on cell boundaries), while two neighbours still touch along the plane (Havok's 0.1 m)."""
    def sup(n):
        return max(_dot(n, p) for p in points)
    planes = []
    for n in DOP_AXES:
        i = [k for k in range(3) if n[k]][0]
        d = sup(n)
        lim = hi[i] if n[i] > 0 else -lo[i]
        planes.append((n, min(d, lim)))
    for o, n in halfspaces:
        m = _neg(n)
        d = _dot(m, o)
        same = [k for k, (q, _) in enumerate(planes) if all(abs(a - b) < 1e-12 for a, b in zip(q, m))]
        if same:
            planes[same[0]] = (m, min(planes[same[0]][1], d))
        else:
            planes.append((m, d))
    verts = polytope_vertices(planes)
    vol = box = polytope_volume(planes, verts)
    cands = [(n, sup(n)) for n in DOP_DIAGONALS + DOP_FREE]
    while True:
        best = None
        for n, d in cands:
            if (n, d) in planes or max(_dot(n, v) for v in verts) <= d + 1e-6:
                continue
            v2 = _clip_vertices(verts, planes, (n, d))
            if len(v2) > max_verts:
                continue
            vol2 = polytope_volume(planes + [(n, d)], v2)
            if vol - vol2 >= gain * vol and (best is None or vol2 < best[2]):
                best = ((n, d), v2, vol2)
        if best is None:
            break
        planes.append(best[0])
        verts, vol = best[1], best[2]
    planes = [(n, d) for n, d in planes if sum(1 for v in verts if abs(_dot(n, v) - d) < 1e-6) >= 3]
    return {"verts": verts, "planes": planes, "volume": vol, "box_volume": box}


def health_shares(volumes, critical):
    """Keen's per-zone health share (BreakableComponentDefinition.PostProcessPrefabs): collider volume, x 2 for a
    critical zone (BreakableBlocksConfiguration CriticalFractureHealthShareMultiplier), over the sum. {id: share}."""
    w = {z: v * (2.0 if z in critical else 1.0) for z, v in volumes.items()}
    t = sum(w.values())
    return {z: round(x / t, 4) for z, x in w.items()}


# A zone's health is its collider's volume share, so a thin zone's shaped hull dies at the first hit. A zone under
# MIN_ZONE_SHARE gets an axis box instead (min_share_box): its LOD0 geometry's box thickened on its thinnest axis, to at
# least MIN_HULL_T and to the share, but never more than MIN_HULL_T beyond its own geometry (the hull is a live
# collider: after any zone dies the Fractured collision is the block's physics, its debris piece and its re-weld
# overlap check). A zone that would need more raises, by name; it is re-zoned or listed in `hooks.min_share_exempt`.
# Zone ids and critical lists are unchanged; the manifest flags a lifted zone min_share.
MIN_ZONE_SHARE = 0.01
MIN_HULL_T = 0.02
_SHARE_MARGIN = 1.02          # aim 2 % over the floor: the manifest's shares are rounded to 4 places


def box_hull(lo, hi):
    """An axis box as a shaped_hull dict."""
    verts = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    planes = [(n, (hi[i] if n[i] > 0 else -lo[i])) for n in DOP_AXES for i in range(3) if n[i]]
    vol = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
    return {"verts": verts, "planes": planes, "volume": vol, "box_volume": vol}


def min_share_box(glo, ghi, lo, hi, need, zone="zone"):
    """The box hull of a zone below MIN_ZONE_SHARE (see above): its geometry box (glo, ghi) within the cells (lo,
    hi), thickened on its thinnest axis to max(MIN_HULL_T, the thickness giving volume `need`), shifted back inside
    the cells. Raises when that is more than MIN_HULL_T beyond the geometry or does not fit the cells."""
    blo = [max(a, c) for a, c in zip(glo, lo)]
    bhi = [min(b, c) for b, c in zip(ghi, hi)]
    a = min(range(3), key=lambda i: bhi[i] - blo[i])
    o1, o2 = [i for i in range(3) if i != a]
    ext = bhi[a] - blo[a]
    t = max(ext, MIN_HULL_T, need / ((bhi[o1] - blo[o1]) * (bhi[o2] - blo[o2])))
    if t - ext > MIN_HULL_T + 1e-9 or t > hi[a] - lo[a] + 1e-9:
        raise RuntimeError(f"{zone}: under MIN_ZONE_SHARE; reaching it needs {t * 1000:.1f} mm on its thinnest axis "
                           f"(geometry {ext * 1000:.1f} mm, at most +{MIN_HULL_T * 1000:.0f} mm): re-zone it or list "
                           f"it in hooks.min_share_exempt")
    c = (blo[a] + bhi[a]) / 2
    z0 = min(max(c - t / 2, lo[a]), hi[a] - t)
    blo[a], bhi[a] = z0, z0 + t
    return box_hull(blo, bhi)


def lift_small_zones(shapes, critical, geo, lo, hi, exempt=()):
    """{zone: min_share_box shape} for the zones whose health share (shapes' volumes, Keen's rule) is under
    MIN_ZONE_SHARE, except the `exempt` ones (they keep their hulls: min_share_exempt); shapes {zone: shaped_hull
    dict}, geo {zone: (glo, ghi)} its LOD0 geometry box. Each is sized against the others' current weight, repeated
    until every share holds."""
    vols = {z: s["volume"] for z, s in shapes.items()}
    out = {}
    for _ in range(8):
        share = health_shares(vols, critical)
        small = [z for z in vols if share[z] < MIN_ZONE_SHARE and z not in exempt]
        if not small:
            return out
        w = {z: 2.0 if z in critical else 1.0 for z in vols}
        for z in small:
            rest = sum(vols[k] * w[k] for k in vols if k != z)
            s = MIN_ZONE_SHARE * _SHARE_MARGIN
            need = s * rest / (1.0 - s) / w[z]
            out[z] = min_share_box(*geo[z], lo, hi, need, z)
            vols[z] = out[z]["volume"]
    raise RuntimeError(f"zones {sorted(out)} stay under MIN_ZONE_SHARE")


def max_fractures(size):
    """The SDK's fracture cap: interpolated on the model's largest edge, rounded half to even."""
    m = max(size)
    tab = [(0.25, 1.0), (0.5, 2.0), (1.5, 5.0), (2.5, 15.0)]
    if m <= tab[0][0]:
        v = tab[0][1]
    elif m >= tab[-1][0]:
        v = tab[-1][1]
    else:
        for (a, va), (b, vb) in zip(tab, tab[1:]):
            if a <= m <= b:
                v = va + (vb - va) * (m - a) / (b - a)
                break
    return int(round(v))       # Python's round is round-half-to-even, as the game rounds here


def fracture_budget(size, lod0_tris, n_lods):
    """Per LOD, the most triangles a fractured / damaged model of this block may have: Keen's fractured limit
    (x 1.83425) with 5 % headroom, and on LOD0 the row value that keeps Keen's LOD count (the count is keyed on the
    model's own LOD0 triangles: crossing row[bias] would ask for one more LOD)."""
    frac = limits.model_limits(size, lod0_tris, fractured=True)
    out = [int(frac["lods"][i]["max_tris"] * 0.95) for i in range(n_lods)]
    r = lod_row(size, lod0_tris)
    if r is not None:
        out[0] = min(out[0], int(LOD_ROW_BAND[0] * r))
    return out


# Keen's importer counts a few % fewer LOD0 triangles than the FBX holds (one light block: 2100 exported, 1960
# counted), so a cut LOD0 must sit clearly on one side of the row its LOD count is keyed on: at most 0.97 x the row
# (same LOD count) or at least 1.15 x (one more LOD, the builder's mid detail).
LOD_ROW_BAND = (0.97, 1.15)


def lod_row(size, lod0_tris):
    """The row value Keen's LOD count for this LOD0 is keyed on (crossing it changes the count), or None when the
    LOD0 already has lod bias 0 (more triangles cannot ask for more LODs)."""
    b = limits.model_limits(size, lod0_tris)["lod_bias"]
    if b == 0:
        return None
    return limits._interp(limits._tri_table(), limits.surface(*size))[0][b]


def lod_side(size, nf_lod0_tris, t0):
    """Where a cut LOD0 of t0 triangles lands against the NonFractured LOD0's row: below | above | ambiguous."""
    r = lod_row(size, nf_lod0_tris)
    if r is None or t0 <= LOD_ROW_BAND[0] * r:
        return "below"
    return "above" if t0 >= LOD_ROW_BAND[1] * r else "ambiguous"


# ---------------------------------------------------------------------------------------------------- plans
def unfractured_reason(hooks, spec):
    """Why a block keeps no fractures (non-breakable), or None: `hooks.unfractured` (a reason keyed by its block
    name, else the callable keyed by its family)."""
    return hooks.unfractured_reason(spec)


def plan(hooks, spec, boxes):
    """The block's fracture plan (`hooks.plans[spec.family](spec, boxes)`, its colliders fitted to the regular
    physics boxes), or None for a non-breakable exception (`hooks.unfractured`). A plan without keep-whole parts gets
    its family's `hooks.keep_whole`. Raises FractureError for a family with no plan. boxes: the collision dict
    (regular physics boxes in the centred Blender frame)."""
    if unfractured_reason(hooks, spec):
        return None
    make = hooks.plans.get(spec.family)
    if make is None:
        raise FractureError(f"{spec.name}: no fracture plan for family {spec.family!r} in hooks.plans (registered: "
                            f"{sorted(hooks.plans)}); register one, or list the block in hooks.unfractured.")
    check_role_tables(hooks.roles)
    p = make(spec, boxes)
    if not p.keep_whole:
        p.keep_whole = tuple(hooks.keep_whole.get(spec.family, ()))
    p.fit_hulls(boxes)
    return p


# ---------------------------------------------------------------------------------------------------- zone grids
def grid_zones(xs, ys, label):
    """Zones over a grid of cells split at the planes x = xs[i], y = ys[j] (row-major: y rows, then x): every cell's
    half spaces, and the seams (one per plane, the zones each side as tuples). label(i, j) names a zone."""
    nx, ny = len(xs) + 1, len(ys) + 1
    zones = []
    for j in range(ny):
        for i in range(nx):
            hs = []
            if i > 0:
                hs.append(((xs[i - 1], 0, 0), (1, 0, 0)))
            if i < nx - 1:
                hs.append(((xs[i], 0, 0), (-1, 0, 0)))
            if j > 0:
                hs.append(((0, ys[j - 1], 0), (0, 1, 0)))
            if j < ny - 1:
                hs.append(((0, ys[j], 0), (0, -1, 0)))
            zones.append(Zone(f"{PREFIX}{len(zones) + 1:02d}", hs, label(i, j)))
    seams = []
    for i, x in enumerate(xs):
        seams.append(Seam((x, 0, 0), (-1, 0, 0), tuple(j * nx + i for j in range(ny)),
                          tuple(j * nx + i + 1 for j in range(ny))))
    for j, y in enumerate(ys):
        seams.append(Seam((0, y, 0), (0, -1, 0), tuple(j * nx + i for i in range(nx)),
                          tuple((j + 1) * nx + i for i in range(nx))))
    return zones, seams


_quadrants = grid_zones   # the legacy name
