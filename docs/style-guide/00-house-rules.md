# House rules

The rules every Rioku Labs mod follows, whatever its style profile. The numbers behind them live in the profiles
(`src/se2modkit/profiles/*.toml`); the other pages cite the key for each one.

| # | Rule | How it is held |
|---|---|---|
| R1 | **Every modeled part has a believable function** that fits the block. Each block has a one-line function chain (input → process → output → control) and a part → purpose table. A part without a row fails review. No greebles. | manual review ([checks](checks.md#manual-review)) |
| R2 | **Vanilla flavor.** A Rioku block next to a Keen block reads as one game: library materials by GUID, Keen's edge language, port and hatch hardware, stencil font, light behavior, scale. | style checks, side-by-side preview |
| R3 | **Original designs.** SE2 vanilla kit parts (conveyor ports, handles, vents, decal atlases) may be reused and are recorded with their source. Overall designs are ours. Nothing from other mods or from SE1. | review, provenance list in the build report |
| R4 | **Match vanilla balance bands** per volume. A block may not make a vanilla block obsolete. | review against the clone source's stats |
| R5 | **Scripts are optional.** The mod is fully usable data-only; scripts add behavior. The scripts-off fallback is minimal: the simplest honest data-only version, never a fake of the scripted one. Worlds saved with scripts load without them. | [scripts and fallback](80-scripts-and-fallback.md) |
| R6 | **Block motion is spring-eased and sequenced** where scripts allow it. | [animation](70-animation.md) |
| R7 | **Broken interiors, Keen's way.** A fracture zone's inside is a solid core whose exposed faces use vanilla `FracturedMaterial01` (`FracturedMaterial03` shredded sheet where it fits), plus only the few large, recognizable parts you would see through the hole. No modeled internals, no empty hollows. | `interior.min_cover`, `interior.max_detail_tris` ([build and damage](60-build-and-damage.md)) |
| R8 | **Previews are generated on disk and never committed.** Only README marketing images go in `docs/media/`. | review, git ignore rules |
| R9 | **No AI attribution** in any repo content: commits, pull requests, code, docs. | org commit policy |
| R10 | **Branding.** The in-game maker mark is the Rioku Labs R4 lab-flask mark. No third-party franchise names, logos or terms. | [decals and marks](40-decals-and-marks.md) |

## Using this guide

- Pick a profile: `vanilla` (the default) or a stricter one such as `industrial`
  ([vanilla](profiles/vanilla.md), [industrial](profiles/industrial.md)).
- Build, then run `se2mod style check` ([checks](checks.md)).
- Fix a finding, or waive it with a reason in `style-waivers.toml` when the block has a real cause to differ.
- Walk the manual review list before a release.
