"""`se2mod queue gui`: a small window on the job queue and the GPUs.

It shows each GPU's load, VRAM, temperature and power, which queue jobs run on it, and the jobs themselves: running,
waiting in the order they will start, and the last finished ones. It can pause and resume the queue, move a waiting
job up or down, cancel jobs, and show the selected job's log as it grows. Everything goes through the queue folder,
the same as the command line, so the window and the agents can act on the queue at the same time.

The window is Qt (PySide6, the optional `gui` extra) in its Fusion style with the desktop's colors, in
`_queue_qt.py`. The row and label formatting below is plain Python and is tested without a display.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import gpu as gpumod
from . import jobqueue as Q
from . import progress

REFRESH_MS = 1000
FINISHED_SHOWN = 15
LOG_LINES = 200
COLUMNS = ("id", "name", "load", "state", "gpu", "elapsed", "exit", "last")
HEADINGS = {"id": "ID", "name": "Name", "load": "Load", "state": "State", "gpu": "GPU", "elapsed": "Elapsed",
            "exit": "Exit", "last": "Last output"}


# --- plain formatting (no Tk) ---------------------------------------------------------------------------------------

def ordered_jobs(jobs: list[Q.JobRecord], finished: int = FINISHED_SHOWN) -> list[Q.JobRecord]:
    """Running jobs, then waiting jobs in the order they will start, then the newest finished ones."""
    running = sorted((j for j in jobs if j.state == "running"), key=lambda j: j.id)
    waiting = sorted((j for j in jobs if j.state == "queued"), key=Q.queue_key)
    done = sorted((j for j in jobs if j.final), key=lambda j: j.id, reverse=True)[:finished]
    return running + waiting + done


def job_row(store: Q.Store, job: Q.JobRecord, place: int | None, now: float) -> tuple[str, ...]:
    """The table cells of one job. `place` is its place in the queue (1 = next to start) for a waiting job."""
    if job.state == "queued":
        state = "cancelling" if job.cancel else f"waiting #{place}"
        dev = "-"
    else:
        state = job.state + (" (stopping)" if job.cancel and not job.final else "")
        dev = "none" if job.gpu is None else str(job.gpu)
    last = progress.last_line(store.log_path(job.id), 90) if job.state == "running" else ""
    return (str(job.id), job.name, Q.load_label(job.load), state, dev, Q.format_elapsed(job.elapsed(now)),
            "-" if job.exit is None else str(job.exit), last)


def gb(n: int | None) -> str:
    return "?" if n is None else f"{n / 1024 ** 3:.1f}"


def gpu_summary(g: gpumod.GpuStats, jobs: list[Q.JobRecord]) -> dict[str, str]:
    """Display strings for one GPU: title, load, VRAM, sensors, and the queue jobs running on it."""
    on = [j for j in jobs if j.state == "running" and j.gpu == g.index and j.gpu_req != "none"]
    hot = "" if g.temp_hot is None else f" (hot spot {g.temp_hot:.0f})"
    temp = "" if g.temp is None else f"{g.temp:.0f} °C{hot}"
    power = "" if g.power is None else f"{g.power:.0f} W" + ("" if g.power_cap is None else f" of {g.power_cap:.0f}")
    fan = "" if g.fan is None else f"fan {g.fan} rpm"
    return {
        "title": f"GPU {g.index}  {g.name or 'GPU'}  ({g.addr.removeprefix('0000:')})",
        "busy": "?" if g.busy is None else f"{g.busy} %",
        "vram": f"{gb(g.vram_used)} / {gb(g.vram_total)} GB",
        "sensors": "   ".join(x for x in (temp, power, fan) if x),
        "jobs": ", ".join(f"{j.id} {j.name}" for j in on) or "no queue job",
    }


def vram_percent(g: gpumod.GpuStats) -> float:
    return 0.0 if not g.vram_total or g.vram_used is None else 100.0 * g.vram_used / g.vram_total


def runner_text(store: Q.Store, jobs: list[Q.JobRecord], settings: Q.Settings) -> str:
    alive = store.runner_alive()
    pid = store.runner_pid() if alive else None
    runner = f"runner running (pid {pid})" if pid else ("runner running" if alive else
                                                         "runner idle (starts with the next job)")
    used = sum(min(j.load, settings.budget) for j in jobs if j.state == "running")
    waiting = sum(1 for j in jobs if j.state == "queued" and not j.cancel)
    paused = "PAUSED: nothing new starts" if store.paused else "running"
    return f"Queue {paused}   ·   {runner}   ·   load {used:g} of {settings.budget:g}   ·   {waiting} waiting"


def tail(path: Path, lines: int = LOG_LINES) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            text = f.read().decode(errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


# --- the window -----------------------------------------------------------------------------------------------------

def main(store: Q.Store, settings: Q.Settings | None = None) -> int:
    try:
        from . import _queue_qt
    except ImportError as e:
        print(f"error: the queue window needs PySide6 ({e}); install it with: pip install 'se2modkit[gui]'")
        return 1
    return _queue_qt.run(store, settings or Q.Settings())
