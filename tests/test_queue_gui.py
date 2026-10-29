"""The queue window's rows and labels, without opening a window."""

from __future__ import annotations

from pathlib import Path

from se2modkit import gpu as gpumod
from se2modkit import jobqueue as Q
from se2modkit import queue_gui as G


def _job(store, name, **state):
    job = store.submit(["true"], name=name, environ={})
    for key, value in state.items():
        setattr(job, key, value)
    store.save(job)
    return job


def test_rows_show_running_then_queue_order_then_newest_finished(tmp_path):
    store = Q.Store(tmp_path / "q")
    old = _job(store, "old", state="done", exit=0, started=1.0, ended=2.0)
    run = _job(store, "render", state="running", gpu=1, started=10.0)
    w1, w2 = _job(store, "first"), _job(store, "second")
    store.move(w2.id, 0)
    jobs = G.ordered_jobs(store.all())
    assert [j.id for j in jobs] == [run.id, w2.id, w1.id, old.id]
    assert G.job_row(store, store.load(w2.id), 1, 20.0)[3:5] == ("waiting #1", "-")
    row = G.job_row(store, store.load(run.id), None, 20.0)
    assert row[3:6] == ("running", "1", Q.format_elapsed(10.0))


def test_gpu_summary_names_the_jobs_on_each_card(tmp_path):
    store = Q.Store(tmp_path / "q")
    run = _job(store, "render", state="running", gpu=1, started=1.0)
    g = gpumod.GpuStats(1, "0000:09:00.0", "AMD Radeon RX 7900 XT", busy=37, vram_used=2 * 1024 ** 3,
                        vram_total=20 * 1024 ** 3, temp=62.0, temp_hot=72.0, power=69.0, power_cap=282.0, fan=583)
    s = G.gpu_summary(g, store.all())
    assert s["title"] == "GPU 1  AMD Radeon RX 7900 XT  (09:00.0)"
    assert (s["busy"], s["vram"]) == ("37 %", "2.0 / 20.0 GB")
    assert "62 °C (hot spot 72)" in s["sensors"] and "69 W of 282" in s["sensors"] and "583 rpm" in s["sensors"]
    assert s["jobs"] == f"{run.id} render"
    assert G.vram_percent(g) == 10.0
    assert G.gpu_summary(gpumod.GpuStats(0, "0000:44:00.0"), [])["vram"] == "? / ? GB"


def test_runner_text_reports_pause_and_load(tmp_path):
    store = Q.Store(tmp_path / "q")
    _job(store, "render", state="running", gpu=0, started=1.0, load=0.5)
    _job(store, "next")
    store.set_paused(True)
    text = G.runner_text(store, store.all(), Q.Settings())
    assert "PAUSED" in text and "load 0.5 of 1" in text and "1 waiting" in text


def test_qt_window_lists_jobs_and_moves_them(tmp_path, monkeypatch):
    """The real window, off screen: it lists the queue in order, and Top moves the selected waiting job."""
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    from se2modkit import _queue_qt as W

    from PySide6 import QtCore

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
    QtCore.QSettings.setPath(QtCore.QSettings.Format.NativeFormat, QtCore.QSettings.Scope.UserScope, str(tmp_path))
    store = Q.Store(tmp_path / "q")
    old = _job(store, "old", state="done", exit=0, started=1.0, ended=2.0)
    first, second = _job(store, "first"), _job(store, "second")
    win = W.QueueWindow(store, Q.Settings(), [])
    try:
        assert [win.tree.topLevelItem(i).text(0) for i in range(win.tree.topLevelItemCount())] == \
            [str(first.id), str(second.id)]
        win.tree.topLevelItem(1).setSelected(True)
        app.processEvents()
        assert win.selected == second.id and all(b.isEnabled() for b in win.move_btns)
        win.move("top")
        assert [j.id for j in store.queued()] == [second.id, first.id]
        assert win.tree.topLevelItem(0).text(3) == "waiting #1"
        win.toggle_pause()
        assert store.paused and win.pill.text() == "Paused"
        win.show_done.setChecked(True)                                  # finished jobs are hidden until asked for
        assert win.tree.topLevelItemCount() == 3 and win.tree.topLevelItem(2).text(0) == str(old.id)
        win.clear_finished()
        assert store.load(old.id) is None and win.tree.topLevelItemCount() == 2
        assert win.blender_btn.text() == "Start Blender" and not win.blender_btn.isEnabled()   # no Blender known
        win.blender = Path("/opt/blender/blender")
        win.toggle_blender()
        live = store.live_blender()
        assert live is not None and store.queued()[0].id == live.id and win.blender_btn.text() == "Cancel Blender"
        win.toggle_blender()                                            # a waiting session is simply cancelled
        assert store.live_blender() is None and win.blender_btn.text() == "Start Blender"
    finally:
        win.timer.stop()
        win.close()
