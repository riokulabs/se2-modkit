"""The GPU job queue: scheduling within the power budget, the runner, waits, cancels and timeouts.

Every job here is a short Python command; GPUs come from a fake sampler. Nothing launches Blender or uses a GPU.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from se2modkit import jobqueue as Q
from se2modkit.gpu import Gpu

posix = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups and signals")


def _job(i, load=Q.HEAVY, req="auto", **kw):
    return Q.JobRecord(id=i, argv=["x"], name=f"j{i}", load=load, gpu_req=req, **kw)


def _ids(started):
    return [j.id for j, _ in started]


def _plan(waiting, running=(), outside=0.0, gpus=(), **settings):
    return Q.plan(list(waiting), list(running), outside=outside, gpus=list(gpus), settings=Q.Settings(**settings))


# --- scheduling ------------------------------------------------------------------------------------------------------

def test_fifo_one_heavy_at_a_time():
    assert _ids(_plan([_job(1), _job(2), _job(3)])) == [1]
    assert _plan([_job(2), _job(3)], running=[_job(1, state="running")]) == []


def test_four_light_jobs_share_a_heavy_runs_alone():
    lights = [_job(i, Q.LOADS["light"]) for i in range(1, 6)]
    assert _ids(_plan(lights)) == [1, 2, 3, 4]
    assert _ids(_plan([_job(9), *lights])) == [9]          # the heavy job is first: it runs alone
    assert _plan([_job(9)], running=[_job(1, Q.LOADS["light"], state="running")]) == []


def test_waiting_heavy_job_is_not_passed():
    running = [_job(1, Q.LOADS["light"], state="running")]
    waiting = [_job(2), _job(3, Q.LOADS["light"]), _job(4, Q.LOADS["light"])]
    assert _plan(waiting, running) == []                    # no backfill: the heavy job keeps its place
    assert _ids(_plan(waiting)) == [2]                     # it starts as soon as the light job is gone


def test_waiting_medium_job_is_not_starved_by_a_stream_of_light_jobs():
    running = [_job(i, Q.LOADS["light"], state="running") for i in (1, 2, 3)]   # 0.75 in use
    waiting = [_job(4, Q.LOADS["medium"]), _job(5, Q.LOADS["light"])]
    assert _plan(waiting, running) == []
    assert _ids(_plan(waiting, running[:1])) == [4, 5]


def test_a_job_larger_than_the_budget_still_runs_alone():
    assert _ids(_plan([_job(1, 3.0), _job(2, Q.LOADS["light"])])) == [1]


def test_least_busy_gpu_and_per_gpu_budget():
    gpus = [Gpu(0, busy=80), Gpu(1, busy=5)]
    started = _plan([_job(1), _job(2), _job(3)], gpus=gpus, budget=2.0)
    assert [(j.id, g) for j, g in started] == [(1, 1), (2, 0)]   # third waits: both cards are full
    running = [_job(1, state="running", gpu=1), _job(2, state="running", gpu=0)]
    assert _plan([_job(3)], running, gpus=gpus, budget=3.0) == []


def test_light_jobs_spread_over_cards_in_one_pass():
    gpus = [Gpu(0, busy=0), Gpu(1, busy=0)]
    started = _plan([_job(i, Q.LOADS["light"]) for i in (1, 2)], gpus=gpus)
    assert sorted(g for _, g in started) == [0, 1]


def test_pinned_gpu_waits_for_its_card():
    gpus = [Gpu(0), Gpu(1)]
    busy0 = _job(1, state="running", gpu=0)
    assert _plan([_job(2, req=0)], [busy0], gpus=gpus, budget=2.0) == []
    assert [(j.id, g) for j, g in _plan([_job(2, req=1)], [busy0], gpus=gpus, budget=2.0)] == [(2, 1)]


def test_no_gpus_means_no_gpu_choice_and_the_budget_still_serializes():
    started = _plan([_job(1), _job(2)])
    assert [(j.id, g) for j, g in started] == [(1, None)]


def test_outside_blender_blocks_a_heavy_job():
    assert _plan([_job(1)], outside=1.0) == []
    assert _plan([_job(1, Q.LOADS["light"])], outside=1.0) == []
    assert _ids(_plan([_job(1)], outside=1.0, budget=2.0)) == [1]


def test_outside_load_counts_other_blenders_and_the_game(monkeypatch):
    monkeypatch.setattr(Q.procs, "game_running", lambda **_: True)
    monkeypatch.setattr(Q.gpumod, "blender_processes", lambda: [(111, "blender -b a"), (222, "blender -b b")])
    monkeypatch.setattr(Q, "_session_id", lambda pid: {111: 111, 222: 500}[pid])
    monkeypatch.setattr(Q.sys, "platform", "linux")
    assert Q.outside_load({500}, Q.Settings()) == 2.0     # the game, and Blender 111 (222 runs in our job's session)
    assert Q.outside_load(set(), Q.Settings(count_outside=False)) == 0.0


def test_loads_gpus_and_settings_parse():
    assert Q.parse_load("heavy") == 1.0 and Q.parse_load("Light") == 0.25 and Q.parse_load("0.3") == 0.3
    for bad in ("0", "-1", "huge", "inf"):
        with pytest.raises(ValueError):
            Q.parse_load(bad)
    assert Q.parse_gpu("auto") == "auto" and Q.parse_gpu("none") == "none" and Q.parse_gpu("2") == 2
    with pytest.raises(ValueError):
        Q.parse_gpu("-1")
    s = Q.Settings.from_config({"queue": {"budget": 2, "gpu_budget": 1.0, "idle_exit": 5, "count_outside": False}})
    assert (s.budget, s.gpu_budget, s.idle_exit, s.count_outside) == (2.0, 1.0, 5.0, False)
    assert Q.Settings.from_config({}) == Q.Settings()
    with pytest.raises(Q.QueueError):
        Q.Settings.from_config({"queue": {"budget": 0}})


def test_exit_codes_and_status_lines(tmp_path):
    assert Q.exit_code(_job(1, state="done", exit=0)) == 0
    assert Q.exit_code(_job(1, state="failed", exit=3)) == 3
    assert Q.exit_code(_job(1, state="timeout", exit=143)) == Q.EXIT_TIMEOUT == 124
    assert Q.exit_code(_job(1, state="cancelled", exit=143)) == Q.EXIT_CANCELLED == 125
    store = Q.Store(tmp_path)
    lines = Q.status_lines(store, [_job(1, state="done", exit=0, started=10.0, ended=135.0, gpu=1),
                                   _job(2, Q.LOADS["light"])], now=200.0)
    assert lines[0].split() == ["ID", "NAME", "LOAD", "STATE", "GPU", "ELAPSED", "EXIT"]
    assert lines[1].split() == ["1", "j1", "heavy", "done", "1", "2m05s", "0"]
    assert lines[2].split() == ["2", "j2", "light", "queued", "-", "-", "-"]


# --- store -----------------------------------------------------------------------------------------------------------

def test_submit_assigns_ids_and_captures_the_display(tmp_path):
    store = Q.Store(tmp_path)
    environ = {"PATH": "/x/bin", "DISPLAY": ":7", "WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": "/run/user/7",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/7/bus", "GH_TOKEN": "secret"}
    a = store.submit(["echo", "a"], environ=environ, env={"EXTRA": "1"})
    b = store.submit(["echo", "b"], environ={})
    assert (a.id, b.id) == (1, 2) and a.name == "echo"
    assert store.load(1).env == {**{k: v for k, v in environ.items() if k != "GH_TOKEN"}, "EXTRA": "1"}
    with pytest.raises(Q.QueueError):
        store.submit([])


def test_cancel_a_queued_job_needs_no_runner(tmp_path):
    store = Q.Store(tmp_path)
    store.submit(["echo"], environ={})
    assert [j.state for j in store.cancel([1])] == ["cancelled"]
    assert Q.wait(store, 1, timeout=5, spawn=lambda root: None) == 125


_SUBMITTER = """
import sys
from pathlib import Path
from se2modkit import jobqueue as Q
store = Q.Store(Path(sys.argv[1]))
for i in range(15):
    store.submit(["echo", str(i)], environ={})
"""


def test_two_concurrent_submitters_get_distinct_ids(tmp_path):
    procs = [subprocess.Popen([sys.executable, "-c", _SUBMITTER, str(tmp_path)]) for _ in range(2)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    ids = [j.id for j in Q.Store(tmp_path).all()]
    assert sorted(ids) == list(range(1, 31))


# --- the runner (real child processes) ---------------------------------------------------------------------------------

def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.fixture
def runner(tmp_path):
    store = Q.Store(tmp_path / "q")
    lines: list[str] = []
    gpus: list[Gpu] = []
    r = Q.Runner(store, Q.Settings(), sampler=lambda: list(gpus), outside=lambda own: 0.0, log=lines.append)
    r.lines, r.gpus = lines, gpus
    yield r
    r.stop_all()


def _spin(r, cond, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition not reached"
        r.tick()
        time.sleep(0.05)


def _text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _state(r, job_id):
    return r.store.load(job_id).state


def _gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


@posix
def test_runner_substitutes_gpu_and_records_exit_and_progress(runner):
    runner.gpus[:] = [Gpu(0, busy=90), Gpu(1, busy=5)]
    job = runner.store.submit(_py("import os, sys; print('dev', sys.argv[1], os.environ['SE2_GPU_DEVICE']); "
                                  "sys.exit(3)") + ["{gpu}"], name="render", environ={})
    _spin(runner, lambda: runner.store.load(job.id).final)
    done = runner.store.load(job.id)
    assert (done.state, done.exit, done.gpu) == ("failed", 3, 1)
    assert runner.store.log_path(job.id).read_text().strip() == "dev 1 1"
    assert any(s.startswith("> job 1 render (heavy, GPU 1)") for s in runner.lines)
    assert any("FAILED (exit 3): job 1 render" in s for s in runner.lines)
    assert Q.wait(runner.store, job.id, timeout=5, spawn=lambda root: None) == 3


@posix
def test_runner_gpu_none_substitutes_zero(runner):
    job = runner.store.submit(_py("import sys; print(sys.argv[1])") + ["--gpu-device={gpu}"], gpu="none", environ={})
    _spin(runner, lambda: runner.store.load(job.id).final)
    assert runner.store.log_path(job.id).read_text().strip() == "--gpu-device=0"
    assert runner.store.load(job.id).gpu is None


@posix
def test_submit_time_display_environment_reaches_the_job(runner):
    environ = {"DISPLAY": ":42", "WAYLAND_DISPLAY": "wayland-9", "XDG_RUNTIME_DIR": "/tmp/xdg-test",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus-test", "PATH": os.environ.get("PATH", "")}
    code = "import os; print(*(os.environ.get(k) for k in ('DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', " \
           "'DBUS_SESSION_BUS_ADDRESS', 'EXTRA')))"
    job = runner.store.submit(_py(code), environ=environ, env={"EXTRA": "yes"})
    _spin(runner, lambda: runner.store.load(job.id).final)
    assert runner.store.log_path(job.id).read_text().split() == [":42", "wayland-9", "/tmp/xdg-test",
                                                                  "unix:path=/tmp/bus-test", "yes"]


@posix
def test_runner_packs_light_jobs_and_runs_heavy_alone(runner, tmp_path):
    gate = tmp_path / "go"
    hold = _py(f"import os, time\nwhile not os.path.exists({str(gate)!r}): time.sleep(0.05)")
    lights = [runner.store.submit(hold, load=Q.LOADS["light"], environ={}) for _ in range(4)]
    heavy = runner.store.submit(hold, environ={})
    late_light = runner.store.submit(hold, load=Q.LOADS["light"], environ={})
    runner.tick()
    assert [_state(runner, j.id) for j in lights] == ["running"] * 4
    assert _state(runner, heavy.id) == "queued" and _state(runner, late_light.id) == "queued"
    gate.touch()
    _spin(runner, lambda: _state(runner, heavy.id) == "running")
    assert _state(runner, late_light.id) == "queued"       # the heavy job runs alone
    _spin(runner, lambda: runner.store.load(late_light.id).final)
    assert all(runner.store.load(j.id).state == "done" for j in [*lights, heavy, late_light])
    order = [runner.store.load(j.id).started for j in (lights[0], heavy, late_light)]
    assert order == sorted(order)


@posix
def test_outside_blender_holds_a_heavy_job(runner):
    outside = [1.0]
    runner.outside = lambda own: outside[0]
    job = runner.store.submit(_py("pass"), environ={})
    for _ in range(3):
        runner.tick()
    assert _state(runner, job.id) == "queued"
    outside[0] = 0.0
    _spin(runner, lambda: runner.store.load(job.id).final)
    assert runner.store.load(job.id).state == "done"


@posix
def test_cancel_stops_the_whole_process_group(runner, tmp_path):
    child = tmp_path / "child.pid"
    code = ("import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(child)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(120)\n")
    job = runner.store.submit(_py(code), environ={})
    _spin(runner, lambda: child.exists() and child.read_text().strip())
    leader = runner.store.load(job.id).pid
    runner.store.cancel([job.id])
    _spin(runner, lambda: runner.store.load(job.id).final)
    assert runner.store.load(job.id).state == "cancelled"
    assert Q.wait(runner.store, job.id, timeout=5, spawn=lambda root: None) == 125
    grandchild = int(child.read_text())
    deadline = time.monotonic() + 5
    while not (_gone(leader) and _gone(grandchild)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _gone(leader) and _gone(grandchild)


@posix
def test_a_job_that_ignores_sigterm_is_killed_after_the_grace(runner, monkeypatch):
    monkeypatch.setattr(Q, "KILL_GRACE", 0.5)
    code = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(120)"
    job = runner.store.submit(_py(code), environ={})
    _spin(runner, lambda: "ready" in _text(runner.store.log_path(job.id)))
    runner.store.cancel([job.id])
    t0 = time.monotonic()
    _spin(runner, lambda: runner.store.load(job.id).final)
    assert time.monotonic() - t0 < 10
    assert runner.store.load(job.id).exit == 137


@posix
def test_job_timeout(runner):
    job = runner.store.submit(_py("import time; time.sleep(120)"), timeout=0.5, environ={})
    _spin(runner, lambda: runner.store.load(job.id).final)
    assert runner.store.load(job.id).state == "timeout"
    assert Q.wait(runner.store, job.id, timeout=5, spawn=lambda root: None) == 124


def test_wait_times_out_with_124(tmp_path):
    store = Q.Store(tmp_path)
    store.submit(["echo"], environ={})
    t0 = time.monotonic()
    assert Q.wait(store, 1, timeout=0.3, poll=0.05, spawn=lambda root: None) == 124
    assert time.monotonic() - t0 < 5


def test_a_job_whose_program_is_missing_fails_with_127(runner):
    job = runner.store.submit(["se2modkit-no-such-program-xyz"], environ={})
    runner.tick()
    assert (runner.store.load(job.id).state, runner.store.load(job.id).exit) == ("failed", 127)


def test_runner_marks_orphaned_running_jobs_lost(runner):
    job = runner.store.submit(["echo"], environ={})
    job.state = "running"
    runner.store.save(job)
    runner.recover()
    assert runner.store.load(job.id).state == "lost"


def test_runner_exits_when_idle_and_releases_its_lock(runner):
    t0 = time.monotonic()
    assert runner.run(idle_exit=0.3, sleep=lambda s: time.sleep(0.02)) == 0
    assert time.monotonic() - t0 < 10
    assert not runner.store.runner_alive()
    assert any("idle" in s for s in runner.lines)


def test_only_one_runner_holds_the_lock(runner):
    lock = runner.store.runner_lock()
    assert lock.acquire(blocking=False)
    try:
        assert runner.store.runner_alive()
        assert runner.run(idle_exit=0.1) == 0
        assert any("already running" in s for s in runner.lines)
    finally:
        lock.release()
    assert not runner.store.runner_alive()


# --- end to end: detached runner, real CLI ------------------------------------------------------------------------------

def _cli(*args, env):
    code = "import sys; from se2modkit.cli import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, env=env, timeout=120)


@posix
def test_submit_starts_one_detached_runner_that_exits_when_idle(tmp_path):
    qdir = tmp_path / "q"
    cfg = tmp_path / "config.toml"
    cfg.write_text("[queue]\nidle_exit = 1.5\ncount_outside = false\n", encoding="utf-8")
    env = {**os.environ, "SE2MODKIT_CONFIG": str(cfg), "DISPLAY": ":99"}
    store = Q.Store(qdir)
    try:
        ids = []
        for i in range(3):
            r = _cli("queue", "--queue-dir", str(qdir), "submit", "--gpu", "none", "--load", "light", "--",
                     sys.executable, "-c", f"import os, time; time.sleep(0.3); print({i}, os.environ['DISPLAY'])",
                     env=env)
            assert r.returncode == 0, r.stderr
            ids.append(int(r.stdout.strip()))
        assert ids == [1, 2, 3]
        for i in ids:
            w = _cli("queue", "--queue-dir", str(qdir), "wait", str(i), "--timeout", "60", env=env)
            assert w.returncode == 0, w.stderr
        assert store.log_path(2).read_text().split() == ["1", ":99"]
        log = store.runner_log.read_text()
        assert log.count("queue runner") >= 1 and log.count(" started (") == 1   # racing extra runners never start
        deadline = time.monotonic() + 30
        while store.runner_alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not store.runner_alive()
        assert "idle for 1.5s; exiting" in store.runner_log.read_text()
    finally:
        pid = store.runner_pid()
        if pid and store.runner_alive():
            os.kill(pid, 15)
            deadline = time.monotonic() + 15
            while store.runner_alive() and time.monotonic() < deadline:
                time.sleep(0.1)

