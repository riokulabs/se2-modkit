# Release checklist

Some checks need the game, the Mod SDK, Blender or a decompiled tree, so they run on the maintainer's machine and not
in CI. Run all of them before tagging. Build and export only while the game is closed.

## Local-only runs

| Run | Command | Pass |
|---|---|---|
| Offline suite | `python -m pytest -q -n auto` and `ruff check .` | no failures, skip count not up |
| Blender model tests | `python -m pytest -q -m blender` | no failures |
| SDK gates | `python -m pytest -q -m "sdk or decompiled"` (facts built first: `se2mod facts build`) | no failures |
| SDK content build | `SE2MODKIT_SDK_BUILD=1 python -m pytest -q -m sdkbuild` | no failures, tree restored |
| Each shipped mod, full suite | `se2mod test MOD` in every mod that pins modkit | same counts as before |
| Determinism gate | `se2mod snapshot MOD --out FILE`, build, `se2mod snapshot MOD --compare FILE` | exit 0, identical outputs |
| Windows manual pass | [windows.md](windows.md) | every box ticked or explained |
| Fresh clone | clone a mod without LFS, new venv, install modkit from the local tag, run its offline tests | 0 failures, 0 collection errors |

## Changelog

`scripts/changelog.sh VERSION` prints the section for VERSION from Conventional Commits (needs `git-cliff`). It prints
only: read it, fix the wording, paste it into `CHANGELOG.md`.

## Tag

Tag only after the Windows manual pass is recorded in `docs/windows.md`.

1. Version in `src/se2modkit/__init__.py` and `compat.toml` match what was tested.
2. `git tag vX.Y.Z` locally. Never move a tag that exists on a remote.
3. Mods bump their pin to the new tag in their own commit.

## Before publishing

1. Run `python -m pytest -q tests/test_no_leaks.py`. It scans every tracked or untracked-not-ignored file for machine
   paths (a home directory, a distro Steam folder, a Proton build name, Windows drive letters) and the bridge plugin's
   config file name. When `tests/.leak-terms.local` exists (git-ignored, one regular expression per line), its terms
   are scanned too.
2. Confirm no file came from a decompiled tree, a vanilla reference folder or a baseline capture:
   `git ls-files | grep -i -E 'decompiled|vanilla-ref|baseline' | grep -v '\.py$'` is empty, and the type-facts JSON is not tracked.
3. Confirm no binaries: nothing matching `*.dll`, `*.exe`, `*.so`, `*.pdb`, `*.dds`, `*.fbx`, `*.mwm`, archives; the repo
   guard (`scripts/check_repo_guard.py`) passes with its size limit.
4. `git log --format=%B` contains no AI attribution trailer.
