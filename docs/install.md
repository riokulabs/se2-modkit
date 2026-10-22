# Install

Python 3.11 or newer. The only hard dependency is `numpy`.

## Develop modkit

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .[dev]
python -m pytest -q
ruff check .
```

## Use it from a mod

A mod is not a package. Its `pyproject.toml` only declares tooling and pins a release tag of modkit, never a branch:

```toml
[project]
name = "my-mod-tooling"
version = "0"
requires-python = ">=3.11"
dependencies = ["se2-modkit[publish] @ git+https://github.com/riokulabs/se2-modkit@v0.1.0"]

[tool.se2modkit]
profile = "vanilla"
mod_name = "MyMod"
prefix = "MM_"
gates = ["bundles", "schema", "stages", "style"]
```

`se2mod new NAME` writes this file for you. `[tool.se2modkit]` holds portable values only: absolute paths and drive
letters are rejected. Upgrading is a one-line change of the tag. See [multi-mod-repos.md](multi-mod-repos.md) for repos
that hold several mods.

## Extras

| Extra | Adds | For |
|---|---|---|
| `publish` | no extra packages (Pillow is used for thumbnails when installed); the extra only names the feature, so mods pin `se2-modkit[publish]` | `se2publish`, `se2mod publish` ([publish.md](publish.md)) |
| `diag` | no extra packages | `se2diag` ([diag.md](diag.md)) |
| `preview` | Pillow | `se2mod preview`, `se2mod sheet`, `se2mod pair` |
| `dev` | pytest, pytest-xdist, ruff, Pillow | working on modkit |

Console scripts: `se2mod`, `se2publish`, `se2diag`. Run `se2mod doctor` after installing.

## Blender

Model builds run inside Blender: `se2mod blender run SCRIPT -- ARGS` starts it in the background with `se2modkit` and
the mod's model source importable (`--windowed` opens the window, `--print-only` prints the command).

Blender is found in this order:

| Order | Source |
|---|---|
| 1 | `--blender PATH` |
| 2 | `SE2_BLENDER`, then `BLENDER` |
| 3 | config key `blender` |
| 4 | `blender` on `PATH` |
| 5 | OS default locations: Linux snap, `/usr/bin`, Flatpak exports, a Steam library's `Blender` app; Windows `Program Files\Blender Foundation\Blender *` (newest) and Steam; macOS `/Applications` |

The **VRAGE Tools** Blender add-on (Keen's exporter, GPL-3.0) is an external requirement. Modkit does not bundle it.
Install it from the Space Engineers 2 Mod SDK documentation into the Blender you use; `se2mod doctor` reports whether
a VRAGE add-on is present in Blender's user add-ons.

## User config

TOML file, read once per run.

| OS | Default path |
|---|---|
| Linux, macOS | `$XDG_CONFIG_HOME/se2modkit/config.toml` (`~/.config/se2modkit/config.toml` if unset) |
| Windows | `%APPDATA%\se2modkit\config.toml` |

`SE2MODKIT_CONFIG` names a different file. A missing file is fine; an unparsable one is reported in one sentence. The
cache (type facts, standin tree, baselines) lives under `$XDG_CACHE_HOME/se2modkit` (Windows `%LOCALAPPDATA%\se2modkit\Cache`).

| Key | Meaning | Environment variable |
|---|---|---|
| `steam_root` | Steam install root | `SE2_STEAM_ROOT` (legacy: `SE2_STEAMAPPS`, the root's `steamapps`) |
| `game_dir` | Space Engineers 2 install folder | none |
| `sdk` | Mod SDK folder (or its `GameData/Vanilla/Assets`) | `SE2_SDK_ASSETS` |
| `game_mods_dir` | the game's Mods folder | none |
| `prefix` | Proton prefix of the game (Linux) | none |
| `proton` | Proton build folder or tool name (Linux) | `SE2_PROTON` |
| `pulsar` | Pulsar launcher (native launch) | `SE2_PULSAR` |
| `blender` | Blender executable | `SE2_BLENDER`, `BLENDER` |
| `logs_dir` | game log folder | `SE2_LOG_DIR` |
| `decompiled_dir` | your own decompiled game tree, for `se2mod facts build` and anchor checks | `SE2_DECOMPILED` |
| `vanilla_ref` | read-only folder of vanilla model references for previews and the spark/stage analysis | none |
| `workspace_mods` | folders where a bare mod name is looked up | `SE2_WORKSPACE` (its `mods` folder) |
| `mcp_cfg` | config file of the in-game bridge plugin (`se2diag`) | `SE2_MCP_CFG` (URL: `SE2_MCP_URL`) |
| `[queue]` | budget and runner settings of `se2mod queue` ([render-queue.md](render-queue.md)) | none |

Other variables: `SE2_PROCESS_NAME` (extra process name counted as "the game is running"), `SE2_LIBSTEAM_API`
(Steamworks library for `publish`), `SE2MODKIT_SDK_BUILD=1` (opt in to the real SDK build tests),
`SE2MODKIT_NO_CACHE=1` (skip the universe cache).

### Precedence

For every value: **flag > environment variable > user config > discovery**. A flag exists only for `--steam-root`,
`--sdk`, `--blender`, `--proton`, `--pulsar`; the other values start at the environment variable (or the config key
where there is none). `se2mod env` prints each value with its source (`flag`, `env`, `config`, `discovered`,
`missing`), so you can see what won. A missing piece gives one sentence that names how to set it, never a traceback.

Per-mod variables are `SE2MODKIT_<NAME>`; a mod's old `<PREFIX><NAME>` form is still read as an alias for one release.
