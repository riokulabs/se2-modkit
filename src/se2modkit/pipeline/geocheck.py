"""Geometry checks a block's model must pass before a picture of it goes to anyone (plain numpy, no Blender).

Each part is a mesh plus what it is for (`role`: mount, strut, pin, lug, cheek, clevis, skin, decal, ...), read from
the part's `se2_role` / `se2_axis` / `se2_point` / `se2_links` properties or, failing that, its name prefix
(`pin_`, `lug_`, `mount_`, ...). `run` returns failures (the picture does not go out) and warnings:

  load_path     every part reaches a mount part through parts that touch it (within 2 mm); decals and skins exempt
  pin_axis      a pin is square to its lug or clevis (within 1 degree; untagged axes are read from the parts' shape)
                and on its eye (within 2 mm, when the eye is tagged with se2_point)
  coplanar      two parts with faces in the same plane facing the same way (the game's depth test flickers there)
  poke_through  a part that passes clean through another part it is not linked to
  box_uv        a curved part (a tube, a short disc, a sphere) whose UVs are a box projection (stretched, seamy
                texture on round surfaces)
  cell_groups   the occupied cells split into more than 100 boxes (the game crashes on removal past 255)
  tris          more triangles than the kit's budget
  untagged      parts with no role (a warning: they still count for the load path, as structure)
  no_mount      no part is tagged as the mount (a warning: the load path can't be checked)
  error         the checks themselves stopped (`guarded`): the set fails rather than going out unchecked
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..model.placement import build_groups
from . import zfight

__all__ = ["CONTACT", "MAX_CELL_GROUPS", "Part", "Report", "guarded", "run"]

CONTACT = 0.002          # m: parts this close touch
PIN_ANGLE = 1.0          # degrees
PIN_OFFSET = 0.002       # m
MAX_CELL_GROUPS = 100
EXEMPT = ("decal", "skin")
PIN_PARTNERS = ("lug", "cheek", "clevis")
ROLES = ("mount", "strut", "pin", "lug", "cheek", "clevis", "skin", "decal", "pipe", "bracket", "panel")


@dataclass
class Part:
    name: str
    verts: np.ndarray                  # (N, 3) world positions, meters
    tris: np.ndarray                   # (M, 3) vertex indices
    role: str | None = None
    axis: tuple | None = None          # pins and lugs: the pin axis
    point: tuple | None = None         # pins and lugs: a point on the pin axis (the eye's center)
    links: tuple = ()                  # names of parts this one is meant to sit in or pass through
    uvs: np.ndarray | None = None      # (M, 3, 2) per triangle corner, when known
    local: np.ndarray | None = None    # (N, 3) the same vertices in the object's own frame (UVs are projected there)

    def __post_init__(self):
        self.verts = np.asarray(self.verts, float).reshape(-1, 3)
        self.tris = np.asarray(self.tris, int).reshape(-1, 3)
        if self.local is not None:
            self.local = np.asarray(self.local, float).reshape(-1, 3)
        if self.role is None:
            base = self.name.split(".")[0].lower()
            self.role = next((r for r in ROLES if base == r or base.startswith(r + "_")), None)

    @property
    def corners(self) -> np.ndarray:
        return self.verts[self.tris]

    def box(self, pad: float = 0.0):
        return self.verts.min(0) - pad, self.verts.max(0) + pad


@dataclass
class Report:
    failures: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    tris: int = 0
    parts: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures

    def add(self, failure: bool, check: str, parts, detail: str) -> None:
        (self.failures if failure else self.warnings).append({"check": check, "parts": list(parts), "detail": detail})

    def to_dict(self) -> dict:
        return {"ok": self.ok, "failures": self.failures, "warnings": self.warnings, "tris": self.tris,
                "parts": self.parts}

    def write(self, path: Path) -> Path:
        path = Path(path)
        tmp = path.with_suffix(".json.tmp")      # whole or not at all: a reader never sees half a report
        tmp.write_text(json.dumps(self.to_dict(), indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path


# ---------------------------------------------------------------- geometry helpers

def _overlap(a, b, pad=0.0) -> bool:
    (alo, ahi), (blo, bhi) = a.box(pad), b.box(pad)
    return bool(np.all(alo <= bhi) and np.all(blo <= ahi))


def _near(points: np.ndarray, P: np.ndarray, eps: float, chunk: int = 200_000) -> np.ndarray:
    """For each point: is it within `eps` of a triangle's plane and over that triangle? (The in-plane slack is 1 % in
    barycentric units, so it scales with the triangle.)"""
    out = np.zeros(len(points), bool)
    if not len(points) or not len(P):
        return out
    a, e1, e2 = P[:, 0], P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    n = np.cross(e1, e2)
    ln = np.linalg.norm(n, axis=1)
    keep = ln > 1e-12
    a, e1, e2, n = a[keep], e1[keep], e2[keep], n[keep] / ln[keep, None]
    d00, d01, d11 = (e1 * e1).sum(1), (e1 * e2).sum(1), (e2 * e2).sum(1)
    den = d00 * d11 - d01 * d01
    step = max(1, chunk // max(1, len(a)))
    for i in range(0, len(points), step):
        p = points[i:i + step, None, :] - a[None]
        dist = np.abs((p * n[None]).sum(2))
        d20, d21 = (p * e1[None]).sum(2), (p * e2[None]).sum(2)
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
        t = 0.01
        out[i:i + step] = ((dist <= eps) & (v >= -t) & (w >= -t) & (v + w <= 1 + t)).any(1)
    return out


def _inside(points: np.ndarray, P: np.ndarray, chunk: int = 200_000) -> np.ndarray:
    """Ray parity: which points lie inside the closed mesh P (a slightly skewed +X ray, Moller-Trumbore). Meaningless
    for an open mesh (a single-sided panel), where only `_near` contact counts."""
    out = np.zeros(len(points), bool)
    if not len(points) or not len(P):
        return out
    d = np.array([1.0, 1.3e-4, 2.7e-4])
    d /= np.linalg.norm(d)
    a, e1, e2 = P[:, 0], P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    h = np.cross(d, e2)
    det = (e1 * h).sum(1)
    ok = np.abs(det) > 1e-14
    a, e1, e2, h, inv = a[ok], e1[ok], e2[ok], h[ok], 1.0 / det[ok]
    step = max(1, chunk // max(1, len(a)))
    for i in range(0, len(points), step):
        s = points[i:i + step, None, :] - a[None]
        u = (s * h[None]).sum(2) * inv
        q = np.cross(s, e1[None])
        v = (q * d).sum(2) * inv
        t = (q * e2[None]).sum(2) * inv
        hit = (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
        out[i:i + step] = hit.sum(1) % 2 == 1
    return out


def _edge_samples(p: Part, lo, hi) -> np.ndarray:
    """Start, middle and end of each of p's edges clipped to the box [lo, hi]: a part laid across a longer one, or
    lapped through it, touches it along edges whose ends are outside the overlap."""
    e = np.concatenate([p.tris[:, [0, 1]], p.tris[:, [1, 2]], p.tris[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    a, b = p.verts[e[:, 0]], p.verts[e[:, 1]]
    d = b - a
    t0, t1 = np.zeros(len(e)), np.ones(len(e))
    with np.errstate(divide="ignore", invalid="ignore"):
        for k in range(3):
            par = np.abs(d[:, k]) < 1e-12
            outside = par & ((a[:, k] < lo[k]) | (a[:, k] > hi[k]))
            t0[outside], t1[outside] = 1.0, 0.0
            ta, tb = (lo[k] - a[:, k]) / d[:, k], (hi[k] - a[:, k]) / d[:, k]
            near_t, far_t = np.minimum(ta, tb), np.maximum(ta, tb)
            t0 = np.where(par, t0, np.maximum(t0, near_t))
            t1 = np.where(par, t1, np.minimum(t1, far_t))
    keep = t0 <= t1
    a, d, t0, t1 = a[keep], d[keep], t0[keep], t1[keep]
    return np.concatenate([a + d * t0[:, None], a + d * ((t0 + t1) / 2)[:, None], a + d * t1[:, None]])


def _touch(a: Part, b: Part) -> bool:
    if not _overlap(a, b, CONTACT):
        return False
    lo, hi = np.maximum(a.box()[0], b.box()[0]) - CONTACT, np.minimum(a.box()[1], b.box()[1]) + CONTACT
    for p, q in ((a, b), (b, a)):
        pts = _edge_samples(p, lo, hi)
        if len(pts) and (_near(pts, q.corners, CONTACT).any() or _inside(pts, q.corners).any()):
            return True
    return False


def _linked(a: Part, b: Part) -> bool:
    return b.name in a.links or a.name in b.links


# ---------------------------------------------------------------- checks

def _load_path(parts, report):
    mounts = [i for i, p in enumerate(parts) if p.role == "mount"]
    if not mounts:
        report.add(False, "no_mount", [], "no part is tagged as the mount (se2_role = mount, or a mount_ name)")
        return
    nodes = [i for i, p in enumerate(parts) if p.role not in EXEMPT]
    reached, frontier = set(mounts), list(mounts)
    while frontier:
        i = frontier.pop()
        for j in nodes:
            if j not in reached and (_linked(parts[i], parts[j]) or _touch(parts[i], parts[j])):
                reached.add(j)
                frontier.append(j)
    lost = [parts[i].name for i in nodes if i not in reached]
    if lost:
        report.add(True, "load_path", lost, f"{len(lost)} part(s) don't reach a mount through parts that touch "
                                            f"them (within {CONTACT * 1000:g} mm)")


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _pin_axis(parts, report):
    for pin in (p for p in parts if p.role == "pin"):
        partners = [p for p in parts if p.role in PIN_PARTNERS and p.name in pin.links]
        if not partners:
            c = pin.verts.mean(0)
            near = [p for p in parts if p.role in PIN_PARTNERS and _overlap(p, pin, CONTACT)]
            partners = sorted(near, key=lambda p: np.linalg.norm(p.verts.mean(0) - c))[:1]
        if not partners:
            report.add(False, "pin_axis", [pin.name], "pin with no lug or clevis around it")
            continue
        pa = _unit(pin.axis) if pin.axis is not None else _principal(pin.verts, longest=True)
        pp = np.asarray(pin.point, float) if pin.point is not None else pin.verts.mean(0)
        for lug in partners:
            why = []
            la = (_unit(lug.axis) if lug.axis is not None
                  else _principal(lug.verts, longest=False) if lug.role == "lug" else None)
            if pa is None or la is None:
                report.add(False, "pin_axis", [pin.name, lug.name],
                           "the axis can't be read from the shape (a clevis, or a short pin): tag se2_axis to check it")
            else:
                ang = math.degrees(math.acos(min(1.0, abs(float(pa @ la)))))
                if ang > PIN_ANGLE:
                    why.append(f"{ang:.1f} degrees off the {lug.role}'s axis")
            if lug.point is not None and pa is not None:
                off = np.linalg.norm(np.cross(np.asarray(lug.point, float) - pp, pa))
                if off > PIN_OFFSET:
                    why.append(f"{off * 1000:.1f} mm off the {lug.role}'s eye")
            elif lug.point is None:
                report.add(False, "pin_axis", [pin.name, lug.name],
                           f"the {lug.role}'s eye isn't tagged (se2_point), so the pin's offset wasn't checked")
            if why:
                report.add(True, "pin_axis", [pin.name, lug.name], "; ".join(why))


def _principal(verts: np.ndarray, *, longest: bool) -> np.ndarray | None:
    """A part's longest (a pin) or thinnest (a lug plate) direction from its vertices' spread, or None when the shape
    doesn't say clearly (a pin no longer than it is wide, a lug no thinner than it is wide)."""
    w, v = np.linalg.eigh(np.cov((verts - verts.mean(0)).T))
    if longest:
        return v[:, 2] if w[2] > 2.0 * w[1] else None
    return v[:, 0] if w[0] < 0.5 * w[1] else None


def _coplanar(parts, report):
    index, recs = zfight.PlaneIndex(), []
    for k, p in enumerate(parts):
        if p.role in ("decal",):
            continue
        P = p.corners
        n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        ln = np.linalg.norm(n, axis=1)
        for tri, nn, l in zip(P, n, ln):
            if l < 1e-12:
                continue
            nu = tuple(float(x) for x in nn / l)
            pts = [tuple(float(x) for x in c) for c in tri]
            d = float(np.dot(nu, pts[0]))
            index.add(nu, d, pts, k)
            recs.append((nu, d, pts, k))
    pairs = zfight.coplanar_pairs(recs, index, same=True)
    for (ka, kb), area in sorted(pairs.items()):
        if ka != kb and not _linked(parts[ka], parts[kb]):
            report.add(True, "coplanar", [parts[ka].name, parts[kb].name],
                       f"{area * 1e4:.2f} cm2 of faces in the same plane, facing the same way")


def _poke_through(parts, report):
    for a in parts:
        for b in parts:
            if a is b or a.role in EXEMPT or b.role in EXEMPT or _linked(a, b) or not _overlap(a, b):
                continue
            (alo, ahi), (blo, bhi) = a.box(), b.box()
            for k in range(3):
                others = [i for i in range(3) if i != k]
                through = alo[k] < blo[k] - CONTACT and ahi[k] > bhi[k] + CONTACT
                within = all(alo[i] >= blo[i] - 1e-9 and ahi[i] <= bhi[i] + 1e-9 for i in others)
                if not (through and within):
                    continue
                probe = (alo + ahi) / 2
                probe[k] = (blo[k] + bhi[k]) / 2
                if _inside(probe[None], b.corners)[0]:
                    report.add(True, "poke_through", [a.name, b.name], f"passes clean through along {'xyz'[k]}")
                    break


def _curved(nu: np.ndarray, area: np.ndarray) -> np.ndarray | None:
    """Which faces make a part's curved surface (unit normals `nu`, weights `area`), or None when it isn't curved:
    most of the whole surface faces away from the six axes (a sphere, a long tube), or most of the band of faces
    around one axis does (a short disc or a ring, whose flat caps outweigh the rim). A few chamfers don't make a box
    curved: they are a small share of the band they sit in."""
    off_axis = np.abs(nu).max(1) < math.cos(math.radians(5))
    if area[off_axis].sum() > 0.5 * area.sum():
        return off_axis
    for k in range(3):
        band = np.abs(nu[:, k]) < math.sin(math.radians(5))
        rim = band & off_axis
        if rim.any() and area[rim].sum() > 0.5 * area[band].sum():
            return rim
    return None


def _box_uv(parts, report):
    for p in parts:
        if p.uvs is None or len(p.tris) < 8:
            continue
        V = p.local if p.local is not None else p.verts
        P = V[p.tris]
        n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-12
        nu, area = n[keep] / ln[keep, None], ln[keep]
        curved = _curved(nu, area)
        if curved is None:
            continue
        k = np.argmax(np.abs(nu[curved]), axis=1)
        proj = np.array([[np.delete(c, kk) for c in tri] for tri, kk in zip(P[keep][curved], k)]).reshape(-1, 2)
        uv = np.asarray(p.uvs, float)[keep][curved].reshape(-1, 2)
        resid = 0.0
        for ch in range(2):
            A = np.c_[proj[:, ch], np.ones(len(proj))]
            coef, *_ = np.linalg.lstsq(A, uv[:, ch], rcond=None)
            spread = uv[:, ch].std() or 1.0
            resid = max(resid, float(np.sqrt(np.mean((A @ coef - uv[:, ch]) ** 2)) / spread))
        if resid < 0.02:
            report.add(True, "box_uv", [p.name], "curved part with box-projected UVs: unwrap it")


def run(parts, *, cells=None, tris_budget: int | None = None) -> Report:
    parts = list(parts)
    report = Report(tris=int(sum(len(p.tris) for p in parts)), parts=len(parts))
    untagged = [p.name for p in parts if p.role is None]
    if untagged:
        report.add(False, "untagged", untagged, f"{len(untagged)} part(s) have no role; they count as structure")
    if parts:
        _load_path(parts, report)
        _pin_axis(parts, report)
        _coplanar(parts, report)
        _poke_through(parts, report)
        _box_uv(parts, report)
    if cells is not None:
        groups = len(build_groups(sorted(set(map(tuple, cells)))))
        if groups > MAX_CELL_GROUPS:
            report.add(True, "cell_groups", [], f"occupied cells split into {groups} boxes (keep it to "
                                                f"{MAX_CELL_GROUPS}; the game crashes on removal past 255)")
    if tris_budget is not None and report.tris > tris_budget:
        report.add(True, "tris", [], f"{report.tris} triangles, budget {tris_budget}")
    return report


def guarded(check) -> Report:
    """`check()` (which returns a Report), or a report failing with the error it raised, so whoever writes the set's
    report still writes it and the error is listed with the other problems."""
    try:
        return check()
    except Exception as e:   # noqa: BLE001 - any error in the checks must fail the set, not lose its report
        report = Report()
        report.add(True, "error", [], f"the geometry checks stopped: {type(e).__name__}: {e}")
        return report
