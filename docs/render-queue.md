# Render queue

Blender renders and exports can keep a GPU at full load for minutes. Two of them at once on two cards can draw more
power than a workstation's UPS supplies. `se2mod queue` runs such jobs for you: one after another, or side by side
when their combined load fits a power budget you set. Scripts, tools and coding agents submit jobs to the queue
instead of starting Blender themselves.

```bash
id=$(se2mod queue submit --name turntable --load heavy -- \
     blender -b scene.blend --gpu-backend vulkan --gpu-device {gpu} -P render.py)
se2mod queue wait "$id" --timeout 3600    # exits with Blender's own exit code
```

## Commands

| Command | What it does |
|---|---|
| `se2mod queue submit [options] -- CMD ARGS...` | Queue a command, print its job ID, start the runner if it is not running |
| `se2mod queue wait ID [--timeout S]` | Block until the job ends; exit with its exit code (124 on timeout, 125 if cancelled). Default timeout 3600 s |
| `se2mod queue status` (or `list`) | One line per job: ID, name, load, state, GPU, elapsed time, exit code; a running job also shows its last log line. `--all` shows every kept job |
| `se2mod queue log ID [-f]` | Print a job's output; `-f` keeps printing until the job ends |
| `se2mod queue cancel ID... \| --all` | Cancel jobs: a queued job is dropped, a running one gets SIGTERM on its process group and SIGKILL 10 s later |
| `se2mod queue run [--idle-exit S]` | Run the runner in the foreground (for debugging; `submit` normally starts it) |

`submit` options:

| Option | Meaning |
|---|---|
| `--name N` | Label for `status` (default: the program name) |
| `--load heavy\|medium\|light\|N` | Power load: heavy 1.0 (default), medium 0.5, light 0.25, or any positive number |
| `--gpu auto\|none\|N` | `auto` (default): the least busy GPU that has room; `none`: no GPU choice; `N`: that Vulkan device |
| `--cwd DIR` | Working directory (default: the current one) |
| `--timeout SECONDS` | Stop the job after this long; `wait` then exits 124 |
| `--env K=V` | Extra environment variable for the job (repeatable) |

Everything after the first `--` is the command, taken verbatim (a `--` of its own, as in
`blender -b --python build.py -- all`, stays part of it). In the command, `{gpu}` is replaced with the chosen Vulkan device index, which is also exported as
`SE2_GPU_DEVICE`. With no GPU choice both are `0`. Every command also takes `--queue-dir DIR` before the subcommand
(default `<cache>/queue`, see [install.md](install.md) for the cache folder).

Windowed commands work too (a Blender export without `-b`): the job gets the display variables of the shell that
submitted it (`DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`) and its
`PATH`, on top of the runner's environment. Nothing else from the submitting shell is stored, so tokens in your
environment never land in the queue folder; pass what a job needs with `--env`.

## Loads and the budget

The runner starts jobs in submission order while their combined load fits two limits:

- the total budget, `[queue] budget` (default 1.0), and
- the per-GPU budget, `[queue] gpu_budget` (default 1.0).

With the defaults a heavy job always runs alone, two medium jobs or four light jobs can share, and a job larger than
the budget runs alone.

A job that does not fit keeps its place. A later, smaller job may start ahead of it only when that cannot delay it:
the waiting job's load stays reserved, so while a heavy job waits for running jobs to finish nothing passes it, and a
steady stream of light jobs cannot starve a bigger one.

Load the queue did not start counts too:

- each running Blender that the queue did not start counts as a heavy job on an unknown GPU;
- the running game counts as a heavy job.

Under the default budget the queue therefore waits while you play or while someone runs Blender by hand. Set
`count_outside = false` to turn this off.

## Choosing a GPU

With `--gpu auto` the runner samples each card's load for a second and puts the job on the least busy card whose
queue load still has room. Load the queue itself placed on a card counts as busy (1.0 is 100 %), because a job started
a moment ago does not show in the sampled load yet. Ties go to the card with less VRAM in use.

`se2mod gpu pick` prints the same choice without the queue: the least busy GPU's Vulkan device index, or 0 when
nothing can be measured. `--busy` exits 1 when a Blender is already running, `-v` prints every card's load.

```bash
blender -b --gpu-backend vulkan --gpu-device "$(se2mod gpu pick)" ...
```

GPU sampling is Linux only: the device list comes from `vulkaninfo` and the load from sysfs
(`/sys/bus/pci/devices/<address>/gpu_busy_percent`, written by the amdgpu driver). On Windows, on macOS, or when no
card reports a load, `--gpu auto` means no GPU choice (`{gpu}` becomes 0); the budget still applies, so heavy jobs
still run one at a time.

## Configuration

In the user config ([install.md](install.md)):

```toml
[queue]
budget = 1.0          # total load that may run at once
gpu_budget = 1.0      # load that may run at once on one GPU
idle_exit = 600       # seconds the runner stays up with nothing queued or running
count_outside = true  # count other Blender processes and the game as heavy jobs
```

A machine with two cards and enough power for both can set `budget = 2.0`: two heavy jobs then run at once, one per
card.

## The runner

`submit` starts the runner in the background when none is running. It keeps running after the submitting shell exits,
writes one line when a job starts (with its GPU) and one when it ends to `<queue folder>/runner.log`, and exits after
`idle_exit` seconds with nothing to do. Only one runner runs at a time: it holds `runner.lock` (which contains its PID)
for its whole life; a second one exits at once.

Jobs run in their own process group. Stopping the runner (SIGTERM or Ctrl-C on `se2mod queue run`) stops its jobs;
on Linux a job also gets SIGTERM if the runner is killed outright. A job still marked running when a new runner starts
is reported as `lost`.

## Storage

`<cache>/queue/` holds one JSON file and one log per job under `jobs/`, plus `queue.lock`, `runner.lock` and
`runner.log`. Every change to a job file happens under a file lock (flock on Linux and macOS, msvcrt on Windows), so
several submitters and the runner can work at once. Finished jobs are deleted after seven days.

## Job states

| State | Meaning | `wait` exits with |
|---|---|---|
| `queued` | waiting for room in the budget | (keeps waiting) |
| `running` | started; `status` shows `running*` once a cancel was requested | (keeps waiting) |
| `done` | exited 0 | 0 |
| `failed` | exited non-zero (127: the program was not found; 128+N: killed by signal N) | the job's exit code |
| `timeout` | stopped by `--timeout` | 124 |
| `cancelled` | cancelled | 125 |
| `lost` | its runner stopped while it ran | 1 |

This queue is the first step toward rendering across several GPUs at once
([#6](https://github.com/riokulabs/se2-modkit/issues/6)).
