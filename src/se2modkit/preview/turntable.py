"""The generic per-block preview set (Blender), on `preview.base`. Per block:

  turntable   six orthographic views (front, back, left, right, top, bottom) and the 3/4 front and 3/4 rear;
  close-ups   `closeups[block name]` (or [family]): [(label, camera xyz, target xyz, lens mm)] in the block frame
              (centred, +Y = front, Z up);
  LOD row     LOD0..n side by side, each captioned with its triangles / Keen's limit (build_report.json);
  paints      `PAINT_ROW` side by side;
  vanilla     the block beside vanilla blocks at the same scale and light (`vanilla_row`, plus `vanilla_extra[family]`).

`built` is the build runner's {block name: {"lods": [(collection, objects), ...]}}; `specs` maps a block name to its
spec (`cells`: the cell box, `family`). Shots render through `base.Shot` into the configured output folder;
`base.configure(only=...)` selects shots by name prefix.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import base as PB

__all__ = ["ORTHO", "PAINT_ROW", "PARTS", "VANILLA_ROW", "closeups", "extent", "lods", "paints", "render_set",
           "turntable", "vanilla_compare"]

# (path under the vanilla reference's Blocks/, label, cell-box edge m). Placed by the cell box, not the mesh bounds:
# a vanilla model is centred on its cell box, and some carry meshes far outside it that would lift it off the floor.
VANILLA_ROW = (("Welder/400/Models/NonFractured/Welder400.fbx", "Welder400", 2.5),
               ("CargoContainers/CargoContainers/250/Models/NonFractured/CargoContainer250.fbx", "CargoContainer250", 2.5),
               ("Batteries/Battery/50/Models/NonFractured/Battery50.fbx", "Battery50", 0.5),
               ("Sensor/25/Model/NonFractured/Sensor25.fbx", "Sensor25", 0.25))
PAINT_ROW = ("keen", "black", "red", "blue")
ORTHO = ("front", "back", "left", "right", "top", "bottom")
PARTS = ("turntable", "closeups", "lods", "paints", "vanilla")


def extent(spec):
    """(width x, depth y, height z) of the block's cell box in the Blender frame (game cells: x, up, across)."""
    return 0.25 * spec.cells[0], 0.25 * spec.cells[2], 0.25 * spec.cells[1]


def _report(report: str | Path | None, name: str) -> dict:
    if not report or not Path(report).exists():
        return {}
    return json.loads(Path(report).read_text(encoding="utf-8")).get("blocks", {}).get(name, {})


def turntable(built, spec, paint="keen"):
    """The six orthos (scale = the block's largest extent + margin) and the two 3/4 views."""
    name = spec.name
    w, d, h = extent(spec)
    m = max(w, d, h)
    zc = h / 2
    D = 3.0 * m + 1.0
    floor = (-m - 1, m + 1, -m - 1, m + 1)
    for v in ORTHO:
        nm = f"{name}_ortho_{v}"
        if not PB.want(nm):
            continue
        s = PB.Shot(nm, res=(1400, 1400), floor=None if v == "bottom" else floor)
        s.inst(PB.painted(built, name, 0, paint), PB.T(0, 0, zc))
        loc = {"front": (0, D, zc), "back": (0, -D, zc), "left": (-D, 0, zc), "right": (D, 0, zc),
               "top": (0, 0.0001, zc + D), "bottom": (0, 0.0001, zc - D)}[v]
        s.camera(loc, (0, 0, zc), 50, ortho=1.25 * m)
        s.render()
    for nm, loc in ((f"{name}_3q_front", (-0.6 * D, 0.8 * D, zc + 0.45 * D)),
                    (f"{name}_3q_rear", (0.6 * D, -0.8 * D, zc + 0.45 * D))):
        if not PB.want(nm):
            continue
        s = PB.Shot(nm, floor=floor)
        s.inst(PB.painted(built, name, 0, paint), PB.T(0, 0, zc))
        s.camera(loc, (0, 0, zc * 0.9), 50)
        s.render()


def closeups(built, spec, table: Mapping, paint="keen"):
    """table[name] (else [family]): one shot each, camera / target in the block frame (centre at the origin)."""
    name = spec.name
    _, _, h = extent(spec)
    for label, cam, tgt, lens in table.get(name, table.get(getattr(spec, "family", None), ())):
        nm = f"{name}_closeup_{label}"
        if not PB.want(nm):
            continue
        s = PB.Shot(nm)
        s.inst(PB.painted(built, name, 0, paint), PB.T(0, 0, h / 2))
        s.camera(tuple(c + (h / 2 if i == 2 else 0) for i, c in enumerate(cam)),
                 tuple(c + (h / 2 if i == 2 else 0) for i, c in enumerate(tgt)), lens)
        s.render()


def lods(built, spec, report=None, paint="keen"):
    """LOD0..n in a row (image left to right), each captioned with tris / Keen's max (build_report.json)."""
    name = spec.name
    nm = f"{name}_lods"
    if not PB.want(nm):
        return
    w, d, h = extent(spec)
    n = len(built[name]["lods"])
    step = w + 0.5
    s = PB.Shot(nm, res=(2400, 1000), floor=(-step * n, step, -d - 1, d + 1))
    for i in range(n):        # cameras look from +Y (+X is image-left): LOD0 left, LOD n right
        s.inst(PB.painted(built, name, i, paint), PB.T(-step * i, 0, h / 2))
    cx = -step * (n - 1) / 2
    s.camera((cx, 1.6 * step * n + d, h * 0.9 + 1.0), (cx, 0, h * 0.45), 50)
    rows = _report(report, name).get("lods") or []
    for i in range(n):
        r = rows[i] if i < len(rows) else {}
        s.caption(f"LOD{i}\n{r.get('tris', '?')}/{r.get('max_tris', '?')} tris", (-step * i, d / 2 + 0.3, h + 0.3),
                  0.08 * max(1.0, h / 2))
    s.render()


def paints(built, spec, row: Sequence[str] = PAINT_ROW):
    """The block in each paint of `row`, a flush row."""
    name = spec.name
    nm = f"{name}_paints"
    if not PB.want(nm):
        return
    w, d, h = extent(spec)
    step = w + 0.25
    n = len(row)
    s = PB.Shot(nm, res=(2400, 1100), floor=(-step * n, step, -d - 1, d + 1))
    cx = -step * (n - 1) / 2
    s.camera((cx + 0.4 * step, 1.4 * step * n + d, h + 1.2), (cx, 0, h * 0.45), 40)
    for i, p in enumerate(row):
        s.inst(PB.painted(built, name, 0, p), PB.T(-step * i, 0, h / 2))
        s.caption(p.upper(), (-step * i, d / 2 + 0.3, 0.06), 0.1 * max(1.0, h / 2))
    s.render()


def vanilla_compare(built, spec, rows=VANILLA_ROW, paint="keen"):
    """Ours beside the vanilla blocks, same scale and light, standing on the floor in a row."""
    name = spec.name
    nm = f"{name}_vanilla"
    if not PB.want(nm):
        return
    w, d, h = extent(spec)
    s = PB.Shot(nm, res=(2600, 1150), floor=(-14, 3, -3, 3))
    s.inst(PB.painted(built, name, 0, paint), PB.T(0, 0, h / 2))
    x = -w / 2 - 0.5
    tops = [h]
    placed = []
    for rel, label, edge in rows:
        c, _, _ = PB.vanilla(rel, paint)
        placed.append((c, PB.T(x - edge / 2, 0, edge / 2), label, x - edge / 2, edge))
        tops.append(edge)
        x -= edge + 0.5
    for c, m, _, _, _ in placed:
        s.inst(c, m)
    cx = x / 2
    span = -x + w / 2
    s.camera((cx, 0.95 * span + 2, max(tops) * 0.7 + 1.0), (cx, 0, max(tops) * 0.4), 35)
    cap = max(0.08, 0.022 * span)               # legible on the sheet whatever the row's length
    s.caption(f"OURS: {name}", (0, d / 2 + 0.3, h + 2 * cap), cap)
    for _, _, label, lx, top in placed:
        s.caption(f"vanilla {label}", (lx, 0.3, top + 2 * cap), cap)
    s.render()


def render_set(built, specs: Mapping, *, report=None, closeup_table: Mapping | None = None,
               vanilla_row=VANILLA_ROW, vanilla_extra: Mapping | None = None, paint_row=PAINT_ROW, parts=PARTS):
    """The generic set for every built block (`parts` selects which of PARTS run)."""
    from . import mats as PM
    PM.get("Caption")
    for name in built:
        spec = specs[name]
        if "turntable" in parts:
            turntable(built, spec)
        if "closeups" in parts:
            closeups(built, spec, closeup_table or {})
        if "lods" in parts:
            lods(built, spec, report)
        if "paints" in parts:
            paints(built, spec, paint_row)
        if "vanilla" in parts:
            extra = tuple((vanilla_extra or {}).get(getattr(spec, "family", None), ()))
            vanilla_compare(built, spec, tuple(vanilla_row) + extra)
