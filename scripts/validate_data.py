#!/usr/bin/env python3
# managed by riokulabs/.github - edit there (templates/), not here
"""Check that tracked JSON data files (globs in .repo-guard.toml: json_globs) and all YAML under .github parse.

JSON is checked with the standard library. YAML needs PyYAML and is skipped with a notice when it is missing.
"""
import fnmatch
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
globs = tomllib.loads((ROOT / ".repo-guard.toml").read_text()).get("json_globs", [])
tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split("\n")
bad = checked = 0
for rel in filter(None, tracked):
    if any(fnmatch.fnmatch(rel, g) for g in globs):
        checked += 1
        try:
            json.loads((ROOT / rel).read_text(encoding="utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            print(f"{rel}: invalid JSON: {e}", file=sys.stderr)
            bad += 1
try:
    import yaml
except ImportError:
    print("PyYAML not installed: skipping YAML check")
else:
    for rel in filter(None, tracked):
        if rel.startswith(".github/") and rel.endswith((".yml", ".yaml")) or rel == ".pre-commit-config.yaml":
            checked += 1
            try:
                yaml.safe_load((ROOT / rel).read_text())
            except Exception as e:  # noqa: BLE001
                print(f"{rel}: invalid YAML: {e}", file=sys.stderr)
                bad += 1
print(f"validated {checked} file(s), {bad} problem(s)")
sys.exit(1 if bad else 0)
