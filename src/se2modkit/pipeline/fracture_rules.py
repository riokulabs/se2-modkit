"""The SDK's import rules for a fractured model, checked offline on the EXPORTED files (no Blender, no SDK).

Reusable for any mod's blocks (plain Python; numpy only for the hull checks, imported lazily):

    from se2modkit.pipeline import fracture_rules as FRR
    FRR.check_fractured_model(fbx_lod0_path, collision_gltf_path) -> [problem strings]   (empty: OK)

The two rules:

1. collider_mesh_group_problems: the SDK's model importer makes a collider a FRACTURE only when LOD0 has a mesh Group
   of the collider's name; it then reads that group's meshes' ColliderMeshGroups ("A|B|C") and, for ONE name naming no group in LOD0, warns and
   returns 0 fractures. The model is then imported unfractured (its Fracture_NN colliders plain ones) and the world
   load fails the game's breakable-model validation. A group with no ColliderMeshGroups only warns. So: every Fracture*
   collider needs a LOD0 group of its name whose meshes carry ColliderMeshGroups, and every ColliderMeshGroups entry
   must name a LOD0 group.
2. connectivity_problems: the physics engine's breakable compound connects two fractures when their colliders lie
   within the maximum edge distance (0.1 m default) of each other; the build fails when there is no connection at
   all, when a node is isolated or when the graph falls into separate components. So
   the fracture colliders must form ONE connected graph at that distance. Box colliders here: the gap between two
   axis-aligned boxes (Euclidean, 0 when they touch or overlap) stands in for Havok's face query (exact for boxes
   facing each other); hull_connectivity_problems runs it on the real convex shapes.

FBX: a minimal reader of binary FBX 7.x (node records, properties, zlib arrays), enough for the Model nodes'
Properties70 user properties and the meshes. glTF: node translation + mesh POSITION accessor min/max per node.
"""

import json
import os
import struct

MAX_EDGE_DISTANCE = 0.1


# ---------------------------------------------------------------------------------------------------- FBX
_ARRAY = {"f": ("f", 4), "d": ("d", 8), "l": ("q", 8), "i": ("i", 4), "b": ("b", 1)}


def _read_props(data, p, n, arrays=False):
    out = []
    for _ in range(n):
        t = chr(data[p])
        p += 1
        if t == "Y":
            out.append(struct.unpack_from("<h", data, p)[0])
            p += 2
        elif t == "C":
            out.append(bool(data[p]))
            p += 1
        elif t == "I":
            out.append(struct.unpack_from("<i", data, p)[0])
            p += 4
        elif t == "F":
            out.append(struct.unpack_from("<f", data, p)[0])
            p += 4
        elif t == "D":
            out.append(struct.unpack_from("<d", data, p)[0])
            p += 8
        elif t == "L":
            out.append(struct.unpack_from("<q", data, p)[0])
            p += 8
        elif t in "fdlib":
            cnt, enc, clen = struct.unpack_from("<III", data, p)
            raw = data[p + 12:p + 12 + clen]
            p += 12 + clen
            if not arrays:
                out.append(None)        # arrays (geometry) are not needed for the import rules
                continue
            if enc == 1:
                import zlib
                raw = zlib.decompress(raw)
            fmt, _ = _ARRAY[t]
            out.append(list(struct.unpack(f"<{cnt}{fmt}", raw)))
        elif t in "SR":
            ln = struct.unpack_from("<I", data, p)[0]
            p += 4
            raw = data[p:p + ln]
            p += ln
            out.append(raw.decode("utf-8", "replace") if t == "S" else raw)
        else:
            raise ValueError(f"FBX: unknown property type {t!r} at {p - 1}")
    return out, p


def _read_node(data, p, wide, arrays=False):
    if wide:
        end, nprops, plen = struct.unpack_from("<QQQ", data, p)
        p += 24
    else:
        end, nprops, plen = struct.unpack_from("<III", data, p)
        p += 12
    nlen = data[p]
    p += 1
    if end == 0:
        return None, p
    name = data[p:p + nlen].decode("ascii", "replace")
    p += nlen
    props, q = _read_props(data, p, nprops, arrays)
    p += plen
    kids = []
    sentinel = 25 if wide else 13
    while p < end:
        if end - p == sentinel and not any(data[p:end]):
            p = end
            break
        k, p = _read_node(data, p, wide, arrays)
        if k is None:
            break
        kids.append(k)
    return (name, props, kids), end


def fbx_nodes(path, arrays=False):
    """The FBX's top-level node records (name, properties, children); arrays=True also decodes the array
    properties (vertices, indices, UVs, materials) into lists."""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"Kaydara FBX Binary"):
        raise ValueError(f"{path}: not a binary FBX")
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    p, out = 27, []
    while p < len(data):
        n, p = _read_node(data, p, wide, arrays)
        if n is None:
            break
        out.append(n)
    return out


def fbx_model_props(path):
    """{model name: {user property: value}} of the FBX's Model nodes (Group, ColliderMeshGroups, Construction...)."""
    out = {}
    for name, _, kids in fbx_nodes(path):
        if name != "Objects":
            continue
        for kn, kp, kk in kids:
            if kn != "Model" or len(kp) < 3 or kp[2] != "Mesh":
                continue
            mname = kp[1].split("\x00")[0]
            props = {}
            for pn, _, pk in kk:
                if pn != "Properties70":
                    continue
                for qn, qp, _ in pk:
                    if qn == "P" and len(qp) >= 5 and "U" in str(qp[3]):
                        props[qp[0]] = qp[4]
            out[mname] = props
    return out


# ---------------------------------------------------------------------------------------------------- glTF
def _gltf(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def gltf_boxes(path):
    """{node name: (lo, hi)} in the glTF frame: each mesh node's POSITION accessor bounds + its translation."""
    g = _gltf(path)
    out = {}
    for n in g.get("nodes", []):
        if "mesh" not in n:
            continue
        acc = g["accessors"][g["meshes"][n["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
        t = n.get("translation") or [0, 0, 0]
        out[n["name"]] = (tuple(a + b for a, b in zip(acc["min"], t)), tuple(a + b for a, b in zip(acc["max"], t)))
    return out


def fracture_colliders(path):
    """The collision glTF's Fracture* colliders (what the importer matches to Fracture groups)."""
    return {k: v for k, v in gltf_boxes(path).items() if k.startswith("Fracture")}


# ---------------------------------------------------------------------------------------------------- rule 1
def collider_mesh_group_problems(props, colliders):
    """props: fbx_model_props of the LOD0 FBX; colliders: names of the collision glTF's Fracture* colliders."""
    groups = {}
    for m, p in props.items():
        g = p.get("Group") or p.get("group")
        if g:
            groups.setdefault(g, []).append((m, p))
    out = []
    for m, p in sorted(props.items()):
        cmg = p.get("ColliderMeshGroups")
        if cmg is None:
            continue
        for g in str(cmg).split("|"):
            if g not in groups:
                out.append(f"{m}: ColliderMeshGroups names {g!r}, no LOD0 group of that name (every fracture "
                           f"would be dropped)")
    for c in sorted(colliders):
        if c not in groups:
            out.append(f"collider {c}: no LOD0 mesh group of that name (not a fracture)")
        elif not any(p.get("ColliderMeshGroups") for _, p in groups[c]):
            out.append(f"collider {c}: its group's meshes carry no ColliderMeshGroups")
    stray = sorted(g for g in groups if g.startswith("Fracture") and not g.endswith(("_Hide", "_Inner", "_Support",
                                                                                     "_Frame")) and g not in colliders)
    out += [f"group {g}: a fracture group with no collider" for g in stray]
    return out


# ---------------------------------------------------------------------------------------------------- rule 2
def box_gap(a, b):
    """Euclidean distance between two axis-aligned boxes (lo, hi); 0 when they touch or overlap."""
    return sum(max(0.0, b[0][i] - a[1][i], a[0][i] - b[1][i]) ** 2 for i in range(3)) ** 0.5


def _components(names, linked):
    adj = {n: set() for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if linked(a, b):
                adj[a].add(b)
                adj[b].add(a)
    seen, comps = set(), []
    for n in names:
        if n in seen:
            continue
        stack, comp = [n], []
        seen.add(n)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(sorted(comp))
    return comps


def connectivity_components(boxes, max_dist=MAX_EDGE_DISTANCE):
    """[[names]] of the graph joining colliders within max_dist (boxes: {name: (lo, hi)})."""
    return _components(sorted(boxes), lambda a, b: box_gap(boxes[a], boxes[b]) <= max_dist + 1e-9)


def connectivity_problems(boxes, max_dist=MAX_EDGE_DISTANCE):
    if len(boxes) < 2:
        return [f"{len(boxes)} fracture collider(s): a breakable collider needs at least 2"]
    comps = connectivity_components(boxes, max_dist)
    if len(comps) > 1:
        return [f"fracture colliders fall into {len(comps)} unconnected groups at {max_dist} m: {comps}"]
    return []


def check_fractured_model(fbx_lod0, collision_gltf, max_dist=MAX_EDGE_DISTANCE):
    """Both rules on one exported fractured model (its LOD0 FBX and its collision glTF)."""
    cols = fracture_colliders(collision_gltf)
    return collider_mesh_group_problems(fbx_model_props(fbx_lod0), set(cols)) + connectivity_problems(cols, max_dist)


# ---------------------------------------------------------------------------------------------------- FBX content
_VOLATILE = ("FBXHeaderExtension", "FileId", "CreationTime", "Creator", "GlobalSettings", "Documents", "Takes")


def fbx_content(path):
    """An FBX's content without what changes between two exports of the same scene (header timestamps, file id,
    the scene's time settings, object uids): the Objects (uid dropped, in file order) and the Connections (uids
    replaced by their object's index). Two exports of the same meshes compare equal."""
    nodes = fbx_nodes(path, arrays=True)
    objs, ids = [], {}
    conns = []
    for name, props, kids in nodes:
        if name == "Objects":
            for i, (kn, kp, kk) in enumerate(kids):
                if kp and isinstance(kp[0], int):
                    ids[kp[0]] = i
                objs.append((kn, kp[1:] if kp and isinstance(kp[0], int) else kp, kk))
    for name, props, kids in nodes:
        if name == "Connections":
            for kn, kp, kk in kids:
                conns.append((kn, kp[0], ids.get(kp[1], kp[1]), ids.get(kp[2], kp[2]), *kp[3:]))
    return objs, conns


def fbx_same_content(a, b):
    try:
        return fbx_content(a) == fbx_content(b)
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------------------------------- checklist
# The checklist on the exported files: recognition, Fractured <-> Damaged parity, cut UV density, and the hull-volume
# rule. numpy is imported lazily (only these need it); the two rules above stay standard library only.
HELPER_PREFIXES = ("occupancy", "mountpoints")      # the importer takes these out before fracture matching
ENGINE_MAX_FRACTURES = 16                           # the importer's fracture cap
CUT_PRESET = "Fracture"                             # a break-face object's ConstructionMeshPreset (vanilla _Cut)


def _objects(nodes):
    for name, _, kids in nodes:
        if name == "Objects":
            return kids
    return []


def _p70(kids):
    out = {}
    for pn, _, pk in kids:
        if pn == "Properties70":
            for qn, qp, _ in pk:
                if qn == "P" and len(qp) >= 5:
                    out[qp[0]] = qp[4] if len(qp) == 5 else tuple(qp[4:])
    return out


def fbx_meshes(path):
    """{model name: {"props": user properties, "verts": [(x, y, z)], "polys": [[vertex index]], "uv": [[(u, v)] per
    polygon corner], "materials": [material name per slot], "poly_mat": [slot per polygon]}} of a binary FBX written
    by the exporter (one geometry per mesh model, identity transform but the FBX axis rotation, metres: the vertices
    are in the Blender block frame)."""
    nodes = fbx_nodes(path, arrays=True)
    objs = _objects(nodes)
    geo, models, mats = {}, {}, {}
    for kn, kp, kk in objs:
        if kn == "Geometry" and len(kp) >= 3 and kp[2] == "Mesh":
            g = {c[0]: c for c in kk}
            v = g["Vertices"][1][0]
            idx = g["PolygonVertexIndex"][1][0]
            polys, cur = [], []
            for i in idx:
                if i < 0:
                    cur.append(~i)
                    polys.append(cur)
                    cur = []
                else:
                    cur.append(i)
            uv = []
            if "LayerElementUV" in g:
                le = {c[0]: c[1] for c in g["LayerElementUV"][2]}
                u, ui = le["UV"][0], le.get("UVIndex", [None])[0]
                k = 0
                for poly in polys:
                    row = []
                    for _ in poly:
                        j = ui[k] if ui is not None else k
                        row.append((u[2 * j], u[2 * j + 1]))
                        k += 1
                    uv.append(row)
            pm = [0] * len(polys)
            if "LayerElementMaterial" in g:
                le = {c[0]: c[1] for c in g["LayerElementMaterial"][2]}
                m = le["Materials"][0]
                pm = m if len(m) == len(polys) else [m[0]] * len(polys)
            geo[kp[0]] = {"verts": [tuple(v[i:i + 3]) for i in range(0, len(v), 3)], "polys": polys, "uv": uv,
                          "poly_mat": pm}
        elif kn == "Model" and len(kp) >= 3 and kp[2] == "Mesh":
            models[kp[0]] = (kp[1].split("\x00")[0], {k: v for k, v in _p70(kk).items()})
        elif kn == "Material":
            mats[kp[0]] = kp[1].split("\x00")[0]
    link_geo, link_mat = {}, {}
    for name, _, kids in nodes:
        if name != "Connections":
            continue
        for kn, kp, _ in kids:
            if kp[0] != "OO":
                continue
            if kp[1] in geo and kp[2] in models:
                link_geo[kp[2]] = kp[1]
            elif kp[1] in mats and kp[2] in models:
                link_mat.setdefault(kp[2], []).append(mats[kp[1]])
    out = {}
    for mid, (mname, props) in models.items():
        user = {k: v for k, v in props.items() if k in ("Group", "ColliderMeshGroups") or
                k.startswith("ConstructionMesh")}
        g = geo.get(link_geo.get(mid), {"verts": [], "polys": [], "uv": [], "poly_mat": []})
        out[mname] = dict(g, props=user, materials=link_mat.get(mid, []))
    return out


def mesh_parts(meshes):
    """The model's mesh parts as Keen names them (MeshGeometryMetadata): <GROUP>-<material> per used material slot,
    the bare material name when the mesh has no Group."""
    out = set()
    for m in meshes.values():
        g = m["props"].get("Group")
        for i in sorted(set(m["poly_mat"])):
            if i < len(m["materials"]):
                out.add(f"{g}-{m['materials'][i]}" if g else m["materials"][i])
    return out


def gltf_hulls(path):
    """{node name: [(x, y, z)] in the BLENDER block frame} of a collision glTF's collider meshes (glTF is Y up:
    Blender (x, y, z) = glTF (x, -z, y)), with their translations."""
    g = _gltf(path)
    with open(os.path.join(os.path.dirname(path), g["buffers"][0]["uri"]), "rb") as f:
        buf = f.read()
    out = {}
    for n in g.get("nodes", []):
        if "mesh" not in n:
            continue
        acc = g["accessors"][g["meshes"][n["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
        bv = g["bufferViews"][acc["bufferView"]]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        flat = struct.unpack_from(f"<{3 * acc['count']}f", buf, off)
        t = n.get("translation") or [0, 0, 0]
        out[n["name"]] = [(flat[i] + t[0], -(flat[i + 2] + t[2]), flat[i + 1] + t[1])
                          for i in range(0, len(flat), 3)]
    return out


def recognition_problems(props, collider_names, size=None):
    """The SDK importer's fracture recognition: a collider is a fracture
    iff its name equals a LOD0 Group; helpers (occupancy* / mountpoints*) aside, fractures and plain colliders may not
    mix; 2 .. 16 fractures, and, given the model size, no more than the SDK's table for it (a warning we keep clear of);
    plus rule 1 (every ColliderMeshGroups entry names a LOD0 group)."""
    groups = {p.get("Group") for p in props.values() if p.get("Group")}
    cols = [c for c in collider_names if not c.lower().startswith(HELPER_PREFIXES)]
    frac = [c for c in cols if c in groups]
    out = []
    plain = sorted(set(cols) - set(frac))
    if frac and plain:
        out.append(f"fracture and plain colliders mixed (B9): plain {plain}")
    if not 2 <= len(frac) <= ENGINE_MAX_FRACTURES:
        out.append(f"{len(frac)} recognised fractures: a breakable collider needs 2 .. {ENGINE_MAX_FRACTURES}")
    if size is not None:
        from .fracture import max_fractures
        if len(frac) > max_fractures(size):
            out.append(f"{len(frac)} fractures above Keen's {max_fractures(size)} for a {max(size):.2f} m model")
    out += collider_mesh_group_problems(props, set(frac) | {c for c in cols if c.startswith("Fracture")})
    return out


def cut_uv_density(meshes):
    """The break faces' texel density: the area-weighted median of sqrt(UV area / 3D area) over the triangles of
    every break-face object (ConstructionMeshPreset Fracture, or all FracturedMaterial01: a hidden part's caps). None
    when the model has no break faces."""
    rows = []
    for m in meshes.values():
        cap = m["props"].get("ConstructionMeshPreset") == CUT_PRESET or (m["materials"] and all(
            x == "FracturedMaterial01" for x in m["materials"]))
        if not cap or not m["uv"]:
            continue
        v = m["verts"]
        for poly, uv in zip(m["polys"], m["uv"]):
            for k in range(1, len(poly) - 1):
                a, b, c = (v[poly[0]], v[poly[k]], v[poly[k + 1]])
                ab = [b[i] - a[i] for i in range(3)]
                ac = [c[i] - a[i] for i in range(3)]
                cr = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
                A = 0.5 * sum(x * x for x in cr) ** 0.5
                (u0, v0), (u1, v1), (u2, v2) = uv[0], uv[k], uv[k + 1]
                UA = 0.5 * abs((u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0))
                if A > 1e-10:
                    rows.append(((UA / A) ** 0.5, A))
    if not rows:
        return None
    rows.sort()
    half, acc = sum(a for _, a in rows) / 2, 0.0
    for d, a in rows:
        acc += a
        if acc >= half:
            return d
    return rows[-1][0]


def zone_geometry(meshes, zone):
    """The vertices of a zone's mesh parts: every mesh whose Group is in the zone's ColliderMeshGroups (its skin,
    _Hide) and its break faces (Group = the zone)."""
    members = set()
    for m in meshes.values():
        if m["props"].get("Group") == zone and m["props"].get("ColliderMeshGroups"):
            members |= set(str(m["props"]["ColliderMeshGroups"]).split("|"))
    members.add(zone)
    return [p for m in meshes.values() if m["props"].get("Group") in members for p in m["verts"]]


def _fib_dirs(n=64):
    import math
    out = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    g = math.pi * (3 - 5 ** 0.5)
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        r = (1 - z * z) ** 0.5
        out.append((r * math.cos(g * i), r * math.sin(g * i), z))
    return out


def hull_fill(hull, points, n=22):
    """The share of a convex collider's volume (hull: its vertices) that lies inside its zone's solid extent
    (points: the zone's geometry; its extent = the convex region bounded by the geometry's support planes in 70
    directions, a close outer approximation of the geometry's convex hull). Sampled on an n^3 lattice over the
    collider's box: (lattice points inside both) / (inside the collider). Health is the collider's VOLUME share,
    so a low fill means health and phantom collision bought with empty space."""
    import numpy as np
    H = np.asarray(hull, float)
    P = np.asarray(points, float)
    lo, hi = H.min(0), H.max(0)
    ax = [np.linspace(lo[i], hi[i], n + 2)[1:-1] for i in range(3)]
    Q = np.stack(np.meshgrid(*ax, indexing="ij"), -1).reshape(-1, 3)
    N, d = hull_planes(H)                       # the collider exactly: its facets' planes
    inside_hull = np.all(Q @ N.T <= d + 1e-9, axis=1)
    D = np.asarray(_fib_dirs(), float)
    inside_geo = np.all(Q @ D.T <= (P @ D.T).max(0) + 1e-9, axis=1)
    k = inside_hull.sum()
    return float((inside_hull & inside_geo).sum() / k) if k else 0.0


def hull_planes(H, eps=1e-7):
    """(normals, offsets) of the facets of the convex hull of the point set H (n . p <= d inside): every plane
    through three of its points with all the others on one side (brute force; a collider has a few dozen)."""
    import itertools

    import numpy as np
    H = np.asarray(H, float)
    H = H[np.unique(np.round(H, 7), axis=0, return_index=True)[1]]
    out = {}
    for i, j, k in itertools.combinations(range(len(H)), 3):
        n = np.cross(H[j] - H[i], H[k] - H[i])
        ln = np.linalg.norm(n)
        if ln < 1e-12:
            continue
        n = n / ln
        s = H @ n - H[i] @ n
        if np.all(s <= eps):
            pass
        elif np.all(s >= -eps):
            n = -n
        else:
            continue
        key = tuple(np.round(n, 6))
        out[key] = (n, float(H[i] @ n))
    N = np.array([v[0] for v in out.values()])
    return N, np.array([v[1] for v in out.values()])


def hull_gap(a, b, iters=64):
    """Euclidean distance between the convex hulls of two point sets (GJK on their Minkowski difference; 0 when
    they touch or overlap): the stand-in for the physics engine's piece-to-piece distance query."""
    import numpy as np
    A, B = np.asarray(a, float), np.asarray(b, float)

    def support(d):
        return A[np.argmin(A @ d)] - B[np.argmax(B @ d)]

    def closest(S):
        best = None
        k = len(S)
        for mask in range(1, 1 << k):
            idx = [i for i in range(k) if mask >> i & 1]
            P = S[idx]
            m = len(idx)
            if m == 1:
                lam = np.array([1.0])
            else:
                M = np.zeros((m + 1, m + 1))
                M[:m, :m] = P @ P.T
                M[:m, m] = 1
                M[m, :m] = 1
                rhs = np.zeros(m + 1)
                rhs[m] = 1
                try:
                    sol = np.linalg.solve(M, rhs)
                except np.linalg.LinAlgError:
                    continue
                lam = sol[:m]
                if np.any(lam < -1e-12):
                    continue
            x = lam @ P
            nx = float(x @ x)
            if best is None or nx < best[0] - 1e-15:
                best = (nx, x, P)
        return best[1], best[2]

    v = A[0] - B[0]
    S = v[None, :]
    for _ in range(iters):
        if float(v @ v) < 1e-14:
            return 0.0
        w = support(v)
        if float(v @ v) - float(v @ w) <= 1e-10:
            break
        S = np.vstack([S, w])
        v, S = closest(S)
        if len(S) == 4:
            return 0.0 if float(v @ v) < 1e-12 else float(np.sqrt(v @ v))
    return float(np.sqrt(v @ v))


def hull_connectivity_problems(hulls, max_dist=MAX_EDGE_DISTANCE):
    """Rule 2 on the colliders' real convex shapes (hulls: {name: vertices}); the box version stands in for boxes."""
    names = sorted(hulls)
    if len(names) < 2:
        return [f"{len(names)} fracture collider(s): a breakable collider needs at least 2"]
    comps = _components(names, lambda a, b: hull_gap(hulls[a], hulls[b]) <= max_dist + 1e-9)
    if len(comps) > 1:
        return [f"fracture colliders fall into {len(comps)} unconnected groups at {max_dist} m: {comps}"]
    return []
