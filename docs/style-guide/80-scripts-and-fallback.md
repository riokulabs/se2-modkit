# Scripts and the data-only fallback

Scripts are an enhancement layer (house rule R5). A player who runs the mod without scripts gets a complete,
honest mod.

| Case | What the player gets |
|---|---|
| scripts on | the full behavior: sequenced, spring-eased motion, extra terminal controls, richer states |
| scripts off | the minimal data-only version: the simplest honest motion and states the vanilla components give |
| world saved with scripts, loaded without | the world loads; blocks fall back to their data-only behavior |

## Rules

- **Minimal, not fake.** The fallback is the simplest honest data-only version (a drawer slides and folds at once;
  a door opens without its scripted sequence), never an imitation of the scripted sequence.
- **Save safety.** Script state is optional data: a block never needs it to load, and a save written by the scripted
  version stays loadable by the data-only one.
- **No hard dependency.** Definitions, models, menus and texts do not reference script types; scripts attach to blocks
  that already work.
- **Publishing.** A mod declares whether scripts are optional or required; the publisher ships the scripts with it
  (`docs/publish.md`).
- **Test both.** The in-game checklist runs the block with scripts on and off.
