# Linux

Two ways to run the game: through Steam (Proton) or natively through Pulsar. The Mod SDK tools (builder, editor) are
Windows programs and run under Proton.

## Proton prefix sharing

The SDK tools run in **the game's own Proton prefix** (`steamapps/compatdata/<game app id>/pfx`) so they see the game
install, its Documents folder and its mods folder exactly as the game does. Modkit finds the prefix in the library that
holds the game, or from config `prefix`.

| Item | How it is chosen |
|---|---|
| Steam root, libraries | `libraryfolders.vdf`; the game and the SDK may sit in different libraries; Flatpak Steam is found |
| Proton build | `--proton` / `SE2_PROTON` / config `proton` (a folder, or the tool name Steam shows), else the build Steam maps to the game, else the newest installed one |
| Game mods folder | Documents/SpaceEngineers2/Mods inside the prefix, or config `game_mods_dir` |

`se2mod env` lists the result. If Steam maps the game to a Proton build that is not installed, the error names it.

## Native launch (Pulsar)

`se2mod play --native` starts the native game through Pulsar. Pulsar is found at `$XDG_CONFIG_HOME/Pulsar/Modern.bin`
(`~/.config/Pulsar/Modern.bin`), or from `--pulsar` / `SE2_PULSAR` / config `pulsar`. `se2mod play` without
`--native` launches through Steam. `--print-only` shows the command without running it.

`se2mod off` detaches the workspace mods so a normal Steam launch sees only Workshop mods; `se2mod on` attaches them
again; `se2mod status` shows the state. These use junctions/symlinks, not copies.

Native Linux issues belong to the Linux compatibility project for the game, not to Pulsar.

## Wine path translation

The SDK builder runs under Wine and needs Wine paths. Modkit reads the prefix's `dosdevices/` links to learn the drive
letters, then translates a host path with the drive whose root matches longest (backslashes, non-ASCII and spaces
intact). Host paths that no drive covers are an error that names the path; add a drive link in the prefix or move the
project.

## Known hazards

| Hazard | What modkit does |
|---|---|
| `texconv` hangs under Wine | Modkit never runs `texconv.exe` under Wine. A native `texconv` on `PATH` is used if present, with a 60 s limit; otherwise textures are written as PNG only and the command says so |
| Building while the game runs corrupts or locks files | `se2mod build` and mounting commands refuse while the game is running, **and** when the process list cannot be read (fail closed). `--assume-game-closed` overrides the second case only |
| Interrupted SDK build | The project file, `Scripts/` and `Content.pruned/` are restored on failure and on Ctrl-C |
| Hung Wine tools | Kill tools you started (`wineserver -k` for that prefix); do not leave them running |
| Uptime bug of the native game | The native game can crash on item drops after about 10 days of uptime; restart it |
| Untested game version | A warning plus a pointer to `se2mod deps check`; the build proceeds. `--strict-game` fails instead |

`se2mod wait game-closed` waits for the game to exit, with a timeout; use it in scripts instead of polling `pgrep`.
`se2mod sdk standin` links the game assemblies where the SDK projects look for them (into the user cache).
