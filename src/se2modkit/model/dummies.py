"""Block model dummies (they live in BlockModelDefinition.Dummies, not in the FBX).

Game space = (bx, bz, -by) + 0.125 (LOD0 grid-align translation). Binding is by dummy TYPE (components take the first
dummy of a type; ConditionalSubParts spawns one subpart per dummy of its type); names are cosmetic. Conveyor dummies
are rotated so their local -Z points out of the port (vanilla Welder400: ConveyorBack = 180 deg about Y,
ConveyorLeft = +90, ConveyorRight = -90).

The per-block dummy list and its fracture-zone lookup belong to the mod, so `dummy_json` now takes the dummy list and an optional zone callback from the calling mod.
"""
import math

from mathutils import Matrix, Vector

# Identifiers of vanilla content that mods reference, as .def files do.
# vanilla dummy types (System/DummyTypes); a mod adds its own type GUIDs through `type_guids`
VANILLA_TYPES = {
    "ParticleEffects": "e9f28118-a0cf-4c6c-b6ad-2e0719e6cf5a",
    "UseConveyor": "29d45e5b-2ddf-4ba5-9031-885260550947",
    "ActivationParticle": "0cdc5a5e-8d33-4f57-b7b3-010102591463",
    "ToolDetection": "fb621cb9-438c-4141-9751-f4a59196658f",
    "ToolDamage": "e7b62998-64ba-4b5c-a0eb-a7cd076077d0",
    "Light": "eb314b59-e80c-4554-a092-8b031d1aa8c4",
}

# Blender -> game basis change (proper rotation): game = (x, z, -y)
B2G = Matrix(((1, 0, 0), (0, 0, 1), (0, -1, 0)))
OFFSET = 0.125


def to_game(m_world):
    """(position, quaternion) in game space from a Blender world matrix."""
    pos = B2G @ m_world.to_translation() + Vector((OFFSET, OFFSET, OFFSET))
    rot = (B2G @ m_world.to_3x3() @ B2G.transposed()).to_quaternion()
    return pos, rot


def conveyor_dummy(name, position, yaw_deg, scale):
    """A UseConveyor entry for all_dummies-style lists: (name, type_key, blender_matrix, scale, section)."""
    rot = Matrix.Rotation(math.radians(yaw_deg), 4, "Z")  # Blender Z = game Y
    return (name, "UseConveyor", Matrix.Translation(position) @ rot, scale, name)


def dummy_json(dummies, type_guids=None, zone_of=None, no_support=()):
    """BlockModelDefinition Dummies entries (Insert list).

    dummies: [(name, type_key, blender_matrix, scale, section_or_None)] (the old all_dummies() item shape).
    type_guids: {type_key: guid} for non-vanilla types.
    zone_of: optional callable blender_world_position -> fracture group Id string, written as SupportingFracture
    (UseConveyor dummies sit on the bounds, so they are nudged 3 % inward first). None: no SupportingFracture.
    no_support: dummy names that never get a SupportingFracture.
    """
    type_guids = type_guids or {}
    out = []
    for name, tkey, m, scale, section in dummies:
        pos, q = to_game(m)
        sup = None
        if zone_of and name not in no_support:
            world = m.to_translation()
            sup = {"Id": zone_of(world * 0.97 if tkey == "UseConveyor" else world)}
        eul = q.to_euler()
        out.append({
            "Name": name,
            "Transform": {"Transform": {"Position": {"X": round(pos.x, 6), "Y": round(pos.y, 6), "Z": round(pos.z, 6)},
                                        "Orientation": {"X": round(q.x, 8), "Y": round(q.y, 8), "Z": round(q.z, 8), "W": round(q.w, 8)}},
                          "EulerHint": {"X": round(math.degrees(eul.x), 4), "Y": round(math.degrees(eul.y), 4),
                                        "Z": round(math.degrees(eul.z), 4)}},
            "Scale": {"X": scale[0], "Y": scale[1], "Z": scale[2]},
            "Type": VANILLA_TYPES.get(tkey) or type_guids[tkey],
            "SupportingFracture": sup,
            "HighlightSection": {"Id": section} if section else None,
        })
    return out
