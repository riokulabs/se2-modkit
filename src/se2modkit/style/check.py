"""Style checks: a mod's `build_report.json` measured against its style profile.

Each check reads per-block measurements (`REPORT_KEYS`) and one profile key, and yields `Finding`s at level `fail` or
`warn`. A measurement a block does not report is **not measured**: it is listed, never counted as a pass. Waivers
(`style-waivers.toml`) mark a finding without hiding it. Judgement checks (the side-by-side look, "every part has a
believable purpose") are manual review items and have no report field (docs/style-guide/checks.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from ..model import limits as keen_limits
from .profile import Profile, load
from .waivers import FILE_NAME, Waiver, Waivers
from .waivers import load as load_waivers

Level = Literal["fail", "warn"]


class StyleError(Exception):
    """A malformed build report, reported to the user as one plain sentence."""


@dataclass(frozen=True)
class Finding:
    block: str
    key: str
    value: float
    limit: float
    level: Level
    waived_by: str | None = None
    where: str = ""        # the LOD or zone the value comes from, when it is not the whole block


@dataclass(frozen=True)
class Result:
    findings: list[Finding]
    not_measured: dict[str, list[str]]      # check key -> blocks that do not report what it needs
    unused_waivers: list[Waiver] = field(default_factory=list)

    @property
    def unwaived_failures(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "fail" and f.waived_by is None]

    @property
    def passed_all(self) -> bool:
        return not self.unwaived_failures and not self.not_measured


# Per-block report fields the checks read, under report["blocks"][<block>]. `lods[*]` fields are what the model
# pipeline already writes; the others are emitted by the mod's build (pipeline.report) when it measures them.
REPORT_KEYS = {
    "lods[*].area_pct": "{material: percent of the LOD's visible area}",
    "lods[*].material_names": "material slot names of the LOD",
    "lods[*].materials": "material count of the LOD",
    "lods[*].tris": "triangles of the LOD",
    "lods[*].max_tris": "Keen's triangle limit for the LOD (else computed from model_size)",
    "lods[*].max_materials": "Keen's material limit for the LOD",
    "model_size": "[x, y, z] model bounds in metres (for Keen's limits when max_tris is absent)",
    "style.emissive_share": "emissive area / visible area at LOD0, lights excepted (else from area_pct)",
    "style.hazard_share": "hazard decal and stripe area / visible area at LOD0",
    "style.archetype": "vanilla archetype the block mimics, e.g. thruster, weapon (for the hazard allowance)",
    "damage.dm_tris": "Deformed (DM) LOD0 triangles",
    "damage.fr_tris": "Fractured (FR) LOD0 triangles",
    "damage.dent_mm": "mean DM dent depth in mm",
    "fracture.breakable": "false for a non-breakable block (zone and interior checks do not apply)",
    "fracture.zone_health": "{zone: share of the block's health}",
    "fracture.interior": "{zone: {exposes, cover, interior_tris, core_m2}}: the broken-interior survey",
}

_NOT_MEASURED = object()


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _r(v: float) -> float:
    return round(float(v), 6)


def _lods(b: dict) -> list[dict]:
    lods = b.get("lods")
    return [x for x in lods if isinstance(x, dict)] if isinstance(lods, list) else []


def _lod0_area(b: dict) -> dict | None:
    lods = _lods(b)
    a = lods[0].get("area_pct") if lods else None
    return a if isinstance(a, dict) else None


def _sub(b: dict, path: str):
    node = b
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _paint(name, b, p):
    area = _lod0_area(b)
    if area is None:
        return _NOT_MEASURED
    share = _r(sum(v for m, v in area.items() if m in p.get("paint.colorable_materials") and _num(v)) / 100)
    lim = p.get("paint.colorable_min_share")
    return [Finding(name, "paint.colorable_min_share", share, lim, "fail")] if share < lim else []


def _emissive(name, b, p):
    share = _sub(b, "style.emissive_share")
    if not _num(share):
        area = _lod0_area(b)
        if area is None:
            return _NOT_MEASURED
        prefix, lights = p.get("emissive.prefix"), set(p.get("emissive.light_materials"))
        share = sum(v for m, v in area.items() if m.startswith(prefix) and m not in lights and _num(v)) / 100
    share, lim = _r(share), p.get("emissive.max_share")
    return [Finding(name, "emissive.max_share", share, lim, "fail")] if share > lim else []


def _materials(name, b, p):
    lods = _lods(b)
    counts = [(i, lod.get("materials")) for i, lod in enumerate(lods) if _num(lod.get("materials"))]
    if not counts:
        return _NOT_MEASURED
    lim = p.get("materials.max_per_lod")
    i, n = max(counts, key=lambda c: (c[1], -c[0]))
    return [Finding(name, "materials.max_per_lod", n, lim, "fail", where=f"LOD{i}")] if n > lim else []


def _max_tris(b: dict, i: int, lod: dict):
    if _num(lod.get("max_tris")):
        return lod["max_tris"]
    size = b.get("model_size")
    if isinstance(size, list) and len(size) == 3 and all(_num(s) for s in size):
        rows = keen_limits.model_limits(size)["lods"]
        return rows[min(i, len(rows) - 1)]["max_tris"]
    return None


def _limits(name, b, p):
    out, seen = [], False
    for field_, key, limit_of in (("tris", "limits.tris", lambda i, lod: _max_tris(b, i, lod)),
                                  ("materials", "limits.materials", lambda i, lod: lod.get("max_materials"))):
        worst = None
        for i, lod in enumerate(_lods(b)):
            v, lim = lod.get(field_), limit_of(i, lod)
            if not (_num(v) and _num(lim)) or lim <= 0:
                continue
            seen = True
            if v > lim and (worst is None or v / lim > worst[1] / worst[2]):
                worst = (i, v, lim)
        if worst:
            out.append(Finding(name, key, worst[1], worst[2], "warn", where=f"LOD{worst[0]}"))
    return out if seen else _NOT_MEASURED


def _decals(name, b, p):
    lods = _lods(b)
    if not lods or not any(isinstance(lod.get("material_names"), list) for lod in lods):
        return _NOT_MEASURED
    prefixes, lim = tuple(p.get("decal.materials_prefix")), p.get("decal.max_lod")
    for i, lod in enumerate(lods):
        if i > lim and any(isinstance(m, str) and m.startswith(prefixes) for m in lod.get("material_names") or ()):
            return [Finding(name, "decal.max_lod", i, lim, "warn", where=f"LOD{i}")]
    return []


def _hazard(name, b, p):
    share = _sub(b, "style.hazard_share")
    if not _num(share):
        return _NOT_MEASURED
    high = _sub(b, "style.archetype") in p.get("hazard.high_archetypes")
    key = "hazard.max_share_drives_guns" if high else "hazard.max_share"
    lim = p.get(key)
    return [Finding(name, key, _r(share), lim, "fail")] if share > lim else []


def _dm_ratio(name, b, p):
    dm, fr = _sub(b, "damage.dm_tris"), _sub(b, "damage.fr_tris")
    if not (_num(dm) and _num(fr)) or fr <= 0:
        return _NOT_MEASURED
    ratio, lim = _r(dm / fr), p.get("dm.tri_ratio_max")
    return [Finding(name, "dm.tri_ratio_max", ratio, lim, "warn")] if ratio > lim else []


def _dent(name, b, p):
    dent = _sub(b, "damage.dent_mm")
    if not _num(dent):
        return _NOT_MEASURED
    lo, hi = p.get("dm.dent_mm")
    if dent < lo:
        return [Finding(name, "dm.dent_mm", _r(dent), lo, "warn")]
    if dent > hi:
        return [Finding(name, "dm.dent_mm", _r(dent), hi, "warn")]
    return []


def _breakable(b) -> bool:
    return _sub(b, "fracture.breakable") is not False


def _zone_health(name, b, p):
    if not _breakable(b):
        return []
    zones = _sub(b, "fracture.zone_health")
    if not isinstance(zones, dict) or not zones:
        return _NOT_MEASURED
    lim = p.get("fracture.min_zone_health")
    return [Finding(name, "fracture.min_zone_health", _r(v), lim, "warn", where=z)
            for z, v in sorted(zones.items()) if _num(v) and v < lim]


def _interior_rows(b):
    if not _breakable(b):
        return {}
    rows = _sub(b, "fracture.interior")
    return rows if isinstance(rows, dict) else None


def _interior_cover(name, b, p):
    rows = _interior_rows(b)
    if rows is None:
        return _NOT_MEASURED
    lim, out = p.get("interior.min_cover"), []
    for z, r in sorted(rows.items()):
        if not isinstance(r, dict) or not r.get("exposes"):
            continue
        cover = r.get("cover")
        cover = float(cover) if _num(cover) else 0.0     # no seam crosses the interior: nothing covers the hole
        if cover < lim:
            out.append(Finding(name, "interior.min_cover", _r(cover), lim, "fail", where=z))
    return out


def _interior_detail(name, b, p):
    rows = _interior_rows(b)
    if rows is None:
        return _NOT_MEASURED
    detail = sum(r.get("interior_tris", 0) for r in rows.values()
                 if isinstance(r, dict) and r.get("exposes") and _num(r.get("interior_tris", 0)))
    lim = p.get("interior.max_detail_tris")
    return [Finding(name, "interior.max_detail_tris", detail, lim, "fail", where="exposing zones")] \
        if detail > lim else []


Check = Callable[[str, dict, Profile], object]
# check key -> (level, what it measures, the check). The key is the profile key it reads (or limits.tris /
# limits.materials, whose limits come from se2modkit.model.limits).
CHECKS: dict[str, tuple[Level, str, Check]] = {
    "paint.colorable_min_share": ("fail", "LOD0 area share of the paint channel", _paint),
    "emissive.max_share": ("fail", "LOD0 emissive area share, lights excepted", _emissive),
    "materials.max_per_lod": ("fail", "material slots on the worst LOD", _materials),
    "limits.tris": ("warn", "triangles against Keen's per-LOD limit", _limits),
    "decal.max_lod": ("warn", "decal and stencil atlases past the last decal LOD", _decals),
    "hazard.max_share": ("fail", "LOD0 hazard area share (drives and guns: hazard.max_share_drives_guns)",
                         _hazard),
    "dm.tri_ratio_max": ("warn", "DM LOD0 triangles / FR LOD0 triangles", _dm_ratio),
    "dm.dent_mm": ("warn", "mean DM dent depth inside the band", _dent),
    "fracture.min_zone_health": ("warn", "each zone's share of the block's health", _zone_health),
    "interior.min_cover": ("fail", "an exposing zone's death leaves a covered core, not a hollow", _interior_cover),
    "interior.max_detail_tris": ("fail", "the exposing zones hold a few large parts, not a modelled machine",
                                 _interior_detail),
}

# Every key a Finding can carry (waivers name one of these).
FINDING_KEYS = frozenset(CHECKS) | {"limits.materials", "hazard.max_share_drives_guns"}


def run(report: dict, profile: Profile, waivers: Waivers) -> Result:
    """Every check over every block of `report`, with waivers applied."""
    blocks = report.get("blocks") if isinstance(report, dict) else None
    if not isinstance(blocks, dict):
        raise StyleError("The build report has no 'blocks' table; rebuild the mod with a current pipeline.")
    findings: list[Finding] = []
    missing: dict[str, list[str]] = {}
    for name in sorted(blocks):
        b = blocks[name]
        if not isinstance(b, dict):
            continue
        for key, (_level, _what, fn) in CHECKS.items():
            got = fn(name, b, profile)
            if got is _NOT_MEASURED:
                missing.setdefault(key, []).append(name)
            else:
                findings.extend(got)
    for extra in profile.get("checks.extra", []):
        if extra not in CHECKS:
            missing[extra] = sorted(n for n, b in blocks.items() if isinstance(b, dict))
    used: set[tuple[str, str]] = set()
    marked = []
    for f in findings:
        w = waivers.find(f.block, f.key)
        if w is not None:
            used.add((w.block, w.key))
            f = replace(f, waived_by=w.label)
        marked.append(f)
    marked.sort(key=lambda f: (f.level != "fail", f.block, f.key, f.where))
    unused = [w for w in waivers.items if (w.block, w.key) not in used]
    return Result(findings=marked, not_measured=missing, unused_waivers=unused)


def check(report: dict, profile: Profile, waivers: Waivers) -> list[Finding]:
    """The findings of `run` (waived ones included, marked with `waived_by`)."""
    return run(report, profile, waivers).findings


def mod_inputs(mod) -> tuple[dict, Profile, Waivers]:
    """(report, profile, waivers) of a project mod (`se2modkit.project.ModConfig`)."""
    from ..report import _read_json
    report = _read_json(mod.paths.report, "build_report.json")
    profile = load(mod.profile, overrides=mod.style)
    rel = mod.style.get("waivers", FILE_NAME)
    waivers = load_waivers(Path(mod.root) / rel)
    return report, profile, waivers


def run_mod(mod) -> Result:
    return run(*mod_inputs(mod))


def gate_failures(mod, result: Result | None = None) -> list[Finding]:
    """What the `style` gate fails on: unwaived `fail` findings, none when the mod sets `warn_only = true`. `result`:
    a `run_mod(mod)` already made (the gate also reports its `not_measured`)."""
    res = run_mod(mod) if result is None else result
    return [] if mod.style.get("warn_only") else res.unwaived_failures
