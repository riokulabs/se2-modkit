"""The shared gates: checks every mod runs the same way, enabled per mod by `[tool.se2modkit] gates`.

A mod collects them with one line in `Tests/test_gates.py`:

    from se2modkit.testing.gates import test_gate  # noqa: F401  (the shared gates)

and gets one test item per listed gate (`test_gate[<gate>]`; `test_gate[<mod>-<gate>]` in a repo with several mods,
plus `test_gate[guids-across-mods]` when any of them lists `guids`). A gate collects every problem it finds and fails
once with the list; notes that are not failures (e.g. what the style check could not measure) are shown as warnings.

  json               every asset JSON file (installed and staged: .def .partialdef .model .meta .gltf .json) parses
  bundles            every "Bundle:Type" name in an asset file is declared in its own $Bundles (validate.bundles);
                     each root must exist and hold at least `gate_options.bundles.min_files` files (default 1)
  guids              no definition GUID and no .meta AssetID is defined twice in the mod (staging over the installed
                     files by path); across mods: no GUID or AssetID in two mods of the repo
  clientserver       the load (vanilla + the mod's Assets + its staging) passes the client/server composition walk
                     (validate.clientserver)                                                       [sdk, decompiled]
  schema             the mod's definitions pass the non-null member and component definition type checks
                     (validate.schema), and so does the built Content/ when there is one; Content outputs current
                                                                                                   [sdk, decompiled]
  fresh_clone        staging from the tracked files alone (a fresh clone's Assets/) gives the package staging from
                     the working tree gives, and mints no new asset GUID (needs the `scratch_stage` fixture)  [slow]
  determinism        two stagings from the same inputs are byte-identical (needs `scratch_stage`)              [slow]
  stages             each built block's installed BuildProgressModels are what pipeline.construction generates from
                     its export.json (seed "<mod name lower>/<block>")                                        [build]
  construction_roles every Fractured LOD's groups exist at LOD0; zones, criticals and NF mesh parts match the golden
                     states baseline (a save binds to them)                                                   [build]
  scaffold           a cut block keeps its LOD count and budgets with its scaffold, its Damaged model is the
                     Fractured minus the scaffold, and a caged block shows the cage only below 30 % and never on a
                     damaged finished block                                                                   [build]
  state_view         the golden states baseline is complete (every built block, every key, real values) and no
                     definition GUID it records was removed
  zfight             no recorded z-fight fault in any built block (pipeline.zfight: decal elements, decal vs
                     geometry, moving subpart vs itself and its frame, spawned subpart vs block; the visible-coplanar
                     rows are the mod's to judge against its known follow-ups)                                [build]
  placement          the placement check on the EXPORTED files reproduces build_report.json's                 [build]
  style              no unwaived style failure (style.check); what it could not measure is a warning          [build]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ..project import ModConfig
from .fixtures import project_for

__all__ = ["GATES", "GateCase", "GateNote", "cases_for", "test_gate"]

LIMIT = 40                       # problems listed in a failure (the count is always given)
CROSS = "guids-across-mods"
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
JSON_SUFFIXES = (".def", ".partialdef", ".model", ".meta", ".gltf", ".json")


class GateNote(UserWarning):
    """A gate's note that is not a failure (shown in pytest's warnings summary)."""


@dataclass(frozen=True)
class GateCase:
    name: str
    mod: ModConfig | None                  # None: the cross-mod case
    mods: tuple[ModConfig, ...] = ()
    marks: tuple[str, ...] = ()
    id: str = ""


@dataclass
class Ctx:
    """What a gate reads: the mod, its fixtures (through the requesting test) and a list of notes."""
    request: pytest.FixtureRequest
    mod: ModConfig | None
    mods: tuple[ModConfig, ...] = ()
    notes: list[str] = field(default_factory=list)

    def fixture(self, name):
        return self.request.getfixturevalue(name)

    def option(self, gate: str, key: str, default):
        """`[tool.se2modkit.gate_options.<gate>] <key>` of the mod, else `default`."""
        return ((self.mod.gate_options or {}).get(gate) or {}).get(key, default)

    @property
    def assets(self) -> Path:
        return Path(self.mod.paths.assets)

    @property
    def staged(self) -> Path | None:
        return self.fixture("staged_assets")

    @property
    def hooks(self):
        from ..pipeline.hooks import ModHooks
        return ModHooks.for_mod(self.mod)

    def built(self) -> list[str]:
        """The blocks the build report has built (a non-zero placement total, as pipeline.report counts them)."""
        rep = self.fixture("build_report")
        names = sorted(n for n, e in (rep.get("blocks") or {}).items() if (e or {}).get("placement", {}).get("total"))
        if not names:
            pytest.fail(f"{self.mod.name}: build_report.json lists no built block (a gate on it would check nothing)",
                        pytrace=False)
        return names

    def manifest(self, name: str) -> dict | None:
        p = Path(self.mod.paths.models) / name / "export.json"
        return _read_json(p) if p.is_file() else None


def _read_json(p: Path):
    with open(p, encoding="utf-8-sig") as fh:
        return json.load(fh)


def _rel(p: Path, root: Path) -> str:
    try:
        return Path(p).relative_to(root).as_posix()
    except ValueError:
        return str(p)


def _files(root: Path | None, suffixes) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if root is None or not Path(root).is_dir():
        return out
    for dp, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            if f.endswith(suffixes):
                p = Path(dp) / f
                out[_rel(p, Path(root))] = p
    return out


def _overlay(ctx: Ctx, suffixes) -> dict[str, Path]:
    """rel path -> file of the package as installed: the staged file over the mod's (same relative path)."""
    return {**_files(ctx.assets, suffixes), **_files(ctx.staged, suffixes)}


# ------------------------------------------------------------------------------------------------ the gates
def gate_json(ctx: Ctx) -> list[str]:
    bad = []
    for label, root in (("installed", ctx.assets), ("staged", ctx.staged)):
        for rel, p in _files(root, JSON_SUFFIXES).items():
            try:
                _read_json(p)
            except (OSError, ValueError) as e:
                bad.append(f"{label} {rel}: {e}")
    return bad


def gate_bundles(ctx: Ctx) -> list[str]:
    from ..validate.bundles import undeclared_in
    want = ctx.option("bundles", "min_files", 1)
    bad = []
    for label, root in (("installed", ctx.assets), ("staged", ctx.staged)):
        if root is None and label == "staged":
            continue                     # nothing staged: the package is the installed files
        if root is None or not Path(root).is_dir():
            bad.append(f"{label} root {root} is missing, so no asset file could be checked")
            continue
        n, rows = undeclared_in(root)
        if n < want:
            bad.append(f"{label} root {_rel(Path(root), Path(ctx.mod.root))} holds {n} asset files, fewer than the "
                       f"{want} the bundles gate expects (gate_options.bundles.min_files)")
        bad += [f"{label} {rel}: undeclared bundle(s) {', '.join(missing)}" for rel, missing in rows]
    return bad


def _ids(files: dict[str, Path]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """({own definition GUID: [files]}, {.meta AssetID: [files]}) over the package files."""
    from ..defs.assetio import own_guid
    owns: dict[str, list[str]] = {}
    metas: dict[str, list[str]] = {}
    for rel, p in files.items():
        try:
            d = _read_json(p)
        except (OSError, ValueError):
            continue                     # the json gate reports it
        if not isinstance(d, dict):
            continue
        if rel.endswith(".meta"):
            aid = (d.get("$Value") or {}).get("AssetID") if isinstance(d.get("$Value"), dict) else None
            if isinstance(aid, str) and aid:
                metas.setdefault(aid.lower(), []).append(rel)
        elif rel.endswith((".def", ".partialdef")):
            g = own_guid(d)
            if isinstance(g, str) and _GUID_RE.match(g):
                owns.setdefault(g.lower(), []).append(rel)
    return owns, metas


def gate_guids(ctx: Ctx) -> list[str]:
    owns, metas = _ids(_overlay(ctx, (".def", ".partialdef", ".meta")))
    bad = [f"definition GUID {g} defined by {len(fs)} files: {', '.join(fs)}" for g, fs in owns.items() if len(fs) > 1]
    bad += [f".meta AssetID {a} in {len(fs)} files: {', '.join(fs)}" for a, fs in metas.items() if len(fs) > 1]
    return sorted(bad)


def gate_guids_across(ctx: Ctx) -> list[str]:
    seen: dict[str, list[tuple[str, str]]] = {}
    for mod in ctx.mods:
        sub = Ctx(ctx.request, mod)
        files = {**_files(sub.assets, (".def", ".partialdef", ".meta")),
                 **_files(_staged_dir(mod), (".def", ".partialdef", ".meta"))}
        owns, metas = _ids(files)
        mine: dict[str, str] = {}
        for table in (owns, metas):
            for g, fs in table.items():
                mine.setdefault(g, fs[0])
        for g, rel in mine.items():
            seen.setdefault(g, []).append((mod.name, rel))
    return sorted(f"{g} in {len(w)} mods: " + "; ".join(f"{m}: {r}" for m, r in w)
                  for g, w in seen.items() if len({m for m, _ in w}) > 1)


def _staged_dir(mod: ModConfig) -> Path | None:
    p = Path(mod.paths.staged)
    return p if p.is_dir() else None


def gate_clientserver(ctx: Ctx) -> list[str]:
    from ..validate import clientserver as csw
    sdk, types = ctx.fixture("sdk_assets"), ctx.fixture("type_index")
    roots = [sdk, ctx.assets] + ([ctx.staged] if ctx.staged is not None else [])
    u = csw.Universe([str(r) for r in roots], cached_roots=[str(sdk)])
    errs = csw.validate(csw.walk(u), types)
    return [csw.format_errors(errs, u, limit=LIMIT)] if errs else []


def _ours(u, *roots) -> list[str]:
    roots = tuple(os.path.abspath(str(r)) + os.sep for r in roots if r is not None)
    return [g for g in u.entries if os.path.abspath(str(u.origin(g))).startswith(roots)]


def gate_schema(ctx: Ctx) -> list[str]:
    from ..validate import clientserver as csw
    from ..validate import schema as ds
    sdk, types = ctx.fixture("sdk_assets"), ctx.fixture("type_index")
    s = ds.Schema(types)
    roots = [sdk, ctx.assets] + ([ctx.staged] if ctx.staged is not None else [])
    u = csw.Universe([str(r) for r in roots], cached_roots=[str(sdk)])
    mine = _ours(u, ctx.assets, ctx.staged)
    bad = []
    errs = ds.nonnull_errors(u, s, mine) + ds.composition_type_errors(u, s, mine)
    if errs:
        bad.append("installed package:\n" + ds.format_errors(errs, limit=LIMIT))
    if s.unknown:
        bad.append("member types the type facts cannot classify (the check would skip them): "
                   + ", ".join(f"{o}.{t} x{n}" for (o, t), n in sorted(s.unknown.items())[:LIMIT]))
    content = Path(ctx.mod.root) / "Content"
    if (content / "definitionsets.vrb").is_file():
        stale = ds.stale_outputs(ctx.assets, content)
        bad += [f"stale Content output {r}: {w} (rebuild: se2mod build)" for r, w in stale]
        u2 = csw.Universe([str(sdk), str(ctx.assets)], cached_roots=[str(sdk)])
        cu, missing = ds.content_view(u2, ctx.assets, content)
        bad += [f"no Content output for {r} (rebuild: se2mod build)" for r in missing]
        mine2 = _ours(u2, ctx.assets)
        cerrs = ds.nonnull_errors(cu, s, mine2) + ds.composition_type_errors(cu, s, mine2)
        if cerrs:
            bad.append("built Content/ as the game loads it:\n" + ds.format_errors(cerrs, limit=LIMIT))
    return bad


def _stage_hook(ctx: Ctx) -> Callable:
    fn = ctx.fixture("scratch_stage")
    if fn is None:
        pytest.skip(f"{ctx.mod.name}: the mod's conftest provides no scratch_stage fixture "
                    "(fn(mod_assets, out, guids) staging the package into a scratch dir)")
    return fn


def _tree(root: Path) -> dict[str, str]:
    out = {}
    for rel, p in _files(root, "").items():
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _tree_diff(a: dict[str, str], b: dict[str, str], la: str, lb: str) -> list[str]:
    out = [f"only in {la}: {r}" for r in sorted(set(a) - set(b))]
    out += [f"only in {lb}: {r}" for r in sorted(set(b) - set(a))]
    out += [f"differs: {r}" for r in sorted(set(a) & set(b)) if a[r] != b[r]]
    return out


def _guids_copy(ctx: Ctx, dst: Path) -> Path:
    src = ctx.mod.paths.asset_guids
    if src is not None and Path(src).is_file():
        shutil.copy2(src, dst)
    return dst


def _guid_problem(ctx: Ctx, copy: Path, label: str) -> list[str]:
    src = ctx.mod.paths.asset_guids
    if src is None or not Path(src).is_file():
        return []
    if not copy.is_file() or _read_json(copy) != _read_json(Path(src)):
        return [f"{label}: staging minted or dropped asset GUIDs (its asset_guids.json differs from the tracked one)"]
    return []


def gate_fresh_clone(ctx: Ctx) -> list[str]:
    stage = _stage_hook(ctx)
    root = Path(ctx.mod.root)
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", ctx.assets.name], capture_output=True)
    if r.returncode != 0:
        pytest.skip(f"{ctx.mod.name}: not a git checkout, so there is no fresh clone to compare with")
    tmp = ctx.fixture("tmp_path_factory").mktemp(f"{ctx.mod.name}-fresh-clone")
    fresh = tmp / "fresh" / ctx.assets.name
    for rel in filter(None, r.stdout.decode("utf-8", "surrogateescape").split("\0")):
        src = root / rel
        if src.is_file():
            dst = tmp / "fresh" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    fresh.mkdir(parents=True, exist_ok=True)
    work = tmp / "work" / ctx.assets.name
    shutil.copytree(ctx.assets, work)
    outs = {}
    bad = []
    for label, assets in (("fresh clone", fresh), ("working tree", work)):
        guids = _guids_copy(ctx, tmp / f"{label.replace(' ', '_')}_asset_guids.json")
        out = tmp / f"{label.replace(' ', '_')}_staged"
        stage(assets, out, guids)
        outs[label] = _tree(out)
        bad += _guid_problem(ctx, guids, label)
    bad += _tree_diff(outs["fresh clone"], outs["working tree"], "the fresh clone's staging",
                      "the working tree's staging")
    return bad


def gate_determinism(ctx: Ctx) -> list[str]:
    stage = _stage_hook(ctx)
    tmp = ctx.fixture("tmp_path_factory").mktemp(f"{ctx.mod.name}-determinism")
    trees, bad = [], []
    for i in (1, 2):
        work = tmp / f"run{i}" / ctx.assets.name
        shutil.copytree(ctx.assets, work)
        guids = _guids_copy(ctx, tmp / f"run{i}_asset_guids.json")
        out = tmp / f"run{i}_staged"
        stage(work, out, guids)
        trees.append(_tree(out))
        bad += _guid_problem(ctx, guids, f"run {i}")
    return bad + _tree_diff(trees[0], trees[1], "run 1", "run 2")


def gate_stages(ctx: Ctx) -> list[str]:
    from ..pipeline import construction as C
    from ..pipeline import states as S
    hooks, bad = ctx.hooks, []
    for n in ctx.built():
        man = ctx.manifest(n)
        if man is None:
            bad.append(f"{n}: built but has no export.json in {ctx.mod.paths.models} (re-export the models)")
            continue
        parts = (man.get("fractured") or {}).get("parts")
        if not parts:
            bad.append(f"{n}: export.json has no Fractured mesh parts to generate the build stages from")
            continue
        parts = {k: tuple(v) for k, v in parts.items()}
        try:
            bmd = S.installed_bmd(hooks, n)
        except (OSError, KeyError, ValueError) as e:
            bad.append(f"{n}: no installed BlockModelDefinition ({e})")
            continue
        want = C.generate_stages(parts, bmd["Model"], bmd["FractureModel"], seed=f"{ctx.mod.name.lower()}/{n}",
                                 damaged_hide=C.scaffold_parts(parts))
        if bmd.get("BuildProgressModels") != want:
            bad.append(f"{n}: the installed build stages are not what export.json's parts generate (restage)")
    return bad


def _golden(ctx: Ctx) -> dict | None:
    p = Path(ctx.mod.paths.golden_states)
    return _read_json(p) if p.is_file() else None


def gate_construction_roles(ctx: Ctx) -> list[str]:
    from ..pipeline import states as S
    hooks, golden, bad = ctx.hooks, _golden(ctx), []
    if golden is None:
        return [f"no golden states baseline at {_rel(Path(ctx.mod.paths.golden_states), Path(ctx.mod.root))} "
                "(write it with pipeline.states.write_baseline)"]
    for n in ctx.built():
        man = ctx.manifest(n)
        if man is None:
            bad.append(f"{n}: no export.json in {ctx.mod.paths.models}")
            continue
        fr = man.get("fractured") or {}
        lods = fr.get("lods") or []
        lod0 = set((lods[0].get("groups") or []) if lods else [])
        for i, lod in enumerate(lods[1:-1], 1):
            extra = set(lod.get("groups") or []) - lod0
            if extra:
                bad.append(f"{n}: Fractured LOD{i} groups not at LOD0: {sorted(extra)}")
        rec = golden.get(n)
        if rec is None:
            bad.append(f"{n}: built but not in the golden states baseline (record it with write_baseline)")
            continue
        now = {"zones": [z["id"] for z in fr.get("zones") or []], "critical": list(fr.get("critical") or [])}
        nf = S.model_fbx(hooks, n, "nf")
        now["nf_parts"] = S.fbx_parts(nf) if nf is not None else None
        for k in ("zones", "critical", "nf_parts"):
            if now[k] != rec.get(k):
                bad.append(f"{n}: {k} {now[k]} != the golden baseline's {rec.get(k)} (a save binds to them)")
    return bad


def gate_scaffold(ctx: Ctx) -> list[str]:
    from ..model import limits
    from ..pipeline import construction as C
    from ..pipeline import fracture_rules as FRR
    from ..pipeline import scaffold as SC
    from ..pipeline import states as S
    hooks, bad = ctx.hooks, []
    mat = C.PRESETS["scaffolding"]["T"][0]
    models = Path(ctx.mod.paths.models)

    def meshes(n, kind, lod):
        man = ctx.manifest(n)
        key = "fractured" if kind == "Fractured" else "damaged"
        return FRR.fbx_meshes(str(models / n / kind / man[key]["lods"][lod]["file"]))

    def cage(ms):
        return [m for m in ms.values() if m["props"].get("ConstructionMeshType") == "Scaffold"]

    def tris(ms):
        return sum(len(p) - 2 for m in ms for p in m["polys"])

    for n in ctx.built():
        man = ctx.manifest(n)
        if man is None:
            bad.append(f"{n}: built but has no export.json in {ctx.mod.paths.models} (re-export the models)")
            continue
        fr = man.get("fractured") or {}
        if fr.get("kind") != "cut":
            continue                     # not cut: no scaffold to check
        lods, sc = fr["lods"], fr.get("scaffold") or {"tris": {}}
        t0 = lods[0]["tris"]
        if limits.optimal_lods(tuple(fr["size"]), t0)[0] != len(lods):
            bad.append(f"{n}: with its scaffold the Fractured model leaves Keen's LOD count for its size")
        for i, lod in enumerate(lods[:-1]):
            got = tris(cage(meshes(n, "Fractured", i)))
            if got != sc["tris"].get(str(i), 0):
                bad.append(f"{n}: LOD{i} scaffold has {got} triangles, export.json says {sc['tris'].get(str(i), 0)}")
            if lod["tris"] > fr["budget"][i]:
                bad.append(f"{n}: Fractured LOD{i} {lod['tris']} triangles over its budget {fr['budget'][i]}")
        if "1" in sc["tris"] and lods[1]["tris"] > SC.LOD1_FILL * fr["budget"][1]:
            bad.append(f"{n}: Fractured LOD1 carries the cage above {SC.LOD1_FILL:.0%} of its budget")
        for i in range(len(lods)):
            frp = FRR.mesh_parts(meshes(n, "Fractured", i))
            dmp = FRR.mesh_parts(meshes(n, "Damaged", i))
            want = frp - ({SC.PART} if cage(meshes(n, "Fractured", i)) else set())
            if dmp != want:
                bad.append(f"{n}: Damaged LOD{i} mesh parts are not the Fractured minus the scaffold: "
                           f"{sorted(dmp ^ want)}")
        dcol = models / n / "Damaged" / man["damaged"]["collision"]["file"]
        fcol = models / n / "Fractured" / fr["collision"]["file"]
        if FRR.gltf_hulls(str(dcol)) != FRR.gltf_hulls(str(fcol)):
            bad.append(f"{n}: the Damaged collision is not the Fractured one")
        if SC.PART not in (fr.get("parts") or {}):
            continue
        st = S.installed_stages(hooks, n)
        for shot, r in S.SHOTS.items():
            s = S.part_state(st, r)
            if r < 0.3 and s.get(SC.PART, (True, None)) != (True, mat):
                bad.append(f"{n} {shot}: the scaffold is not shown in its scaffold material below 30 %")
            if r >= 0.3 and s.get(SC.PART, (False, None))[0] is not False:
                bad.append(f"{n} {shot}: the scaffold is visible from 30 %")
        if S.part_state(st, 1.0, damaged=True) != {SC.PART: (False, None)}:
            bad.append(f"{n}: a damaged finished block does not hide exactly its scaffold")
        if S.last_stage_changes(st) != [{"MeshPart": SC.PART, "ShowPart": False, "SetMaterial": None,
                                         "ResetToSkinMaterial": False, "ApplyOnlyWhenDamaged": True}]:
            bad.append(f"{n}: the last stage's damaged-only changes are not the one scaffold hide")
    return bad


def gate_state_view(ctx: Ctx) -> list[str]:
    from ..pipeline import states as S
    p = Path(ctx.mod.paths.golden_states)
    if not p.is_file():
        pytest.skip(f"{ctx.mod.name}: no golden states baseline at {_rel(p, Path(ctx.mod.root))} "
                    "(write it with pipeline.states.write_baseline)")
    g = _read_json(p)
    bad = []
    shared = g.pop(S.SHARED_KEY, None)
    if not shared or not all(isinstance(x, str) and _GUID_RE.match(x) for x in shared) \
            or shared != sorted(set(shared)):
        bad.append(f"{S.SHARED_KEY}: not a sorted list of unique GUIDs")
    rp = Path(ctx.mod.paths.report)
    if rp.is_file():
        built = set(ctx.built())
        if set(g) != built:
            bad.append(f"baseline blocks != built blocks: missing {sorted(built - set(g))}, "
                       f"extra {sorted(set(g) - built)}")
    for n, r in sorted(g.items()):
        if set(r) != set(S.BASELINE_KEYS) | {"last_stage"}:
            bad.append(f"{n}: keys {sorted(r)}")
            continue
        if not set(r["critical"]) <= set(r["zones"]):
            bad.append(f"{n}: a critical zone is not a zone")
        if not (r["nf_parts"] and r["fr_parts"]):
            bad.append(f"{n}: no NF or FR mesh parts")
        if not re.fullmatch(r"[0-9a-f]{40}", str(r["stages_sha1"])):
            bad.append(f"{n}: stages_sha1 is not a SHA-1")
        dg = r["def_guids"]
        if not dg or not all(_GUID_RE.match(x) for x in dg) or dg != sorted(set(dg)):
            bad.append(f"{n}: def_guids is not a sorted list of unique GUIDs")
        last = r["last_stage"]
        if last.get("BuildRatioLowerBound") != 1 or not isinstance(last.get("PartChanges"), list):
            bad.append(f"{n}: last_stage is not the stage at 1 with its PartChanges list")
    hooks = ctx.hooks
    for n, r in sorted(g.items()):
        gone = set(r.get("def_guids") or []) - set(S.def_guids(hooks, n))
        if gone:
            bad.append(f"{n}: definition GUIDs removed since the baseline: {sorted(gone)[:5]}")
    gone = set(shared or []) - set(S.shared_def_guids(hooks, list(g)))
    if gone:
        bad.append(f"shared definition GUIDs removed since the baseline: {sorted(gone)[:5]}")
    return bad


# The visible-coplanar audit ("coplanar") lists faces a mod may accept as known follow-ups (a per-family list in its
# own tests); every other audit row is a fault.
ZFIGHT_MOD_POLICY = frozenset({"coplanar"})


def gate_zfight(ctx: Ctx) -> list[str]:
    from ..pipeline import zfight as Z
    rep, bad = ctx.fixture("build_report")["blocks"], []
    for n in ctx.built():
        zf = rep[n].get("zfight")
        if zf is None:
            bad.append(f"{n}: no zfight audit in build_report.json (rebuild the models)")
            continue
        for key, rows in zip(Z.REPORT_KEYS, Z.conflicts(zf)):
            if rows and key not in ZFIGHT_MOD_POLICY:
                bad.append(f"{n}: {key}: {len(rows)} fault(s), first {rows[0]}")
    return bad


def gate_placement(ctx: Ctx) -> list[str]:
    rep, bad = ctx.fixture("build_report")["blocks"], []
    models = Path(ctx.mod.paths.models)
    for n in ctx.built():
        p = models / n / "placement_exported.json"
        if not p.is_file():
            bad.append(f"{n}: no placement_exported.json (run the placement check on the exported files)")
            continue
        got_all = _read_json(p)
        fbx = models / n / "NonFractured" / f"{n}.fbx"
        if not fbx.is_file():
            bad.append(f"{n}: no exported NonFractured/{n}.fbx to match the placement check against")
        elif got_all.get("fbx_sha256") != hashlib.sha256(fbx.read_bytes()).hexdigest():
            bad.append(f"{n}: the placement check measured a different FBX than the export (rerun it)")
        got, want = got_all.get("nonfractured") or {}, rep[n]["placement"]
        if (got.get("occupied"), got.get("total")) != (want["occupied"], want["total"]):
            bad.append(f"{n}: occupied/total {got.get('occupied')}/{got.get('total')} != "
                       f"{want['occupied']}/{want['total']}")
        if sorted(map(tuple, got.get("free_cells") or [])) != sorted(map(tuple, want.get("free_cells") or [])):
            bad.append(f"{n}: free cells differ")
        for d, f in (want.get("faces") or {}).items():
            g = (got.get("faces") or {}).get(d) or {}
            if g.get("groups") != f["groups"] or abs((g.get("coverage") or 0) - f["coverage"]) > 1e-4:
                bad.append(f"{n}: face {d} groups/coverage differ")
    return bad


def gate_style(ctx: Ctx) -> list[str]:
    from ..style import check
    ctx.fixture("build_report")              # skips when the mod is not built
    res = check.run_mod(ctx.mod)
    fails = check.gate_failures(ctx.mod, result=res)
    if res.not_measured:
        ctx.notes.append(f"{ctx.mod.name}: style checks not measured (the report lacks what they need): "
                         + "; ".join(f"{k} ({len(v)} block{'s' if len(v) != 1 else ''})"
                                     for k, v in sorted(res.not_measured.items())))
    return [f"{f.block}: {f.key} {f.value:g} over {f.limit:g}{f' ({f.where})' if f.where else ''}" for f in fails]


GATES: dict[str, tuple[Callable[[Ctx], list[str]], tuple[str, ...]]] = {
    "json": (gate_json, ()),
    "bundles": (gate_bundles, ()),
    "guids": (gate_guids, ()),
    "clientserver": (gate_clientserver, ("sdk", "decompiled")),
    "schema": (gate_schema, ("sdk", "decompiled")),
    "fresh_clone": (gate_fresh_clone, ("slow",)),
    "stages": (gate_stages, ("build",)),
    "construction_roles": (gate_construction_roles, ("build",)),
    "scaffold": (gate_scaffold, ("build",)),
    "state_view": (gate_state_view, ()),
    "zfight": (gate_zfight, ("build",)),
    "placement": (gate_placement, ("build",)),
    "style": (gate_style, ("build",)),
    "determinism": (gate_determinism, ("slow",)),
}


def cases_for(path: Path) -> list[GateCase]:
    """The gate items of the test file at `path`: its mod's gates, or every mod's when the file is in no mod (a
    repo-level Tests/), plus the cross-mod GUID check when the repo has several mods and one of them lists `guids`."""
    proj = project_for(path)
    if proj is None:
        return []
    here = proj.containing(path)
    if here is None and len(proj.mods) == 1:
        here = next(iter(proj.mods.values()))
    mods = [here] if here is not None else [proj.mods[n] for n in sorted(proj.mods)]
    multi = len(proj.mods) > 1
    out = []
    for mod in mods:
        for g in mod.gates:
            if g not in GATES:
                raise pytest.UsageError(f"Unknown gate {g!r} in the [tool.se2modkit] gates of {mod.name}; known: "
                                        f"{', '.join(GATES)}.")
            out.append(GateCase(g, mod, marks=GATES[g][1], id=f"{mod.name}-{g}" if multi else g))
    every = tuple(proj.mods[n] for n in sorted(proj.mods))
    if multi and any("guids" in m.gates for m in every):
        out.append(GateCase("guids", None, mods=every, id=CROSS))
    return out


def test_gate(se2_gate: GateCase, request):
    """One shared gate of one mod (`[tool.se2modkit] gates`; see `se2modkit.testing.gates`)."""
    ctx = Ctx(request, se2_gate.mod, se2_gate.mods)
    fn = gate_guids_across if se2_gate.mod is None else GATES[se2_gate.name][0]
    problems = fn(ctx)
    for note in ctx.notes:
        warnings.warn(note, GateNote, stacklevel=1)
    if problems:
        who = se2_gate.mod.name if se2_gate.mod is not None else "across mods"
        shown = problems[:LIMIT]
        more = f"\n... {len(problems) - LIMIT} more" if len(problems) > LIMIT else ""
        pytest.fail(f"{who}: {se2_gate.name} gate: {len(problems)} problem(s):\n" + "\n".join(shown) + more,
                    pytrace=False)
