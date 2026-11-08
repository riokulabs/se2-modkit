"""The fracture check of a block (plain Python, no bpy): its fail lines and summary, from the report the Blender half
(`fracture_probe.check_block`) writes before any export.

A report: {"block", "family", "status": "checked" | "unfractured" | "error", "error", "cell_groups", "tris" (the cut
LOD0), "loose": {part: {"verts", "edges"}} (LOD0 geometry with no face), "zones": [{"id", "label", "critical",
"share" (Keen's health share, fracture.health_shares), "lifted" (a min-share box hull)}], "seams": [{"zones",
"snap_mm" (its snap window, 0: fixed), "moved_mm" (how far snap_seams moved it), "clear_mm" (from its plane to the
nearest LOD0 vertex of its zones, at most fracture_probe.NEAR)}]}.

Fail lines: an error (the plan, the cut or the colliders could not be made), more cell groups than the game allows,
and, when a limit is given, a critical zone
holding more than `max_key_share` of the block's health. Without a limit that last one is a warning at MAX_KEY_SHARE:
a critical zone counts twice, so a large one makes the block hard to break, but a heavy critical core can be the
design; the bar belongs in the mod's kit, not here. Loose vertices or edges in LOD0 are a warning: the export drops
them from every model it writes (`standins.merged`), so they never ship, but a source that carries many is worth a
look.
"""

from __future__ import annotations

from ..model import limits

__all__ = ["MAX_KEY_SHARE", "problems", "summary", "warnings"]

MAX_KEY_SHARE = 0.20


def _heavy_zones(report: dict, limit: float) -> list[str]:
    name = report["block"]
    return [f"{name}: critical zone {z['id']} ({z.get('label', '')}) holds {z['share'] * 100:.1f}% of the health, "
            f"over {limit * 100:g}%: split it or move its seams" for z in report.get("zones") or ()
            if z.get("critical") and z.get("share", 0.0) > limit]


def _loose(report: dict) -> list[str]:
    name = report["block"]
    return [f"{name}: part {part} has {n.get('verts', 0)} loose vertices and {n.get('edges', 0)} loose edges in "
            "LOD0 (the export drops them)" for part, n in sorted((report.get("loose") or {}).items())
            if n.get("verts") or n.get("edges")]


def warnings(report: dict, max_key_share: float | None = None) -> list[str]:
    """Lines worth a look that do not fail: loose LOD0 geometry, and critical zones over MAX_KEY_SHARE when no limit
    is given."""
    return _loose(report) + ([] if max_key_share is not None else _heavy_zones(report, MAX_KEY_SHARE))


def problems(report: dict, max_key_share: float | None = None) -> list[str]:
    """The block's fail lines (empty: it passes); critical zone shares fail only against a given max_key_share."""
    name = report["block"]
    out = []
    if report.get("status") == "error":
        out.append(f"{name}: {report.get('error') or 'the check failed'}")
    if max_key_share is not None:
        out += _heavy_zones(report, max_key_share)
    groups = report.get("cell_groups")
    if groups:
        err = limits.cell_groups_error(name, groups)
        if err:
            out.append(err)
    return out


def summary(report: dict, max_key_share: float | None = None) -> list[str]:
    """Readable lines: the block, its zones and seams, then a WARN line per warning and a FAIL line per problem."""
    name = report["block"]
    if report.get("status") == "unfractured":
        lines = [f"{name}: not fractured ({report.get('error') or 'no plan'})"]
    else:
        lines = [f"{name} ({report.get('family')}): {len(report.get('zones') or ())} zones, cut LOD0 "
                 f"{report.get('tris', '?')} tris, {report.get('cell_groups') or '?'} cell groups"]
    for z in report.get("zones") or ():
        tags = [t for t, on in (("critical", z.get("critical")), ("lifted", z.get("lifted"))) if on]
        lines.append(f"  {z['id']}  {z.get('share', 0.0) * 100:5.1f}%  {z.get('label', '')}"
                     + (f"  [{', '.join(tags)}]" if tags else ""))
    for s in report.get("seams") or ():
        a, b = s["zones"]
        where = (f"moved {s.get('moved_mm', 0.0):.1f} mm (window {s['snap_mm']:.0f} mm)" if s.get("snap_mm")
                 else "fixed")
        lines.append(f"  seam {a} | {b}: {where}, clear {s.get('clear_mm', 0.0):.1f} mm")
    lines += [f"WARN {w}" for w in warnings(report, max_key_share)]
    lines += [f"FAIL {p}" for p in problems(report, max_key_share)]
    return lines
