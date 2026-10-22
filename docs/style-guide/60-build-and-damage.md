# Build states and damage

How a block builds, dents, breaks and dies, the vanilla way (game version 4.0).

## Construction

| Rule | Value | Key |
|---|---|---|
| Stage template (build ratio of each stage) | 0, 0.1, 0.2, 0.3, 0.533, 0.767, 1 | `construction.stages` |
| Scaffold cage, share of Fractured LOD0 triangles | 0.05 to 0.25 | `construction.scaffold_share` |

- Stage 0 swaps to the Fractured model; later stages show or hide its mesh parts; the last stage swaps to the intact
  model. Up to the fourth stage the scaffold and a ghosted skin show; after it, break faces and inner frames.
- Construction attributes on the Fractured model: skin always visible; break faces and inner frames (`_Frame`,
  `_Inner`) hidden until the second sequence; brackets in `_Support`; decals, screens, lamps and loose cables in
  `_Hide`, always hidden; the scaffold in BrushedMetal, ungrouped. Set `OrderId` where order matters.
- The stage-1.0 hide targets the scaffold's real mesh-part name, so a damaged finished block never shows it.
- Glass and gratings use the `WindowGlass` and `GratingMetallic` presets; conveyor rings use `Conveyor_Port`.
- Regenerate the stage list whenever the Fractured mesh changes.

## Fracture zones

| Rule | Value | Key |
|---|---|---|
| Zones per breakable block | 2 to 16 | `fracture.min_zones`, `fracture.max_zones` |
| Warn when a zone holds less of the block's health than | 0.005 | `fracture.min_zone_health` |
| Break-face texture density (UV units per meter, as the painted metals) | 0.4 | `fracture.cap_uv_per_m` |

- A zone's health is its collider's volume share (critical zones count double). Zones must touch.
- Every zone is a closed shell; its break faces carry `FracturedMaterial01` and show only when a neighbor is gone.
- Dummies with a supporting fracture (ports, terminals, effects) switch off when their zone dies.
- Honest open geometry where the job leaves space: shots and sight lines pass gaps.

## Broken interiors (Keen's way)

What a player sees through a hole is a **solid core**, not an empty hollow and not a modeled machine:

- the inside of a zone is a solid core whose exposed faces use `FracturedMaterial01` (the torn wiring and boards
  texture), or the plain `FracturedMaterial03` shredded sheet metal where it fits
  ([materials](30-materials.md#break-face-materials));
- plus only the few large, recognizable parts you would see through the hole (a fan wall, a busbar row);
- no fully modeled internals: no pipes, valves, boards or looms behind the skin.

| Check | Value | Key |
|---|---|---|
| An exposing zone's death leaves this share of its seams covered by its neighbors | at least 0.8 | `interior.min_cover` |
| Interior triangles of all exposing zones together | at most 800 | `interior.max_detail_tris` |

The mod's build surveys the cut Fractured LOD0 and reports per zone whether its death exposes the block's interior,
how much of its seams the neighbors cover, and how many triangles of modeled detail sit behind the skin
([checks](checks.md)). A block that reports no survey is "not measured", never a pass.

## Damaged model

| Rule | Value | Key |
|---|---|---|
| Deformed LOD0 triangles over Fractured LOD0 triangles | at most 1.2 | `dm.tri_ratio_max` |
| Mean dent depth, mm | 5 to 15 | `dm.dent_mm` |

- The damaged model is the Fractured model minus the scaffold, with identical mesh-part names.
- Paint, Darker, BrushedMetal and Chrome slots map to the Deformed materials; lamps, screens and glass to their broken
  materials ([materials](30-materials.md)). The parallax materials carry the dents, so do not densify the mesh.
- Non-breakable blocks still spawn broken-looking debris (a broken damaged model, as vanilla windows do).

## Fragility and effects

- Fragility class matches the archetype the block mimics: Structural for frames, trays, floors and decor
  electronics; Functional for anything with a functional block component; Windows for pure glazing. Changing it also
  changes maximum health.
- Effects follow the vanilla analogue: structural blocks get death dust; electrical decor gets a single spark band;
  small electronics the timer set; machines the production set; doors the door set.
- Every damage band that names a dummy type needs at least one dummy of that type in the model, or the effect never
  plays.
- Moving subparts get baked `Fracture_NN-<Material>_Subpart` stand-ins listed in `MeshPartsToHideWhenActive`, so
  they build and break with their zone. Static client-prefab children never float in place of a dead zone.
