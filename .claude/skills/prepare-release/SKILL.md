---
name: prepare-release
description: Prepare a release of se2-modkit - version, changelog section, tag command. Use when asked to cut, prepare or draft a release. Prepares only; never tags, pushes or publishes.
---
<!-- managed by riokulabs/.github - edit there (templates/), not here -->

# Prepare a release of se2-modkit

Do these in order and stop before anything leaves the machine.

1. Working tree clean, on `main`, `scripts/test.sh` passes.
2. Pick the version (Semantic Versioning) from the commits since the last tag: `git log --oneline $(git describe --tags --abbrev=0)..HEAD`.
3. Draft the changelog section: `scripts/changelog.sh <version>`. Edit the wording for users, then add it under the right heading in `CHANGELOG.md` (format: `## [x.y.z] - YYYY-MM-DD`, Keep a Changelog groups).
   Check `src/se2modkit/compat.toml` and the README still match the game build you tested.
4. Commit as `chore(release): <version>`.
5. Show the maintainer the tag command (`git tag -s v<version>` or `git tag -a`) and stop. The maintainer pushes the tag; pushing a `v*` tag makes CI draft a GitHub release from the notes.

Never push, tag or publish yourself.
