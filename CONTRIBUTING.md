<!-- managed by riokulabs/.github - edit there (templates/), not here -->
# Contributing to se2-modkit

Thanks for looking. Rioku Labs is a small team, and we want more people building mods and tools with us. Contributions of any size are welcome; small, focused pull requests are the quickest to review and merge.

## Before you start

- For a bug, open an issue with the form. For anything bigger than a small fix, open an issue or a Discussion first so we agree on the approach before you write code.
- Check existing issues so the work is not duplicated.

## Building and testing

Python 3.11+ and git.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
scripts/test.sh              # python -m pytest -q
```

The default tests are offline: they need no game, SDK, Blender or Steam. Tests that do are marked (`blender`, `sdk`, `decompiled`, `build`, `sdkbuild`) and skip when the piece is missing.

If the repository has a `scripts/test.sh`, it runs the same checks as CI and the pull request template.

## Security-sensitive behavior

Changes must keep these true: nothing stores, logs or asks for credentials or tokens, nothing uploads or publishes without explicit user approval, and file access stays inside the project. A pull request that weakens one of these will not be merged.

Do not add Keen code, assets or type data here. Test fixtures are synthetic and hand-written.

## Making a change

1. Fork the repository and branch from `main`.
2. Keep one change per pull request.
3. Run the checks in the pull request template.
4. Sign off your commits (below) and open the pull request.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`, for example `fix(kvm): keep the drawer closed while the rack is off`. Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`. Put `!` after the type for a breaking change. The changelog and release notes are generated from these messages with [git-cliff](https://git-cliff.org), so write the summary for a player or user, not for yourself.

Optional: `scripts/install-hooks.sh` turns on local hooks that check the message, keep forbidden files out and catch obvious secrets. CI checks the same things on pull requests.

## No AI attribution

Do not add AI attribution to commits, pull requests or code: no `Co-Authored-By` trailers naming an AI tool, no "generated with ..." notes. You may use any tools you like, but you are the author and you are responsible for what you submit. Pull requests with such marks will be asked to remove them.

## License, CLA and sign-off

The code is under the GNU GPL v3.0 (see `LICENSE`). You keep your copyright in what you contribute.

**Contributor Licence Agreement (CLA).** Before your first pull request can be merged, you sign the [Rioku Labs CLA](https://github.com/riokulabs/.github/blob/cla-v1.0/CLA.md) once, by posting this comment on the pull request:

```
I have read the Rioku Labs CLA and I hereby sign it
```

A bot checks it and records it. One signature covers every Rioku Labs repository. If you contribute for a company, it signs the [entity version](https://github.com/riokulabs/.github/blob/cla-v1.0/CLA-ENTITY.md).

**Why:** you give us a license (not ownership) to use your contribution and to choose its license within fixed limits. The default stays GPL. The CLA lets us make some parts MIT or offer two licenses, change a license or hand a project to a new maintainer without tracking everyone down, and give Keen Software House the license its EULA asks for when a mod is uploaded to Steam Workshop. It never allows closed source: only open source, free software or Creative Commons licenses, plus the license a game platform needs to publish a mod. Your contribution always stays available under the license the project used when you submitted it. Mods stay free and non-commercial, as Keen's EULA requires.

**Under 18?** A parent or legal guardian must also agree, with a comment from their own account. The CLA (section 8.2) has the exact text.

**What is stored:** your GitHub username and numeric ID, the signing comment's ID, the date, and the repository and pull request, in a private repository. No name, email or date of birth. See the CLA's privacy notice (section 7).

**Sign-off (DCO).** We also ask for a [Developer Certificate of Origin](https://developercertificate.org/) sign-off on each commit, which says you wrote the change or have the right to submit it. The CLA covers the rights once per person; the sign-off covers where each commit came from. Add it with `git commit -s`, which appends:

```
Signed-off-by: Your Name <you@example.com>
```

## Conduct

Be decent. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
