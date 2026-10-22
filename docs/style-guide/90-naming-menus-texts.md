# Naming, menus and texts

## IDs and names

| Item | Pattern |
|---|---|
| Block ID (subtype, file names) | `<PREFIX>_<Family>_<Variant>`, the prefix from `[tool.se2modkit] prefix` |
| Display name | plain English, function first, then size or variant |
| Description | what it does and how to use it, in one or two sentences |
| Stencil ID on the block | short code, matches the display name's variant |

- One prefix per mod; never reuse a vanilla name for a new block.
- Every new definition gets its own GUID; never reuse a vanilla GUID for a new object.

## Build menu

The menu tree is Category → Subcategory → BlockGroup (one tile) → BlockKind (a tile inside the group) → the kind's
blocks (the size selector).

- **One subcategory per mod**, inside the vanilla category the blocks belong to.
- **One group per family**, holding one kind per type.
- **Sizes of one type share a kind**: the game orders them by display size and fills the size slots in that order.
- Same-size variants get a kind each, as vanilla does.
- A clone that keeps the vanilla kind joins the vanilla tile: always give clones their own kind.

## Texts

- Every display string (name, description, tooltip) is a localisation key in the mod's own culture texts, one file
  per culture (`<Mod>/Texts/<culture>/<Mod>.loc-texts`) with its culture definition.
- A key that is missing logs a warning at world load and shows the raw key: a test checks every display string has an
  entry.
- English first; keys stay stable once published.
