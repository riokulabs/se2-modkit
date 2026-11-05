"""`se2mod look`: a fast look set of a block, rendered straight from the working file in the look-dev scene.

The plain-Python half: which views a set needs, the next free round, the job the Blender side (`look_run`) reads, the
queue command, material names to Keen slots, the cameras a `--before` set used, and `finish`, which runs once the
render is back: it draws the close-ups' locators, checks exposure and the shot standard, and writes the contact sheet
(this round's pictures, without the `_before` copies) and `report.json`, listing the geometry checks' failures
(`geocheck.json`) and any `--close` part that matched nothing with the rest. Nothing here imports Blender;
Pillow only in `finish`.

A look set is never the export: no LODs, no fracture, no build. The source is appended into a fresh scene and never
saved, so a `.blend` open in a live Blender is safe to point at (its last save is what renders).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import lookdev, shots
from .compose import PreviewError

__all__ = ["SOURCE_KINDS", "UNMAPPED", "before_cameras", "build_job", "close_hits", "finish", "material_key", "next_round",
           "queue_argv", "source_kind", "views_for"]

SOURCE_KINDS = ("blend", "glb", "gltf", "fbx")
UNMAPPED = "Unmapped"
LOAD = "medium"
_ROUND = re.compile(r"r(\d+)")


def source_kind(path: Path) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in ("step", "stp"):
        raise PreviewError(f"{Path(path).name}: Blender can't read STEP; export the blockout as a GLB and look at that.")
    if ext not in SOURCE_KINDS:
        raise PreviewError(f"{Path(path).name}: a look set renders a {', '.join('.' + k for k in SOURCE_KINDS)} file.")
    return ext


def views_for(spec: str, interior: bool = False) -> list[str]:
    """`look` or `concept` (the shot standard's required set, plus `inside` for a block with an interior), or a
    comma list of view names (old aliases allowed)."""
    if spec in shots.REQUIRED and shots.REQUIRED[spec]:
        views = list(shots.REQUIRED[spec])
    else:
        views = [shots.canonical(v.strip()) for v in spec.split(",") if v.strip()]
        if not views:
            raise PreviewError("no views asked for.")
    if interior and "inside" not in views:
        views.append("inside")
    return views


def next_round(block_dir: Path) -> int:
    """One more than the highest `rN` folder under the block's look folder (1 when there is none)."""
    block_dir = Path(block_dir)
    if not block_dir.is_dir():
        return 1
    nums = [int(m[1]) for d in block_dir.iterdir() if d.is_dir() and (m := _ROUND.fullmatch(d.name))]
    return max(nums, default=0) + 1


def material_key(name: str, table: dict) -> tuple[str, bool]:
    """(stand-in key, unmapped?) for a working-file material: the mod's `[lookdev.materials]` table first, then the
    Keen slot rules; anything else is the loud `Unmapped` magenta and is reported."""
    from . import mats
    base = name.split(".")[0]
    if base in table:
        return mats.keen_key(table[base]), False
    if mats.is_keen_slot(name):
        return mats.keen_key(name), False
    return UNMAPPED, True


def close_hits(names, part: str) -> list[str]:
    """The object names a `--close PART` frames: the part itself, or its Blender copies (`Nozzle.001`)."""
    return [n for n in names if n == part or n.split(".")[0] == part]


def before_cameras(before_dir: Path, block: str, views: list[str]) -> dict[str, dict]:
    """{view: {"png": path, "camera": saved camera}} from an earlier look set of the same block (its `_after` image
    when it was itself a pair). Every view asked for must have one."""
    found: dict[str, dict] = {}
    for png in sorted(Path(before_dir).glob("*.png")):
        n = shots.parse_shot_name(png.stem)
        if not n or n.block != block or n.view not in views or n.extra not in ("", "after"):
            continue
        cam = shots.load_camera(png)
        if cam and (n.view not in found or n.extra == "after"):
            found[n.view] = {"png": str(png), "camera": cam}
    missing = [v for v in views if v not in found]
    if missing:
        raise PreviewError(f"{before_dir}: no saved camera for {', '.join(missing)} (re-render that set first).")
    return found


def build_job(*, source: Path, block: str, out_dir: Path, stage: str, rnd: int, views: list[str], set_name: str,
              collection: str = "", mount_face: str = "-Z", grid: str = "large", neighbors=(), materials=None,
              armor_dir: Path | None = None, before: Path | None = None, close=(), fast: bool = True,
              geocheck: bool = True, tris_budget: int | None = None) -> dict:
    """The JSON `look_run` reads."""
    source = Path(source)
    if not source.is_file():
        raise PreviewError(f"no such file: {source}")
    kind = source_kind(source)
    from . import mats
    bad = sorted(f"{k}={v}" for k, v in (materials or {}).items() if not mats.is_keen_slot(v))
    if bad:
        raise PreviewError(f"not a Keen material slot: {', '.join(bad)} (use slot names such as PaintedMetalDarker, "
                           "MetalDull, BrushedMetal).")
    lookdev.set_spec(set_name)
    lookdev.mount_rotation(mount_face)
    return {
        "source": str(source.resolve()), "kind": kind, "collection": collection, "block": block, "stage": stage,
        "round": rnd, "views": list(views), "set": set_name, "mount_face": mount_face, "grid": grid,
        "neighbors": list(neighbors), "materials": dict(materials or {}),
        "armor_dir": str(armor_dir) if armor_dir else "", "out": str(Path(out_dir).resolve()),
        "before": before_cameras(before, block, list(views)) if before else {}, "close": list(close),
        "fast": fast, "load": LOAD, "priority": True, "geocheck": geocheck, "tris_budget": tris_budget,
    }


def queue_argv(job_file: Path, blender_argv: list[str], *, name: str, priority: bool, timeout: int = 900) -> list[str]:
    """`se2mod queue submit` arguments for a look job (`priority` only when the queue has a priority lane)."""
    argv = ["queue", "submit", "--name", name, "--load", LOAD, "--timeout", str(timeout)]
    if priority:
        argv.append("--priority")
    return argv + ["--", *blender_argv]


def finish(out: Path, *, block: str, stage: str, rnd: int, interior: bool = False) -> dict:
    """After the render: locators on close-ups, the shot standard's checks, the contact sheet, `report.json`."""
    from .compose import make_sheet
    out = Path(out)
    close = out / "close.json"
    if close.is_file():
        for stem, info in json.loads(close.read_text(encoding="utf-8")).items():
            png = out / f"{stem}.png"
            if png.is_file() and (out / info["overview"]).is_file():
                shots.add_locator(png, out / info["overview"], info["rect"], png)
    report_path = out / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    problems = shots.check_set(out, block, stage, rnd, interior=interior)
    problems += [f"unmapped material: {m} (renders magenta; map it in [lookdev.materials])"
                 for m in report.get("unmapped_materials", [])]
    problems += [f"close-up: no part named {p!r}" for p in report.get("close_missing", [])]
    geo = out / "geocheck.json"
    if geo.is_file():
        try:
            g = json.loads(geo.read_text(encoding="utf-8"))
            report["geocheck"] = {"ok": g.get("ok", True), "failures": len(g.get("failures", [])),
                                  "warnings": len(g.get("warnings", []))}
        except (ValueError, AttributeError):   # check_set has already listed it as unreadable
            report["geocheck"] = {"ok": False, "unreadable": True}
    report["exposure"] = {p.stem: shots.exposure(p).__dict__ for p in sorted(out.glob(f"{block}_{stage}_r{rnd}_*.png"))}
    report["problems"] = problems
    # the `_before` halves are copies of an earlier set's pictures: the sheet shows this round
    pngs = sorted(p for p in out.glob(f"{block}_{stage}_r{rnd}_*.png")
                  if not ((n := shots.parse_shot_name(p.stem)) and n.extra == "before"))
    if pngs:
        make_sheet([str(p) for p in pngs], out / "sheet.png", cols=3, labels=True, thumb=480)
    report_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return report
