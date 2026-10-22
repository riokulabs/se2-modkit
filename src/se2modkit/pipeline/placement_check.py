"""Offline placement check on the EXPORTED files of a block (Blender, read-only on its inputs).

Imports the LOD0 FBX (the game builds the precise mount-point collider from LOD0), reads the collision glTF (regular /
occupancy* / mountpoints* nodes) and the Fractured glTF (Fracture_NN hulls), and runs the placement analysis with the
block's cell bounds and per-axis grid shift. Writes <models>/<name>/placement_exported.json:
  nonfractured: the generator on LOD0 + collision only (must equal build_report.json "placement", which was computed
                from the in-Blender meshes before export; a mod's tests compare them);
  fractured:    the same plus the fracture hulls, which split mount groups per fracture as the game does
                (informational: group counts can be higher, coverage and occupancy must not change), and which
                fracture supports each mount cell (the game's FindFractureId: the first fracture collider the cell's
                box query hits; a cell no fracture covers keeps its mount whatever breaks).

`analyse(tris, regular, occupancy, mount, fracture_hulls, bounds=..., shift="bounds")` defaults to
model.placement.analyse; a mod with its own occupancy range or mount directions passes its own.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

__all__ = ["check", "rep"]


def rep(r) -> dict:
    """The compact report of one analysis."""
    return {"occupied": r["occupied_in_box"], "total": r["box_cells"], "outside": r["occupied_outside_box"],
            "faces": {d: {"groups": f["groups"], "coverage": round(f["box_face_coverage_pct"] / 100.0, 4)}
                      for d, f in r["faces"].items()},
            "free_cells": [list(c) for c in r["free_cells"]], "shift": list(r["shift"])}


def check(hooks, spec, bounds, *, analyse: Callable | None = None, models_dir=None) -> dict:
    """Check block `spec`'s exported models in <models_dir or hooks.paths.models>/<name>/; returns (and writes) the
    placement_exported.json content."""
    import bpy

    from ..model import placement as PL
    analyse = analyse or PL.analyse
    name = spec.name
    d = Path(models_dir or hooks.paths.models) / name
    nf, fr = d / "NonFractured", d / "Fractured"
    fbx = nf / f"{name}.fbx"
    col, frcol = nf / f"{name}_collision.gltf", fr / f"{name}_Fractured_collision.gltf"
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.import_scene.fbx(filepath=str(fbx))
    tris = PL.tris_from_objects([o for o in bpy.context.scene.objects if o.type == "MESH"])
    reg, occ, mnt = PL.split_hulls(PL.gltf_hulls(str(col)))
    frac = [(n, v) for n, v in PL.gltf_hulls(str(frcol)).items() if n.lower().startswith("fracture")]
    r0 = analyse(tris, reg, occ, mnt, bounds=bounds, shift="bounds")
    r1 = analyse(tris, reg, occ, mnt, frac, bounds=bounds, shift="bounds")
    hulls = [PL.Hull(n, v) for n, v in frac]
    by_frac = {}
    for f in r1["faces"].values():
        for c in f["mount_cell_list"]:
            z = PL.fracture_of(c, hulls, r1["shift"]) if hulls else None
            by_frac[z or "none"] = by_frac.get(z or "none", 0) + 1
    out = {"model": name, "fbx_sha256": hashlib.sha256(fbx.read_bytes()).hexdigest(),
           "inputs": [p.relative_to(d).as_posix() for p in (fbx, col, frcol)],
           "colliders": {"regular": len(reg), "occupancy_hints": len(occ), "mount_hints": len(mnt),
                         "fractures": len(frac)},
           "render_tris": len(tris),
           "nonfractured": dict(rep(r0), summary=PL.summary_line(name, r0)),
           "fractured": dict(rep(r1), summary=PL.summary_line(name, r1), mount_cells_by_fracture=by_frac)}
    with open(d / "placement_exported.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print("PLACEMENT (exported, NonFractured)", out["nonfractured"]["summary"])
    print("PLACEMENT (exported, + fractures) ", out["fractured"]["summary"])
    return out
