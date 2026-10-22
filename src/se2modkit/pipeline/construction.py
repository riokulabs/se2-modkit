"""BlockModelDefinition.BuildProgressModels without the Editor (no Blender needed).

The SDK Editor builds the build stages from three FBX custom properties per object; here they are read as data from
the Fractured LOD0's mesh parts:

  ConstructionMeshType       Default | Scaffold | Split
  ConstructionMeshVisibility AlwaysVisible | HiddenUntilFirstSequenceStart | HiddenUntilSecondSequenceStart | AlwaysHidden
  ConstructionMeshPreset     Name of a ConstructionMaterialSequenceDefinition (Default, Fracture, Support, ...)

Mesh parts are "{Group}-{Material}" (or the material alone without a Group). The second sequence starts at 0.3, timing
precision 4 decimals. Stage 0 = FractureModel, the last stage (ratio 1) = Model; a plain Default part gives the stage
template (0, 0.1, 0.2, 0.3, 0.5333, 0.7667, 1).

The stage-1.0 ApplyOnlyWhenDamaged hide names exactly the parts passed as `damaged_hide` (`scaffold_parts`: the
Scaffold-type mesh parts, so a finished block that is shot does not show its construction cage) and is empty without
them, a form vanilla 4.0 ships too. Pure Python; no mod tables.
"""

import uuid

__all__ = ["PRESETS", "SECOND", "generate_stages", "preset_of", "scaffold_parts"]

# Identifiers of vanilla content that mods reference, as .def files do: the ConstructionMaterialSequenceDefinition
# presets (vanilla System/Configurations/ConstructionPresets/*.def)
PRESETS = {
    "default": {"T": ["2a8225c1-df6c-41a7-ad6f-b58a77bdd88d", "d5d7f641-9756-4776-abb5-ffd52f2f80a8",
                      "9f97552e-a56c-4f5a-8c6c-3be96ab453f5"],
                "S": ["857c023a-0cbb-43e0-9af4-5ce18d9ab587", "f49fee87-8abf-4faf-89d1-d49b9ef46a0b",
                      "851d4d6e-9ec8-467f-87a5-06ef06e97336"]},
    "fracture": {"T": ["0f2772fd-72d6-4ee9-9211-38d1781ba8b9"], "S": []},
    "support": {"T": ["4050183d-1340-4643-b8b9-124981f2121d"], "S": ["0c3be477-8bff-4e2c-8a0e-d7d842905ae9"]},
    "scaffolding": {"T": ["4050183d-1340-4643-b8b9-124981f2121d"], "S": []},
    "conveyor_port": {"T": ["7d57e640-f4bb-4850-b0d2-033149cffe27"], "S": ["e1c15fae-c412-4da5-bc23-833ec10a95bb"]},
    # 4.0 (SDK System/Configurations/ConstructionPresets/*): glass faces keep their glass through both sequences (the
    # glass role), parked subpart stand-ins likewise with the subpart construct material. The SDK's GratingMetallic
    # has no user yet and is left out (no unused presets).
    "windowglass": {"T": ["83c075f5-f177-460d-94a7-8aca19fd4d6f"], "S": ["83c075f5-f177-460d-94a7-8aca19fd4d6f"]},
    "subpartconstruct": {"T": ["851d4d6e-9ec8-467f-87a5-06ef06e97336"],
                         "S": ["851d4d6e-9ec8-467f-87a5-06ef06e97336"]},
}
SECOND = 0.3


def preset_of(name):
    """The preset's {"T", "S"} materials; no preset (None / "") is the plain form with none. An unknown name raises (a
    typo such as "WindowGlas" would otherwise build silently with no construction materials)."""
    if not name:
        return {"T": [], "S": []}
    try:
        return PRESETS[name.lower()]
    except KeyError:
        raise KeyError(f"unknown ConstructionMeshPreset {name!r} (known: {sorted(PRESETS)})") from None


def scaffold_parts(parts):
    """Sorted names of the Scaffold-type mesh parts in {mesh part: (type, visibility, preset)}: the parts the
    stage-1.0 damaged-only hide names (a finished block that is shot must not show its construction cage)."""
    return sorted(p for p, (t, _v, _pr) in parts.items() if t == "Scaffold")


def generate_stages(parts, model_ref, fracture_ref, seed, damaged_hide=()):
    """parts: {mesh part name: (type, visibility, preset)} of the Fractured LOD0. Returns the stage list. The last
    stage (ratio 1, the finished Model) holds one ApplyOnlyWhenDamaged hide per `damaged_hide` part, [] without."""
    events = {}  # (time, part) -> dict

    def ev(t, part, **kw):
        # float always: the stage Key and ratio come from whichever event wrote a time first, int 0 or float 0.0, so
        # adding a part that sorts first could otherwise rename stage 0
        e = events.setdefault((round(float(t), 4), part), {})
        e.update(kw)

    for part, (T, V, preset) in sorted(parts.items()):
        pr = preset_of(preset)
        TS, SS = pr["T"], pr["S"]
        vis1 = V == "AlwaysVisible" and T in ("Default", "Scaffold")
        con1 = V in ("AlwaysVisible", "HiddenUntilFirstSequenceStart") and T in ("Default", "Scaffold")
        vis2 = V in ("AlwaysVisible", "HiddenUntilFirstSequenceStart") and T in ("Default", "Split")
        con2 = V != "AlwaysHidden" and T in ("Default", "Split")
        if not vis1:
            ev(0, part, ShowPart=False)
        if con1:
            for i, mat in enumerate(TS):
                t = SECOND * i / len(TS)
                if i == 0 and not vis1:
                    ev(t, part, ShowPart=True, SetMaterial=mat)
                else:
                    ev(t, part, SetMaterial=mat)
        if con1 and not vis2:
            ev(SECOND, part, ShowPart=False)
        if con1 and vis2 and con2 and TS and not SS:
            ev(SECOND, part, ResetToSkinMaterial=True)
        if not con1 and vis2:
            ev(SECOND, part, ShowPart=True)
        if con2:
            for i, mat in enumerate(SS):
                t = SECOND + (1 - SECOND) * i / len(SS)
                if i == 0 and not vis2:
                    ev(t, part, ShowPart=True, SetMaterial=mat)
                else:
                    ev(t, part, SetMaterial=mat)
        if con2 and not SS and not vis2:
            ev(SECOND, part, ShowPart=True)
    times = sorted({t for t, _ in events})
    rng = uuid.uuid5(uuid.NAMESPACE_URL, seed)

    def gid(*k):
        return str(uuid.uuid5(rng, "/".join(map(str, k))))

    stages = []
    for t in times:
        changes = []
        for (tt, part), e in sorted(events.items()):
            if tt != t:
                continue
            changes.append({"Key": gid("chg", t, part), "Value": {
                "MeshPart": part, "ShowPart": e.get("ShowPart"), "SetMaterial": e.get("SetMaterial"),
                "ResetToSkinMaterial": bool(e.get("ResetToSkinMaterial", False)), "ApplyOnlyWhenDamaged": False}})
        stages.append({"Key": gid("stage", t), "Value": {"BuildRatioLowerBound": t,
                                                         "Model": fracture_ref if t == 0 else None,
                                                         "PartChanges": {"_data": changes}}})
    # last stage: the finished model, plus vanilla's "hide the scaffold when damaged" entries (none without one)
    stages.append({"Key": gid("stage", 1), "Value": {"BuildRatioLowerBound": 1, "Model": model_ref, "PartChanges": {
        "_data": [{"Key": gid("chg", 1, p), "Value": {"MeshPart": p, "ShowPart": False, "SetMaterial": None,
                                                      "ResetToSkinMaterial": False, "ApplyOnlyWhenDamaged": True}}
                  for p in sorted(damaged_hide)]}}})
    return stages
