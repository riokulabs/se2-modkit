# Budgets and LODs

## Keen's model limits

The game's model validator limits triangles and material slots per LOD and per model file. The limits scale with
the model's bounding-box surface area, interpolated between rows Keen measured for cubes of a quarter meter, half a
meter, a large cell and two large cells. `se2modkit.model.limits` computes the same limits offline;
`se2mod report` prints each LOD as a share of its limit.

| Limit | Value | Key |
|---|---|---|
| Fractured models: triangle limit multiplier | 1.83425 | `limits.fracture_tri_mult` |
| Fractured models: material slots per LOD | 32 | `limits.fracture_materials` |
| Our own cap on material slots per LOD (all models) | 8 | `materials.max_per_lod` |

- The last LOD always allows one material and a box's worth of triangles: the baked LastLOD.
- Going over a limit is a validator warning, not a build error. Treat it as a target anyway and keep headroom.
- Colliders scale with the largest dimension; fractured blocks get more.
- A subpart is its own model file with its own budget: moving a separable assembly into a subpart frees block
  triangles and gives the part its own LOD chain.

## The LOD chain

- Name LOD files `_LOD1`, `_LOD2`, ... and regenerate each LOD at lower detail; never decimate hard-surface meshes.
- Step down by dropping bevel segments, cylinder sides (halve them), bolts, vents and decals.
- Decals stop after `decal.max_lod` ([decals](40-decals-and-marks.md)).
- Keep conveyor-port sections through LOD2, as vanilla does.
- The last LOD is a box with the baked LastLOD material (a lit `_ON` copy can swap with the working state).

## Icons

- Render icons with Keen's camera and lighting so they sit beside vanilla icons in the build menu.
