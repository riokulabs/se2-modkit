"""The shot standard every render path follows: view names, file names, camera fit, exposure and set checks.

A standard set is one PNG per view, named `<block>_<stage>_r<N>_<view>[_<extra>]`, with a `<stem>.cam.json` beside
each render so a later round can reuse the exact camera. `extra` is free text after the view: `close-<part>` for a
close-up (which needs a locator thumbnail, see `add_locator`), `before` / `after` for a same-camera pair.

`check_set` is the gate: a set with a missing view, a failed exposure check, a close-up without a locator or an
`_after` without a matching `_before` is not ready to show anyone. Plain Python; Pillow is imported only by the
functions that read or write images, so Blender-side modules can import the names and camera math.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .compose import PreviewError, _import_pillow

__all__ = ["ALIASES", "REQUIRED", "VIEWS", "Exposure", "ShotName", "View", "add_locator", "canonical", "check_set",
           "exposure", "fit_distance", "load_camera", "mark_locator", "parse_shot_name", "save_camera", "shot_name"]


@dataclass(frozen=True)
class View:
    direction: tuple[float, float, float]   # unit vector from the block's center towards the camera; +Y front, Z up
    up: tuple[float, float, float]
    eye: bool                               # True: taken from a standing player's eye (perspective, in context)


def _unit(x, y, z) -> tuple[float, float, float]:
    n = math.sqrt(x * x + y * y + z * z)
    return (x / n, y / n, z / n)


_Z, _Y = (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)
VIEWS: dict[str, View] = {
    "front": View(_unit(0, 1, 0), _Z, False),
    "back": View(_unit(0, -1, 0), _Z, False),
    "left": View(_unit(-1, 0, 0), _Z, False),
    "right": View(_unit(1, 0, 0), _Z, False),
    "top": View(_unit(0, 0.0001, 1), _Y, False),
    "bottom": View(_unit(0, 0.0001, -1), _Y, False),
    "3q_front": View(_unit(-0.6, 0.8, 0.45), _Z, True),
    "3q_back": View(_unit(0.6, -0.8, 0.45), _Z, True),
    "3q_low": View(_unit(-0.6, 0.8, 0.12), _Z, True),
    "mount": View(_unit(0.8, 0.55, 0.05), _Z, True),     # grazing, close: how the block meets what it sits on
    "inside": View(_unit(0, -1, 0.1), _Z, True),         # nominal; the look-dev scene aims it per block
}

# Names older render paths used. They keep working everywhere a view name is read.
ALIASES = {"3q_rear": "3q_back", "3q_front_low": "3q_low", "3q_front_high": "3q_front", "iso": "3q_front",
           "three_q": "3q_front", "iso_back": "3q_back", "three_q_back": "3q_back", "iso2": "3q_low", "side": "right",
           "close": "3q_front", "rear": "back", "nozzle": "back"}

REQUIRED = {
    "look": ("3q_front", "3q_back", "3q_low", "front", "back", "left", "right", "top", "mount"),
    "concept": ("3q_front", "3q_back", "front", "top"),
    "fix": (),
}

# Exposure limits on the luminance (0..1) of the subject's pixels, set from renders that read well in review.
MEAN_LO, MEAN_HI = 0.18, 0.72
CLIP_HI_MAX, CLIP_LO_MAX = 0.02, 0.05
_HI, _LO = 250 / 255, 5 / 255


def canonical(name: str) -> str:
    """The canonical view for `name` (itself, or what an old alias meant); raises PreviewError on an unknown name."""
    if name in VIEWS:
        return name
    if name in ALIASES:
        return ALIASES[name]
    raise PreviewError(f"unknown view {name!r} (views: {', '.join(VIEWS)}).")


@dataclass(frozen=True)
class ShotName:
    block: str
    stage: str
    rnd: int
    view: str
    extra: str = ""


_STEM_RE = re.compile(r"^(?P<block>.+)_(?P<stage>[a-z]+)_r(?P<rnd>\d+)_(?P<rest>.+)$")
_BY_LENGTH = sorted(VIEWS, key=len, reverse=True)


def shot_name(block: str, stage: str, rnd: int, view: str, extra: str = "") -> str:
    if view not in VIEWS:
        canonical(view)  # raises with the list of names
        view = ALIASES[view]
    return f"{block}_{stage}_r{rnd}_{view}" + (f"_{extra}" if extra else "")


def parse_shot_name(stem: str) -> ShotName | None:
    m = _STEM_RE.match(stem)
    if not m:
        return None
    rest = m["rest"]
    for v in _BY_LENGTH:
        if rest == v or rest.startswith(v + "_"):
            return ShotName(m["block"], m["stage"], int(m["rnd"]), v, rest[len(v) + 1:])
    return None


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def fit_distance(half_extents, direction, lens_mm: float, aspect: float, fill: float = 0.8) -> float:
    """Distance from the box's center along `direction` at which the box fills `fill` of the frame (Blender's 36 mm
    sensor across the wider side). The box is axis-aligned with the given half extents."""
    d = _unit(*direction)
    up = _Y if abs(d[2]) > 0.99 else _Z
    right = _unit(*_cross(d, up))
    up = _cross(right, d)
    span = lambda axis: sum(abs(h * a) for h, a in zip(half_extents, axis))  # noqa: E731
    sensor_w, sensor_h = (36.0, 36.0 / aspect) if aspect >= 1 else (36.0 * aspect, 36.0)
    tan_w, tan_h = sensor_w / 2 / lens_mm, sensor_h / 2 / lens_mm
    near = max(span(right) / (fill * tan_w), span(up) / (fill * tan_h))
    return near + span(d)


@dataclass(frozen=True)
class Exposure:
    mean: float
    clip_hi: float
    clip_lo: float
    ok: bool
    why: str = ""


def exposure(png: Path) -> Exposure:
    """Exposure of a render: mean luminance and the shares of blown and crushed pixels, over opaque pixels when the
    image has alpha, else the whole frame."""
    import numpy as np
    Image = _import_pillow()[0]
    with Image.open(png) as im:
        im.load()
        rgba = np.asarray(im.convert("RGBA"), dtype=np.float64) / 255.0
    lum = rgba[..., 0] * 0.2126 + rgba[..., 1] * 0.7152 + rgba[..., 2] * 0.0722
    mask = rgba[..., 3] > 0.5
    px = lum[mask] if mask.any() else lum.ravel()
    mean, hi, lo = float(px.mean()), float((px >= _HI).mean()), float((px <= _LO).mean())
    why = []
    if mean < MEAN_LO:
        why.append(f"too dark (mean {mean:.2f} < {MEAN_LO})")
    if mean > MEAN_HI:
        why.append(f"too bright (mean {mean:.2f} > {MEAN_HI})")
    if hi > CLIP_HI_MAX:
        why.append(f"blown highlights ({hi:.1%} > {CLIP_HI_MAX:.0%})")
    if lo > CLIP_LO_MAX:
        why.append(f"crushed blacks ({lo:.1%} > {CLIP_LO_MAX:.0%})")
    return Exposure(mean, hi, lo, not why, "; ".join(why))


def _cam_path(png: Path) -> Path:
    return png.with_name(png.stem + ".cam.json")


def save_camera(png: Path, matrix, lens: float, ortho: float | None = None) -> Path:
    """Write the camera a render used (4x4 world matrix rows, lens in mm, ortho scale for an orthographic view)."""
    path = _cam_path(Path(png))
    cam = {"matrix": [[float(x) for x in row] for row in matrix], "lens": float(lens)}
    if ortho:
        cam["ortho"] = float(ortho)
    path.write_text(json.dumps(cam))
    return path


def load_camera(png: Path) -> dict | None:
    path = _cam_path(Path(png))
    return json.loads(path.read_text()) if path.is_file() else None


def _same_camera(a: dict, b: dict) -> bool:
    return (abs(a["lens"] - b["lens"]) < 1e-6 and abs(a.get("ortho", 0.0) - b.get("ortho", 0.0)) < 1e-6
            and all(abs(x - y) < 1e-6 for ra, rb in zip(a["matrix"], b["matrix"]) for x, y in zip(ra, rb)))


def _locator_mark(png: Path) -> Path:
    return png.with_name(png.name + ".locator")


def mark_locator(png: Path, overview: str = "", rect=None) -> Path:
    """Record that `png` carries a locator thumbnail (written by `add_locator`, or by a render path drawing its own)."""
    path = _locator_mark(Path(png))
    path.write_text(json.dumps({"overview": overview, "rect": list(rect) if rect else None}))
    return path


def add_locator(close_png: Path, overview_png: Path, rect_px, out: Path, *, share: float = 0.25) -> Path:
    """The close-up with the overview as a thumbnail in its bottom right, `rect_px` (x0, y0, x1, y1 in the
    overview's pixels) drawn on the thumbnail in red."""
    Image, _, ImageDraw, _ = _import_pillow()
    with Image.open(close_png) as c, Image.open(overview_png) as o:
        close, over = c.convert("RGB"), o.convert("RGB")
    w = max(1, round(close.width * share))
    s = w / over.width
    h = max(1, round(over.height * s))
    thumb = over.resize((w, h), Image.LANCZOS)
    draw = ImageDraw.Draw(thumb)
    x0, y0, x1, y1 = (round(v * s) for v in rect_px)
    draw.rectangle((x0, y0, x1, y1), outline=(230, 30, 30), width=max(2, w // 60))
    ImageDraw.Draw(thumb).rectangle((0, 0, w - 1, h - 1), outline=(240, 240, 240), width=1)
    margin = 8
    close.paste(thumb, (close.width - w - margin, close.height - h - margin))
    out = Path(out)
    close.save(out)
    mark_locator(out, Path(overview_png).name, rect_px)
    return out


def check_set(folder: Path, block: str, stage: str, rnd: int, *, interior: bool = False) -> list[str]:
    """Every reason this set is not ready to show, one line each; [] when it passes."""
    folder = Path(folder)
    problems = []
    views = REQUIRED.get(stage, ()) + (("inside",) if interior else ())
    for v in views:
        name = shot_name(block, stage, rnd, v)
        # the `_after` half of a same-camera pair stands for the view
        if not (folder / f"{name}.png").is_file() and not (folder / f"{name}_after.png").is_file():
            problems.append(f"missing view: {v} ({name}.png)")
    ours = {}
    for png in sorted(folder.glob("*.png")):
        n = parse_shot_name(png.stem)
        if n and (n.block, n.stage, n.rnd) == (block, stage, rnd):
            ours[png.stem] = (png, n)
    for stem, (png, n) in ours.items():
        e = exposure(png)
        if not e.ok:
            problems.append(f"exposure: {stem}: {e.why}")
        if n.extra.startswith("close") and not _locator_mark(png).is_file():
            problems.append(f"close-up without a locator: {stem}")
        if n.extra == "after":
            base = stem[: -len("_after")]
            if f"{base}_before" not in ours:
                problems.append(f"after without a before: {stem}")
                continue
            a, b = load_camera(ours[f"{base}_before"][0]), load_camera(png)
            if a is None or b is None:
                problems.append(f"before/after without saved cameras: {base}")
            elif not _same_camera(a, b):
                problems.append(f"before/after cameras differ: {base}")
    return problems
