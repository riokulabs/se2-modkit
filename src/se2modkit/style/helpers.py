"""Shared modelling helpers for the house style: bolts, lamps, stencils, labels, hazard triangles, chamfered plates
and the maker plate. Sizes come from the active style profile (`use()`; vanilla by default), so a mod and its checks
read the same numbers.

The outline helpers (`oct_pts`, `rect`, `inset`, `maker_mark_layout`) are plain Python. The drawing helpers need
Blender: they import `se2modkit.model.geom` / `decals` when called, never at import time. Every drawing helper writes
into se2modkit `Part`s on a `geom.Face` frame F (a along u, b along v, d along the outward normal).
"""

from __future__ import annotations

from ..model import config
from .profile import Profile, load

_ACTIVE: list[Profile] = []
# Optional callbacks a mod installs to record what was drawn (e.g. a connectivity model):
#   HOOKS["bolt"](seat_point, normal, radius)    after every bolt
#   HOOKS["part"](kind, a, b, size)              after every lamp and maker plate
HOOKS = {"bolt": None, "part": None}


def use(profile: Profile | str) -> Profile:
    """Make `profile` (a Profile or a profile name) the one the helpers read their sizes from."""
    p = load(profile) if isinstance(profile, str) else profile
    _ACTIVE[:] = [p]
    return p


def active() -> Profile:
    if not _ACTIVE:
        _ACTIVE.append(load("vanilla"))
    return _ACTIVE[0]


def const(key: str):
    """A value of the active profile (`const("hw.bolt_r")`)."""
    return active().get(key)


def role(name: str) -> str:
    """The library material of a role (`role("dark")` -> PaintedMetalDarker)."""
    return active().get(f"roles.{name}")


def _hook(name, *args):
    fn = HOOKS.get(name)
    if fn is not None:
        fn(*args)


# ------------------------------------------------------------------------------------------------ 2D outlines
def oct_pts(a0, b0, a1, b1, c):
    """Rectangle [a0,a1] x [b0,b1] with 45 degree clipped corners (Keen panel shape), counter-clockwise."""
    if c <= 0:
        return rect(a0, b0, a1, b1)
    return [(a0 + c, b0), (a1 - c, b0), (a1, b0 + c), (a1, b1 - c), (a1 - c, b1), (a0 + c, b1), (a0, b1 - c),
            (a0, b0 + c)]


def rect(a0, b0, a1, b1):
    return [(a0, b0), (a1, b0), (a1, b1), (a0, b1)]


def inset(pts, c):
    """A convex polygon moved inward by c on every edge (either winding; the result keeps the input's winding)."""
    n = len(pts)
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    s = 1.0 if area > 0 else -1.0
    lines = []
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        length = (ex * ex + ey * ey) ** 0.5
        nx, ny = -ey / length * s, ex / length * s
        lines.append(((x0 + nx * c, y0 + ny * c), (ex, ey)))
    out = []
    for i in range(n):
        (p, d), (q, e) = lines[i - 1], lines[i]
        den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 1e-12:
            out.append(q)
            continue
        t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
        out.append((p[0] + d[0] * t, p[1] + d[1] * t))
    return out


# ------------------------------------------------------------------------------------------------ geometry
def chamfer_plate(p, F, pts, d0, d1, mat, c=None, side_mat=None):
    """Prism of a convex outline from depth d0 to d1, its top edge broken by a 45 degree chamfer c (default
    `edge.chamfer_small`); c under 1 mm or a plate thinner than 2c gives the plain prism."""
    from ..model import geom as G
    c = min(const("edge.chamfer_small") if c is None else c, (d1 - d0) / 2)
    if c < 0.001:
        return G.prism(p, F, pts, d0, d1, mat, side_mat=side_mat)
    bm = p.bm
    top = inset(pts, c)
    lo = [bm.verts.new(F(a, b, d0)) for a, b in pts]
    mid = [bm.verts.new(F(a, b, d1 - c)) for a, b in pts]
    hi = [bm.verts.new(F(a, b, d1)) for a, b in top]
    mi, si = p.mi(mat), p.mi(side_mat or mat)
    bm.faces.new(list(reversed(lo))).material_index = si
    face = bm.faces.new(hi)
    face.material_index = mi
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((lo[i], lo[j], mid[j], mid[i])).material_index = si
        bm.faces.new((mid[i], mid[j], hi[j], hi[i])).material_index = mi
    return face


def bolt(p, F, a, b, d=0.0, r=None, mat=None, lod=0):
    """Hex bolt head (6 sides) seated on face F at depth d, 6 mm tall; radius `hw.bolt_r`, bare metal (role
    `hardware`); none past LOD1. Returns whether it was drawn."""
    if lod > 1:
        return False
    from ..model import geom as G
    r = const("hw.bolt_r") if r is None else r
    G.cylinder(p, F(a, b, d - 0.001), F(a, b, d + 0.006), r, 6, mat or role("hardware"))
    _hook("bolt", F(a, b, d), F.n, r)
    return True


def lamp(p, F, a, b, d=0.0, r=None, mat=config.ES, lod=0):
    """Status light: a lens (radius `hw.led_size`) recessed 3 mm in a role-`dark` bezel ring 12 mm tall."""
    from ..model import geom as G
    r = const("hw.led_size") if r is None else r
    n = G.nseg(r + 0.008, lod)
    G.ring(p, F, G.ngon(r + 0.008, n), G.ngon(r, n), d, d + 0.012, role("dark"), ca=a, cb=b)
    G.prism(p, F, G.ngon(r, n), d, d + 0.009, mat, ca=a, cb=b)
    _hook("part", "lamp", a, b, r + 0.008)


def text_width(height, text, track=0.10):
    from ..model import decals as DC
    return DC.text_width(height, text, track)


def stencil(p, F, a, b, height, text, d=0.0, align="center", track=0.10, white=True):
    """Keen stencil text (decal quads, `decal.offset` proud) on face F; (a, b) is the anchor at the cap middle.

    white=True uses the clean white glyph atlas (`<roles.stencil>_CleanWhite`, 1024 px): white text that reads on
    any paint when it sits on a dark plate. white=False uses the paint-masked main atlas."""
    from ..model import decals as DC
    w = DC.text_width(height, text, track)
    a0 = a - w / 2 if align == "center" else (a - w if align == "right" else a)
    if not white:
        return DC.text(p, F, a0, b, height, text, d=d, track=track)
    mat = role("stencil") + "_CleanWhite"
    s, x = height / DC.CAP_PX, 0.0
    for ch in text:
        if ch == " ":
            x += 0.45 * height
            continue
        x0, y0, x1, y1 = DC.GLYPHS[ch]
        gw, gh = (x1 - x0) * s, (y1 - y0) * s
        u0, u1, v0, v1 = x0 / 1024, x1 / 1024, 1 - y1 / 1024, 1 - y0 / 1024
        DC._quad(p, F, [(a0 + x, b - gh / 2), (a0 + x + gw, b - gh / 2), (a0 + x + gw, b + gh / 2),
                        (a0 + x, b + gh / 2)], d, mat, ((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
        x += gw + track * height
    return x


def label(solid, decal, F, a, b, height, text, d=0.0, align="center", lod=0, pad=None, plate=True):
    """A stencil on its own role-`dark` label plate (1.5 mm proud, clipped corners): white text that reads on any
    paint. (a, b) anchors the text. The plate stays to LOD2, the text to `decal.max_lod`. Returns the plate box."""
    from ..model import geom as G
    w = text_width(height, text)
    pad = pad if pad is not None else max(0.35 * height, 0.008)
    t0 = a - w / 2 if align == "center" else (a - w if align == "right" else a)
    box = (t0 - pad, b - height / 2 - pad, t0 + w + pad, b + height / 2 + pad)
    if plate and lod <= 2:
        G.prism(solid, F, oct_pts(*box, min(0.006, pad * 0.6)), d, d + 0.0015, role("dark"))
    if lod <= const("decal.max_lod"):
        stencil(decal, F, a, b, height, text, d=d + (0.0015 if plate else 0.0), align=align)
    return box


def hazard_triangle(p, F, a, b, width, key="tri_electric", d=0.0):
    """A warning triangle decal (Stickers02, drawn point up) centred on (a, b) of face F, base `width` wide. Put
    one only next to a real hazard (house rule: hazard restraint)."""
    from ..model import decals as DC
    uv, ratio = DC.hazard_triangle_uv(key)
    h = width * ratio
    return DC._quad(p, F, [(a - width / 2, b - h / 2), (a + width / 2, b - h / 2), (a, b + h / 2)], d, config.S2, uv)


# ------------------------------------------------------------------------------------------------ maker plate
# The plate drawn at cap 60 mm (`maker.cap`); everything scales with the cap height except `maker.proud`.
MAKER_PLATE = {"width": 0.210, "height": 0.080, "clip": 0.014, "bolt_d": 0.009, "bolt_in": 0.014, "mark_left": 0.032}


def maker_mark_layout(h=None):
    """The maker plate scaled to cap height h (default `maker.cap`; at least `maker.cap_min`). Offsets are from the
    plate's left edge / centre line."""
    cap, cap_min = const("maker.cap"), const("maker.cap_min")
    h = cap if h is None else h
    if h < cap_min - 1e-9:
        raise ValueError(f"maker mark cap {h} m is under the minimum {cap_min} m (maker.cap_min)")
    s = h / cap
    W, H = MAKER_PLATE["width"] * s, MAKER_PLATE["height"] * s
    bin_ = MAKER_PLATE["bolt_in"] * s
    return {"cap": h, "width": W, "height": H, "clip": MAKER_PLATE["clip"] * s, "proud": const("maker.proud"),
            "mark_left": MAKER_PLATE["mark_left"] * s, "bolt_r": MAKER_PLATE["bolt_d"] * s / 2,
            "bolts": [(bin_, H / 2 - bin_), (bin_, -H / 2 + bin_)]}


def r4_mark(decal, F, a, b, size, d=0.0):
    """The Rioku Labs R4 lab-flask mark: one square decal quad `size` m across centred on (a, b) of face F, from the
    Rioku atlas (`se2modkit/kit/atlas/rioku.toml`; the 1x cell up to 40 mm, the 2x cell above). The mod defines the
    atlas material and ships the texture `se2mod kit build` makes."""
    from ..kit import atlas as KA
    from ..model import decals as DC
    A = KA.default()
    u0, v0, u1, v1 = A.uv(A.pick("r4_mark", size).name)
    s = size / 2
    return DC._quad(decal, F, [(a - s, b - s), (a + s, b - s), (a + s, b + s), (a - s, b + s)], d, A.material,
                    ((u0, v0), (u1, v0), (u1, v1), (u0, v1)))


def maker_mark(solid, decal, F, a, b, h=None, lod=0, d=0.0, mark=r4_mark):
    """The Rioku Labs maker plate centred on (a, b) of face F: a role-`dark` plate `maker.proud` proud with its
    top-right corner clipped 45 degrees, two bolts down its left edge (LOD0) and the R4 lab-flask mark from the Rioku
    decal atlas on it (`r4_mark`, up to `decal.max_lod`; `mark=None` leaves it off, or pass your own
    `mark(decal, F, a, b, size, d)`). The plate stays to LOD2; nothing at LOD3 or later. At most
    `maker.max_per_block` per block, on a service face near the ID stencil, never on a mount face. Returns the box."""
    if lod >= 3:
        return None
    from ..model import geom as G
    L = maker_mark_layout(h)
    W, H, c = L["width"], L["height"], L["clip"]
    a0, b0 = a - W / 2, b - H / 2
    pts = [(a0, b0), (a0 + W, b0), (a0 + W, b0 + H - c), (a0 + W - c, b0 + H), (a0, b0 + H)]
    G.prism(solid, F, pts, d, d + L["proud"], role("dark"))
    if lod == 0:
        for da, db in L["bolts"]:
            bolt(solid, F, a0 + da, b + db, d + L["proud"], r=L["bolt_r"], mat=role("steel"))
    if mark is not None and lod <= const("decal.max_lod"):
        size = min(H - 2 * c, W - c - L["mark_left"])
        mark(decal, F, a0 + L["mark_left"] + (W - c - L["mark_left"]) / 2, b, size, d + L["proud"])
    _hook("part", "maker", a, b, W)
    return (a0, b0, a0 + W, b0 + H)
