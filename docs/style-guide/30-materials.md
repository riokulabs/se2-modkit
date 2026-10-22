# Materials

Assign library materials by name; the export maps each name to the vanilla material definition GUID. Never bake the
hull color: the player's paint is the hull color.

## Roles

| Role (`roles.*`) | Material | Use |
|---|---|---|
| `roles.paint` | `PaintedMetalColorable` (`be89c5c3…`) | shells, covers, skins: the player's paint |
| `roles.dark` | `PaintedMetalDarker` (`cb933a73…`) | housings, recesses, mechanical cores, label plates |
| `roles.steel` | `BrushedMetal` (`0c3be477…`) | load paths: rails, flanges, brackets, grab bars |
| `roles.hardware` | `MetalDull` (`0e9e7d42…`) | black-oxide bolts on paint, pins, hinge knuckles |
| `roles.chrome` | `Chrome` (`3312f76c…`) | precision sliding surfaces only: rods, guide bars, pins |
| `roles.trim` | `PaintedMetalYellow` (`d74fe97a…`) | thin trim lines, port frames, real hazards |
| `roles.rubber` | `Rubber` (`ea603341…`) | cable jackets, seals, hoses |
| `roles.glass` | `ClearGlass` (`30072ea8…`) | domes and windows |
| `roles.stencil` | `AtlasFont_BarlowCondensed` (`481b2a1f…`) | stencil text only |
| `roles.break_face` | `FracturedMaterial01` (`0f2772fd…`) | break faces and core faces in damaged models |

A mod may add roles (`[tool.se2modkit.style.roles]`), never re-map an existing one.

## Break-face materials

| Material | Definition GUID | Use |
|---|---|---|
| `FracturedMaterial01` | `0f2772fd-72d6-4ee9-9211-38d1781ba8b9` | the standard: torn wiring and boards on break faces and solid cores |
| `FracturedMaterial03` (plain) | `a47991a9-af63-479d-b38a-2cc46f34b289` | opaque shredded sheet metal |
| `FracturedMaterial03_Masked` | `466b08ac-cdec-4471-b5b7-1ad0a6e56911` | the alpha-masked variant; vanilla drill and piston models map their `FracturedMaterial03` slot to it |

For a solid interior core that uses "FracturedMaterial03", map the slot to the plain material, unless the block wants
the alpha-masked (see-through) look. Neither variant is a profile role yet: map it in the mod's material table.

## Budgets

| Budget | Value | Key |
|---|---|---|
| Paint channel share of LOD0 visible area (vanilla is mostly paint) | at least 0.45 | `paint.colorable_min_share` |
| Emissive share of LOD0 visible area, feature lights excepted | at most 0.003 | `emissive.max_share` |
| Material slots per LOD | at most 8 | `materials.max_per_lod` |

- Emissive slots are those whose name starts with `Emissive`. Only a profile's own `emissive.light_materials`
  are excepted; a mod cannot add to that list. A block with a real feature light that exceeds the budget gets a
  waiver (reason and date).
- Status lights: the status slot defaults to `EmissiveRed` (`a68b9acc…`) and swaps to `EmissiveGreen` (`78f2cbaa…`)
  through the block material swapper; unlit lenses use `EmissiveOff` (`d2648963…`).
- Every slot is a library material, so Keen's wear and detail come with it. Atlas materials (`ConveyorsAtlas`, decal
  atlases, fonts) need atlas UVs, never a world projection.

## Damaged ("Deformed") models

Since game version 4.0 the Deformed models point their painted-metal slots at parallax Deformed materials. The slot
keeps its name; only the material reference changes.

| Slot | Deformed material GUID (prefix) |
|---|---|
| `PaintedMetalColorable` | `d8847d2a…` |
| `PaintedMetalDarker` | `ea15e749…` |
| `BrushedMetal` | `d2d76bf2…` |
| `Chrome` | `4418666d…` |
| lamps (`EmissiveOff`, status and feature slots) | `EmissiveOffBroken` (`bcefad69…`) |
| screens | `Display01Broken` (`0f138e31…`) or `LCDScreen_Off` |
| glass | `WindowGlassBroken` (`b9db8238…`) |
