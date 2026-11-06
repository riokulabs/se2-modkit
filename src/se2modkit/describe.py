"""A block model as numbers: every part's size, place, nearest neighbor, contacts and mirror partner, for a reader that
judges geometry better from figures than from pictures (`se2mod describe`, and the live Blender session).

This module imports cleanly anywhere: Blender's own Python (no se2modkit CLI, no third-party packages) and plain
Python (the target parser and checker, the analysis on given boxes and the text are unit-tested without Blender).
`bpy` and `mathutils` are imported only inside the functions that read a scene.

describe_scene(collections=None, envelope=None, targets=None)   the open scene (or the named collections) as a dict.
shipping_objects(top)        the objects under a collection that a build ships (the rule `ship check` uses).
parse_targets(markdown)      the proportion targets in the ```targets fenced blocks of a markdown file.
check_targets(targets, parts) PASS/FAIL per target on described parts.
text(report)                 the compact text: one line per part, then FLOATING / OVERLAP / MIRROR-ERROR / TARGETS.

Units: Blender meters in, millimeters out (sizes, centers, gaps); grid cells are 0.25 m from the block origin.
"""

from __future__ import annotations

import fnmatch
import math
import re
from contextlib import contextmanager

__all__ = ["CELL", "CONTACT", "GEOMETRY", "NEAR", "ROLES", "TargetError", "analyze", "bbox_gap", "check_targets",
           "describe_scene", "parse_targets", "role_of", "ship_reason", "shipping_objects", "text",
           "walk_shipping"]

CELL = 0.25          # m: a small grid cell
CONTACT = 0.5        # mm: parts this close (or overlapping) touch
NEAR = 50.0          # mm: under this box gap, the gap is measured on the meshes themselves
OVERLAP = 0.5        # mm: deeper than this, an overlap is listed
MIRROR_LISTED = 1.0  # mm: a mirror pair off by more than this is listed
GEOMETRY = ("MESH", "CURVE", "SURFACE", "META", "FONT")

# Role by name (first match wins), then by collection. Only "part" roles count for contacts, floating and mirrors.
_W = r"(^|[_\s.-])"   # a word start: names are snake_case, dotted or plain
ROLES = (
    ("collider", rf"collision|collider|^ucx_|hitbox|{_W}col($|[_\s.-])"),
    ("lod", rf"{_W}lod[1-9]"),
    ("dummy", rf"{_W}(dummy|socket|detector|empty)"),
    ("decal", rf"{_W}(decal|sticker|label|logo|stencil)"),
    ("port", rf"{_W}(port|connector|conveyor|nozzle|exhaust)"),
    ("light", rf"{_W}(light|lamp|emissive)"),
    ("glass", rf"{_W}(glass|window)"),
    ("hull", rf"{_W}(hull|body|shell|housing|frame|armor|casing)"),
)
SOLID_ROLES = {"hull", "port", "light", "glass", "decal", "part"}


def role_of(name: str, collection: str = "") -> str:
    for text_ in (name, collection):
        low = (text_ or "").lower()
        for role, pat in ROLES:
            if re.search(pat, low):
                return role
    return "part"


# ------------------------------------------------------------------------------------------------ what ships
def ship_reason(o, collection_render_hidden: bool = False, cutters=()) -> str | None:
    """Why the object `o` does not ship, or None when it does: under a render-hidden collection, hidden from renders
    itself, a boolean operand (its name is in `cutters`), or not geometry. The one rule behind `walk_shipping` and the
    ship check's loose objects."""
    return ("render-hidden collection" if collection_render_hidden else "render-hidden" if o.hide_render else
            "boolean cutter" if o.name in cutters else None if o.type in GEOMETRY else f"not geometry ({o.type})")


def walk_shipping(top):
    """Every object under the collection `top` once, as (object, path, collection name, why it does not ship or None,
    hidden in the viewport). The rule: `top` ships whatever its own flags (a build takes it by name); below it an
    object ships when it is geometry and neither it nor a collection between it and `top` is hidden from renders, and
    it is not a boolean operand of another object. Viewport hiding never matters (an export takes render visibility)."""
    every = [o for o in top.all_objects if o is not None]
    cutters = set()
    for o in every:
        for m in getattr(o, "modifiers", ()):
            if m.type == "BOOLEAN":
                if getattr(m, "object", None) is not None:
                    cutters.add(m.object.name)
                if getattr(m, "collection", None) is not None:
                    cutters.update(x.name for x in m.collection.all_objects if x is not None)
    out, seen = [], set()

    def walk(c, path, hid_v, hid_r):
        for o in c.objects:
            if o is not None and o.name not in seen:
                seen.add(o.name)
                out.append((o, f"{path}/{o.name}", c.name, ship_reason(o, hid_r, cutters),
                            bool(hid_v or o.hide_viewport)))
        for ch in c.children:
            walk(ch, f"{path}/{ch.name}", hid_v or ch.hide_viewport, hid_r or ch.hide_render)
    walk(top, top.name, getattr(top, "hide_viewport", False), False)
    return out


def shipping_objects(top) -> list[tuple]:
    """[(object, collection name)] of what ships under `top` (see `walk_shipping`)."""
    return [(o, coll) for o, _path, coll, why, _hv in walk_shipping(top) if why is None]


# ------------------------------------------------------------------------------------------------ pure analysis
def bbox_gap(a_lo, a_hi, b_lo, b_hi) -> float:
    """Gap between two boxes (same unit as given): the largest separation along an axis; when they overlap on every
    axis, minus the smallest overlap."""
    return max(max(a_lo[k], b_lo[k]) - min(a_hi[k], b_hi[k]) for k in range(3))


def _center(p):
    return [(p["lo"][k] + p["hi"][k]) / 2 for k in range(3)]


def _size(p):
    return [p["hi"][k] - p["lo"][k] for k in range(3)]


def _sort_key(p):
    return (p["collection"].lower(), p["name"].lower(), p["name"])


def analyze(parts: list[dict], *, mesh_gap=None, mid=None) -> dict:
    """Number and relate parts. Each part: {"name", "collection", "role", "lo", "hi" (mm), "tris"}; `mesh_gap(a, b)`
    gives the real gap in mm between two parts whose boxes are closer than NEAR (else the box gap is used); `mid` is the
    mirror planes (mm, per axis), default the overall box center. Returns the report body (see `describe_scene`)."""
    parts = sorted(parts, key=_sort_key)
    for i, p in enumerate(parts, 1):
        p["id"] = i
        p["size"] = _size(p)
        p["center"] = _center(p)
        p["cell"] = [c / (CELL * 1000) for c in p["center"]]
    solid = [p for p in parts if p["role"] in SOLID_ROLES]
    gaps: dict[tuple[int, int], float] = {}
    for x in range(len(solid)):
        a = solid[x]
        for y in range(x + 1, len(solid)):
            b = solid[y]
            g = bbox_gap(a["lo"], a["hi"], b["lo"], b["hi"])
            if g < NEAR and mesh_gap is not None:
                m = mesh_gap(a, b)
                if m is not None:
                    g = m
            gaps[(a["id"], b["id"])] = gaps[(b["id"], a["id"])] = g
    by_id = {p["id"]: p for p in parts}
    for p in parts:
        p["nearest"], p["contacts"], p["floating"] = None, [], False
        if p["role"] not in SOLID_ROLES:
            continue
        mine = [(g, j) for (i, j), g in gaps.items() if i == p["id"]]
        if mine:
            g, j = min(mine)
            p["nearest"] = {"id": j, "name": by_id[j]["name"], "gap_mm": g}
            p["contacts"] = sorted(j for g, j in mine if g <= CONTACT)
            p["floating"] = not p["contacts"]
    overlaps = sorted(({"a": i, "b": j, "gap_mm": g} for (i, j), g in gaps.items() if i < j and g < -OVERLAP),
                      key=lambda r: r["gap_mm"])
    lo = [min(p["lo"][k] for p in parts) for k in range(3)] if parts else [0.0] * 3
    hi = [max(p["hi"][k] for p in parts) for k in range(3)] if parts else [0.0] * 3
    if mid is None:
        mid = [(lo[k] + hi[k]) / 2 for k in range(3)]
    _mirror(solid, mid)
    mirror_errors, seen = [], set()
    for p in solid:
        m = p["mirror"]
        if m and m["error_mm"] > MIRROR_LISTED:
            key = (min(p["id"], m["id"]), max(p["id"], m["id"]), m["axis"])
            if key not in seen:
                seen.add(key)
                mirror_errors.append({"a": key[0], "b": key[1], "axis": m["axis"], "error_mm": m["error_mm"]})
    for p in parts:
        p.setdefault("mirror", None)
        p.setdefault("symmetric", None)
    return {"parts": parts, "lo": lo, "hi": hi, "mid": list(mid),
            "floating": [p["id"] for p in parts if p["floating"]], "overlaps": overlaps,
            "mirror_errors": sorted(mirror_errors, key=lambda r: -r["error_mm"])}


def _mirror(solid, mid) -> None:
    """Each part's mirror partner across the X, Y or Z mid-plane: another part of the same role and size (within 2 mm
    or 5% per axis) whose center sits nearest the part's center reflected; error = the larger of that miss and the size
    difference, kept when within 5 mm or 10% of the part's largest size. Apart from that, the planes the part itself
    straddles and is symmetric about ("symmetric": the axes, and the error = how far its reflected center misses its
    own)."""
    for p in solid:
        best = None
        cap = max(5.0, 0.1 * max(p["size"]))
        for k, axis in enumerate("xyz"):
            if abs(p["center"][k] - mid[k]) < MIRROR_LISTED:
                continue
            want = list(p["center"])
            want[k] = 2 * mid[k] - want[k]
            for q in solid:
                if q is p or q["role"] != p["role"]:
                    continue
                if any(abs(p["size"][i] - q["size"][i]) > max(2.0, 0.05 * max(p["size"][i], q["size"][i]))
                       for i in range(3)):
                    continue
                dsize = max(abs(p["size"][i] - q["size"][i]) for i in range(3))
                err = max(math.dist(q["center"], want), dsize)
                if err <= cap and (best is None or err < best["error_mm"]):
                    best = {"axis": axis, "id": q["id"], "name": q["name"], "error_mm": err}
        p["mirror"] = best
        own = [(a, 2 * abs(p["center"][k] - mid[k])) for k, a in enumerate("xyz") if p["lo"][k] < mid[k] < p["hi"][k]]
        own = [(a, e) for a, e in own if e <= cap]
        p["symmetric"] = {"axes": "".join(a for a, _ in own), "error_mm": max(e for _, e in own)} if own else None


# ------------------------------------------------------------------------------------------------ targets
class TargetError(ValueError):
    """A targets block that cannot be read; the message says which and why."""


_FENCE = re.compile(r"^(`{3,}|~{3,})[ \t]*targets[ \t]*$\n(.*?)^\1[ \t]*$", re.M | re.S)
_REF = re.compile(r"^(.+)\.([xyz])$")


def parse_targets(markdown: str, source: str = "targets") -> list[dict]:
    """Every [[target]] in the markdown's ```targets fenced blocks (TOML): {"name", "a": (glob, axis), "b": (glob,
    axis) or None, "value", "tol"}. `a` and `b` are `GLOB.AXIS` (axis x, y or z of the matched parts' union box);
    without `b` the value is a length in mm, with it the ratio a/b. An optional `collections` (a list of name
    patterns) limits a target to runs that describe a matching collection, so one file can hold targets for several
    block families; any other run skips that target."""
    import tomllib
    out = []
    for n, m in enumerate(_FENCE.finditer(markdown), 1):
        try:
            data = tomllib.loads(m.group(2))
        except tomllib.TOMLDecodeError as e:
            raise TargetError(f"{source}: targets block {n}: {e}") from None
        for t in data.get("target") or []:
            name = t.get("name") or t.get("a") or "?"
            where = f"{source}: target {name!r}"
            if "a" not in t or "value" not in t or "tol" not in t:
                raise TargetError(f"{where}: needs a, value and tol")
            refs = []
            for key in ("a", "b"):
                if key not in t:
                    refs.append(None)
                    continue
                r = _REF.match(str(t[key]).strip())
                if not r:
                    raise TargetError(f"{where}: {key} = {t[key]!r} is not GLOB.AXIS (axis x, y or z)")
                refs.append((r.group(1), r.group(2)))
            try:
                value, tol = float(t["value"]), float(t["tol"])
            except (TypeError, ValueError):
                raise TargetError(f"{where}: value and tol must be numbers") from None
            colls = t.get("collections", [])
            if isinstance(colls, str):
                colls = [colls]
            if not isinstance(colls, list) or not all(isinstance(c, str) for c in colls):
                raise TargetError(f"{where}: collections must be a list of name patterns")
            out.append({"name": str(name), "a": refs[0], "b": refs[1], "value": value, "tol": tol,
                        "collections": colls})
    return out


def _measure(ref, parts):
    glob, axis = ref
    hit = [p for p in parts if fnmatch.fnmatchcase(p["name"], glob)]
    if not hit:
        return None, 0
    k = "xyz".index(axis)
    return max(p["hi"][k] for p in hit) - min(p["lo"][k] for p in hit), len(hit)


def check_targets(targets: list[dict], parts: list[dict]) -> list[dict]:
    """[{"name", "ok", "measured", "value", "tol", "why"}]: a length in mm, or a ratio when the target has `b`.
    A target with `collections` is measured on the parts of the blocks whose collection matches: a part's `block`
    (the collection the run named, nested collections included) or, in a whole-scene run, its own collection. It is
    left out when no part matches."""
    out = []
    for t in targets:
        pats = t.get("collections") or []
        mine = parts
        if pats:
            mine = [p for p in parts
                    if any(fnmatch.fnmatchcase(p.get("block") or p.get("collection", ""), pat) for pat in pats)]
            if not mine:
                continue
        r = {"name": t["name"], "value": t["value"], "tol": t["tol"], "measured": None, "ok": False, "why": ""}
        a, na = _measure(t["a"], mine)
        b, nb = _measure(t["b"], mine) if t["b"] else (None, 0)
        if a is None:
            r["why"] = f"{'.'.join(t['a'])} matches no part"
        elif t["b"] and b is None:
            r["why"] = f"{'.'.join(t['b'])} matches no part"
        elif t["b"] and b <= 0:
            r["why"] = f"{'.'.join(t['b'])} is 0 mm"
        else:
            r["measured"] = a / b if t["b"] else a
            r["ok"] = abs(r["measured"] - t["value"]) <= t["tol"]
            r["why"] = f"a {a:.1f} mm over {na} part(s)" + (f", b {b:.1f} mm over {nb} part(s)" if t["b"] else "")
        out.append(r)
    return out


# ------------------------------------------------------------------------------------------------ text
def _mm(x: float) -> str:
    return f"{x:.0f}" if abs(x) >= 100 else f"{x:.1f}".replace("-0.0", "0.0")


def _vec(v, sep=",") -> str:
    return sep.join(_mm(x) for x in v)


def text(report: dict) -> list[str]:
    """The compact text for a reader: a header, one line per part, then the lists."""
    parts = {p["id"]: p for p in report["parts"]}
    b = report["block"]
    head = f"{report.get('source') or 'scene'}"
    if report.get("collections"):
        head += f" [{', '.join(report['collections'])}]"
    lines = [f"{head}: {b['parts']} parts, {b['tris']} tris",
             f"bbox {_vec(b['size_mm'], ' x ')} mm, from ({_vec(b['lo_mm'])}) to ({_vec(b['hi_mm'])}) mm"]
    env = b.get("envelope_m")
    if env:
        f = b["fill"]
        lines.append(f"envelope {' x '.join(f'{e:g}' for e in env)} m: fill x {f['x']:.1f}% y {f['y']:.1f}% "
                     f"z {f['z']:.1f}%, bbox volume {f['volume']:.1f}%")
    lines.append(f"mirror planes at x={_mm(b['mid_mm'][0])} y={_mm(b['mid_mm'][1])} z={_mm(b['mid_mm'][2])} mm "
                 f"({b['mid_from']}); cells are {CELL:g} m from the origin")
    lines.append("#id name role [collection] size mm | center mm | cell | nearest gap mm | touches | mirror")
    for p in report["parts"]:
        near = (f"#{p['nearest']['id']} {_mm(p['nearest']['gap_mm'])}" if p["nearest"] else "-")
        touch = ",".join(f"#{j}" for j in p["contacts"][:8]) + (f"+{len(p['contacts']) - 8}"
                                                                if len(p["contacts"]) > 8 else "")
        m = p.get("mirror")
        sym = p.get("symmetric")
        mir = " ".join(x for x in (f"{m['axis']}:#{m['id']} {_mm(m['error_mm'])}" if m else "",
                                   f"self {sym['axes']} {_mm(sym['error_mm'])}" if sym else "") if x) or "-"
        flag = " FLOATING" if p["floating"] else ""
        lines.append(f"#{p['id']} {p['name']} {p['role']} [{p['collection']}] {_vec(p['size'], 'x')} | "
                     f"{_vec(p['center'])} | {','.join(f'{c:.1f}' for c in p['cell'])} | near {near} | "
                     f"touch {touch or '-'} | mirror {mir}{flag}")
    fl = report["floating"]
    lines.append(f"FLOATING ({len(fl)}): " + ("; ".join(
        f"#{i} {parts[i]['name']} (nearest #{parts[i]['nearest']['id']} at {_mm(parts[i]['nearest']['gap_mm'])} mm)"
        for i in fl) or "none"))
    ov = report["overlaps"]
    lines.append(f"OVERLAP ({len(ov)}): " + ("; ".join(
        f"#{r['a']} {parts[r['a']]['name']} x #{r['b']} {parts[r['b']]['name']} {_mm(r['gap_mm'])} mm"
        for r in ov[:40]) or "none") + (f"; +{len(ov) - 40} more" if len(ov) > 40 else ""))
    me = report["mirror_errors"]
    lines.append(f"MIRROR-ERROR ({len(me)}): " + ("; ".join(
        f"#{r['a']} {parts[r['a']]['name']} / #{r['b']} {parts[r['b']]['name']} ({r['axis']}) {_mm(r['error_mm'])} mm"
        for r in me[:40]) or "none") + (f"; +{len(me) - 40} more" if len(me) > 40 else ""))
    for t in report.get("targets") or []:
        got = "-" if t["measured"] is None else f"{t['measured']:.4g}"
        lines.append(f"{'PASS' if t['ok'] else 'FAIL'} target {t['name']}: {got} (want {t['value']:g} "
                     f"± {t['tol']:g}; {t['why']})")
    return lines


def finish(body: dict, *, envelope=None, targets=None, mid_from="bbox center", **extra) -> dict:
    """The report dict from `analyze`'s body: block totals, envelope fill and targets added."""
    parts = body["parts"]
    size = [body["hi"][k] - body["lo"][k] for k in range(3)] if parts else [0.0] * 3
    block = {"parts": len(parts), "tris": sum(p.get("tris", 0) for p in parts), "lo_mm": body["lo"],
             "hi_mm": body["hi"], "size_mm": size, "mid_mm": body["mid"], "mid_from": mid_from}
    if envelope:
        env = [float(e) for e in envelope]
        fill = {a: 100.0 * size[k] / (env[k] * 1000) for k, a in enumerate("xyz")}
        fill["volume"] = 100.0 * math.prod(size) / math.prod(e * 1000 for e in env)
        block["envelope_m"], block["fill"] = env, fill
    rep = {**extra, "block": block, "parts": parts, "floating": body["floating"], "overlaps": body["overlaps"],
           "mirror_errors": body["mirror_errors"]}
    rep["targets"] = check_targets(targets, parts) if targets else []
    return rep


# ------------------------------------------------------------------------------------------------ Blender
@contextmanager
def _included(tops):
    """Bring `tops` into the evaluated scene while reading, as the build does, then put every flag back: they, the
    collections above and inside them, included in the view layer and enabled in viewports, and their objects enabled
    in viewports (a parent's flags pass down to its children). A
    collection or object kept out is not evaluated: its objects keep the world matrix of their last evaluation and
    come out without modifiers, while the build turns these flags off and ships them as they are."""
    import bpy

    colls = {t.name: t for t in tops} | {c.name: c for t in tops for c in t.children_recursive}
    changed = []    # (owner, attribute), each flag that was True

    def clear(owner, attr):
        if getattr(owner, attr):
            changed.append((owner, attr))
            setattr(owner, attr, False)

    def walk(lc, above):
        if lc.collection.name in colls:
            for x in above + [lc]:
                clear(x, "exclude")   # a parent's flag sets its children's: those are walked, and set, after it
                if x.collection is not master:
                    clear(x.collection, "hide_viewport")
        for c in lc.children:
            walk(c, above + [lc])

    vl, master = bpy.context.view_layer, bpy.context.scene.collection
    try:
        walk(vl.layer_collection, [])
        for c in colls.values():
            if c is not master:
                clear(c, "hide_viewport")
            for o in c.objects:
                clear(o, "hide_viewport")
        if changed:
            vl.update()
        yield
    finally:
        for owner, attr in reversed(changed):
            setattr(owner, attr, True)
        if changed:
            vl.update()


def _scene_parts(collections):
    """Read the parts (evaluated meshes in world space) from the open file. Returns (parts, missing collections)."""
    import bpy

    scene = bpy.context.scene
    if collections:
        tops = [bpy.data.collections.get(c) for c in collections]
        missing = [c for c, t in zip(collections, tops) if t is None]
        tops = [t for t in tops if t is not None]
    else:
        tops, missing = [scene.collection], []
    with _included(tops):
        return _read_parts(tops, collections), missing


def _read_parts(tops, collections):
    import bpy
    from mathutils.bvhtree import BVHTree

    dg = bpy.context.evaluated_depsgraph_get()
    parts, seen = [], set()
    for top in tops:
        block = top.name if collections else None   # the block a part belongs to, for collection targets
        for ob, coll in shipping_objects(top):
            if ob.name in seen:
                continue
            seen.add(ob.name)
            ev = ob.evaluated_get(dg)
            try:
                me = ev.to_mesh()
            except RuntimeError:
                continue
            try:
                if me is None or not len(me.vertices):
                    continue
                mw = ev.matrix_world
                verts = [mw @ v.co for v in me.vertices]
                polys = [tuple(p.vertices) for p in me.polygons]
                edges: dict[tuple, int] = {}
                for p in me.polygons:
                    for e in p.edge_keys:
                        edges[e] = edges.get(e, 0) + 1
                closed = bool(edges) and all(n == 2 for n in edges.values())
                tris = sum(len(p) - 2 for p in polys)
            finally:
                ev.to_mesh_clear()
            lo = [min(v[k] for v in verts) * 1000 for k in range(3)]
            hi = [max(v[k] for v in verts) * 1000 for k in range(3)]
            parts.append({"name": ob.name, "collection": coll, "block": block, "role": role_of(ob.name, coll),
                          "lo": lo, "hi": hi, "tris": tris, "_verts": verts, "_closed": closed,
                          "_bvh": BVHTree.FromPolygons(verts, polys) if polys else None})
    return parts


SAMPLE = 20000   # most vertices tested per direction for one pair


_RAY = (0.5772, 0.5774, 0.5778)   # off every axis and diagonal, so a ray rarely runs along an edge


def _inside(bvh, v) -> bool:
    """Is `v` inside the closed mesh: an odd number of crossings along one ray (the nearest face's normal alone
    misleads near concave corners and inside hollow parts)."""
    from mathutils import Vector
    d = Vector(_RAY).normalized()
    o, n = Vector(v), 0
    for _ in range(256):
        loc, _normal, _i, _dist = bvh.ray_cast(o, d)
        if loc is None:
            break
        n += 1
        o = loc + d * 1e-6
    return n % 2 == 1


def _one_way(a, b, reach):
    """(nearest vertex-of-a to surface-of-b distance m, deepest a vertex inside b m) for a's vertices near b's box."""
    lo = [x / 1000 - reach for x in b["lo"]]
    hi = [x / 1000 + reach for x in b["hi"]]
    near = [v for v in a["_verts"] if all(lo[k] <= v[k] <= hi[k] for k in range(3))]
    if len(near) > SAMPLE:
        near = near[:: len(near) // SAMPLE + 1]
    best, deep = math.inf, 0.0
    for v in near:
        loc, normal, _i, d = b["_bvh"].find_nearest(v)
        if loc is None:
            continue
        if b["_closed"] and d > 1e-7 and (v - loc).dot(normal) < 0 and _inside(b["_bvh"], v):
            deep = max(deep, d)
        else:
            best = min(best, d)
    return best, deep


def mesh_gap(a, b):
    """Real gap in mm between two read parts: negative is the deepest vertex of one inside the other (closed meshes),
    0 where surfaces cross with no vertex inside; None when neither has faces."""
    if a.get("_bvh") is None or b.get("_bvh") is None:
        return None
    reach = NEAR / 1000
    d1, in1 = _one_way(a, b, reach)
    d2, in2 = _one_way(b, a, reach)
    deep = max(in1, in2)
    if deep > 0:
        return -deep * 1000
    if a["_bvh"].overlap(b["_bvh"]):
        return 0.0
    d = min(d1, d2)
    return d * 1000 if d < math.inf else None


def describe_scene(collections=None, envelope=None, targets=None, source=None) -> dict:
    """The open Blender scene as numbers (read only: nothing in the file changes). `collections`: names of the
    collections to describe, each taken as a block (default: everything that renders in the scene); `envelope`: the
    block's size in m (x, y, z) for the fill and for the mirror planes (then the block origin); `targets`: parsed
    targets (`parse_targets`). Returns {"source", "collections", "missing", "block", "parts", "floating", "overlaps",
    "mirror_errors", "targets"}; `text(report)` prints it. Parts in edit mode are read as last left edit mode."""
    import bpy
    parts, missing = _scene_parts(list(collections or []))
    mid = [0.0, 0.0, 0.0] if envelope else None
    body = analyze(parts, mesh_gap=mesh_gap, mid=mid)
    for p in body["parts"]:
        for k in ("_verts", "_bvh", "_closed"):
            p.pop(k, None)
    return finish(body, envelope=envelope, targets=targets,
                  mid_from="block origin" if envelope else "bbox center",
                  source=source or (bpy.path.basename(bpy.data.filepath) or "unsaved scene"),
                  collections=list(collections or []), missing=missing)
