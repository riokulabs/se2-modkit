# Style checks

`se2mod style check [MOD] [--warn-only]` reads the mod's `build_report.json`, loads the mod's profile with its
`[tool.se2modkit.style]` overrides and its `style-waivers.toml`, and prints every finding. It exits 1 on an unwaived
`fail` unless `--warn-only` is given. The test plugin's `style` gate runs the same checks
(`se2modkit.style.check.gate_failures`) and fails only when the mod has not set `warn_only = true`.

## Automated checks

Each finding carries the block, the key below, the measured value, the limit and the level. Values are shares (0 to
1) unless the key says otherwise.

| Finding key | Measures | Report fields read | Level |
|---|---|---|---|
| `paint.colorable_min_share` | paint channel share of LOD0 visible area | `blocks[B].lods[0].area_pct` | fail |
| `emissive.max_share` | emissive share of LOD0 visible area, `emissive.light_materials` excepted | `blocks[B].style.emissive_share`, else `blocks[B].lods[0].area_pct` | fail |
| `materials.max_per_lod` | material slots on the worst LOD | `blocks[B].lods[*].materials` | fail |
| `limits.tris` | triangles against Keen's limit, worst LOD | `blocks[B].lods[*].tris`, `blocks[B].lods[*].max_tris` or `blocks[B].model_size` | warn |
| `limits.materials` | material slots against Keen's limit, worst LOD | `blocks[B].lods[*].materials`, `blocks[B].lods[*].max_materials` | warn |
| `decal.max_lod` | decal or font atlases on a LOD past the last decal LOD | `blocks[B].lods[*].material_names` (`decal.materials_prefix`) | warn |
| `hazard.max_share` | hazard decal and stripe share of LOD0 visible area | `blocks[B].style.hazard_share` | fail |
| `hazard.max_share_drives_guns` | the same for a block whose archetype is in `hazard.high_archetypes` | `blocks[B].style.hazard_share`, `blocks[B].style.archetype` | fail |
| `dm.tri_ratio_max` | damaged over fractured LOD0 triangles | `blocks[B].damage.dm_tris`, `blocks[B].damage.fr_tris` | warn |
| `dm.dent_mm` | mean dent depth inside the band | `blocks[B].damage.dent_mm` | warn |
| `fracture.min_zone_health` | each zone's share of the block's health | `blocks[B].fracture.zone_health` | warn |
| `interior.min_cover` | each exposing zone's covered seam share: a solid core, not a hollow | `blocks[B].fracture.interior` | fail |
| `interior.max_detail_tris` | interior triangles of the exposing zones together: not a modeled machine | `blocks[B].fracture.interior` | fail |

Keys on design constants (`edge.*`, `hw.*`, `mount.keep_out`, `decal.offset`, `maker.*`) are not measured from the
report: `se2modkit.style.helpers` draws to them, and the z-fight and placement gates hold the geometry.

## Report fields

Every field is written as its path in `build_report.json`, `blocks[B]` being one block. The `lods` fields come from
the model pipeline. The
`style`, `damage` and `fracture` tables are written by the mod's build when it measures them
(`se2modkit.pipeline.report`; a mod can add them from its own exporter until then).

| Field | Content |
|---|---|
| `blocks[B].lods[*].area_pct` | material name → share of the LOD's visible area in percent |
| `blocks[B].lods[*].material_names`, `blocks[B].lods[*].materials` | material slots of the LOD and their count |
| `blocks[B].lods[*].tris`, `blocks[B].lods[*].max_tris`, `blocks[B].lods[*].max_materials` | triangles and Keen's limits for the LOD |
| `blocks[B].model_size` | model bounds in meters, used for Keen's limits when `max_tris` is absent |
| `blocks[B].style.emissive_share`, `blocks[B].style.hazard_share` | measured shares of LOD0 visible area (fractions) |
| `blocks[B].style.archetype` | the vanilla archetype the block mimics, e.g. `thruster`, `weapon` |
| `blocks[B].damage.dm_tris`, `blocks[B].damage.fr_tris`, `blocks[B].damage.dent_mm` | damaged and fractured LOD0 triangles; mean dent in mm |
| `blocks[B].fracture.breakable` | `false` for a non-breakable block: zone and interior checks do not apply |
| `blocks[B].fracture.zone_health` | zone → share of the block's health |
| `blocks[B].fracture.interior` | zone → `exposes`, `cover`, `interior_tris`, `core_m2`: the broken-interior survey |

The interior survey (one row per zone, from the cut Fractured LOD0):

| Survey field | Meaning |
|---|---|
| `exposes` | the zone lies mostly inside the block's interior (the bounds shrunk past skins, liners and covers), so its death opens the interior |
| `cover` | across the zone's seams inside the interior, the share of the seam from which a short ray into the neighbors meets a face (a core face, break face or part); empty or missing when no seam crosses the interior |
| `interior_tris` | triangles of the zone's own model, not its break faces, whose center lies in the interior: modeled detail behind the skin |
| `core_m2` | break-face area inside the interior |

An exposing zone with no `cover` counts as cover 0: nothing is there to see.

## Not measured is not a pass

When a block does not report what a check needs, the check is listed as **not measured** for that block, with the
block names. It is never counted as a pass. Extra checks a profile switches on (`checks.extra`) that modkit does not
implement are listed the same way until a mod library provides them.

## Waivers

`style-waivers.toml` in the mod root (or the path in `[tool.se2modkit.style] waivers`):

```toml
[[waiver]]
block = "MyMod_Cabinet"
key = "paint.colorable_min_share"
reason = "dark equipment cabinet: its vanilla analogue is mostly Darker"
date = 2026-10-21
```

- Every waiver needs a block, a key, a reason and a date; one waiver per block and key.
- A waived finding is still printed with its reason and date; a waiver that matches nothing is listed as unused.

## Overrides

A mod tightens its profile in `[tool.se2modkit.style]`:

- a key with `min` in its name may go up, one with `max` may go down;
- a two-number band may narrow;
- a list of names may grow (extra checks, decal prefixes), except the fixed lists (`emissive.light_materials`,
  `paint.colorable_materials`, `hazard.high_archetypes`, `construction.stages`);
- `roles` may gain new roles.

Design constants, fixed lists and existing roles cannot change. Anything that relaxes the profile stops the check with
one sentence naming the key. `warn_only` and `waivers` are settings, not overrides.

## Manual review

These need judgment and have no report field. The side-by-side preview and the in-game checklist record sign-off.

- **Every modeled part has a believable function** that fits the block (house rule R1): the block's function chain
  and its part → purpose table, one row per visible part. No greebles.
- **The side-by-side look**: next to its vanilla clone source, painted the same, the block reads as the same game.
- **Balance**: stats inside the clone source's band per volume.
- **Hazard placement**: each hazard mark sits next to the hazard it names.
- **Scripts-off fallback**: minimal and honest.
