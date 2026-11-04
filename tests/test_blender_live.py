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
