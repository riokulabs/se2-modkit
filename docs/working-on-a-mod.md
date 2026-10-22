# Working on a mod

The everyday commands: scaffold, build models, preview and test.

## Scaffold

`se2mod new NAME` scaffolds a complete mod repo; `--into REPO` adds a mod to a multi-mod repo and `--library` makes a
library mod. Multi-mod repos, the MOD argument every command takes and a CI matrix are in [multi-mod-repos.md](multi-mod-repos.md).

## Model builds

`se2mod build-models MOD [BLOCK ...] [-j N] [--force] [--export]` runs the mod's build script in
Blender and rebuilds only stale blocks: a block's key covers its builder and the mod sources it imports, its catalog
entry, its hooks tables, the modkit, the style profile and the runtime (`src/se2modkit/pipeline/incremental.py`).
Stale blocks build in N headless Blender workers (default min(CPUs, 4)); `--export` stays one windowed Blender (Keen's
VRAGE Tools). The merged `build_report.json` is byte-identical to a full build. `se2mod check MOD BLOCK` rebuilds one
block if stale, runs `se2mod test --block BLOCK`, renders one `se2mod preview --still` and prints one summary line.

## Previews

`se2mod preview [MOD] [--set NAME] [--block B] [--shot NAME] [--still]` renders the shots a mod lists in
its `previews.toml` (blocks, camera preset or position, state: intact / construction stage / damaged / zones dead,
LOD, paint, lighting, resolution; format in `src/se2modkit/preview/plan.py`) from its exported models, in Blender
without a window, into the mod's git-ignored `Source/Model/preview/<set>/`. `se2mod sheet` and `se2mod pair` lay
images out side by side.

## Tests

se2-modkit installs a pytest plugin. It registers the markers `build`, `sdkbuild` (opt-in:
`SE2MODKIT_SDK_BUILD=1`), `blender`, `sdk`, `decompiled`, `scripts` and `slow` (a test whose input is missing skips and
says what to set), fixtures such as `mod_root`, `sdk_assets`, `type_index` and `universe`, and the shared gates a mod
lists in `[tool.se2modkit] gates` and collects with `Tests/test_gates.py` (json, bundles, guids, the client/server
walk, the definition schema, fresh clone, determinism, stages, construction roles, scaffold, state view, z-fight,
placement, style; see `src/se2modkit/testing/gates.py`). The default `staged_assets` fixture reads the staged
package as it is on disk, without restaging (it warns when that is older than the build report); a mod that stages in
its tests overrides it in its conftest. `se2mod test [MOD] [--fast | --changed | --gate NAME]` runs a
mod's tests and prints one summary line plus failures. The walk and schema gates classify the game's types from type
facts: run `se2mod facts build` once per game version (it reads the decompiled game, `SE2_DECOMPILED` or the user
config's `decompiled_dir`, and writes names only into the user cache; facts are never committed).


Untested game versions produce a warning and a pointer to `se2mod deps check`; `--strict-game` turns that into a failure.
