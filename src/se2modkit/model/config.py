"""Generic constants for SE2 block modelling: SDK paths, vanilla library material slot names, preview
stand-ins (the slot GUIDs are in defs.material_guids). Per-mod items (block name, bounds, output paths, LOD detail
table, LastLOD name) stay in the mod's own build script."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))  # se2modkit/model


def __getattr__(name):
    """`SDK`, `VANILLA_ASSETS` and `VANILLA_REF` are resolved on access (never at import) and are `None` when not
    found: the SDK through `discover_env()`, the vanilla reference through the `vanilla_ref` config key."""
    if name in ("SDK", "VANILLA_ASSETS"):
        from ..env.core import discover_env
        env = discover_env()
        path = env.sdk_dir if name == "SDK" else env.sdk_assets
        return str(path) if path is not None else None
    if name == "VANILLA_REF":
        # Read-only hard links of the SDK's vanilla Blocks/ and Materials/: the source of reused Keen kit parts
        # (kit.py). Never written to. No default path: set `vanilla_ref` in the user config.
        from ..env.config import load_user_config
        ref = load_user_config().get("vanilla_ref")
        return os.path.expanduser(str(ref)) if ref else None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ------------------------------------------------------------------ materials (library names only)
C, DK, YL, CH, BM, EM, EC, CV = ("PaintedMetalColorable", "PaintedMetalDarker", "PaintedMetalYellow", "Chrome",
                                 "BrushedMetal", "EmissiveOff", "EmissiveColorable", "ConveyorsAtlas")
RB, GL, FR1 = "Rubber", "ClearGlass", "FracturedMaterial01"
MD = "MetalDull"  # joints, pins, hinge knuckles (vanilla LandingGear500)
# Status lights get their own slot so the material swapper can turn them red -> green (vanilla expresses only
# working / not working). Custom slot names are fine: the .model maps any slot name to a material GUID.
ES = "EmissiveStatus"
SUBPART_SUFFIX = "_Subpart"  # static copies of subparts in Fractured/Deformed use "<material>_Subpart" slots (vanilla Reactor)
T1, T2, T3 = "AtlasDecal_Trims01", "AtlasDecal_Trims02", "AtlasDecal_Trims03"
P1, S1, S2 = "AtlasDecal_Parts01", "AtlasDecal_Stickers01", "AtlasDecal_Stickers02"
FONT = "AtlasFont_BarlowCondensed"   # vanilla stencil font atlas (the welder's "WD-03")
# Preview stand-ins: name -> (base colour, metallic, roughness, emission strength). Only the NAME is exported.
PREVIEW = {
    # painted metals are dielectric paint (opaque, low metallic); bare metals are fully metallic
    C: ((0.50, 0.52, 0.55, 1), 0.0, 0.55, 0.0),
    DK: ((0.07, 0.075, 0.08, 1), 0.0, 0.55, 0.0),
    YL: ((0.85, 0.55, 0.05, 1), 0.0, 0.55, 0.0),
    CH: ((0.92, 0.93, 0.95, 1), 1.0, 0.06, 0.0),
    BM: ((0.62, 0.62, 0.64, 1), 1.0, 0.3, 0.0),
    EM: ((0.1, 0.8, 1.0, 1), 0.0, 0.4, 3.0),
    EC: ((0.05, 1.0, 0.15, 1), 0.0, 0.4, 5.0),
    ES: ((1.0, 0.08, 0.05, 1), 0.0, 0.4, 4.0),
    CV: ((0.12, 0.12, 0.13, 1), 0.7, 0.5, 0.0),
    RB: ((0.03, 0.03, 0.03, 1), 0.0, 0.8, 0.0),
    MD: ((0.40, 0.40, 0.39, 1), 0.9, 0.5, 0.0),
    GL: ((0.6, 0.8, 0.9, 1), 0.0, 0.05, 0.0),
    "PV_Plume": ((0.75, 0.9, 1.0, 1), 0.0, 0.5, 1.5),   # preview-only puff (never exported)
    FR1: ((0.35, 0.3, 0.27, 1), 0.8, 0.7, 0.0),
    T1: ((0.5, 0.5, 0.5, 1), 0.5, 0.5, 0.0),
    T2: ((0.5, 0.5, 0.5, 1), 0.5, 0.5, 0.0),
    T3: ((0.5, 0.5, 0.5, 1), 0.5, 0.5, 0.0),
    P1: ((0.5, 0.5, 0.5, 1), 0.5, 0.5, 0.0),
    S1: ((0.5, 0.5, 0.5, 1), 0.5, 0.5, 0.0),
    S2: ((0.5, 0.5, 0.5, 1), 0.5, 0.5, 0.0),
    FONT: ((0.05, 0.05, 0.05, 1), 0.0, 0.6, 0.0),
}

UV_SCALE = 0.5        # UV units per metre for the world-space box projection on tiling library materials
RECESS = 0.05         # depth of framed face recesses
SKIN = 0.004          # outer frames sit 4 mm inside the bounds so 2 mm decals never poke out
DECAL_OFFSET = 0.002  # decals float 2 mm above their surface (vanilla does the same)
