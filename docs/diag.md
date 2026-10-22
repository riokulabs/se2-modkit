# se2diag: diagnostics for Space Engineers 2

`se2diag` (extra `diag`, module `se2modkit.diag`) looks inside Space Engineers 2: frame-time hitches, sim speed, GC
pauses, PSO stutter, hot threads and methods, slow world loads, log errors and the state of one block. It was built
for the game running natively on Linux through Pulsar and LinuxCompat; the log and load-time commands also work on
other setups (see "Where it works").

Every game member is looked up by name at run time. After a game update the tool keeps working and lists anything it
could not find under "missing in this game build".

## What it is made of

```
se2diag (Python CLI)  -- host side: /proc, logs, dotnet-counters/-trace/-gcdump, perf
     |
     +- probes/*.csx  -- small read-only C# scripts, sent over MCP to the in-game bridge plugin
                         (the in-game bridge), run inside the game, answer with one JSON line
```

No game plugin of ours is involved: everything in-game runs through an existing in-game bridge plugin (a Pulsar
plugin that runs an MCP server inside the game). `probes.py` packages
a probe by putting the shared helpers in `probes/_prelude.csx` in front, replacing each `//#include _x.csx` line with
that file, and adding parameters as escaped C# string literals.

`se2modkit.diag.bridge` is the bridge client on its own: `bridge.call(tool, args, url=None)` calls one MCP tool and
returns its result; `bridge.token(env)` reads the token (never print it).

## Install

```bash
pip install -e .[diag]
se2diag --help
```

Standard library only. The host profilers are optional: `se2diag tools` shows what is present, and
`se2diag tools --install` runs `dotnet tool install --global dotnet-counters dotnet-trace dotnet-gcdump` into your
home (never sudo; it says so before installing). `perf` comes from the distribution's linux-tools package.

## Commands

Every command prints a plain report; `--json` gives machine-readable output and `--out FILE` also saves the JSON.
Commands that need the game exit with code 3 when it is not reachable; host-side and log commands work without it.

| Command | Needs the game? | What it tells you |
|---|---|---|
| `status` | optional | Game build, runtime, `-loadScripts`, mods, Harmony patch counts by owner, world, pause state, entity counts, GC, JIT, threads, lock contentions; process and system uptime with the Stopwatch-overflow warnings; the newest log's problem counts; the latest Pulsar launch. |
| `perf game [--seconds 10]` | yes | One sample per frame: frame-time distribution, hitches and what coincided with them (GC, JIT, locks, the slowest main-thread stage), server and client sim speed, engine stage timings, GC pause %, allocation rate, thread pools, PSO cache growth. `--from FILE` re-analyses saved samples. |
| `perf host [--seconds 5]` | running process | `/proc` sampling: CPU per named thread, context switches, page faults, I/O and resident memory. |
| `perf counters` / `perf trace` / `perf gcdump --allow-gc` | running process | `dotnet-counters` summary; `dotnet-trace` CPU sampling with Harmony, LinuxCompat and plugin frames marked (`--patches FILE` marks patched vanilla methods); managed heap by type (forces a full GC, hence `--allow-gc`). |
| `perf native` | running process | Linux `perf` by library and symbol. JIT frames get names only with `DOTNET_PerfMapEnabled=1`. |
| `loadtime [LOG] [--compare LOG2]` | no | Load-phase timings from a game log (`LOADING_TIME` markers, definition sets, slow component inits, silent gaps, shader statistics from `_Render12.log`) plus the matching Pulsar launch. `LOG` is a path, a file name in the log folder, `newest` or `proton`. |
| `logs [LOG]` | no | Known-bad pattern counts since process start or `--since-session`; `--grep`, `--pattern`, `--tail SECONDS`, `--strict`. |
| `inspect [look \| id:<id> \| name:<text>]` | yes | One entity in depth: definition, integrity, fracture zones, conditional subparts, render state, inventories, mount points, children and a field dump. |
| `modcheck [--mods NAME:PREFIX,...]` | partly | PASS / FAIL / WARN / SKIP per in-game check that a program can do, plus the log greps. Default mods: those of the project in the current directory. Exit 1 on any FAIL. |
| `shot [label]` | yes | Screenshot into the previews folder (never committed). |
| `stats` | yes, or `--file` | The engine stat tree; `--file X_Stats.log` parses a saved one and `--compare` diffs two. |
| `patches` / `render` | yes | Harmony-patched methods and owners; Render12 caches and render stats. |
| `tools [--install]` | no | Which profilers are available. |
| `probe list \| build NAME \| run NAME` | `run` only | Raw probes for developers (`-p key=value`). |

## Where it works

| Feature | Needs |
|---|---|
| Game logs, `loadtime`, `logs`, `stats --file` | the game's log folder (discovered, `SE2_LOG_DIR` or config `logs_dir`); any OS |
| Proton logs (`loadtime proton`, `--proton`) | Linux and a discovered Proton prefix (`SE2_STEAMAPPS` or config `prefix`), or `SE2_PROTON_LOG_DIR` |
| Pulsar launch timings (`status`, `loadtime`) | a discovered Pulsar (`SE2_PULSAR` or config `pulsar`), or `SE2_PULSAR_LOG`; any OS |
| Host sampling (`perf host/counters/trace/gcdump/native`, process uptime) | Linux (`/proc`) |
| In-game probes | the in-game bridge plugin and its config file (`SE2_MCP_CFG` or config `mcp_cfg`) |

When something is missing the command prints one line, `not supported here: <reason>`, naming how to set it.

## Mod checks

A PASS means the programmatic invariant held for every block
present, not that the visual item is done; looks, motion and feel stay manual. SKIP means there was nothing to check
in this world.

| ID | Check |
|---|---|
| LOG-1 / LOG-2 | "should not be on server compositions" and "Definition that is linked from", since the last world load. |
| TAG-0, TAG-0b | The mod's definitions (named PREFIX*) loaded; its script assemblies (`<Name>.Simulation` / `.Client`) match the `-loadScripts` switch. |
| TAG-SUB | Conditional subparts of the mod's blocks spawn only while Functional; the baked stand-ins are hidden exactly then. |
| TAG-DBL | Nothing doubled: no two same-named children of a block at one place. |
| TAG-7a | Save safety: no server entity carries a `<Name>.*` script component. |
| TAG-REC, TAG-LOG | The mod's recipes after vanilla and together; no error lines naming the mod or its prefix. |

TAG is the mod's prefix without its underscore (`MyMod:MM_` gives MM-0, MM-SUB, ...). Checks specific to one mod's
blocks belong in that mod's own probes.

## Uptime bugs (native Linux)

On native Linux, .NET's `Stopwatch` counts nanoseconds since boot; Windows and Proton count at 10 MHz. `status`
reports two thresholds even with the game closed: the TimeSpan-ms overflow at about 10.7 days of uptime (the game
then crashes on item drops; fixed by LinuxCompat 1.0.9 or later) and the x10^7 overflow after about 15.4 minutes
(a game clock then wraps every 30.7 minutes).

## Probe rules

* Read only: no setters, Harmony, event subscriptions, spawning or file writes. `tests/diag/test_probes.py` lints
  every probe for forbidden calls and enforces an allow-list of reflected invocations.
* Defensive: use the prelude's `Get` / `GetQ` / `St` / `T`; they record what was missing instead of throwing.
* Cheap: long scans `yield return null` between chunks; the bridge plugin gives all scripts together 1000 ms per frame.
* By name: types and members are found by reflection. No decompiled game code is copied into this repository.

## Security

* The bridge plugin's bearer token is read at call time from the file named by `SE2_MCP_CFG` (or user config `mcp_cfg`);
  modkit never guesses that path. The token is never printed, logged, saved, kept on an object or put in an error
  message, and replies are scrubbed of it (tested).
* The bridge client refuses any endpoint that is not plain http to `localhost`, `127.0.0.1` or `::1`, with no user
  info, before reading the token. It ignores proxy variables and never follows redirects. Never forward the
  plugin's port.
* `perf host` reads only `TMPDIR`, `DOTNET_EnableDiagnostics` and `DOTNET_PerfMapEnabled` from the game's
  environment.
* Captures go to the user cache (outside every git repo); screenshots go to the previews folder.

## Configuration

| Variable | Default |
|---|---|
| `SE2_LOG_DIR` | the discovered game log folder (`SE2Env.logs_dir`) |
| `SE2_PROTON_LOG_DIR` | the discovered Proton prefix's `AppData/Roaming/SpaceEngineers2/Temp/Logs` (Linux) |
| `SE2_PULSAR_LOG` | `Modern/info.log` next to the discovered Pulsar |
| `SE2_MCP_CFG` | none: set it (or config `mcp_cfg`) to the bridge plugin's config file |
| `SE2_MCP_URL` | `http://localhost:6789/` |
| `SE2_PROCESS_NAME` | `Modern.bin` |
| `SE2_DIAG_OUT` | `<user cache>/diag` |
| `SE2_DIAG_PREVIEWS` | `$SE2_WORKSPACE/resources/previews/diag`, else `<user cache>/diag/previews` |

## Tests

`python -m pytest -q tests/diag` runs offline on synthetic fixtures (game log, Render12 log, Pulsar info.log,
Stats.log, speedscope, dotnet-counters JSON, gcdump report). A fake loopback MCP server stands in for the plugin.

## Limits

* Per-component update costs are not exposed inside the process; use `perf trace` for method-level costs.
* The `look` pick follows the camera's center ray against block bounding spheres, so check the listed candidates.
* `perf trace` shows Harmony replacement methods only under the runtime's rundown names (usually `DMD<...>`).
