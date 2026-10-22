# Profile: industrial

The industrial profile (`src/se2modkit/profiles/industrial.toml`). It `extends` vanilla: every vanilla key,
role and budget stays (materials, edge language, paint behavior, ports, font, lights, scale), so a block built to it
still reads as the same game. What changes is proportion, orientation, signage and restraint.

## What it adds

| Key | Value | Why |
|---|---|---|
| `geometry.plate_row` | 0.625 m | horizontal hull-plate rows at a quarter cell, lined up across stacked blocks |
| `geometry.deck_height` | 2.5 m | one deck per large cell, floor to floor |
| `geometry.service_deck` | 5.0 m | service decks span two cells |
| `ergonomics.grab_bar_z` | 1.05 m | grab bars at the same height above the deck on every block |
| `ergonomics.grab_bar_standoff` | 0.08 m | the same reach on every block |
| `stencil.id_h` | 0.06 m | block ID and maker code cap height, readable at a few meters |
| `stencil.deck_h` | 0.30 m | deck numbers on bulkheads |
| `stencil.haz_h` | 0.10 m | hazard words |
| `hazard.max_share` | 0.04 | as vanilla, restated: hazard marks only where a hazard is |
| `hazard.max_share_drives_guns` | 0.08 | as vanilla, restated |

| Non-numeric key | Value | Why |
|---|---|---|
| `finish.chrome_only_on` | pin, rail, ram | matte and utilitarian: chrome only where a real part is polished |
| `finish.decorative_emissive` | false | no glowing strips; emissives are status lenses and real lights |
| `atlas.extra` | `rioku` | the Rioku emblem and signage atlas, all original artwork |
| `checks.extra` | faction parity, orientation, rationale coverage, hazard tags | profile checks (below) |

## Design rules

- **The shape is the job.** Silhouettes follow function; the cell volume stays open where the job does not fill it.
- **A thrust axis.** Every block declares which way is "down"; furniture, hatches and stencils read correctly that way.
- **Plate rows.** Long horizontal plates with real shadow gaps, staggered, at `geometry.plate_row`.
- **Ship-yard signage.** ID and maker code, deck numbers, hatch numbers, content bands, lifting points, all at the
  `stencil.*` sizes, in the vanilla stencil font.
- **Matte finish.** Wear comes from the library materials.
- **Shells.** One core block can carry several trim shells; stats, mount points and port positions stay identical
  across shells.

## Profile checks

The four `checks.extra` checks belong to a mod's own library, not to modkit. Until it provides them,
`se2mod style check` lists them as **not measured** on every block.

| Check | Fails when |
|---|---|
| faction parity | the shells of one core differ in cell box, mount faces, ports, dummies or stat band |
| orientation | a part that depends on the thrust axis faces the wrong way |
| rationale coverage | a visible part has no row in the block's part → purpose table (believability stays a manual review item) |
| hazard tags | a hazard mark has no hazard tag in the part → purpose table |
