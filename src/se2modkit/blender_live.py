"""Runs inside a live Blender started by `se2mod queue blender` (passed with --python; Blender's own Python only).

When the queue stops the session (SIGTERM to the job), Blender saves the open file first, or a copy if the file was
never saved, and then quits, instead of dying with the work unsaved. It saves whether or not Blender thinks the file
changed: edits made through Python (the MCP add-on, scripts) don't mark the file as changed. Saving keeps Blender's
usual .blend1 backup of the previous version. Closing Blender by hand is unaffected. The handler runs on Blender's main
thread through a short timer, so it acts within half a second.

An idle session stops itself the same way (save, then quit) when nothing in it has changed for SE2_LIVE_IDLE seconds
(default 1200; 0 turns it off) AND another job is waiting in the queue, so a forgotten Blender doesn't hold the GPU
from everyone. Looking around the viewport doesn't count as a change, which is why it never stops while nobody waits.
If that save fails, the session stays open and the idle watch starts over, so it tries again later.
"""

import json
import os
import signal
import time
from pathlib import Path

try:   # Blender only; the idle logic below imports without it
    import bpy  # pylint: disable=import-error
except ImportError:   # pragma: no cover - outside Blender
    bpy = None

RECOVERY_DIR = Path(os.environ.get("SE2_LIVE_RECOVERY") or Path.home() / ".cache" / "se2modkit" / "live-blender")


DEFAULT_IDLE = 1200.0
CHECK_EVERY = 30.0


class IdleWatch:
    """Seconds since the session last changed; `limit` 0 means never idle."""

    def __init__(self, limit: float, now: float):
        self.limit, self.last = limit, now

    def touch(self, now: float) -> None:
        self.last = now

    def expired(self, now: float) -> bool:
        return self.limit > 0 and now - self.last >= self.limit


def idle_limit() -> float:
    try:
        return max(0.0, float(os.environ.get("SE2_LIVE_IDLE", DEFAULT_IDLE)))
    except ValueError:
        return DEFAULT_IDLE


def others_waiting(queue_root) -> bool:
    """Is any job waiting in the queue at `queue_root`? (Read straight from its job files; a torn one is skipped.)"""
    if not queue_root or Path(queue_root, "paused").exists():   # a paused queue starts nothing after us anyway
        return False
    for path in Path(queue_root, "jobs").glob("*.json"):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("state") == "queued":
                return True
        except (OSError, ValueError):
            continue
    return False


def should_stop(watch: IdleWatch, now: float, *, waiting: bool) -> bool:
    return waiting and watch.expired(now)


def _save() -> bool:
    """Save the open file (or a copy if it has no name); False if Blender could not."""
    wm = bpy.context.window_manager
    window = wm.windows[0] if wm and wm.windows else None
    try:
        with bpy.context.temp_override(window=window):
            if bpy.data.filepath:
                result = bpy.ops.wm.save_mainfile()
                where = bpy.data.filepath
            elif bpy.data.objects or bpy.data.is_dirty:
                RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
                where = RECOVERY_DIR / time.strftime("unsaved-%Y%m%d-%H%M%S.blend")
                result = bpy.ops.wm.save_as_mainfile(filepath=str(where), copy=True)
            else:
                return True
    except (RuntimeError, OSError) as exc:
        print(f"se2mod: could not save: {exc}")
        return False
    if "FINISHED" not in result:
        print(f"se2mod: could not save {where}")
        return False
    print(f"se2mod: saved {where} before closing" if bpy.data.filepath else
          f"se2mod: the open file had no name; saved a copy to {where}")
    return True


def _quit() -> None:
    wm = bpy.context.window_manager
    with bpy.context.temp_override(window=wm.windows[0] if wm and wm.windows else None):
        bpy.ops.wm.quit_blender()


def _save_and_quit():
    """The queue is stopping the job: save what can be saved, then quit either way (SIGKILL follows the grace)."""
    try:
        _save()
    finally:
        _quit()
    return None


def _idle_stop():
    """Idle while others wait: close only once the work is saved. A failed save keeps Blender open and re-arms the
    idle watch, so the stop is tried again after another idle period instead of never."""
    if _save():
        _quit()
        return None
    print("se2mod: keeping the session open; the idle stop will try again later")
    _WATCH.touch(time.monotonic())
    bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    return None


def _on_term(_signum, _frame):
    bpy.app.timers.register(_save_and_quit, first_interval=0.0)


_WATCH = IdleWatch(idle_limit(), time.monotonic())
_NEXT_CHECK = [time.monotonic() + CHECK_EVERY]


def _touch(*_args):
    _WATCH.touch(time.monotonic())


def _tick():
    now = time.monotonic()
    if now >= _NEXT_CHECK[0]:
        _NEXT_CHECK[0] = now + CHECK_EVERY
        if should_stop(_WATCH, now, waiting=others_waiting(os.environ.get("SE2_QUEUE_ROOT"))):
            print(f"se2mod: idle for {_WATCH.limit / 60:g} min while other jobs wait; saving and closing")
            bpy.app.timers.register(_idle_stop, first_interval=0.0)
            return None
    return 0.5   # keeps Python running now and then, so the signal handler gets its turn


if bpy is not None:
    # Persistent, and on load_post too: Blender drops plain handlers when a file is opened, which would leave the idle
    # watch with no input and close a session that is being worked in.
    _touch = bpy.app.handlers.persistent(_touch)
    signal.signal(signal.SIGTERM, _on_term)
    bpy.app.handlers.depsgraph_update_post.append(_touch)
    bpy.app.handlers.load_post.append(_touch)
    bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    print("se2mod: live Blender session; stopping it from the queue saves the open file first")
