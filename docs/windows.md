# Windows

Modkit is written to run on Windows, but its author has not yet verified everything there. This page says which parts
are expected to work, which are untested, and holds the manual checklist.

## Expected to work

| Area | Notes |
|---|---|
| Discovery of Steam, libraries, game, SDK, Blender | Registry-free: default Steam locations, `libraryfolders.vdf`, `Program Files\Blender Foundation`; the unit tests cover it with synthetic trees |
| Mods folder | `%USERPROFILE%\Documents\SpaceEngineers2\Mods`, or config `game_mods_dir` |
| Process check | `tasklist`; fails closed when it gives nothing |
| Paths | `pathlib` throughout; text files written with LF; spaces and non-ASCII (for example a user named Jörg) are quoted |
| Pure-Python commands | `defs`, `chain`, `outline`, `report`, `snapshot`, `style`, `test`, `deps check` |
| Linux-only parts | Proton prefix and Wine translation are never imported on Windows; the SDK tools run natively |

## Untested

| Area | Open question |
|---|---|
| `se2mod build` | Native SDK builder run end to end |
| `se2mod blender run` | Finding Blender and the VRAGE Tools add-on |
| `se2mod play`, `play --native` | Steam launch; Pulsar has no default location on Windows (set `SE2_PULSAR` or config `pulsar`) |
| `se2mod off` / `on` | Junction creation and removal, and permissions |
| `se2mod sdk standin` | Whether Windows needs it at all |
| `se2diag` | Linux only |
| `se2mod publish` | Steamworks library lookup (`SE2_LIBSTEAM_API`) |

## Windows manual pass (to be filled in by the maintainer)

Record the result, the date, game version and modkit version beside each item. Fix findings before tagging.

- [ ] `se2mod env`: Steam root, libraries, game and SDK folders, mods folder, versions are all right
- [ ] `se2mod doctor`: Blender and the VRAGE Tools add-on found, SDK present
- [ ] `se2mod build` of a mod succeeds
- [ ] `se2mod blender run` of one block works
- [ ] `se2mod play` starts the game with the mods mounted
- [ ] `se2mod play --native`, if Pulsar is installed
- [ ] `se2mod off` and `se2mod on` (junctions) work and are reversible
- [ ] `se2mod sdk standin`: needed or not (record which)

Results:

| Item | Result | Date | Notes |
|---|---|---|---|
|  |  |  |  |
