# When the game updates

Modkit is tested against specific game builds, listed in `src/se2modkit/compat.toml`
(`game_versions_tested`, `game_min`, `sdk_buildid_tested`). An untested build warns and builds; `--strict-game` fails.
Everything below runs locally. Captured baselines, reports and type facts contain Keen material: they go to the user
cache and are never committed.

| Step | Command | Result |
|---|---|---|
| 1. See what changed in what mods depend on | `se2mod deps check` | Re-checks recorded dependencies (file hashes, GUIDs, code anchors, build ids); `--min-risk`, `--refs`, `--report-json FILE` |
| 2. Freeze the old state (before updating) | `se2mod deps baseline capture` | Writes `<cache>/baseline/<version>/`; `--api` adds the assembly inventory |
| 3. Compare after updating | `se2mod deps baseline diff` | Report under the cache: changed, added and removed files, API surface |
| 4. Accept the new build | edit `compat.toml` (a maintainer step in a modkit checkout, followed by a new release tag that mods then pin) | Add the version to `game_versions_tested`, raise `game_min` if needed, update `sdk_buildid_tested` |
| 5. Refresh recorded hashes | `se2mod deps check --rebaseline` | Writes a copy with current hashes (to the cache, or over the file you pass) |
| 6. Re-run the gates | `se2mod test --gate NAME`, then the full suite | See [the gates](../README.md); SDK build tests need `SE2MODKIT_SDK_BUILD=1` |
| 7. Rebuild type facts | `se2mod facts build` | Reads your own decompiled game (`SE2_DECOMPILED` / `decompiled_dir`), writes names only into the cache |
| 8. Compare outputs | `se2mod snapshot` before and after | Generated files must be identical, or each difference is explained |

Notes:

- Capture a baseline **before** the update; without it step 3 has nothing to compare against.
- Type facts are generated on your machine from your install. They are not published; the gates that need them skip
  on a machine without them.
- Nothing from the game, the SDK, a decompile, a baseline or the type facts is committed to this repository.
- Build only while the game is closed.
