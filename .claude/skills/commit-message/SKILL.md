---
name: commit-message
description: Write a commit message that passes this repo's rules - Conventional Commits, DCO sign-off, no AI attribution. Use whenever creating a commit or squashing commits.
---
<!-- managed by riokulabs/.github - edit there (templates/), not here -->

# Commit message

- Subject: `type(scope): summary`, imperative, under about 70 characters. Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert. `!` after the type for breaking changes.
- Body (optional): why, not what. Wrap near 72.
- Sign off: `git commit -s` adds `Signed-off-by:`.
- No AI attribution of any kind: no `Co-Authored-By` for an AI tool, no "generated with" line. The commit-msg hook and CI reject them.
- One logical change per commit. Do not push; leave that to the maintainer.
