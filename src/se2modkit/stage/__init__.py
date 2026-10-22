"""Staging helpers for a mod's integration package (no Blender).

Everything is staged under `hooks.paths.staged` exactly as it must land in the mod's `Assets/` (`hooks.paths.assets`);
an install step copies it over. Asset GUIDs come from the mod's asset GUID store (`defs.guids.Guids`), so a rebuild
never changes a reference. Every function that reads the mod's tables, paths or stamps takes the mod's `ModHooks` as
its first argument; the files staged in this run are tracked on that registry (`reset(hooks)` starts a run).

  reset                     start a staging run (clears the staged tree; reads the SDK's stamps once if not pinned)
  read_mod / read_current   the installed definition / this run's staged copy if it was patched already
  stage_path / stage_meta / asset_id / stage_def / stage_file
                            write a staged file and its .meta (AssetID = the definition's own GUID or a stable one)
  type_bundles / declare_bundles / bundle_versions
                            every "Bundle:Type" name resolves through the file's own $Bundles
  model_asset / subpart_model_asset / stage_models / stage_subpart_model
                            the .model assets (LOD FBX + collision; a subpart's without collision unless given one)
  dummy / put_dummy / quat_axis_angle / quat_from_matrix / euler_hint / rot_apply
                            BlockModelDefinition dummies
  breakable_definition / ensure_breakable
                            the block's BreakableBlockDefinition with its CriticalFractures
  add_components / drop_components / add_client_components / tag_slots / patch_body / block_files / one_file /
  write_folder_metas / partial / vanilla_object_builders
"""

from __future__ import annotations

import math
import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..defs.assetio import PARTIAL, body, envelope, meta_id, own_guid, read_asset, write_asset, write_meta
from ..defs.material_guids import damaged_slot as _damaged_slot, guid as mat_guid
from ..validate.bundles import TYPE_FIELDS, type_bundles  # noqa: F401  (part of the staging API)

if TYPE_CHECKING:
    from ..defs.block_chain import Index
    from ..defs.guids import Guids
    from ..env.core import SE2Env
    from ..pipeline.hooks import ModHooks

MODEL_TYPE = "VRage:Keen.VRage.ContentPipeline.Models.ModelAssetData"


def reset(hooks: ModHooks, env: SE2Env | None = None) -> None:
    """A fresh staging run: the hooks checked (`hooks.check()`), the staged tree cleared (only `<staged>/<Mod>/` left)
    and the staged-file record emptied. Resolves the bundle stamps now (`hooks.stamps(env)`), so a missing SDK fails
    before anything is written; with `accumulate_bundles` the run starts from fresh stamps."""
    hooks.check()
    hooks._run_stamps = None
    hooks.stamps(env)
    st = hooks.paths.staged
    if st.is_dir():
        shutil.rmtree(st)
    (st / hooks.name).mkdir(parents=True)
    hooks._staged.clear()


# ------------------------------------------------------------------------------------------------ files and metas
def _installed(hooks: ModHooks, rel: str) -> Path:
    return hooks.paths.assets / rel


def read_mod(hooks: ModHooks, rel: str) -> dict:
    """The installed (mod) copy. A definition already staged in this run must be read with read_current, or the
    later stage_def would silently drop this run's earlier patch."""
    if rel in hooks._staged:
        raise RuntimeError(f"{rel}: already staged in this run; read it with read_current, not read_mod")
    return read_asset(_installed(hooks, rel))


def stage_path(hooks: ModHooks, rel: str) -> Path:
    return hooks.paths.staged / rel


def read_current(hooks: ModHooks, rel: str) -> dict:
    """A definition as it will be installed: the staged copy when this run already patched it, else the mod's."""
    p = stage_path(hooks, rel)
    return read_asset(p) if p.exists() else read_mod(hooks, rel)


def stage_meta(hooks: ModHooks, rel: str, asset_id_: str) -> None:
    """Stage <rel>.meta unless the mod already has one with this AssetID (the installed .meta keeps the builder's
    hashes and processor info, so a reinstall doesn't rewrite it); a different installed AssetID is an error."""
    have = meta_id(_installed(hooks, rel))
    if have == asset_id_:
        return
    if have:
        raise RuntimeError(f"{rel}: installed .meta AssetID {have} != {asset_id_}")
    write_meta(stage_path(hooks, rel), asset_id_, hooks.stamps())


def asset_id(hooks: ModHooks, rel: str) -> str | None:
    """AssetID of a staged-or-installed asset (staged .meta first)."""
    return meta_id(stage_path(hooks, rel)) or meta_id(_installed(hooks, rel))


# "Bundle:Type" names resolve through the file's own $Bundles (the game's migration helper throws "There is no bundle
# named X" otherwise). A patch that brings a bundle's first type into a file declares it at the version our values
# are written against (the hooks' game stamps), so no migration rewrites them.
# (TYPE_FIELDS and type_bundles live in validate.bundles, shared with the bundles gate.)


def bundle_versions(hooks: ModHooks) -> dict[str, str]:
    """The version a newly declared bundle gets: the Game2 and VRage stamps of `hooks.stamps()`."""
    game = hooks.stamps()[1]
    return {"Game2": game["Game2"], "VRage": game["VRage"]}


def declare_bundles(hooks: ModHooks, data: dict) -> None:
    """Add every bundle data's type names use to its $Bundles (the ones already declared keep their version), as a new
    dict. With `hooks.accumulate_bundles` the file's own $Bundles dict is extended in place first, as the legacy
    pipelines did (when that dict is the run's stamps, every later envelope of the run carries the addition)."""
    versions = bundle_versions(hooks)
    bundles = data.setdefault("$Bundles", {}) if hooks.accumulate_bundles else dict(data.get("$Bundles") or {})
    for b in sorted(type_bundles(data) - set(bundles)):
        if b not in versions:
            raise RuntimeError(f"type bundle {b!r} has no known version")
        bundles[b] = versions[b]
    data["$Bundles"] = dict(sorted(bundles.items()))


def stage_def(hooks: ModHooks, rel: str, data: dict) -> None:
    """A patched (or new) definition, staged with a .meta whose AssetID is its own GUID (and must equal the mod's), its
    $Bundles declaring every bundle its type names use."""
    declare_bundles(hooks, data)
    g = own_guid(data)
    have = meta_id(_installed(hooks, rel))
    if g and have and have != g:
        raise RuntimeError(f"{rel}: mod .meta AssetID {have} != definition GUID {g}")
    if not (g or have):
        raise RuntimeError(f"{rel}: no AssetID for its .meta (the definition has no own GUID and the mod has no "
                           ".meta for it)")
    write_asset(stage_path(hooks, rel), data)
    hooks._staged.add(rel)
    stage_meta(hooks, rel, g or have)


def stage_file(hooks: ModHooks, src: str | os.PathLike, rel: str, G: Guids) -> str:
    """Copy a built file into the package with a stable AssetID; returns that AssetID."""
    dst = stage_path(hooks, rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    stage_meta(hooks, rel, G[rel])
    return G[rel]


def block_files(hooks: ModHooks, spec, pattern: str) -> list[str]:
    """Cloned definition files of a block whose name contains pattern (mod-relative paths, no .meta)."""
    assets = hooks.paths.assets
    root = assets / hooks.name / spec.name
    return sorted((Path(dp) / f).relative_to(assets).as_posix() for dp, _, fs in os.walk(root) for f in fs
                  if pattern in f and f.endswith((".def", ".partialdef")))


def one_file(hooks: ModHooks, spec, pattern: str) -> str:
    files = block_files(hooks, spec, pattern)
    if len(files) != 1:
        raise RuntimeError(f"{spec.name}: expected one {pattern}, found {files}")
    return files[0]


def patch_body(hooks: ModHooks, spec, pattern: str, fn: Callable[[dict], Any]) -> str:
    """Apply fn to the body of the block's one cloned `pattern` definition (as currently staged) and stage it."""
    f = one_file(hooks, spec, pattern)
    d = read_current(hooks, f)
    fn(body(d))
    stage_def(hooks, f, d)
    return f


def write_folder_metas(hooks: ModHooks, G: Guids) -> None:
    """A .meta for every staged folder: the installed folder's AssetID, else a stable one ("dir/<rel>")."""
    st = hooks.paths.staged
    for dp, ds, _ in os.walk(st):
        for d in ds:
            rel = (Path(dp) / d).relative_to(st).as_posix()
            stage_meta(hooks, rel, meta_id(_installed(hooks, rel)) or G["dir/" + rel])


def partial(hooks: ModHooks, base: str, manip: dict, dtype: str, bundles: dict | None = None) -> dict:
    """A Copy partialdef of `base` (manip carries the new Guid); `bundles` defaults to the hooks' VRage stamps."""
    return envelope(PARTIAL, {"BaseDefinition": base, "PartialDefinitionKind": "Copy", "Manipulator": manip,
                              "DefinitionType": dtype, "PriorityOverride": False},
                    hooks.stamps()[0] if bundles is None else bundles)


# ------------------------------------------------------------------------------------------------ models
def damaged_slot(hooks: ModHooks, slot: str) -> str:
    """The material slot a Damaged .model maps a slot to (`defs.material_guids.damaged_slot`)."""
    return _damaged_slot(hooks, slot)


def model_asset(hooks: ModHooks, geom: list[tuple[str, list[str]]], collision_guid: str, shift,
                remap: Callable[[str], str] | None = None) -> dict:
    """geom: [(fbx asset guid, used materials)] in LOD order. LOD0 carries the per-axis grid-align translation
    (vanilla: SnapToGrid false, the shift written explicitly). remap: slot -> material slot for the Damaged model."""
    remap = remap or (lambda m: m)
    names_ = sorted({m for _, mats in geom for m in mats})
    ga = []
    for i, (g, mats) in enumerate(geom):
        t = shift if i == 0 else [0, 0, 0]
        ga.append({"AssetReference": "{G}" + g, "UsedMaterials": mats,
                   "Transformations": {"Translation": {"X": t[0], "Y": t[1], "Z": t[2]},
                                       "Rotation": {"X": 0, "Y": 0, "Z": 0}, "Scale": 1}})
    return envelope(MODEL_TYPE, {
        "MaterialAssignments": [{"MaterialName": m, "MaterialReference": mat_guid(hooks, remap(m))} for m in names_],
        "MaterialStateOverrides": [], "GeometryAssets": ga, "SnapToGrid": False, "SuggestedModelSize": None,
        "CollisionShapesAsset": {"AssetReference": "{G}" + collision_guid, "GroupsByFracture": [],
                                 "GeneratePreciseCollider": True}}, hooks.stamps()[0])


def subpart_model_asset(hooks: ModHooks, geom: list[tuple[str, list[str]]], collision_guid: str | None = None) -> dict:
    """A subpart .model: LODs with no translation (the FBX origin is the pivot) and NO collision (vanilla subparts are
    visual only; ConditionalSubParts would otherwise add the collider to the block's static collision), unless
    `collision_guid` gives one (a door leaf's own collider, as vanilla sliding-door leaves have)."""
    names_ = sorted({m for _, mats in geom for m in mats})
    ga = [{"AssetReference": "{G}" + g, "UsedMaterials": mats,
           "Transformations": {"Translation": {"X": 0, "Y": 0, "Z": 0}, "Rotation": {"X": 0, "Y": 0, "Z": 0},
                               "Scale": 1}} for g, mats in geom]
    col = ({"AssetReference": None, "GroupsByFracture": []} if not collision_guid else
           {"AssetReference": "{G}" + collision_guid, "GroupsByFracture": [], "GeneratePreciseCollider": True})
    return envelope(MODEL_TYPE, {
        "MaterialAssignments": [{"MaterialName": m, "MaterialReference": mat_guid(hooks, m)} for m in names_],
        "MaterialStateOverrides": [], "GeometryAssets": ga, "SnapToGrid": False, "SuggestedModelSize": None,
        "CollisionShapesAsset": col}, hooks.stamps()[0])


def manifest(hooks: ModHooks, spec) -> dict | None:
    """The block's export manifest (`<models>/<block>/export.json`), or None before its first export."""
    p = hooks.paths.models / spec.name / "export.json"
    return read_asset(p) if p.exists() else None


def built_names(hooks: ModHooks, specs: Iterable, report: dict | None = None) -> set[str]:
    """Blocks of `specs` with a placement entry in the build report AND an export."""
    if report is None:
        rp = hooks.paths.report
        report = read_asset(rp) if rp.exists() else {"blocks": {}}
    names = {b.name for b in specs}
    return {n for n, e in report["blocks"].items() if e.get("placement", {}).get("total") and n in names
            and (hooks.paths.models / n / "export.json").exists()}


VARIANTS = (("NonFractured", "nonfractured", ""), ("Fractured", "fractured", "_Fractured"),
            ("Damaged", "damaged", "_Damaged"))


def stage_models(hooks: ModHooks, spec, man: dict, G: Guids) -> dict[str, str]:
    """Copy the exported files, write the .model assets. Returns {variant: .model guid}."""
    out = {}
    for variant, key, suffix in VARIANTS:
        if key not in man:
            continue
        src = hooks.paths.models / spec.name / variant
        rel = f"{hooks.name}/{spec.name}/Models/{variant}"
        geom = [(stage_file(hooks, src / lod["file"], f"{rel}/{lod['file']}", G), lod["materials"])
                for lod in man[key]["lods"]]
        c = man[key]["collision"]
        col = stage_file(hooks, src / c["file"], f"{rel}/{c['file']}", G)
        stage_file(hooks, src / c["bin"], f"{rel}/{c['bin']}", G)
        mrel = f"{rel}/{spec.name}{suffix}.model"
        remap = (lambda s: _damaged_slot(hooks, s)) if key == "damaged" else None
        write_asset(stage_path(hooks, mrel), model_asset(hooks, geom, col, man["shift"], remap))
        stage_meta(hooks, mrel, G[mrel])
        out[variant] = G[mrel]
    return out


def stage_subpart_model(hooks: ModHooks, spec, man: dict, kind: str, G: Guids) -> tuple[str, int]:
    """A subpart kind's exported LODs (<models>/<name>/<dir>/) and its .model. Returns (guid, lods)."""
    info = man["subparts"][kind]
    src = hooks.paths.models / spec.name / info["dir"]
    rel = f"{hooks.name}/{spec.name}/Models/Subparts/{kind}"
    geom = [(stage_file(hooks, src / lod["file"], f"{rel}/{lod['file']}", G), lod["materials"])
            for lod in info["lods"]]
    mrel = f"{rel}/{spec.name}_{kind}.model"
    write_asset(stage_path(hooks, mrel), subpart_model_asset(hooks, geom))
    stage_meta(hooks, mrel, G[mrel])
    return G[mrel], len(geom)


# ------------------------------------------------------------------------------------------------ dummies
IDENTITY = (0.0, 0.0, 0.0, 1.0)


def dummy(name, typ, pos, q=IDENTITY, euler=(0, 0, 0), scale=(1, 1, 1), support=None, highlight=None) -> dict:
    """A BlockModelDefinition dummy value (vanilla member order)."""
    return {"Name": name,
            "Transform": {"Transform": {"Position": dict(zip("XYZ", (round(float(v), 6) for v in pos))),
                                        "Orientation": dict(zip("XYZW", (round(float(v), 8) for v in q)))},
                          "EulerHint": dict(zip("XYZ", (round(float(v), 4) for v in euler)))},
            "Scale": dict(zip("XYZ", (round(float(v), 6) for v in scale))), "Type": typ,
            "SupportingFracture": {"Id": support} if support else None,
            "HighlightSection": {"Id": highlight} if highlight else None}


def quat_axis_angle(axis, deg):
    """(x, y, z, w) of a turn of `deg` about a unit axis (game frame)."""
    h = math.radians(deg) / 2
    s = math.sin(h)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def quat_from_matrix(m):
    """(x, y, z, w) of a proper 3x3 rotation matrix (rows), w >= 0."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)
    if q[3] < 0:
        q = tuple(-v for v in q)
    n = math.sqrt(sum(v * v for v in q))
    return tuple(v / n for v in q)


def euler_hint(m):
    """Vanilla's EulerHint (degrees, X Y Z; informational) for a rotation matrix (rows), Rz Ry Rx order."""
    sy = -m[2][0]
    y = math.degrees(math.asin(max(-1.0, min(1.0, sy))))
    if abs(sy) < 0.99999:
        x = math.degrees(math.atan2(m[2][1], m[2][2]))
        z = math.degrees(math.atan2(m[1][0], m[0][0]))
    else:
        x, z = math.degrees(math.atan2(-m[1][2], m[1][1])), 0.0
    return tuple(0.0 if abs(v) < 1e-9 else v for v in (x, y, z))


def rot_apply(m, v):
    return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))


def put_dummy(spec, dummies: dict, G: Guids, v: dict) -> None:
    """Insert dummy v (or replace the one of its name, keeping its delta key and list position). Idempotent: a new
    entry's delta key is stable (asset GUID store "dummy/<block>/<name>")."""
    keys, changed = dummies.setdefault("Keys", []), dummies.setdefault("Changed", [])
    dummies.setdefault("Removed", [])
    for c in changed:
        if c["Value"].get("Name") == v["Name"]:
            c["Value"] = v
            return
    keys.append(G[f"dummy/{spec.name}/{v['Name']}"])
    changed.append({"Kind": "Insert", "Index": len(keys) - 1, "Value": v})


# ------------------------------------------------------------------------------------------------ breakable
BREAKABLE_KEY = "bc5652a0-1296-4040-adc3-a2b9c35d5afc"   # BreakableBlockComponent in every Base*Breakable template
BREAKABLE_SLOTS = ("BreakableBlockComponent", "IIntegrityModifier", "IMountPointModifier", "PhysicsColliderProvider",
                   "IBlockPhysicsColliderProvider")
BLOCK_MODEL_KEY = "d358358e-30b3-484a-b8b8-2e5568c7298b"     # BlockModelComponent: the breakable key follows it
BBD_BASE = "a74c6fd1-fa39-4f24-89cb-17a46e65cfd7"            # Templates/Blocks/BaseDefinitions/BreakableBlockDefinition
T_BBD = "Game2:Keen.Game2.Simulation.WorldObjects.CubeBlocks.BreakableBlockDefinition"


def keeps_breakable(man: dict) -> bool:
    """A block is breakable exactly when its FractureModel really fractures (>= 2 fracture zones)."""
    return len(man["fractured"]["zones"]) >= 2


def breakable_definition(hooks: ModHooks, spec, man: dict, G: Guids) -> tuple[str, str]:
    """The block's BreakableBlockDefinition with its CriticalFractures (export.json "critical"): the cloned one
    patched, or a new Copy of the base template. Returns (rel, guid)."""
    crit = {"$DeltaEncoded": True, "Removed": [], "Added": [{"Id": f} for f in man["fractured"].get("critical", [])]}
    files = block_files(hooks, spec, "_BreakableBlockDefinition")
    if files:
        rel = patch_body(hooks, spec, "_BreakableBlockDefinition", lambda v: v.update(CriticalFractures=crit))
        return rel, own_guid(read_current(hooks, rel))
    rel = f"{hooks.name}/{spec.name}/{spec.name}_BreakableBlockDefinition.partialdef"
    guid = G[f"def/{spec.name}/BreakableBlockDefinition"]
    stage_def(hooks, rel, partial(hooks, BBD_BASE, {"Guid": guid, "CriticalFractures": crit}, T_BBD,
                                  hooks.stamps()[1]))
    return rel, guid


def ensure_breakable(hooks: ModHooks, spec, man: dict, G: Guids) -> tuple[list[str], str]:
    """The breakable chain of a fractured block: its CriticalFractures, and both compositions keep the
    BreakableBlockComponent bound to the block's BreakableBlockDefinition (the clones already have it: asserted, not
    re-inserted). Returns the files changed and the definition's GUID."""
    rel, bbd = breakable_definition(hooks, spec, man, G)
    for side in ("Server", "Client"):
        c = body(read_current(hooks, one_file(hooks, spec, f"_{side}Composition")))
        if BREAKABLE_KEY not in c["Components"]["Keys"] or BREAKABLE_KEY in c["Components"].get("Removed", []):
            raise RuntimeError(f"{spec.name}: the cloned {side} composition lost its BreakableBlockComponent")
        removed = set((c.get("TagSlots") or {}).get("RemovedKeys") or [])
        if removed & set(BREAKABLE_SLOTS):
            raise RuntimeError(f"{spec.name}: {side} composition removes breakable tag slots {removed}")
    return [rel], bbd


# ------------------------------------------------------------------------------------------------ compositions
def add_components(hooks: ModHooks, spec, side: str, new) -> tuple[str, str]:
    """Add components (key, definition, type, tag slot(s), object builder) to the cloned side ("Server" / "Client")
    composition (delta-encoded additions only: never re-Assign), each with its tag slot(s) (a str or a tuple), and
    their object builders to that side's prefab (appended in the same order). Idempotent: re-staging over an
    installed copy refreshes the definition and the object builder."""
    cc_file = one_file(hooks, spec, f"_{side}Composition")
    cc = read_current(hooks, cc_file)
    cm = body(cc)
    keys = cm["Components"]["Keys"]
    for k, d, t, slots, _ in new:
        value = {"Definition": d, "Type": t}
        if k not in keys:
            keys.append(k)
        i = keys.index(k)
        cm["Components"]["Changed"] = sorted([c for c in cm["Components"]["Changed"] if c["Index"] != i] + [
            {"Kind": "Insert", "Index": i, "Value": value}], key=lambda c: c["Index"])
        mk = cm.setdefault("TagSlots", {"$DeltaEncoded": True, "RemovedKeys": [], "ModifiedKeys": []})["ModifiedKeys"]
        for slot in ((slots,) if isinstance(slots, str) else slots):
            mk[:] = [m for m in mk if m["$Key"] != slot] + [{"$Key": slot, "$Value": k}]
    stage_def(hooks, cc_file, cc)
    cl_file = one_file(hooks, spec, f"_{side}.partialdef")
    cl = read_current(hooks, cl_file)
    ob = body(cl)["_entity"]["ObjectBuilders"]
    for k, *_, value in new:
        if k not in ob["Keys"]:
            ob["Keys"].append(k)
        i = ob["Keys"].index(k)
        ob["Changed"] = sorted([c for c in ob["Changed"] if c["Index"] != i] +
                               [{"Kind": "Insert", "Index": i, "Value": value}], key=lambda c: c["Index"])
    stage_def(hooks, cl_file, cl)
    return cc_file, cl_file


def drop_components(hooks: ModHooks, spec, side: str, keys) -> None:
    """Remove components this package inserted (keys) from the side composition, their tag slots and their prefab
    object builders (the inverse of add_components, so a fallback package staged over an install that had them loses
    them). Only Inserted keys are dropped: a clone's own key raises."""
    def drop(delta):
        for k in keys:
            if k not in delta["Keys"]:
                continue
            i = delta["Keys"].index(k)
            ch = [c for c in delta["Changed"] if c["Index"] == i]
            if not ch or ch[0]["Kind"] != "Insert":
                raise RuntimeError(f"{spec.name}: {k} is not a component this package inserted")
            del delta["Keys"][i]
            delta["Changed"] = [c for c in delta["Changed"] if c["Index"] != i]
            for c in delta["Changed"]:
                if c["Index"] > i:
                    c["Index"] -= 1
    cc_file = one_file(hooks, spec, f"_{side}Composition")
    cc = read_current(hooks, cc_file)
    cm = body(cc)
    drop(cm["Components"])
    ts = cm.get("TagSlots")
    if ts:
        ts["ModifiedKeys"] = [m for m in ts.get("ModifiedKeys", []) if m["$Value"] not in keys]
    stage_def(hooks, cc_file, cc)
    cl_file = one_file(hooks, spec, f"_{side}.partialdef")
    cl = read_current(hooks, cl_file)
    drop(body(cl)["_entity"]["ObjectBuilders"])
    stage_def(hooks, cl_file, cl)


def add_client_components(hooks: ModHooks, spec, new) -> tuple[str, str]:
    """Add components (key, definition, type, tag slot) to the cloned client composition and a null object builder
    for each to the client prefab. Idempotent."""
    return add_components(hooks, spec, "Client", [(k, d, t, slot, None) for k, d, t, slot in new])


def tag_slots(hooks: ModHooks, spec, side: str) -> dict[str, str]:
    """{tag: component key} of the block's side composition as staged (its ModifiedKeys only: the clone's delta)."""
    c = body(read_current(hooks, one_file(hooks, spec, f"_{side}Composition")))
    return {m["$Key"]: m["$Value"] for m in (c.get("TagSlots") or {}).get("ModifiedKeys", [])}


# ------------------------------------------------------------------------------------------------ vanilla (read-only)
def vanilla_object_builders(idx: Index, prefab_guid: str) -> dict:
    """{component key: object builder} of a vanilla prefab, merged through its Copy chain (`idx`: a
    `defs.block_chain.Index` over the Mod SDK's vanilla Assets, built once by the caller)."""
    p = idx.resolve(prefab_guid) or {}
    return {ob["Key"]: ob["Value"] for ob in (p.get("_entity") or {}).get("ObjectBuilders") or []}
