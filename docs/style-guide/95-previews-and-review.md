# Previews and review

## Previews

| Preview | What it shows |
|---|---|
| hero | the block in the house lighting, three-quarter view |
| side by side | the block next to its vanilla clone source, painted the same |
| LOD strip | every LOD in a row, same scale |
| states | construction stages, damaged, broken |
| motion | key poses of every moving part |

- Previews are generated on disk into git-ignored directories and **never committed** (house rule R8). Share them as
  files. Only README marketing images go in `docs/media/`.
- `se2mod sheet` lays previews out as a contact sheet and `se2mod pair` puts two side by side; `se2mod review-pack`
  writes a commit range as one diff file for code review.

## Review

1. `se2mod style check` passes, or every finding is waived with a reason.
2. The manual review items in [checks](checks.md#manual-review) are signed off.
3. In game, from the mod's in-game checklist: placed next to the clone source and painted the same; built from
   empty to finished with a welder; damaged, broken and ground down; every moving part run with scripts on and off;
   the build menu entry, names and descriptions read right.
4. The checklist records who signed off and when.
