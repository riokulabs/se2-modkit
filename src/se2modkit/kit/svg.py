"""A small, dependency-light SVG rasteriser for the kit's flat artwork (Pillow, imported lazily).

The kit's SVG sources are one-colour marks, so this renders *coverage*, not colour: every filled shape adds to the
mark, and inside a `<mask>` white shapes add and black shapes cut (SVG luminance-mask semantics for pure black and
white). Supported: `<path>` with straight segments only (M L H V Z, absolute and relative), `<polygon>`, `<rect>`,
`<circle>`, `<ellipse>`, `<g>`, `transform` (matrix, translate, scale, rotate), hex fills or `fill="none"`,
`fill-rule` (a nonzero-rule shape must be a single subpath; draw holes with `fill-rule="evenodd"`) and
`mask="url(#id)"` on a group. Anything else (curves, arcs, strokes, opacity, clip paths, gradient or `url()` fills,
unit lengths, nested masks) raises `SvgError`, so a source that would render differently in a browser fails loudly
instead of quietly.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

__all__ = ["SvgError", "coverage", "render"]

_NS = "{http://www.w3.org/2000/svg}"
_SKIP = {"title", "desc", "metadata", "defs", "mask"}
_UNSUPPORTED = ("opacity", "fill-opacity", "clip-path", "filter", "stroke-opacity")
_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CIRCLE_SEGMENTS = 96


class SvgError(ValueError):
    """The SVG uses something this rasteriser does not draw."""


def _tag(el) -> str:
    return el.tag[len(_NS):] if el.tag.startswith(_NS) else el.tag


def _mul(m, n):
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def _transform(text: str | None):
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not text:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        v = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix" and len(v) == 6:
            n = tuple(v)
        elif name == "translate":
            n = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            n = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate":
            r = math.radians(v[0])
            cs, sn = math.cos(r), math.sin(r)
            n = (cs, sn, -sn, cs, 0, 0)
            if len(v) == 3:
                n = _mul(_mul((1, 0, 0, 1, v[1], v[2]), n), (1, 0, 0, 1, -v[1], -v[2]))
        else:
            raise SvgError(f"unsupported transform {name}()")
        m = _mul(m, n)
    return m


def _path(d: str) -> list[list[tuple[float, float]]]:
    toks = _TOKEN.findall(d)
    subs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = 0.0
    i, cmd = 0, None
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    subs.append(cur)
                    x, y = cur[0]
                cur = []
                continue
            if i >= len(toks) or toks[i].isalpha():
                raise SvgError(f"path command {cmd!r} has no coordinates")
        elif cmd is None:
            raise SvgError("path data starts with a number")
        if cmd not in "MmLlHhVv":
            raise SvgError(f"unsupported path command {cmd!r} (straight segments only)")
        rel = cmd.islower()
        px, py = x, y
        if cmd in "Hh":
            v = float(toks[i])
            i += 1
            x = x + v if rel else v
        elif cmd in "Vv":
            v = float(toks[i])
            i += 1
            y = y + v if rel else v
        else:
            a, b = float(toks[i]), float(toks[i + 1])
            i += 2
            x, y = (x + a, y + b) if rel else (a, b)
        if cmd in "Mm":
            if cur:
                subs.append(cur)
            cur = []
            cmd = "l" if rel else "L"  # implicit lineto after a moveto
        elif not cur:
            cur = [(px, py)]  # a drawing command after Z starts a subpath at the current point
        cur.append((x, y))
    if cur:
        subs.append(cur)
    return [s for s in subs if len(s) >= 3]


def _ellipse(cx, cy, rx, ry):
    n = _CIRCLE_SEGMENTS
    return [[(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n)]]


def _num(el, key, default=0.0) -> float:
    return float(el.get(key, default))


def _shape(el) -> list[list[tuple[float, float]]] | None:
    tag = _tag(el)
    if tag == "path":
        return _path(el.get("d", ""))
    if tag == "polygon":
        v = [float(t) for t in re.split(r"[\s,]+", el.get("points", "").strip()) if t]
        return [list(zip(v[::2], v[1::2]))]
    if tag == "rect":
        x, y, w, h = (_num(el, k) for k in ("x", "y", "width", "height"))
        if el.get("rx") or el.get("ry"):
            raise SvgError("rounded <rect> is not supported; draw it as a path")
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h)]]
    if tag == "circle":
        r = _num(el, "r")
        return _ellipse(_num(el, "cx"), _num(el, "cy"), r, r)
    if tag == "ellipse":
        return _ellipse(_num(el, "cx"), _num(el, "cy"), _num(el, "rx"), _num(el, "ry"))
    return None


def _style(el) -> dict[str, str]:
    out = {k: el.get(k) for k in ("fill", "fill-rule", "stroke", *_UNSUPPORTED) if el.get(k) is not None}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _luminance(fill: str) -> float:
    f = fill.strip().lower()
    named = {"white": "#ffffff", "black": "#000000"}
    f = named.get(f, f)
    if re.fullmatch(r"#[0-9a-f]{3}", f):
        f = "#" + "".join(c * 2 for c in f[1:])
    if not re.fullmatch(r"#[0-9a-f]{6}", f):
        raise SvgError(f"unsupported fill {fill!r} (use hex colours)")
    r, g, b = (int(f[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class _Canvas:
    def __init__(self, Image, ImageChops, ImageDraw, size, view, scale):
        self.Image, self.Chops, self.Draw = Image, ImageChops, ImageDraw
        self.size, self.view, self.scale = size, view, scale

    def blank(self, value=0):
        return self.Image.new("L", self.size, value)

    def fill(self, polys, m, evenodd):
        out = self.blank()
        vx, vy = self.view
        for poly in polys:
            pts = [((m[0] * x + m[2] * y + m[4] - vx) * self.scale, (m[1] * x + m[3] * y + m[5] - vy) * self.scale)
                   for x, y in poly]
            one = self.blank()
            self.Draw.Draw(one).polygon(pts, fill=255)
            out = self.Chops.logical_xor(out.convert("1"), one.convert("1")).convert("L") if evenodd \
                else self.Chops.lighter(out, one)
        return out


def _walk(cv, el, m, masks, in_mask, acc, inherited):
    tag = _tag(el)
    if tag in _SKIP:
        return acc
    own = _style(el)
    for key in _UNSUPPORTED:
        if key in own:
            raise SvgError(f"{key} is not supported (on <{tag}>)")
    st = {**inherited, **own}
    m = _mul(m, _transform(el.get("transform")))
    mask_ref = el.get("mask")
    if mask_ref and (tag != "g" or in_mask):
        raise SvgError(f"mask= is supported only on a <g> outside a mask (found on <{tag}>)")
    if tag in ("svg", "g"):
        if mask_ref:
            mid = re.fullmatch(r"url\(#([^)]+)\)", mask_ref.strip())
            if not mid or mid.group(1) not in masks:
                raise SvgError(f"mask {mask_ref!r} not found")
            group = cv.blank()
            for child in el:
                group = _walk(cv, child, m, masks, False, group, st)
            mask = cv.blank()
            for child in masks[mid.group(1)]:
                mask = _walk(cv, child, m, masks, True, mask, {})
            return cv.Chops.lighter(acc, cv.Chops.multiply(group, mask))
        for child in el:
            acc = _walk(cv, child, m, masks, in_mask, acc, st)
        return acc
    if st.get("stroke") not in (None, "none"):
        raise SvgError("strokes are not supported; draw outlines as filled paths")
    polys = _shape(el)
    if polys is None:
        raise SvgError(f"unsupported element <{tag}>")
    fill = st.get("fill", "#000000")
    if fill == "none" or not polys:
        return acc
    lum = _luminance(fill)  # also rejects url(...) and other non-hex fills
    evenodd = st.get("fill-rule") == "evenodd"
    if not evenodd and len(polys) > 1:
        raise SvgError(f"<{tag}> has {len(polys)} subpaths under the nonzero fill rule; use fill-rule=\"evenodd\"")
    cov = cv.fill(polys, m, evenodd)
    if in_mask and lum < 0.5:
        return cv.Chops.subtract(acc, cov)
    return cv.Chops.lighter(acc, cov)


def _pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        raise SvgError("Pillow is required to rasterise kit artwork: pip install 'se2-modkit[preview]'") from None
    return Image, ImageChops, ImageDraw


def coverage(svg_text: str, width: int, height: int | None = None, *, supersample: int = 4):
    """The mark's coverage as a Pillow "L" image of width x height px (height from the viewBox aspect if omitted;
    a different aspect centres the drawing, as SVG's default "xMidYMid meet"), anti-aliased by drawing at
    `supersample` times the size and box-filtering down. Every failure is an `SvgError`."""
    Image, ImageChops, ImageDraw = _pillow()
    try:
        return _coverage(Image, ImageChops, ImageDraw, svg_text, width, height, supersample)
    except SvgError:
        raise
    except ET.ParseError as e:
        raise SvgError(f"malformed SVG ({e})") from None
    except (ValueError, IndexError, ZeroDivisionError) as e:
        raise SvgError(f"unreadable number or length in the SVG ({e}); plain user units only") from None


def _coverage(Image, ImageChops, ImageDraw, svg_text, width, height, supersample):
    root = ET.fromstring(svg_text)
    if _tag(root) != "svg":
        raise SvgError("not an SVG document")
    vb = [float(v) for v in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if v]
    if len(vb) != 4:
        raise SvgError("the SVG needs a viewBox")
    vx, vy, vw, vh = vb
    height = round(width * vh / vw) if height is None else height
    ss = max(1, supersample)
    scale = min(width / vw, height / vh) * ss  # preserveAspectRatio="xMidYMid meet"
    vx -= (width * ss / scale - vw) / 2
    vy -= (height * ss / scale - vh) / 2
    masks = {el.get("id"): el for el in root.iter() if _tag(el) == "mask"}
    cv = _Canvas(Image, ImageChops, ImageDraw, (width * ss, height * ss), (vx, vy), scale)
    big = _walk(cv, root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), masks, False, cv.blank(), {})
    return big.resize((width, height), Image.BOX) if ss > 1 else big


def render(svg_text: str, width: int, height: int | None = None, *, ink=(255, 255, 255), background=None,
           supersample: int = 4):
    """The mark as an RGBA image: `ink` with the coverage as alpha, or flattened onto an RGB `background`."""
    Image, _, _ = _pillow()
    cov = coverage(svg_text, width, height, supersample=supersample)
    if background is None:
        out = Image.new("RGBA", cov.size, (*ink, 0))
        out.putalpha(cov)
        return out
    out = Image.new("RGB", cov.size, tuple(background))
    out.paste(Image.new("RGB", cov.size, tuple(ink)), (0, 0), cov)
    return out
