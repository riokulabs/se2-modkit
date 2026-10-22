#!/usr/bin/env bash
# managed by riokulabs/.github - edit there (templates/), not here
# Opt-in: use the hooks in .githooks/ for this clone. Undo with: git config --unset core.hooksPath
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
echo "hooks enabled (core.hooksPath=.githooks). Undo: git config --unset core.hooksPath"
echo "Optional extra: pip install pre-commit && pre-commit install   (adds gitleaks and ruff, see .pre-commit-config.yaml)"
