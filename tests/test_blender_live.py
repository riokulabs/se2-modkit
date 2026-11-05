"""The live Blender session's idle stop: plain logic, importable without Blender."""
import json

from se2modkit import blender_live as BL


def test_idle_watch_expires_after_limit():
    w = BL.IdleWatch(1200, now=0.0)
    assert not w.expired(1199.0) and w.expired(1200.0)
    assert not BL.IdleWatch(0, now=0.0).expired(1e9)                            # 0 turns the idle stop off


def test_activity_resets_idle_watch():
    w = BL.IdleWatch(1200, now=0.0)
    w.touch(1000.0)
    assert not w.expired(2100.0) and w.expired(2200.0)


def test_limit_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SE2_LIVE_IDLE", "300")
    assert BL.idle_limit() == 300.0
    monkeypatch.setenv("SE2_LIVE_IDLE", "nonsense")
    assert BL.idle_limit() == BL.DEFAULT_IDLE
    monkeypatch.delenv("SE2_LIVE_IDLE")
    assert BL.idle_limit() == BL.DEFAULT_IDLE == 1200.0


def test_others_waiting_reads_the_queue(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    assert BL.others_waiting(tmp_path) is False
    (jobs / "000001.json").write_text(json.dumps({"id": 1, "state": "running", "kind": "blender"}))
    assert BL.others_waiting(tmp_path) is False
    (jobs / "000002.json").write_text(json.dumps({"id": 2, "state": "queued", "kind": ""}))
    (jobs / "000003.json").write_text("{torn")
    assert BL.others_waiting(tmp_path) is True
    assert BL.others_waiting(None) is False


def test_stops_only_when_idle_and_someone_waits(tmp_path):
    w = BL.IdleWatch(10, now=0.0)
    assert BL.should_stop(w, 20.0, waiting=True) and not BL.should_stop(w, 20.0, waiting=False)
    assert not BL.should_stop(w, 5.0, waiting=True)


def test_activity_hook_survives_file_loads(monkeypatch):
    """Inside Blender the hook is persistent and also runs on load_post, so opening another file counts as activity
    instead of silently dropping the idle watch's only input."""
    import importlib
    import sys
    import types

    marked = []
    handlers = types.SimpleNamespace(depsgraph_update_post=[], load_post=[],
                                     persistent=lambda f: marked.append(f) or f)
    fake = types.SimpleNamespace(app=types.SimpleNamespace(handlers=handlers, timers=types.SimpleNamespace(
        register=lambda *a, **k: None)))
    monkeypatch.setitem(sys.modules, "bpy", fake)
    monkeypatch.setattr("signal.signal", lambda *a: None)
    mod = importlib.reload(BL)
    try:
        assert mod._touch in marked
        assert mod._touch in handlers.depsgraph_update_post and mod._touch in handlers.load_post
    finally:
        monkeypatch.delitem(sys.modules, "bpy")
        importlib.reload(BL)


def test_a_paused_queue_does_not_count_as_waiting(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "000002.json").write_text(json.dumps({"id": 2, "state": "queued"}))
    assert BL.others_waiting(tmp_path) is True
    (tmp_path / "paused").write_text("")
    assert BL.others_waiting(tmp_path) is False


def _fake_bpy(save):
    """Just enough of bpy for the save-and-quit paths: `save` is what save_mainfile does (raise, or return a result)."""
    import contextlib
    import types

    calls = types.SimpleNamespace(quit=0, timers=[])
    wm_ops = types.SimpleNamespace(save_mainfile=save, quit_blender=lambda: setattr(calls, "quit", calls.quit + 1))
    fake = types.SimpleNamespace(
        context=types.SimpleNamespace(window_manager=None, temp_override=lambda **k: contextlib.nullcontext()),
        data=types.SimpleNamespace(filepath="/work/block.blend", objects=[], is_dirty=True),
        ops=types.SimpleNamespace(wm=wm_ops),
        app=types.SimpleNamespace(timers=types.SimpleNamespace(
            register=lambda f, **k: calls.timers.append(f))))
    return fake, calls


def _refuse():
    raise RuntimeError("Error: Cannot open file /work/block.blend@ for writing: Permission denied")


def test_a_failed_idle_save_keeps_the_session_and_watches_again(monkeypatch):
    """If the idle stop cannot save, Blender stays open (closing would lose the work) and the idle watch is re-armed,
    instead of the timer being gone for good."""
    for save in (_refuse, lambda: {"CANCELLED"}):
        fake, calls = _fake_bpy(save)
        monkeypatch.setattr(BL, "bpy", fake)
        monkeypatch.setattr(BL, "_WATCH", BL.IdleWatch(10, now=0.0))
        assert BL._idle_stop() is None
        assert calls.quit == 0
        assert calls.timers == [BL._tick]
        assert not BL._WATCH.expired(BL.time.monotonic())             # a fresh idle period before the next try


def test_an_idle_save_that_works_closes_the_session(monkeypatch):
    fake, calls = _fake_bpy(lambda: {"FINISHED"})
    monkeypatch.setattr(BL, "bpy", fake)
    BL._idle_stop()
    assert calls.quit == 1 and calls.timers == []
