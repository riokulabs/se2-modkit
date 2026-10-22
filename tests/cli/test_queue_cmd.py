"""`se2mod queue`: argument handling, status, log and cancel. The runner is never started here (see test_jobqueue)."""

from __future__ import annotations

import pytest

from se2modkit import jobqueue as Q


@pytest.fixture
def q(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(Q, "spawn_runner", lambda root: spawned.append(root))
    monkeypatch.delenv("SE2MODKIT_CONFIG", raising=False)
    qdir = tmp_path / "queue"
    return qdir, spawned


def test_submit_prints_the_id_and_starts_the_runner(run, q):
    qdir, spawned = q
    code, out, _ = run("queue", "--queue-dir", str(qdir), "submit", "--name", "turntable", "--load", "light",
                       "--gpu", "1", "--timeout", "30", "--env", "A=b", "--", "blender", "-b", "--gpu-device", "{gpu}")
    assert code == 0 and out.strip() == "1" and spawned == [qdir]
    job = Q.Store(qdir).load(1)
    assert job.argv == ["blender", "-b", "--gpu-device", "{gpu}"]
    assert (job.name, job.load, job.gpu_req, job.timeout, job.env["A"]) == ("turntable", 0.25, 1, 30.0, "b")


def test_submit_rejects_bad_values(run, q):
    qdir, _ = q
    for bad in (["--load", "huge"], ["--gpu", "best"], ["--timeout", "0"], ["--env", "NOEQUALS"]):
        with pytest.raises(SystemExit):
            run("queue", "--queue-dir", str(qdir), "submit", *bad, "--", "true")
    code, _, err = run("queue", "--queue-dir", str(qdir), "submit")
    assert code == 1 and "after --" in err


def test_status_log_and_cancel(run, q):
    qdir, _ = q
    store = Q.Store(qdir)
    store.submit(["echo", "a"], environ={})
    done = store.submit(["echo", "b"], name="b", environ={})
    done.state, done.exit, done.started, done.ended = "done", 0, 1.0, 3.0
    store.save(done)
    store.log_path(done.id).write_text("hello\n", encoding="utf-8")

    code, out, _ = run("queue", "--queue-dir", str(qdir), "status")
    assert code == 0 and "runner: not running" in out
    rows = [ln.split() for ln in out.splitlines()[2:]]
    assert rows == [["1", "echo", "heavy", "queued", "-", "-", "-"], ["2", "b", "heavy", "done", "none", "2s", "0"]]
    assert run("queue", "--queue-dir", str(qdir), "list")[1] == out

    assert run("queue", "--queue-dir", str(qdir), "log", "2")[:2] == (0, "hello\n")
    assert run("queue", "--queue-dir", str(qdir), "log", "2", "-f")[:2] == (0, "hello\n")   # ends with the job

    code, out, _ = run("queue", "--queue-dir", str(qdir), "cancel", "--all")
    assert code == 0 and out.strip() == "cancelled job 1 echo"
    assert run("queue", "--queue-dir", str(qdir), "wait", "1", "--timeout", "5")[0] == 125
    assert run("queue", "--queue-dir", str(qdir), "wait", "2", "--timeout", "5")[0] == 0
    code, _, err = run("queue", "--queue-dir", str(qdir), "wait", "9", "--timeout", "1")
    assert code == 1 and "no job 9" in err
    assert run("queue", "--queue-dir", str(qdir), "cancel")[0] == 1


def test_bad_queue_config_is_one_sentence(run, q, tmp_path, monkeypatch):
    qdir, _ = q
    cfg = tmp_path / "c.toml"
    cfg.write_text("[queue]\nbudget = -1\n", encoding="utf-8")
    monkeypatch.setenv("SE2MODKIT_CONFIG", str(cfg))
    code, _, err = run("queue", "--queue-dir", str(qdir), "submit", "--", "true")
    assert code == 1 and "queue.budget must be a positive number" in err
