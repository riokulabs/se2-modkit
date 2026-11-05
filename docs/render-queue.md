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
| `se2mod queue cancel ID... \| --all` | Cancel jobs: a queued job is dropped, a running one gets SIGTERM on its process group and SIGKILL 10 s later (two minutes for a live Blender) |
| `se2mod queue clear` | Delete finished jobs and their logs; jobs that ended in the last 10 s stay, so a tool waiting on one still sees it |
| `se2mod queue pause` / `resume` | Pause the queue (running jobs carry on, nothing new starts) or resume it |
| `se2mod queue move ID --top \| --bottom \| --up \| --down \| --to N` | Move a waiting job within the queue (`--to 1` is next to start) |
| `se2mod queue blender [FILE.blend] [--gpu auto\|N]` | Start a live, windowed Blender as a queue job, next in line (see below) |
| `se2mod queue gui` | Open a window on the queue and the GPUs (see below) |
| `se2mod queue run [--idle-exit S]` | Run the runner in the foreground (for debugging; `submit` normally starts it) |

`submit` options:

| Option | Meaning |
|---|---|
| `--name N` | Label for `status` (default: the program name) |
| `--load heavy\|medium\|light\|N` | Power load: heavy 1.0 (default), medium 0.5, light 0.25, or any positive number |
| `--gpu auto\|none\|N` | `auto` (default): the least busy GPU that has room; `none`: no GPU choice; `N`: that Vulkan device |
| `--cwd DIR` | Working directory (default: the current one) |
| `--timeout SECONDS` | Stop the job after this long; `wait` then exits 124 |
| `--priority` | The priority lane: start before plain waiting jobs, never beside a running heavy one |
| `--env K=V` | Extra environment variable for the job (repeatable) |

Everything after the first `--` is the command, taken verbatim (a `--` of its own, as in
`blender -b --python build.py -- all`, stays part of it). In the command, `{gpu}` is replaced with the chosen Vulkan device index, which is also exported as
`SE2_GPU_DEVICE`. With no GPU choice both are `0`. Every command also takes `--queue-dir DIR` before the subcommand
(default `<cache>/queue`, see [install.md](install.md) for the cache folder).

Windowed commands work too (a Blender export without `-b`): the job gets the display variables of the shell that
submitted it (`DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`) and its
`PATH`, on top of the runner's environment. Nothing else from the submitting shell is stored, so tokens in your
environment never land in the queue folder; pass what a job needs with `--env`.

A windowed build script closes Blender when it finishes only if `se2mod` started that Blender (`se2mod blender run`,
`se2mod build-models`). A queued command that starts a windowed `blender` itself needs `--env SE2MODKIT_OWNS_BLENDER=1`
for the job to end; without it the window stays open until you close it.

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

`submit` starts the runner in the background when none is running. Where a systemd user manager runs (most Linux
desktops), the runner is its own user service, `se2mod-queue-<hash>.service`, so its jobs don't share a cgroup with the
app that submitted them: restarting that app neither stops the runner nor makes the kernel move days of the jobs' file
cache to another cgroup, which can stall disk writes for many minutes. Elsewhere it is a detached process. It keeps
running after the submitting shell exits,
writes one line when a job starts (with its GPU) and one when it ends to `<queue folder>/runner.log`, and exits after
`idle_exit` seconds with nothing to do. Only one runner runs at a time: it holds `runner.lock` (which contains its PID)
for its whole life; a second one exits at once.

Jobs run in their own process group. Stopping the runner (SIGTERM or Ctrl-C on `se2mod queue run`) stops its jobs;
on Linux a job also gets SIGTERM if the runner is killed outright. A job still marked running when a new runner starts
is reported as `lost`.

Waiting jobs start in submit order unless one was moved: `move` gives every waiting job a rank, and new jobs still
join at the end. Jobs submitted with `--priority` (such as `se2mod look`) form a lane ahead of the plain jobs; `move`
orders jobs within their lane. A priority job is next in line, not a pre-emption: a running heavy job finishes first.
The lane is meant for short jobs: a steady stream of them keeps a waiting heavy job waiting.
`se2mod build-models MOD BLOCK... --per-block` queues one heavy job per block (`export:<mod>:<block>`) instead of
building them in one process, so a preview can run between two blocks instead of after the whole batch. While the
queue is paused (`<queue folder>/paused` exists), the runner keeps running jobs going and stays up, but starts nothing.

## Live Blender sessions

`se2mod queue blender` queues the configured Blender with a window, on the GPU the queue picks, with online access on
so the Blender Lab MCP add-on starts its server (it autostarts by default). The session goes to the front of the queue
but still waits for running jobs; once it starts it holds the heavy slot, so renders and exports wait until it closes.
Only one session can be queued or running at a time, because each would want the same MCP port.

A session that has had no changes for 20 minutes (`SE2_LIVE_IDLE` seconds; `0` turns this off) saves and closes
itself, but only while another job is waiting: a forgotten Blender then doesn't hold the GPU from everyone, and one
you are only looking around in is never closed when nobody else needs the GPU.

Close it by hand, or cancel its job: the queue's SIGTERM makes Blender save the open file first (a file that was never
saved goes to `~/.cache/se2modkit/live-blender/`) and quit. The queue gives that save up to two minutes before it
kills Blender (other jobs get 10 s). It saves even when Blender doesn't flag the file as changed,
because edits made through Python (the MCP add-on) don't flag it; the save keeps Blender's `.blend1` backup. A tool that needs Blender open waits for the job to run,
then talks to it through the MCP add-on, and the maintainer can watch the window as it works.

## The queue window

`se2mod queue gui` opens a small Qt window that refreshes every second. It needs PySide6: `pip install
'se2modkit[gui]'`.

- **GPUs:** each card's load, VRAM in use, edge and hot-spot temperature, power against its cap, fan speed, and which
  queue jobs run on it (Linux with the amdgpu driver; elsewhere the panel says the readings aren't available).
- **Jobs:** running jobs, then waiting jobs in the order they will start (`waiting #1` is next), with load, GPU,
  elapsed time, exit code and a running job's last output line. **Show finished** adds the last 15 finished jobs (the
  window remembers the choice); **Clear finished** deletes them, as `se2mod queue clear` does.
- **Controls:** start or stop a live Blender session, pause or resume the queue, move the selected waiting job to the top, up, down or to the bottom
  (Ctrl+Home, Ctrl+Up, Ctrl+Down, Ctrl+End), and cancel the selected job (Delete; a running one asks first).
- **Log:** the selected job's output, following it as it grows.

The window works on the queue folder like the command line does, so it can stay open while tools submit and wait.

## Storage

`<cache>/queue/` holds one JSON file and one log per job under `jobs/`, plus `queue.lock`, `runner.lock`,
`runner.log` and, while the queue is paused, `paused`. Every change to a job file happens under a file lock (flock on Linux and macOS, msvcrt on Windows), so
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

## Leasing a shared file

When two people or sessions both edit one file, such as a shared `.blend`, `se2mod lock take <file>` leases it to you
for 4 hours (`--ttl 30m`, `--owner NAME`; the owner defaults to `$SE2_SESSION`, then your user name). Taking a file
someone else holds exits 1 and names them. `se2mod lock release <file>` drops your lease, and `se2mod lock list`
shows every lease still in force. Leases are advisory: they don't block a save, so take one before you edit and
release it when you're done.
