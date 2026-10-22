# se2-modkit

Shared build tooling for Space Engineers 2 mods: environment discovery, a Blender-to-game model pipeline, the Mod SDK
build, validation, tests, a Steam Workshop publisher and diagnostics. It runs on Linux (native or Proton) and Windows.

Status: early. Tested against game 4.0.0.3155 (`src/se2modkit/compat.toml`); an untested game version gives a
warning and a pointer to `se2mod deps check`.

No game files are part of this repository. Modkit reads the game and the Mod SDK from your own installation.

## Install

Python 3.11 or newer. A mod pins a release tag in its tooling `pyproject.toml`:

```toml
dependencies = ["se2-modkit[publish] @ git+https://github.com/riokulabs/se2-modkit@v0.1.0"]
```

Or install a tag directly:

```bash
pip install "se2-modkit @ git+https://github.com/riokulabs/se2-modkit@v0.1.0"
se2mod doctor
```

To work on modkit itself:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
python -m pytest -q
ruff check .
```

Extras, Blender, the user config and environment variables are in [docs/install.md](docs/install.md).

## Commands

Three console scripts: `se2mod` (everything below), `se2publish` (the Workshop publisher) and `se2diag`
(diagnostics). Run `se2mod COMMAND --help` for details.

| Group | Commands |
|---|---|
| Environment | `se2mod env`, `se2mod doctor`, `se2mod status`, `se2mod sdk`, `se2mod wait` |
| Mods | `se2mod new`, `se2mod list`, `se2mod build`, `se2mod test`, `se2mod snapshot`, `se2mod style`, `se2mod report` |
| Running the game | `se2mod play`, `se2mod off`, `se2mod on` |
| Blender and previews | `se2mod blender`, `se2mod build-models`, `se2mod check`, `se2mod preview`, `se2mod sheet`, `se2mod pair`, `se2mod kit` |
| GPU jobs | `se2mod queue` (run renders and exports within a power budget), `se2mod gpu pick` |
| Definitions | `se2mod defs`, `se2mod chain`, `se2mod show`, `se2mod copy`, `se2mod override`, `se2mod clone-block` |
| Game updates | `se2mod deps`, `se2mod facts` |
| Publishing | `se2mod publish` |
| Helpers | `se2mod outline`, `se2mod review-pack` |

## Documentation

| Page | Contents |
|---|---|
| [docs/install.md](docs/install.md) | Install, pinning a tag, extras, Blender, user config, environment variables and precedence |
| [docs/working-on-a-mod.md](docs/working-on-a-mod.md) | Scaffolding, model builds, previews and tests |
| [docs/linux.md](docs/linux.md) | Proton prefix, Pulsar, Wine paths, known hazards |
| [docs/windows.md](docs/windows.md) | What is expected to work, what is untested, the manual checklist |
| [docs/when-the-game-updates.md](docs/when-the-game-updates.md) | `se2mod deps`, baselines, `compat.toml`, gates, type facts |
| [docs/multi-mod-repos.md](docs/multi-mod-repos.md) | Several mods in one repo, the MOD argument, CI matrix |
| [docs/render-queue.md](docs/render-queue.md) | Job queue for Blender renders and exports, the power budget, GPU picking |
| [docs/incremental-builds.md](docs/incremental-builds.md) | What an incremental model build tracks and what to declare |
| [docs/publish.md](docs/publish.md) | Steam Workshop publisher; read its risk note before a real upload |
| [docs/diag.md](docs/diag.md) | Diagnostics toolkit |
| [docs/style-guide/](docs/style-guide/00-house-rules.md) | House style guide; its numbers live in the style profiles that `se2mod style check` measures against |
| [docs/release-checklist.md](docs/release-checklist.md) | Checks that run locally, and tagging a release |

## License

GPL-3.0-only. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md). Space Engineers 2 is a product of Keen
Software House; this project is not affiliated with or endorsed by Keen.

## Contributing

Contributions are welcome. They need a DCO sign-off (`git commit -s`) and the Rioku Labs CLA; see
[CONTRIBUTING.md](CONTRIBUTING.md). Questions: [SUPPORT.md](SUPPORT.md). Security issues: [SECURITY.md](SECURITY.md).
