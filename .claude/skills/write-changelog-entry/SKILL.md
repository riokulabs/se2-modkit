---
name: write-changelog-entry
description: Write a CHANGELOG.md entry in Keep a Changelog format from commits or a diff, in plain user-facing wording. Use when asked to update the changelog, write release notes, or describe what changed.
---
<!-- managed by riokulabs/.github - edit there (templates/), not here -->

# Write a changelog entry

Format (this is what tools that read `CHANGELOG.md` expect, so keep it exact):

```
## [1.2.0] - 2026-10-20

### Added
- ...
### Changed
- ...
### Fixed
- ...
```

Rules:

- Heading is `## [version] - date`. New work goes under `## [Unreleased]` until released.
- Groups: Added, Changed, Deprecated, Removed, Fixed, Security. Leave out empty ones.
- Write for the person using the project, not the developer. Say what they will notice. No commit hashes, no internal task names.
- Start from `scripts/changelog.sh` (git-cliff output from Conventional Commits), then rewrite anything vague.
- Plain words, short lines. No hype.
