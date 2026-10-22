#!/usr/bin/env bash
# managed by riokulabs/.github - edit there (templates/), not here
# Draft a CHANGELOG.md section from Conventional Commits with git-cliff (https://git-cliff.org).
#   scripts/changelog.sh              print the unreleased section
#   scripts/changelog.sh 1.2.0        print it as the section for version 1.2.0 (to paste into CHANGELOG.md)
# It prints; it never edits CHANGELOG.md. Read it, fix the wording, then paste.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
command -v git-cliff >/dev/null || { echo "git-cliff not found. Install: cargo install git-cliff, or your package manager." >&2; exit 1; }
if [ $# -ge 1 ]; then
  git-cliff --unreleased --tag "$1" --strip header
else
  git-cliff --unreleased --strip header
fi
