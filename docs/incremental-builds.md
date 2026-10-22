# Incremental model builds

`se2mod build-models MOD` (and any `se2mod blender run` of a build script that uses `Runner`) rebuilds only the
blocks whose inputs changed since their last build. This is the default everywhere. `--force` rebuilds every selected
block. `se2mod check MOD BLOCK` does the same for one block, then runs its tests and renders one still. What is
published comes from a release build instead (`--release`, see [Release builds](#release-builds)).

A wrong "up to date" ships an old model, so the rule is: when unsure, rebuild. This page lists what a block's key
covers on its own and what you must declare.

## Tracked automatically

| Input | How |
|---|---|
| The block's builder, collision, extras, audits and placement functions | Identity, default values and closure values |
| Mod source code | The builder modules and everything they import, transitively, including imports inside functions, imports through `sys.path` edits, and modules loaded some other way. Code that mentions `importlib`, `__import__`, `runpy`, `exec` and the like makes every `.py` file in the mod part of the key |
| Catalog entry | All fields of the block's spec |
| Hooks tables | The block's family entries; mod-wide tables whole |
| se2modkit | Its version and every file in the package |
| Style profile | `profile`, `style` and `prefix` from `[tool.se2modkit]` |
| Tools | Python, Blender and VRAGE Tools add-on versions, game version, Mod SDK build id |
| Environment | `SE2MODKIT_*`, `SE2_*` and `<prefix>*` variables (render-only ones excluded) |
| Files the build reads | Every file opened for reading, whether by `open`, `json.load`, `Path.read_text`, `numpy.load` or `r+`. This covers reads at import time, also outside the mod folder (the system temp folder included) or through a symlink. A file that was opened but missing counts as absent, so creating it later rebuilds the block. Which blocks a read counts for: see below |
| Folders the build lists | `glob`, `Path.glob`/`iterdir`, `os.listdir`/`scandir`, `os.walk`: the sorted entry names, so an added or removed file rebuilds the block |
| `.blend` libraries | `bpy.data.libraries.load` is wrapped |
| Exported models | With `--export`, a block is stale until its `Models/<block>/` folder holds exactly what an export under its current key wrote. `placement_exported.json` is excluded |

These are not tracked: Python's standard library and site-packages, Blender's own files, the run's private temp
folder and the build's outputs (see below). Each of these is either covered by a version above or written by the
build itself.

## Which blocks a read counts for

What decides it is the code between the read and the runner's call into the builder: the path of calls that led
to the read.

- **Read along the builder's own path** while the block builds: that block only. A data file only `XX_A`'s
  builder reads rebuilds `XX_A` alone, whatever the process built after it. The path is every frame from the read
  out to the runner's own call into the block's builder (`Runner.build_block` makes it through
  `incremental.call_builder`), and each of them must be one of two things:
  - the builder's own code: the function the family's `builders=` or `block_builders=` entry is, unwrapped only
    through `__wrapped__`, plus every function, lambda or generator nested inside it. Nothing it closes over counts.
    A builder that is (or wraps, at any layer) a partial, a bound method, a callable object or a cache wrapper has
    no own code;
  - I/O plumbing that keeps nothing between calls: `open` and `io`, `codecs` and `encodings`, `os` and `os.path`,
    `pathlib` (with the `glob` and `fnmatch` it lists folders through), `json`, `tomllib`, `csv`, and
    `se2modkit.pipeline.inputs`.

  The whole path is checked, not just the part up to the first frame of the builder's code. A builder that calls
  back into itself through a shared helper (a lambda or partial handed to a memo such as `once(key, lambda:
  build(...))`) has that helper on the path, so the read counts the shared way even though the builder's own code
  made it.
- **Read with any other frame on that path, or by other code while a block builds** (a helper in the builder's file,
  a shared module, a decorator's wrapper, a loader the builder closes over, a nested function a shared helper runs
  and keeps, code compiled from a string, a standard-library helper such as `linecache`, the catalog, hooks, a
  `functools.cache`d loader): that block and every block the same process builds after it, because those may use
  what was read without reading it again. So a table a shared module caches rebuilds every block that uses it. Some
  later blocks rebuild that need not; that is the safe side.
- **A decorator on the builder:** a decorator written in Python (with `functools.wraps` or without) runs its wrapper
  between the runner's call and the builder, and the wrapper is not the builder's own code. Every read the builder
  makes then counts for later blocks too, because the wrapper could hand one block's result to the next without
  running the builder (a memo keyed on the family, say). A wrapper that keeps nothing rebuilds more than it needs to
  for the same reason: the build cannot tell the two apart. Leave the builder undecorated when its reads should stay
  per-block. A cache on the builder itself (`functools.cache`, `functools.lru_cache`, at any layer of the
  decorators) can give a later block whose arguments compare equal the first block's result without running the
  builder, so a cached builder has no own code and every read it makes counts for later blocks too.
- **Code loaded lazily:** a mod module first imported while a block builds, and what its top-level code reads while
  it is being imported, count the same way: that block and every later block of the process.
- **Read outside any block's build:** every block. That is import time (a catalog loading a table or listing a
  folder, including a listing made by a module's top-level code while it is being imported), the catalog call and
  the hooks.

One limit remains: a value the builder's own body keeps for later blocks (a memo in a default argument or a
function attribute, filled while one block builds and reused by the next) looks like a per-block read. Load such
tables in a helper function or at import time, or name the file in `Runner(inputs=...)`.

## Files the build writes

A file the build writes is still an input when a block reads it. Each read is keyed by its content:

- **When it was read**, for a file the block did not write. If a later block or another parallel worker changes the
  file afterwards in the same run, the reader rebuilds on the next run and picks the new content up. An intermediate
  file (`XX_A` writes it, `XX_C` reads it) therefore reaches its reader at the latest one run later, never stays stale.
- **When it was read at import time**, for a file read or a folder listed outside any block (a catalog's `TABLE =
  open("data.txt").read()`, `PARTS = sorted(os.listdir("parts"))`), wherever the file or folder is, in the mod or not.
  If a block rewrites that file, or writes into that folder, later in the run, in the same process or in another
  `--jobs` worker, the blocks built from the imported table still used what was read, so they rebuild on the next
  run (with the warning below when this process or a worker wrote it).
- **When the block finished**, for a file the block wrote itself. A block that reads a file and rewrites it in place
  (reformatted, or even the same bytes) settles once the content is stable, and rebuilds when you edit the file.
  When the same file was also read at import time, what was read at import wins: the block was built from that too,
  so a block that rewrites its own import-time input rebuilds once more on the next run (with the warning), then
  settles; a user edit of the file rebuilds it again.
- A file more than one parallel worker wrote in the run cannot be told apart, so the blocks of those workers that
  read it rebuild on the next run.

A file the build changes on every run, like a log two blocks append to and read back, therefore rebuilds blocks on
every run. The build notices: when this run's own writes changed a file after a block read it, it prints one line per
file, for example

    warning: Source/Model/shared.log was changed by this build after XX_A read it, so it rebuilds on the next run
    (every run, if the build changes it each time); if it is a build output (a log, a cache), declare it in
    Runner(outputs=[...])

`se2mod build-models` repeats the line after the build. Under `--jobs N` the merge run does not take the build's own
writes for an edit made while it ran, so such a file never fails the build with "inputs changed during the build".

## Running the build more than once in one process

A build started by `se2mod build-models` or `se2mod blender run` is a fresh process for each run, which is the
normal case. A process that runs the build again (a Blender session that runs the script twice, a test process)
keeps modules, their tables and their caches from the earlier run, so it is careful:

- A read the mod's code made in an earlier run and not again in this one (a module-level table, a
  `functools.cache` that is not read again) is recorded as unknown. The blocks built in this run rebuild on the next
  run, and keep doing so in that process; a fresh process then settles them.
- A mod module whose source file changed since the process loaded it still runs the old code, so the blocks that
  import it are rebuilt on every run of that process too.
- Reads made by code outside the mod folder (a test, a helper library outside the mod) during or between runs are
  keyed by what is there when the block finishes, unless the run's build wrote the path.

These rules only add rebuilds. For a release, use a fresh process: `se2mod build-models --release` (below).

## Outputs never count

Only these are never inputs:

- The build's own files: the models, the report, the fragments and the lock.
- **Declared:** `Runner(outputs=["Source/Model/*.log", "cache/**"])`. These are globs relative to the mod folder: `*`
  and `?` stay within one folder, `**` crosses folders, and a glob that names a folder covers everything under it.
  Declare every file the build writes for its own use (a log, a cache): once declared, a hand edit or a write by any
  block rebuilds nothing.

Nothing is remembered between runs: a file the build once wrote is an ordinary input as soon as it is read.

## Temp files

Each run gets a private temp folder: the runner points `TMPDIR`, `TEMP`, `TMP` and Python's `tempfile` at it, and
removes it at the end. Under `se2mod build-models` that folder sits inside the worker's own temp folder. Only that
folder is left out of the reads, never the system temp folder, so a file in `/tmp` that a block reads is an input
whichever way the build was started.

## Declare these yourself

The audit hook cannot see the following reads. Report them with `se2modkit.pipeline.inputs`, or name the files in
`Runner(inputs=fn(spec) -> [paths])` (a folder there means every file under it):

- Blender's C loaders:
  - `inputs.load_image(...)` instead of `bpy.data.images.load`.
  - `inputs.import_scene("fbx", path)` instead of `bpy.ops.import_scene.fbx`. For OBJ, `inputs.import_scene("obj",
    path)` calls Blender 4's `bpy.ops.wm.obj_import`; `bpy.ops.import_scene.obj` no longer exists. STL, PLY, USD and
    Alembic also go to their `bpy.ops.wm.*_import` operators.
  - `inputs.append(blend, "Object", name)` instead of `bpy.ops.wm.append`.
  - For anything else, call `inputs.read(path)`. Given a folder, `inputs.read` walks it like `Runner(inputs=...)`
    does: every file under it and every folder's listing, so a file added, removed or changed anywhere below
    rebuilds the block.
- A bare `Path.exists()`, `os.path.isfile()` or `os.stat()` check. Nothing is opened, so nothing is seen.
- Environment variables outside `SE2MODKIT_*`, `SE2_*` and `<prefix>*`.
- Files read by C extensions without going through Python's `open` (sqlite databases, native libraries).
- Files read before `se2modkit.pipeline` is imported. `se2mod blender run` imports it before the build script, and the
  scaffolded build script imports it first, so this only applies to scripts started some other way.

## Release builds

Incremental builds are for working on the mod. What you publish comes from a release build:

    se2mod build-models MOD --release

It:

1. Refuses when the mod's git tree has uncommitted changes (`git status --porcelain` in the mod folder, untracked
   files included, plus files git ignores under the models folder or `Assets/`, since those would ship), when the
   build script is outside the mod's git repository or not committed, and when anything under the models folder or
   `Assets/` is a symlink. The build is then exactly one commit.
2. Ignores the saved build state: it rebuilds and exports every block (`--force --export`, one windowed Blender).
   Block names are not accepted. `--script` works as for any build.
3. Checks the tree again. If the build changed tracked files (new or different models, the report), it stops and
   asks you to commit them and run it again: the exporter keeps the bytes of every file whose content did not
   change, so the second run leaves the tree clean. It also stops when a model file (`.fbx`, `.gltf`, `.bin`,
   `.model`) under `Assets/<mod>/` (where a publish stages from) is not accounted for. Each geometry copy must sit
   at `Assets/<mod>/<block>/Models/<its path in the block's export folder>` and be byte-identical to that exported
   file; a `.model` must sit in the Models folder of an exported block and be byte-identical to the staged package's
   file at the same place (`paths.staged`, which must be there for a release build), so an older copy, a copy
   swapped between two blocks' same-named files, and the models a renamed or removed block left behind are all
   caught, each named. Install the export with the mod's integration step, remove what it no longer has, commit, and
   run it again.
4. Writes the release stamp, `release.json`, next to the build state (below): the mod, the commit HEAD points at,
   the build script, the se2modkit version, one hash over every block's input key, the time, every file in the
   models folder and every model file under `Assets/<mod>/`, each with its SHA-256.

`se2mod publish` and `se2publish publish` check that stamp before anything is staged: its commit must be HEAD, the
tree must be clean, the stamp must be this mod's, its build script must still be there, nothing may be a symlink,
and the model files and every model file under `Assets/<mod>/` must be the stamped ones and accounted for by the
export. A dry run shows a missing or outdated release build as a blocker plus a warning; a real upload is refused
with one sentence telling you to run `se2mod build-models MOD --release`. This applies to every mod with a
`[tool.se2modkit]` project that has a model build script or model files, whatever the script is named; a mod that
builds its models some other way only gets a warning. See [publish.md](publish.md).

## Where the state lives

Each block's fragment lives in the user cache (`builds/`, or under `$SE2MODKIT_BUILD_STATE`), never in the mod. The
fragment holds the key, the report entry, the reads and the export stamp. The release stamp (`release.json`) sits in
the same folder. Only one build of a mod runs at a time; the
lock sits next to the fragments. Deleting the fragments makes every block stale, which is safe. A parallel worker also
leaves `.written.json` (the paths it wrote) in its own run folder for the merge run; nothing of it is kept.
