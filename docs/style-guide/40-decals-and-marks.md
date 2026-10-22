# Decals and marks

Fine detail comes from Keen's decal atlases, not geometry: bolt heads, seams, small grilles, hatch outlines,
stickers and stencils.

## Decal rules

| Rule | Value | Key |
|---|---|---|
| Decal quads float above their surface | 0.002 m | `decal.offset` |
| Last LOD that carries decals | LOD1 (index 1) | `decal.max_lod` |

- Use the vanilla atlases: `AtlasDecal_Trims01`/`02`/`03`, `AtlasDecal_Parts01`, `AtlasDecal_Stickers01`/`02`, and
  a mod's own atlas for its own emblems.
- Decals, screens, lamps and loose cables in the damaged model go in `_Hide` groups set `AlwaysHidden`.
- Map each decal onto its atlas region; a world projection samples the wrong band.
- Warning triangles from `AtlasDecal_Stickers02` are drawn as exact triangles (the atlas interlocks them), point up.

## Stencils

- Font: the `AtlasFont_BarlowCondensed` family only (clean and dirty, white and black). No other typeface in
  geometry.
- White text on a dark label plate reads on any paint (`helpers.label`).

## Hazard restraint

| Budget | Value | Key |
|---|---|---|
| Hazard decal and stripe area, share of LOD0 visible area | at most 0.04 | `hazard.max_share` |
| The same for drives and guns (`style.archetype` thruster or weapon) | at most 0.08 | `hazard.max_share_drives_guns` |

Yellow and black only where a real hazard is (plume, muzzle, high voltage, moving parts), and each hazard label
names the hazard next to it. Never as wallpaper.

## Maker mark

The maker mark is a small bolted maker plate carrying the **Rioku Labs R4 lab-flask mark**. The artwork is a decal
element in the Rioku atlas; `helpers.maker_mark` draws the plate, its bolts and the mark (`helpers.r4_mark`).

- Artwork: `src/se2modkit/kit/maker-mark/r4-mark-40mm.svg`, the R4 mark simplified to read at 40 mm on a 64 px
  atlas cell (flask outline, a ringed planet in the liquid, one spark). One color: white ink, the alpha carries it.
- Atlas: `src/se2modkit/kit/atlas/rioku.toml` lists each element's name, rect and drawn size (1600 px/m, so 64 px per
  40 mm; a 2x cell for marks drawn larger). `se2mod kit build` renders it into the user cache (PNG, and DDS when a
  native `texconv` is on PATH); `se2mod kit fetch` downloads a release's zip and checks its sha256; `se2mod kit path
  RiokuAtlas01.png` prints where it is. The binaries are never committed. A mod defines the `RiokuAtlas01` decal
  material and copies the texture into its own `Assets/`.
- Level of detail: the plate to LOD2, the bolts at LOD0, the mark to `decal.max_lod`, nothing at LOD3.

| Plate | Value | Key |
|---|---|---|
| Cap height as drawn | 0.06 m | `maker.cap` |
| Smallest cap height | 0.04 m | `maker.cap_min` |
| Plate stands proud of the face by | 0.0018 m | `maker.proud` |
| Plates per block | at most 1 | `maker.max_per_block` |

- Place it only where a real maker plate would sit: a service face, near the ID stencil.
- Never on a mount face.
