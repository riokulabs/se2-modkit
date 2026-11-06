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

`se2mod look SOURCE --block B [--set neighbors] [--views look|concept|v1,v2] [--before FOLDER] [--close PART]`
renders a block's look set straight from its working file (`.blend`, `.glb`, `.gltf` or `.fbx`; read, never written)
in the look-dev scene (`src/se2modkit/preview/lookdev.py`): mounted on a section of vanilla armor by `--mount-face`,
beside the mod's `previews.toml` `[lookdev] neighbors` and a scale figure, from in-game eye views. It goes through the
job queue as a medium job, waits, then writes `sheet.png` (this round's pictures; a `--before` set's copied
`_before` images stay off it) and `report.json` (time, triangles, exposure, unmapped materials, `--close` parts that
matched nothing) and lists anything that keeps the set from being shown, including the geometry checks
(`src/se2modkit/pipeline/geocheck.py`, written to `geocheck.json`: parts that don't reach the mount, pins off their
lug, coplanar faces, parts passing clean through others, box-projected UVs on curved parts, cell groups, and the
triangle budget given with `--tris-budget`; if the checks themselves stop on an error, the set fails with that error
and its report is still written). `--no-check` skips them for a quick warp test (a `--tris-budget` given with it is
not checked, and the command warns). Material names that are not Keen slots map
through `[lookdev.materials]`; anything still unmapped renders magenta. No LODs, fracture or export: those come with
`se2mod build-models` once the look is approved.

Keen slots draw from the Mod SDK's own source maps when the SDK is installed (`src/se2modkit/preview/sdkmat.py` reads
each material's `.def`, its `.comptex` recipes and their packing presets): albedo with the player paint through the
color mask, normal (OpenGL green, as the SDK ships it), roughness, metalness, AO and emissive, downsized once into the
private texture cache. Glass, screens without an emissive mask, decal atlases and the fracture material keep their
own stand-ins, and every slot falls back to a flat stand-in color without the SDK.

`se2mod kit library` unpacks Keen's Blender material and decal library from the SDK's `BlenderMaterialLibrary` zip
into the user cache once and prints the folder to add as a Blender asset library. `--check` opens it in a queued,
read-only Blender and lists its image paths that do not exist here (they point at Keen's machines; most have a copy
in the game or SDK install, which it names). `--relink` writes a copy of the library beside the unpacked one
(`<folder>-relinked/`) whose .blend points those images at the copies in this game or SDK install, and lists the ones
it could not find; it runs through the queue and never changes the install or the unpacked folder. `--register` adds
the folder (the relinked copy when there is one) to your Blender preferences, also through the queue; it is never done
unless asked. `--print-only` prints what would be unpacked and the queue commands, and changes nothing.

`se2mod fracture check MOD BLOCK ... [--max-key-share F] [--json]` checks a block's fracture before an export. It
builds the block in a headless Blender through the job queue (a medium job), then plans its fracture, cuts LOD0 and
fits the zone colliders the way the export does (`src/se2modkit/pipeline/fracture_probe.py`), without writing any
model, report or build fragment. It prints each zone's health share (the number the export writes to `export.json`),
where each seam landed (how far it snapped and how close the nearest vertex is) and the cell groups. It exits 1 on a
plan, cut or collider that can't be made, loose vertices or edges in LOD0, too many cell groups, or, given
`--max-key-share`, a critical zone over that share of the health; without it such a zone is a WARN line, since a heavy
critical core can be the design. Most of its time is the block's own build: seconds for a small block, minutes for a
large drive, against tens of minutes for an export. It takes the export's first cut only and leaves out its LOD-count
and fractured triangle budget checks, so a block can still pass here and fail the export on those. `--check-fracture`
on the build script does the same in Blender.

`se2mod ship check MOD BLOCK ... [--max-key-share F] [--blend FILE] [--json] [--print-only]` is the one check to run
before a drive's export, read-only. First, before anything is queued, the queue order
(`src/se2modkit/pipeline/ship_check.py`): a job that saves or holds the block's working `.blend` (a live Blender on
it, or any job naming it) must not wait behind, or run beside, a build or export of the block, or that run reads the
old file; that is a FAIL and nothing is queued. A save queued ahead of the export, a live Blender on another file and
an `se2mod lock` lease on the `.blend` are WARN lines: save and release before the export. The `.blend` files are the
ones the block's last build read, any `--blend`, and, after the run, the ones it loaded. Then one medium headless job
builds the block as the export would and reports what ships: every model the export takes, and each working-file
collection the build loaded with every object in it, by render visibility (a viewport-hidden object still ships and
gets a WARN; a render-hidden stand-in is listed as out; a shipping object named like a stand-in gets a WARN). It runs
the export's own z-fight audit on the built models, so coplanar faces between collections (after the build has merged
them) fail, then the fracture check above, and renders a tinted exploded picture of the fracture zones with each
zone's box and a legend of names and health shares. It writes only `Source/Model/preview/ship/<block>/exploded.png`
and `ship.json`; one summary line per block, then PASS or FAIL. Exit 0 pass, 1 a FAIL line, 2 the run failed.
`--check-ship --ship-out=DIR` on the build script does the Blender part.

### A model as numbers

`se2mod describe FILE.blend [--collection C ...] [--mod MOD] [--targets FILE.md] [--envelope X,Y,Z] [--json OUT]`
lists a model as numbers instead of pictures: geometry is easier to judge, by a person or a script, from sizes and
gaps than from a render. It runs one read-only headless Blender job through the queue (a light job; it renders nothing
and never saves the file). The parts are what a build would ship (render visibility, no boolean cutters), from each
`--collection` taken as the block, or from the whole scene. Each part gets a stable number (`#1`, `#2`, ... sorted by
collection, then name) and one line: role (hull, port, light, glass, decal, collider, LOD, dummy or part, guessed from
the name), size and center in mm, center in 0.25 m grid cells from the origin, nearest part and gap (measured on the
meshes when the boxes are within 50 mm; negative is the deepest point of one closed part inside another), the parts it
touches (gap of 0.5 mm or less), its mirror partner across the X, Y or Z mid-plane with the error in mm, and the
planes it is symmetric about itself. Then come the FLOATING parts (touching nothing), the OVERLAP pairs and the
MIRROR-ERROR pairs. `--envelope` (meters) adds how much of the block the model fills and puts the mirror planes at the
block origin instead of the model's box center. `--json OUT` writes everything, and `--mod` finds a relative file
under the mod's model source.

`--targets FILE.md` checks proportions. Every fenced block with the info string `targets` in that markdown file is
TOML:

```targets
[[target]]
name = "nozzle to body length"
a = "Nozzle*.z"   # parts matching the name pattern, then the size along x, y or z of their joint box
b = "Body.z"      # optional: the value checked is then a / b
value = 0.6       # in mm when there is no b
tol = 0.05
```

An optional `collections = ["*_belt_v1"]` (name patterns) limits a target to the parts of matching collections, so
one file can hold targets for several block families; a run that describes none of them skips the target.

Each target prints PASS or FAIL with what it measured; a pattern that matches no part is a FAIL. Exit 0 when every
target passes, 1 on a FAIL, 2 when the run itself fails (a missing file or collection, a malformed targets block).

The same summary is available in a running Blender: `se2modkit.describe.describe_scene(collections=None,
envelope=None, targets=None)` returns the report as a dict, and `se2modkit.describe.text(report)` gives the lines
above. That module imports nothing outside Blender's own Python.

Every render path follows one shot standard (`src/se2modkit/preview/shots.py`): eleven view names (`front`, `back`,
`left`, `right`, `top`, `bottom`, `3q_front`, `3q_back`, `3q_low`, `mount`, `inside`; older names such as `3q_rear`,
in a camera or a file name, still count as their view), files named `<block>_<stage>_r<N>_<view>[_<extra>]`, a
`<stem>.cam.json` beside each render so a later round reuses the camera, a locator thumbnail on every close-up
(`_close-<part>`), and same-camera `_before` / `_after` pairs. `se2mod shots check FOLDER --block B --stage
look|concept|fix --round N [--interior]` exits 1 and lists what keeps a set from being shown: a missing view, a failed
exposure check (too dark, too bright, blown or crushed), a close-up without a locator, an `_after` without a matching
`_before`, or a failure in the set's `geocheck.json`.

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
