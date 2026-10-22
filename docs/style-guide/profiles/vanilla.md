# Profile: vanilla

The default profile (`src/se2modkit/profiles/vanilla.toml`). A block built to it sits next to a Keen block and
reads as one game. Values are first-pass, measured from vanilla blocks; a mod may tighten them, never relax them
([overrides](../checks.md#overrides)).

| Key | Value | Why |
|---|---|---|
| `paint.colorable_min_share` | 0.45 | vanilla blocks are mostly paint, so the player's color covers the block |
| `emissive.max_share` | 0.003 | vanilla lights are small lenses; feature lights excepted |
| `materials.max_per_lod` | 8 | our cap, under Keen's per-size limits |
| `limits.fracture_tri_mult` | 1.83425 | Keen's triangle allowance for fractured models (`se2modkit.model.limits`) |
| `limits.fracture_materials` | 32 | Keen's material allowance for fractured models |
| `edge.chamfer_large` | 0.09 m | Keen's big chamfer on outer housing edges |
| `edge.chamfer_small` | 0.015 m | the crisp small break |
| `edge.shadow_gap` | 0.016 m | a gap that reads at distance |
| `mount.keep_out` | 0.027 m | non-mounting parts stay inside the box |
| `hw.bolt_r` | 0.009 m | readable at first-person range |
| `hw.led_size` | 0.012 m | lens radius of a status light |
| `decal.offset` | 0.002 m | as vanilla decals |
| `decal.max_lod` | 1 | decals stop after LOD1 |
| `hazard.max_share` | 0.04 | hazard restraint |
| `hazard.max_share_drives_guns` | 0.08 | drives and guns carry more real hazards |
| `dm.tri_ratio_max` | 1.2 | parallax Deformed materials carry the dents; vanilla is near 1.0 |
| `dm.dent_mm` | 5 to 15 | vanilla's mean dent band |
| `fracture.min_zone_health` | 0.005 | a zone weaker than this dies from almost any hit |
| `fracture.cap_uv_per_m` | 0.4 | break faces tile like the painted metals |
| `fracture.min_zones`, `fracture.max_zones` | 2, 16 | the engine's bounds for a breakable block |
| `interior.min_cover` | 0.8 | a broken zone opens onto a core, not a hollow |
| `interior.max_detail_tris` | 800 | a few large parts inside, not a modeled machine |
| `construction.stages` | 0, 0.1, 0.2, 0.3, 0.533, 0.767, 1 | vanilla's seven-stage template |
| `construction.scaffold_share` | 0.05 to 0.25 | vanilla scaffold cages |
| `maker.cap`, `maker.cap_min` | 0.06 m, 0.04 m | maker plate cap height as drawn, and the smallest allowed |
| `maker.proud` | 0.0018 m | the plate stands proud of the face |
| `maker.max_per_block` | 1 | one maker plate per block |
| `animation.overshoot` | 0.002 to 0.009 | the house spring feel |

Non-numeric keys: `roles.*` (material roles, [materials](../30-materials.md)), `paint.colorable_materials`,
`emissive.prefix`, `emissive.light_materials`, `decal.materials_prefix`, `hazard.high_archetypes` and `checks.extra`
(empty: no extra checks).
