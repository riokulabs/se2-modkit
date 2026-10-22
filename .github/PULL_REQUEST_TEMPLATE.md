<!-- managed by riokulabs/.github - edit there (templates/), not here -->
## What this changes

<!-- What and why, in a few sentences. Link the issue: "Fixes #123". -->

## How you checked it

<!-- Commands you ran, what you saw. -->

## Checklist

- [ ] `scripts/test.sh` passes (pytest, offline: nothing needs the game, the SDK, Blender or Steam)
- [ ] Nothing in the change reads, stores, logs or asks for Steam credentials or tokens
- [ ] Nothing uploads or publishes without explicit user approval
- [ ] No machine-specific path (home directories, Steam library paths, Proton builds, drive letters) is a default; discovery rules only
- [ ] No game files, decompiled code, Keen type data or captured baselines are committed (`python3 scripts/check_repo_guard.py`)
- [ ] README updated if a command, environment variable or supported game version changed
- [ ] Commits are signed off (`git commit -s`, see CONTRIBUTING.md)
- [ ] I have signed the Rioku Labs CLA (the bot asks on your first pull request; see CONTRIBUTING.md)
