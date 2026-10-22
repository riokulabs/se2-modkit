# Mod repos: single-mod, multi-mod and library mods

A repo holds one mod or a whole series of mods. Both use the same layout; a single-mod repo is the one-mod case.

## Single-mod repo

```bash
se2mod new "My Mod" [--prefix MM_] [--profile vanilla]
```

creates `My Mod/` (in the first `workspace_mods` folder, else the current directory) and runs `git init` in it
(no commit):

| Path | What |
|---|---|
| `My Mod.vrgproj`, `Assets.meta`, `Content.meta`, `ModProject.modhub-metadata`, `Assets/` | the files the Mod HUB writes; VRage stamps come from the installed Mod SDK |
| `pyproject.toml` | a tooling manifest (the mod is not a package): `se2-modkit[publish] @ git+https://github.com/riokulabs/se2-modkit@v<version>`, the release tag of the modkit that made it; `[tool.se2modkit]` with `mod_name`, `profile`, `prefix`; pytest config |
| `Source/Model/<pkg>/__init__.py`, `hooks.py`, `catalog.py` | model source skeleton; `<pkg>` is the prefix in lower case (`MM_` -> `mm`) |
| `Source/Model/build_<name>.py` | the build script for `se2mod blender run` (see below) |
| `Tests/conftest.py`, `Tests/test_smoke.py` | `python -m pytest` passes in the new repo |
| `README.md`, `CHANGELOG.md`, `LICENSE` (GPL-3.0), `.gitignore`, `.gitattributes` (Git LFS for binaries) | |

`.gitignore` covers build output (`Content/`, the builder caches), what an interrupted `se2mod build` leaves behind
(`.Scripts.hidden-for-build/`, `Content.pruned/`, `Content.tmp/`), every `preview/` folder (renders stay out of git),
C# and Python build folders, caches and `.venv/`.

The pin always names a release tag, never a branch. A development build of modkit has no tag yet: `se2mod new` then
prints a warning, and the pin must be bumped to a release tag before CI can install it.

The prefix defaults to the name's initials (`Space Tug` -> `ST_`). It also names the model package, so every mod in
a repo needs its own.

`build_<name>.py` runs the modkit model pipeline (`se2modkit.pipeline.runner.Runner`) with the mod's `hooks` and
`catalog`. Until the pipeline ships in modkit it prints that it needs a later se2-modkit version and exits 2.

### Org-managed files

CI, contribution guides, issue forms and the other shared files come from an org template repo with a
`tools/sync-github-structure.py` script and one `repos/<name>.toml` per repo. Without `--org-sync`, `se2mod new`
prints how to run it. With it:

```bash
se2mod new "My Mod" --org-sync ../org-template [--org-name my-mod]
```

checks first, before creating anything, that `repos/my-mod.toml` exists, its `target` points at the new repo, and no
file the scaffold writes is also an org template file unless `keep` lists it; then it runs the script as a dry run
and, when that is clean, with `--apply`. `.gitattributes` comes from the org template then, unless `keep` lists it.

## Multi-mod repo

`pyproject.toml` declares every mod in its own table; shared settings sit in `[tool.se2modkit]` and a mod's table
overrides them:

```toml
[tool.se2modkit]
profile = "vanilla"

[tool.se2modkit.mods.Core]
path = "mods/Core"
prefix = "CO_"

[tool.se2modkit.mods.Drives]
path = "mods/Drives"
prefix = "DR_"
```

Each mod is its own SE2 project (own `.vrgproj` and ProjectID) with its own `CHANGELOG.md`, so the publisher can
release it as its own Workshop item.

Start one from an empty `[tool.se2modkit]` table and add mods into it:

```bash
se2mod new Core --prefix CO_ --into path/to/repo
se2mod new Drives --prefix DR_ --into path/to/repo
```

`--into` writes `mods/<name>/` (Mod HUB files, model skeleton, tests, README, CHANGELOG) and appends the mod's table.
It refuses a name or prefix the repo already has, and leaves the repo untouched when it refuses.

A single-mod repo becomes a multi-mod repo only on request:

```bash
se2mod new Drives --into path/to/my-mod-repo --convert
```

moves `[tool.se2modkit]` (and its sub-tables) to `[tool.se2modkit.mods.<old name>]` with `path = "."`, then adds
the new mod under `mods/`. The repo root stays a mod itself: commands run anywhere in the repo outside `mods/<name>/`
(including `mods/` itself) act on the root mod, so name the mod you mean. Without `--convert` it is an error. Run a nested
mod's tests with `se2mod test <name>` or `python -m pytest mods/<name>/Tests`.

## The MOD argument

Every command that acts on a mod (`build`, `override`, `copy`, `clone-block`, `play`, `report`, `snapshot`,
`style check`, `test`, `blender run --mod`) takes MOD as a name or a path:

1. a mod name of the `[tool.se2modkit]` project at or above the current directory;
2. a path to a mod directory (or any directory inside one);
3. a mod name in a `workspace_mods` folder (user config, or `$SE2_WORKSPACE/mods`).

Without MOD, a command uses the mod the current directory is in (the innermost one when mods nest). At a multi-mod
repo root it stops with "Which mod?" and the list of names; it never picks the first mod.

## CI

Run checks per mod in a matrix, then the cross-mod checks once. `docs/examples/ci-matrix.yml` is a starting point:
it lists the mods from `pyproject.toml`, runs `se2mod test MOD --fast` and `se2mod style check MOD` for each on
Linux and Windows, and checks that no two mods share a prefix.

## Library mods

```bash
se2mod new "Shared Core" --library [--into REPO]
```

scaffolds a library mod: the Mod HUB files, an empty `Assets/` for shared data definitions, a `Scripts/` folder for
shared C# code, a smoke test and a README. It has no model skeleton. How one SE2 mod depends on another and whether
scripts can call across mods is still being worked out in the core-library spike; the README says so, and the
scaffold will follow what the spike finds.
