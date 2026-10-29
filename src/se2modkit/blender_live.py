"""Runs inside a live Blender started by `se2mod queue blender` (passed with --python; Blender's own Python only).

When the queue stops the session (SIGTERM to the job), Blender saves the open file first, or a recovery copy if the
file was never saved, and then quits, instead of dying with the work unsaved. Closing Blender by hand is unaffected.
The handler runs on Blender's main thread through a short timer, so it acts within half a second.
"""

import os
import signal
import time
from pathlib import Path

import bpy  # pylint: disable=import-error

RECOVERY_DIR = Path(os.environ.get("SE2_LIVE_RECOVERY") or Path.home() / ".cache" / "se2modkit" / "live-blender")


def _save_and_quit():
    wm = bpy.context.window_manager
    window = wm.windows[0] if wm and wm.windows else None
    try:
        with bpy.context.temp_override(window=window):
            if bpy.data.is_dirty and bpy.data.filepath:
                bpy.ops.wm.save_mainfile()
                print(f"se2mod: saved {bpy.data.filepath} before closing")
            elif bpy.data.is_dirty:
                RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
                path = RECOVERY_DIR / time.strftime("unsaved-%Y%m%d-%H%M%S.blend")
                bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
                print(f"se2mod: the open file had no name; saved a copy to {path}")
    finally:
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.quit_blender()
    return None


def _on_term(_signum, _frame):
    bpy.app.timers.register(_save_and_quit, first_interval=0.0)


def _tick():
    return 0.5   # keeps Python running now and then, so the signal handler gets its turn


signal.signal(signal.SIGTERM, _on_term)
bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
print("se2mod: live Blender session; stopping it from the queue saves the open file first")
