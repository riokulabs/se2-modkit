"""Material slot name -> vanilla material definition GUID, for a `.model`'s MaterialAssignments (no Blender).

Every GUID is the `Guid` of a vanilla BlockMaterials .def or Copy .partialdef (SDK GameData/Vanilla/Assets/
BlockMaterials/<path>). Slot names are free (a .model maps any name), so the table keeps readable ones; a mod adds its
own slots with `ModHooks.material_slots` and its Damaged-model remaps with `ModHooks.damaged_slots`.

  guid(hooks, slot)          the GUID a slot maps to (`<prefix><block>_LastLOD` and `<slot>_Subpart` resolved)
  damaged_slot(hooks, slot)  the slot a Damaged .model maps a slot to (vanilla's broken / Deformed materials)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline.hooks import ModHooks

__all__ = ["DAMAGED_SLOTS", "LASTLOD_FALLBACK", "MATERIAL_GUIDS", "SUBPART_SUFFIX", "damaged_slot", "damaged_table",
           "guid", "material_table"]

# Identifiers of vanilla content that mods reference, as .def files do.
MATERIAL_GUIDS: dict[str, tuple[str, str]] = {
    # name: (guid, vanilla .def under BlockMaterials/)
    "PaintedMetalColorable": ("be89c5c3-25ce-42e9-93f9-72ba32c3d8a7", "PaintedMetalColorable.def"),
    "PaintedMetalDarker": ("cb933a73-bce4-4224-a7d8-c5083935dac8", "PaintedMetalDarker.def"),
    "PaintedMetalYellow": ("d74fe97a-dbf1-4b0f-8e1b-90f6fa3b1b27", "PaintedMetalYellow.def"),
    "BrushedMetal": ("0c3be477-8bff-4e2c-8a0e-d7d842905ae9", "BrushedMetal.def"),
    "MetalDull": ("0e9e7d42-9533-47ed-b814-cf4be55b6aa8", "MetalDull.def"),
    "Chrome": ("3312f76c-b118-4616-bd95-adfb9b59a996", "Chrome.def"),
    "Rubber": ("ea603341-6e46-42f7-afae-ce60b8848ec5", "Rubber.def"),
    "EmissiveGreen": ("78f2cbaa-112e-4068-99e7-c146d4da58f2", "EmissiveGreen.def"),
    "EmissiveOff": ("d2648963-ef2b-442d-af7b-ee3d79dad677", "EmissiveOff.def"),
    "EmissiveRed": ("a68b9acc-bcaa-4023-9a26-10df67be55ca", "EmissiveRed.def"),
    "AtlasDecal_Stickers02": ("c3e749ff-35ff-469c-b7ae-84a5a5d829b3",
                              "Decals/Atlases/AtlasDecal_Stickers02/AtlasDecal_Stickers02.def"),
    # the vanilla Battery50 slot frame's decal atlas (kit part), mapped as Battery50.model maps it
    "AtlasDecal_Parts02": ("8affab53-a343-4c9d-b513-2f82180c0326",
                           "Decals/Atlases/AtlasDecal_Parts02/AtlasDecal_Parts02.def"),
    "AtlasFont_BarlowCondensed": ("481b2a1f-b394-4d99-b58a-1c370e013315",
                                  "Decals/Fonts/BarlowCondensed/AtlasFont_BarlowCondensed.def"),
    # vanilla's own clean-white glyph atlas material
    "AtlasFont_BarlowCondensed_CleanWhite": ("d9b7cea3-19fa-4aee-843a-a6838dda5990",
                                             "Decals/Fonts/BarlowCondensed/AtlasFont_BarlowCondensed_CleanWhite.def"),
    # slots named as vanilla's mesh parts, mapped as vanilla maps them
    "Terminal": ("60363146-c219-42d4-ba67-eceb934647ce", "Terminal.def"),          # Timer50 / EventController screen
    "Emissive01": ("d2648963-ef2b-442d-af7b-ee3d79dad677", "EmissiveOff.def"),     # EventController100.model
    "Emissive02": ("d2648963-ef2b-442d-af7b-ee3d79dad677", "EmissiveOff.def"),     # EventController100.model
    # the LCD surface slot: vanilla's 2:1 screen material (LCDWide300/500, NarrowLCD50 map LCDScreen_Off to it)
    "LCDScreen_Off": ("bd7f946b-3867-4aaf-8aaf-2a8eed16e832", "LCDScreen_Off_2.0.def"),
    # a static amber lamp. Vanilla has no amber PBR emissive; its only steady amber is this ColorIndicatorMaterial
    # (115, 86, 0), Emissivity 20, AnimationParameters null (the wind turbine's low-efficiency state), used here as a
    # model's own material assignment (vanilla swaps indicator materials in at runtime instead)
    "EmissiveAmber": ("4a74bc1c-0530-4c06-a5c5-5d91f11353f4",
                      "Indicators_Placeholder/WindTurbineIndicators/IndicatorWindTurbineEnabledLowEfficiency.def"),
    # the same static amber as its own mesh part (a script can swap it without touching the EmissiveAmber lamps)
    "EmissiveScriptStatus": ("4a74bc1c-0530-4c06-a5c5-5d91f11353f4",
                             "Indicators_Placeholder/WindTurbineIndicators/IndicatorWindTurbineEnabledLowEfficiency.def"),
    "AtlasDecal_Stickers01": ("2a953884-e538-4bec-a334-1fef9d3712ce",
                              "Decals/Atlases/AtlasDecal_Stickers01/AtlasDecal_Stickers01.def"),
    # vanilla perforated steel (a construction-stage material; DefaultState f97448f5, the alpha-tested two-sided state
    # the hair / *_TwoSided materials use; its source has an _alphamask.png: ~44 % open)
    "MetalPerforationSteel": ("6b6c351e-f5cf-4bf0-bd9d-09b06e7bce7c",
                              "Constructions/MetalPerforationSteel/MetalPerforationSteel.def"),
    "PaintedMetalGrey": ("9db1117a-b2a1-4fa1-a536-19a35139c3fc", "PaintedMetalGrey.def"),
    # alpha-tested grating (vanilla's catwalk floor)
    "GratingMetallic": ("2cc33fe2-00c5-4a64-a648-d1ea7aa8e3ee", "GratingMetallic.def"),
    # vanilla glazing, both PBRTransparentMaterialDefinition (vanilla users: Projector300, InteriorLight50)
    "FrostedGlass": ("c41f2732-51b4-4634-8fc4-b740f52a4ff5", "FrostedGlass.def"),
    "WindowGlassThin": ("646cbd24-5fd8-4b80-9c45-4e3ddee5a79d", "WindowGlassThin.def"),
    # vanilla's static white emissive (a PBR material, EmissivityMultiplier 80), as HangarDoor_Base.model maps its
    # light strips (no swapper: always lit)
    "EmissiveWhite": ("8ffced10-c911-4a5a-921f-075ee31ca2cc", "EmissiveWhite.def"),
    # vanilla's break-face material (every Fracture_NN_Cut of Timer50, Battery50, EventController100) and the broken
    # states vanilla's Deformed models map their lamp and screen slots to (DAMAGED_SLOTS; only a Damaged .model's
    # MaterialReference, never a slot by itself)
    "FracturedMaterial01": ("0f2772fd-72d6-4ee9-9211-38d1781ba8b9", "FracturedMaterial01.def"),
    "EmissiveOffBroken": ("bcefad69-a79d-498b-97c8-5f2c024cfc52", "EmissiveOffBroken.def"),
    "Display01Broken": ("0f138e31-e25c-415e-94a8-137b9c3f5f7f", "Display01Broken.def"),
    "LCDScreen_Off_Damaged": ("73b146d6-2a17-405c-ac00-dd7ddb4454d6", "LCDScreen_Off.def"),   # LCDFlat50_Deformed
    # the broken glazing vanilla's Deformed models map their WindowGlass slot to (Piston500, Reactor250)
    "WindowGlassBroken": ("b9db8238-9373-4690-b743-17e37759179e", "WindowGlassBroken.def"),
    # vanilla 4.0's scratched parallax Deformed materials (PBRParallaxMaterialDefinition), what vanilla's Deformed
    # models map the four metal slots to (e.g. Timer50_Deformed). The Darker one is a Copy .partialdef of the
    # Colorable one (its own textures). Only a Damaged .model's MaterialReference (DAMAGED_SLOTS).
    "PaintedMetalColorableDeformed": ("d8847d2a-cbb3-4982-9036-ee7785cce3c1",
                                      "PaintedMetalColorable_Deformed/PaintedMetalColorableDeformed.def"),
    "PaintedMetalDarkerDeformed": ("ea15e749-58ce-4ca8-95e2-307af0bd273d",
                                   "PaintedMetalColorable_Deformed/PaintedMetalDarkerDeformed.partialdef"),
    "BrushedMetalDeformed": ("d2d76bf2-2c7c-4798-8eec-c515c07b84cc", "BrushedMetalDeformed/BrushedMetalDeformed.def"),
    "ChromeDeformed": ("4418666d-ba5b-481c-b9a3-e7233fc001a8", "Chrome_Deformed/ChromeDeformed.def"),
    # the 4:1 LCD surfaces on vanilla's 4:1 off-screen material, as ControlSeat250 maps its 4:1 CustomDisplay04
    "LCDScreen_Off_4": ("fa0a1f34-75c1-4899-bc3c-761ec9f70877", "LCDScreen_Off_4.0.def"),
    "LCDScreen_Off_4_Back": ("fa0a1f34-75c1-4899-bc3c-761ec9f70877", "LCDScreen_Off_4.0.def"),
}

# Interim LastLOD material (no bake yet): the last-LOD box takes the block's paint, like the Colorable body that
# covers about half of every model's surface.
LASTLOD_FALLBACK = "PaintedMetalColorable"
SUBPART_SUFFIX = "_Subpart"   # a baked stand-in's slot wears its base slot's material

# The material a Damaged .model maps a slot to: the broken lamp / screen / glass states and the scratched Deformed
# metals, as vanilla's Deformed models map them (the player colour stays on the Colorable one: its colour mask).
DAMAGED_SLOTS: dict[str, str] = {
    "EmissiveOff": "EmissiveOffBroken", "EmissiveGreen": "EmissiveOffBroken",
    "EmissiveRed": "EmissiveOffBroken", "EmissiveAmber": "EmissiveOffBroken",
    "Emissive01": "EmissiveOffBroken", "Emissive02": "EmissiveOffBroken",
    "EmissiveScriptStatus": "EmissiveOffBroken",
    "Terminal": "Display01Broken", "LCDScreen_Off": "LCDScreen_Off_Damaged",
    "FrostedGlass": "WindowGlassBroken",
    "LCDScreen_Off_4": "LCDScreen_Off_Damaged", "LCDScreen_Off_4_Back": "LCDScreen_Off_Damaged",
    "PaintedMetalColorable": "PaintedMetalColorableDeformed",
    "PaintedMetalDarker": "PaintedMetalDarkerDeformed",
    "BrushedMetal": "BrushedMetalDeformed", "Chrome": "ChromeDeformed",
}


def material_table(hooks: ModHooks) -> dict[str, tuple[str, str]]:
    """The generic slots, then the mod's own (`hooks.material_slots`, which win on a clash)."""
    return {**MATERIAL_GUIDS, **hooks.material_slots}


def damaged_table(hooks: ModHooks) -> dict[str, str]:
    """The generic Damaged remaps, then the mod's own (`hooks.damaged_slots`)."""
    return {**DAMAGED_SLOTS, **hooks.damaged_slots}


def guid(hooks: ModHooks, slot: str) -> str:
    """GUID for a material slot used in an exported model; KeyError if it has none."""
    if slot.startswith(hooks.prefix) and slot.endswith("_LastLOD"):
        slot = LASTLOD_FALLBACK
    if slot.endswith(SUBPART_SUFFIX):
        slot = slot[:-len(SUBPART_SUFFIX)]
    return material_table(hooks)[slot][0]


def damaged_slot(hooks: ModHooks, slot: str) -> str:
    """The slot a Damaged .model maps `slot` to: its remap, or that of its base slot when it is a stand-in slot
    (`<slot>_Subpart`), else the slot itself (the .model maps by slot name, so a stand-in slot needs no re-suffixed
    Deformed name; `guid` resolves `_Subpart`)."""
    table = damaged_table(hooks)
    if slot in table:
        return table[slot]
    if slot.endswith(SUBPART_SUFFIX):
        return table.get(slot[:-len(SUBPART_SUFFIX)], slot)
    return slot
