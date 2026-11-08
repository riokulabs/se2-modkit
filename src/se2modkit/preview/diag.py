"""Measured views beside a look set: pictures that show geometry plainly, and numbers a reader can check.

A render that looks right can still hide a floating part, a clipped one or a wrong proportion, and a picture of a
lit, textured block is the hardest place to see them. So every look set also gets:

- `diag.png`: the block in clay with cavity shading, as a black silhouette, with its triangle wireframe, colored per
  part with numbers (the legend lists each part's name; the numbers follow `se2mod describe`'s order, sorted by
  collection then name), two section cuts through its center that contain its long axis (red is the inside of a
  closed part the cut went through), and the 3/4 front shot shrunk to the size the block takes on a 1080p screen
  at 10, 25 and 50 m with the game's field of view.
- `ortho.png`: front, side and top orthographic views with the bounding size in mm and the block envelope (from
  `--envelope`, else the smallest whole-grid-cell box around the bounds, drawn only when it is at most twice the
  block's size and otherwise just named).
- `refdiff.png` (with `--reference`): our front or side silhouette against a reference picture's, scaled to the
  same height and centered; red is ours only, blue is the reference only, with the overlap (IoU) in `report.json`.

The Blender side (`diag_run`) renders the raw tiles into `<set>/diag/` and writes `diag/diag.json`; `compose` draws
the sheets from them with Pillow. Everything here is plain Python (Pillow only in the drawing functions).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import lookdev, shots
from .compose import PreviewError, _font, _import_pillow

__all__ = ["DISTANCES_M", "ORTHO_VIEWS", "REFERENCE_VIEWS", "SCREEN", "TILE", "apparent_px", "compare_reference",
           "compose", "distance_plan", "distance_thumbs", "envelope_box", "finish", "number_parts", "part_color",
           "placed_size", "section_cuts", "silhouette"]

DISTANCES_M = (10, 25, 50)
SCREEN = (1920, 1080)            # the gameplay-distance strip assumes a 1080p view
TILE = (800, 450)                # diagnostic tiles
ORTHO = (1280, 720)              # measured views
ORTHO_VIEWS = ("front", "right", "top")
ORTHO_NAMES = {"front": "front", "right": "side", "top": "top"}
REFERENCE_VIEWS = {"front": "front", "side": "right"}
_AXES = "xyz"
_AXIS_VIEW = {"x": "right", "y": "front", "z": "top"}
_AXIS_FROM = {"x": "from +X (right)", "y": "from +Y (front)", "z": "from above"}
_DIM = ("width", "depth", "height")   # the look frame: X across, Y front to back, Z up from the mount face

WHITE, INK, GRID_INK = (255, 255, 255), (30, 30, 34), (120, 120, 128)
SHEET_BG = (236, 237, 240)
ENV_INK = (40, 90, 220)
EXTRA, MISSING, BOTH = (220, 40, 40), (40, 90, 220), (95, 95, 100)


# --------------------------------------------------------------------------- numbers

def part_color(name: str) -> tuple[int, int, int]:
    """A stable color for a part name: the hue from a hash of the name, mid saturation and value so a number in white
    or black reads on it."""
    h = hashlib.sha1(name.encode("utf-8")).digest()
    hue = int.from_bytes(h[:2], "big") / 65536
    sat = 0.55 + 0.35 * h[2] / 255
    val = 0.62 + 0.3 * h[3] / 255
    import colorsys
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, val))


def number_parts(parts: list[dict]) -> list[dict]:
    """[{"name", "collection"}] numbered from 1 in `se2mod describe`'s order (collection, then name), each with its
    color."""
    from .. import describe as D
    out = []
    for i, p in enumerate(sorted(parts, key=D._sort_key), 1):
        out.append({**p, "id": i, "color": list(part_color(p["name"]))})
    return out


def placed_size(size, mount_face: str) -> tuple[float, float, float]:
    """A size along the source's X, Y, Z as it lies in the look frame once the block stands on its mount face."""
    rot = lookdev.mount_rotation(mount_face)
    return tuple(sum(abs(rot[i][j]) * size[j] for j in range(3)) for i in range(3))


def envelope_box(half, *, envelope=None, mount_face: str = "-Z", origin=None, grid: str = "large") -> dict:
    """The block envelope in the look frame (the block's bounds centered on x = y = 0 with its bottom at z = 0).

    With `envelope` (meters along the source's X, Y, Z, as `se2mod describe --envelope` takes it) the box is centered
    on the block origin (`origin`, in the look frame); without it, the smallest whole-grid-cell box around the
    bounds, standing on the mount face."""
    if envelope is not None:
        if len(envelope) != 3 or min(envelope) <= 0:
            raise PreviewError("--envelope is X,Y,Z in meters (three numbers over 0).")
        size = placed_size([float(v) for v in envelope], mount_face)
        o = list(origin) if origin is not None else [0.0, 0.0, half[2]]
        lo = [o[i] - size[i] / 2 for i in range(3)]
        hi = [o[i] + size[i] / 2 for i in range(3)]
        mm = " x ".join(f"{v * 1000:.0f}" for v in size)
        return {"from": "--envelope", "size_m": list(size), "lo": lo, "hi": hi, "label": f"envelope {mm} mm"}
    g = lookdev.GRID[grid]
    cells = [max(1, math.ceil(2 * h / g - 1e-3)) for h in half]
    size = [c * g for c in cells]
    lo = [-size[0] / 2, -size[1] / 2, 0.0]
    hi = [size[0] / 2, size[1] / 2, size[2]]
    return {"from": "grid", "cells": cells, "size_m": size, "lo": lo, "hi": hi,
            "label": f"fits {' x '.join(map(str, cells))} {grid}-grid cells ({g:g} m)"}


def section_cuts(half) -> list[dict]:
    """Two cuts through the block's center that both contain its long axis: the plane across the larger of the
    other two axes first. Each is seen square on from its plane's + side."""
    long = max(range(3), key=lambda i: half[i])
    others = sorted((i for i in range(3) if i != long), key=lambda i: -half[i])
    return [{"normal": _AXES[i], "view": _AXIS_VIEW[_AXES[i]],
             "label": f"cut at mid {_AXES[i].upper()}, {_AXIS_FROM[_AXES[i]]}; red = cut solid"} for i in others]


def _basis(direction):
    d = shots._unit(*direction)
    up = (0.0, 1.0, 0.0) if abs(d[2]) > 0.99 else (0.0, 0.0, 1.0)
    right = shots._unit(*shots._cross(d, up))
    up = shots._cross(right, d)
    return d, right, up


def apparent_px(half, direction, distance: float, *, res=SCREEN, fov_v: float = lookdev.GAME_FOV_V):
    """(width, height) in pixels a box with half extents `half` covers on a `res` screen, seen from `distance` m
    along `direction` (from its center) with the game's vertical field of view."""
    d, right, up = _basis(direction)
    f = res[1] / 2 / math.tan(fov_v / 2)       # focal length in pixels
    xs, ys = [], []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                p = (sx * half[0], sy * half[1], sz * half[2])
                depth = distance - sum(p[i] * d[i] for i in range(3))
                xs.append(f * sum(p[i] * right[i] for i in range(3)) / depth)
                ys.append(f * sum(p[i] * up[i] for i in range(3)) / depth)
    return (max(xs) - min(xs), max(ys) - min(ys))


def distance_plan(half, direction) -> list[dict]:
    """The block's on-screen size at each of `DISTANCES_M`."""
    return [{"m": m, "px": [round(v, 1) for v in apparent_px(half, direction, m)]} for m in DISTANCES_M]


# --------------------------------------------------------------------------- drawing

def _on_white(im):
    Image, *_ = _import_pillow()
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, (*WHITE, 255))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def silhouette(im):
    """Black where the render has the block (its alpha), white elsewhere."""
    Image, *_ = _import_pillow()
    a = im.convert("RGBA").getchannel("A")
    return Image.merge("RGB", [a.point(lambda v: 255 - v)] * 3)


def _wire_over(clay, wire):
    _Image, ImageChops, *_ = _import_pillow()
    return ImageChops.multiply(_on_white(clay), _on_white(wire))


def _label(draw, xy, text, font, fill=INK):
    draw.text(xy, text, fill=fill, font=font)


def _dashed_rect(draw, rect, color, dash=10, width=2):
    x0, y0, x1, y1 = rect
    for a, b, horiz, fixed in ((x0, x1, True, y0), (x0, x1, True, y1), (y0, y1, False, x0), (y0, y1, False, x1)):
        t = a
        while t < b:
            e = min(t + dash, b)
            draw.line([(t, fixed), (e, fixed)] if horiz else [(fixed, t), (fixed, e)], fill=color, width=width)
            t += 2 * dash


def _parts_tile(im, parts, font):
    _Image, _c, ImageDraw, _f = _import_pillow()
    im = _on_white(im)
    draw = ImageDraw.Draw(im)
    for p in parts:
        if not p.get("xy"):
            continue
        x, y = p["xy"]
        t = str(p["id"])
        w = draw.textlength(t, font=font)
        r = max(9, w / 2 + 4)
        draw.ellipse([x - r, y - 9, x + r, y + 9], fill=WHITE, outline=INK)
        draw.text((x - w / 2, y - 7), t, fill=INK, font=font)
    return im


def distance_thumbs(folder: Path, dist: dict) -> list[dict]:
    """The 3/4 front shot cropped round the block and resized so the block is as many pixels as it would be on a
    1080p screen at each distance: [{"m", "px", "image"}]."""
    Image, *_ = _import_pillow()
    src = Image.open(Path(folder) / dist["shot"]).convert("RGB")
    x0, y0, x1, y1 = dist["rect"]
    rw, rh = max(1, x1 - x0), max(1, y1 - y0)
    mx, my = rw * 0.1, rh * 0.1
    crop = src.crop((max(0, round(x0 - mx)), max(0, round(y0 - my)), min(src.width, round(x1 + mx)),
                     min(src.height, round(y1 + my))))
    out = []
    for t in dist["targets"]:
        tw, th = t["px"]
        s = math.sqrt(max(tw, 1) * max(th, 1) / (rw * rh))
        size = (max(1, round(crop.width * s)), max(1, round(crop.height * s)))
        out.append({"m": t["m"], "px": t["px"], "image": crop.resize(size, Image.LANCZOS), "upscaled": s > 1})
    return out


def _distance_strip(folder, dist, font, zoom_h=220):
    Image, _c, ImageDraw, _f = _import_pillow()
    cells = []
    for t in distance_thumbs(folder, dist):
        im = t["image"]
        z = None
        if im.height < zoom_h:
            k = max(2, zoom_h // im.height)
            z = im.resize((im.width * k, im.height * k), Image.NEAREST)
        cells.append((t, im, z))
    h = max(max(im.height, z.height if z else 0) for _t, im, z in cells) + 40
    w = sum(im.width + (z.width + 12 if z else 0) + 40 for _t, im, z in cells) + 10
    strip = Image.new("RGB", (w, h), WHITE)
    draw = ImageDraw.Draw(strip)
    x = 10
    for t, im, z in cells:
        strip.paste(im, (x, 24 + (h - 40 - im.height) // 2))
        note = f"{t['m']} m: {t['px'][0]:.0f} x {t['px'][1]:.0f} px" + (" (upscaled)" if t["upscaled"] else "")
        if z:
            strip.paste(z, (x + im.width + 12, 24))
            note += f", right: x{z.width // im.width}"
        _label(draw, (x, 4), note, font)
        x += im.width + (z.width + 12 if z else 0) + 40
    return strip


def _legend(parts, height, font, line=17):
    Image, _c, ImageDraw, _f = _import_pillow()
    per = max(1, (height - 30) // line)
    cols = max(1, -(-len(parts) // per))
    colw = 260
    im = Image.new("RGB", (cols * colw + 10, height), WHITE)
    draw = ImageDraw.Draw(im)
    _label(draw, (8, 6), f"parts ({len(parts)}), numbered as in se2mod describe", font)
    for i, p in enumerate(parts):
        x, y = 8 + (i // per) * colw, 28 + (i % per) * line
        draw.rectangle([x, y + 2, x + 12, y + 14], fill=tuple(p["color"]), outline=INK)
        name = p["name"] if len(p["name"]) <= 30 else p["name"][:29] + "..."
        _label(draw, (x + 18, y), f"#{p['id']} {name}", font)
    return im


def _grid(tiles, cols, tile, font, strip=22):
    Image, _c, ImageDraw, _f = _import_pillow()
    rows = -(-len(tiles) // cols)
    gap = 8
    im = Image.new("RGB", (gap + cols * (tile[0] + gap), gap + rows * (tile[1] + strip + gap)), SHEET_BG)
    draw = ImageDraw.Draw(im)
    for i, (label, t) in enumerate(tiles):
        x, y = gap + (i % cols) * (tile[0] + gap), gap + (i // cols) * (tile[1] + strip + gap)
        t = t.copy()
        t.thumbnail(tile)
        im.paste(t, (x, y + strip))
        _label(draw, (x + 2, y + 3), label, font)
    return im


def _stack(blocks, bg=SHEET_BG):
    """Blocks top to bottom, each a list of images left to right."""
    Image, *_ = _import_pillow()
    rows = [(sum(b.width for b in row) + 8 * (len(row) - 1), max(b.height for b in row)) for row in blocks]
    im = Image.new("RGB", (max(w for w, _ in rows) + 16, sum(h for _, h in rows) + 8 * (len(rows) + 1)), bg)
    y = 8
    for row, (_w, h) in zip(blocks, rows):
        x = 8
        for b in row:
            im.paste(b, (x, y))
            x += b.width + 8
        y += h + 8
    return im


def _ortho_cell(folder, view, o, info, font, big):
    Image, _c, ImageDraw, _f = _import_pillow()
    im = _on_white(Image.open(Path(folder) / o["png"]))
    # crop to the bounds and the envelope with room for the dimensions: an orthographic frame of a tall block is
    # mostly empty sides
    rects = [o["bbox"]] + ([o["envelope"]] if o.get("envelope") else [])
    m = 30
    cx0 = max(0, round(min(r[0] for r in rects)) - m)
    cy0 = max(0, round(min(r[1] for r in rects)) - m)
    cx1 = min(im.width, round(max(r[2] for r in rects)) + m)
    cy1 = min(im.height, round(max(r[3] for r in rects)) + m)
    im = im.crop((cx0, cy0, cx1, cy1))
    o = {**o, "bbox": [o["bbox"][0] - cx0, o["bbox"][1] - cy0, o["bbox"][2] - cx0, o["bbox"][3] - cy0]}
    if o.get("envelope"):
        e = o["envelope"]
        o["envelope"] = [e[0] - cx0, e[1] - cy0, e[2] - cx0, e[3] - cy0]
    pad = 90
    cell = Image.new("RGB", (im.width + 2 * pad, im.height + 2 * pad), WHITE)
    cell.paste(im, (pad, pad))
    draw = ImageDraw.Draw(cell)
    bx0, by0, bx1, by1 = (v + pad for v in o["bbox"])
    draw.rectangle([bx0, by0, bx1, by1], outline=GRID_INK, width=1)
    env = o.get("envelope")
    if env:
        er = [v + pad for v in env]
        _dashed_rect(draw, er, ENV_INK)
    h, v = o["h_axis"], o["v_axis"]
    size = info["size_mm"]
    # width below, height on the left, each between end ticks
    lo_y = max(by1, er[3] if env else by1) + 24
    draw.line([(bx0, lo_y), (bx1, lo_y)], fill=INK, width=2)
    for x in (bx0, bx1):
        draw.line([(x, lo_y - 7), (x, lo_y + 7)], fill=INK, width=2)
    t = f"{_DIM[h]} {size[h]:.0f} mm"
    draw.text(((bx0 + bx1) / 2 - draw.textlength(t, font=big) / 2, lo_y + 6), t, fill=INK, font=big)
    left_x = min(bx0, er[0] if env else bx0) - 24
    draw.line([(left_x, by0), (left_x, by1)], fill=INK, width=2)
    for y in (by0, by1):
        draw.line([(left_x - 7, y), (left_x + 7, y)], fill=INK, width=2)
    t = f"{_DIM[v]} {size[v]:.0f} mm"
    tw = draw.textlength(t, font=big)
    txt = Image.new("RGBA", (round(tw) + 4, 22), (255, 255, 255, 0))
    ImageDraw.Draw(txt).text((2, 0), t, fill=(*INK, 255), font=big)
    txt = txt.rotate(90, expand=True)
    cell.paste(txt, (round(left_x - 6 - txt.width), round((by0 + by1) / 2 - txt.height / 2)), txt)
    if env:
        e = info["envelope"]["size_mm"]
        et = f"envelope {e[h]:.0f} x {e[v]:.0f} mm"
        draw.text((er[0] + 4, er[1] + 4), et, fill=ENV_INK, font=font)
    draw.text((8, 6), f"{ORTHO_NAMES[view]} (orthographic)", fill=INK, font=big)
    if not env:
        draw.text((8, 30), f"{info['envelope']['label']} (not drawn: far larger than the block)", fill=ENV_INK,
                  font=font)
    return cell


def compose(folder: Path) -> dict:
    """Draw `diag.png` and `ortho.png` from `<folder>/diag/diag.json`. Returns {"sheet", "ortho", "files"} (paths
    relative to the set folder)."""
    from .compose import _save
    Image, _chops, ImageDraw, ImageFont = _import_pillow()
    folder = Path(folder)
    info = json.loads((folder / "diag" / "diag.json").read_text(encoding="utf-8"))
    font, big = _font(ImageFont, 13), _font(ImageFont, 18)
    tile = tuple(info.get("tile", TILE))
    t = info["tiles"]
    clay = Image.open(folder / t["clay"])
    tiles = [("clay, cavity shading", _on_white(clay)), ("silhouette", silhouette(clay)),
             ("wireframe (triangles)", _wire_over(clay, Image.open(folder / t["wire"]))),
             ("parts (numbers in the legend)", _parts_tile(Image.open(folder / t["parts"]), info["parts"], font))]
    tiles += [(s["label"], _on_white(Image.open(folder / s["png"]))) for s in info["sections"]]
    grid = _grid(tiles, 3, tile, font)
    size = info["size_mm"]
    head = Image.new("RGB", (grid.width, 30), SHEET_BG)
    d = ImageDraw.Draw(head)
    d.text((8, 6), f"bounds {size[0]:.0f} x {size[1]:.0f} x {size[2]:.0f} mm (width x depth x height); "
                   f"{info['envelope']['label']}", fill=INK, font=big)
    rows = [[head], [grid, _legend(info["parts"], grid.height, font)]]
    if info.get("distance") and (folder / info["distance"]["shot"]).is_file():
        rows.append([_distance_strip(folder, info["distance"], font)])
    _save(_stack(rows), folder / "diag.png", "PNG")
    cells = [_ortho_cell(folder, v, info["ortho"][v], info, font, big) for v in ORTHO_VIEWS if v in info["ortho"]]
    if cells:
        for c in cells:
            c.thumbnail((ORTHO[0] + 180, ORTHO[1] + 180))
        _save(_stack([cells], bg=WHITE), folder / "ortho.png", "PNG")
    raw = sorted(p.relative_to(folder).as_posix() for p in (folder / "diag").iterdir() if p.is_file())
    return {"sheet": "diag.png", "ortho": "ortho.png" if cells else None, "files": raw}


# --------------------------------------------------------------------------- reference

def _ref_mask(im):
    """A reference picture's shape: its alpha when it has one, else every pixel that differs from the border's
    color (a drawing or render on a plain background). Openings the background shows through stay open, so a
    highlight inside the shape as light as the background reads as a hole: a cut-out with alpha is the sure input."""
    Image, ImageChops, *_ = _import_pillow()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        a = im.convert("RGBA").getchannel("A")
        if a.getextrema()[0] < 250:
            return a.point(lambda v: 255 if v > 127 else 0)
    rgb = im.convert("RGB")
    w, h = rgb.size
    border = [rgb.getpixel((x, y)) for x in range(0, w, max(1, w // 64)) for y in (0, h - 1)]
    border += [rgb.getpixel((x, y)) for y in range(0, h, max(1, h // 64)) for x in (0, w - 1)]
    bg = tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
    return diff.point(lambda v: 255 if v > 40 else 0)


def _our_mask(im):
    return im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 127 else 0)


def _count(mask) -> int:
    return mask.histogram()[255]


def compare_reference(ours_png: Path, ref_png: Path, out_png: Path) -> dict:
    """Our silhouette against the reference's: the reference is cropped to its shape, scaled to our height and
    centered on ours (tried mirrored too; the better fit is kept). Writes `out_png` (ours, the reference, the
    difference) and returns {"iou", "mirrored", "extra_share", "missing_share", "aspect_ours", "aspect_reference"}."""
    Image, ImageChops, ImageDraw, ImageFont = _import_pillow()
    try:
        ref_im = Image.open(ref_png)
        ref_im.load()
    except OSError as e:
        raise PreviewError(f"cannot read the reference {Path(ref_png).name}: {type(e).__name__}.") from None
    ours = _our_mask(Image.open(ours_png))
    ref = _ref_mask(ref_im)
    ob, rb = ours.getbbox(), ref.getbbox()
    if not ob:
        raise PreviewError("our view rendered empty: nothing to compare with the reference.")
    if not rb:
        raise PreviewError(f"the reference {Path(ref_png).name} has no shape on its background.")
    ours, ref = ours.crop(ob), ref.crop(rb)
    s = ours.height / ref.height
    ref = ref.resize((max(1, round(ref.width * s)), ours.height), Image.LANCZOS).point(lambda v: 255 if v > 127 else 0)
    w = max(ours.width, ref.width)

    def place(m):
        c = Image.new("L", (w, ours.height), 0)
        c.paste(m, ((w - m.width) // 2, 0))
        return c
    a = place(ours)
    best = None
    for mirrored in (False, True):
        b = place(ref.transpose(Image.FLIP_LEFT_RIGHT) if mirrored else ref)
        inter = _count(ImageChops.multiply(a, b).point(lambda v: 255 if v > 127 else 0))
        union = _count(ImageChops.lighter(a, b))
        iou = inter / union if union else 0.0
        if best is None or iou > best[0] + 1e-9:
            best = (iou, mirrored, b, inter, union)
    iou, mirrored, b, inter, union = best
    extra = _count(ImageChops.subtract(a, b))
    missing = _count(ImageChops.subtract(b, a))
    diff = Image.new("RGB", a.size, WHITE)
    diff.paste(BOTH, mask=ImageChops.multiply(a, b))
    diff.paste(EXTRA, mask=ImageChops.subtract(a, b))
    diff.paste(MISSING, mask=ImageChops.subtract(b, a))
    font = _font(ImageFont, 16)
    panels = [("ours", a.convert("RGB").point(lambda v: 255 - v)), ("reference", b.convert("RGB").point(lambda v: 255 - v)),
              (f"IoU {iou:.3f}: red ours only, blue reference only", diff)]
    sheet = Image.new("RGB", (sum(p.width + 20 for _, p in panels) + 20, a.height + 60), SHEET_BG)
    dr = ImageDraw.Draw(sheet)
    x = 20
    for label, p in panels:
        sheet.paste(p, (x, 40))
        dr.text((x, 12), label, fill=INK, font=font)
        x += p.width + 20
    from .compose import _save
    _save(sheet, Path(out_png), "PNG")
    return {"iou": round(iou, 4), "mirrored": mirrored, "extra_share": round(extra / union, 4) if union else 0.0,
            "missing_share": round(missing / union, 4) if union else 0.0,
            "aspect_ours": round((ob[2] - ob[0]) / (ob[3] - ob[1]), 4),
            "aspect_reference": round((rb[2] - rb[0]) / (rb[3] - rb[1]), 4)}


# --------------------------------------------------------------------------- finish

def finish(out: Path, report: dict, *, reference: Path | None = None, reference_view: str = "front") -> list[str]:
    """After the render: draw the sheets and, with a reference, the silhouette compare; record them in `report`
    (`diag`, `reference`). Returns warnings (a failed diagnostic never keeps the set back)."""
    out = Path(out)
    warnings = []
    d = report.get("diag")
    if d is None or not (out / "diag" / "diag.json").is_file():
        if d and d.get("error"):
            warnings.append(f"diagnostic views: {d['error']}")
        if reference:
            report["reference"] = {"image": str(reference), "error": "no measured views to compare (--no-diag?)"}
            warnings.append("reference: no measured views to compare with")
        return warnings
    d.update(compose(out))
    if reference:
        view = REFERENCE_VIEWS[reference_view]
        png = out / "diag" / f"ortho_{view}.png"
        try:
            r = compare_reference(png, Path(reference), out / "refdiff.png")
            report["reference"] = {"image": str(reference), "view": reference_view, "diff": "refdiff.png", **r}
        except PreviewError as e:
            report["reference"] = {"image": str(reference), "view": reference_view, "error": str(e)}
            warnings.append(f"reference: {e}")
    return warnings
