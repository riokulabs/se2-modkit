import pytest

from se2modkit.cli import wait_cmd
from se2modkit.env import procs


class Clock:
    """Fake time: `sleep` advances `now` and runs the scripted `on_sleep` hook; nothing really waits."""

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = on_sleep

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(wait_cmd, "_monotonic", c.monotonic)
    monkeypatch.setattr(wait_cmd, "_sleep", c.sleep)
    monkeypatch.setattr(procs, "listing_available", lambda **_: True)
    return c


def test_game_closed_immediately(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    code, out, _ = run("wait", "game-closed")
    assert code == 0 and clock.sleeps == []


def test_game_closed_after_polling(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "game_running", lambda **_: len(clock.sleeps) < 3)
    code, _, _ = run("wait", "game-closed", "--interval", "2", "--timeout", "60")
    assert code == 0 and clock.sleeps == [2.0, 2.0, 2.0]


def test_game_closed_times_out(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "game_running", lambda **_: True)
    code, _, err = run("wait", "game-closed", "--timeout", "10", "--interval", "3")
    assert code == 124
    assert err.strip().count("\n") == 0 and "timed out" in err
    assert clock.now <= 10


def test_hub_closed_uses_hub_names(run, clock, monkeypatch):
    seen = []

    def fake(names, **_):
        seen.append(names)
        return [(5, "hub")] if len(seen) < 2 else []
    monkeypatch.setattr(procs, "find_processes", fake)
    code, _, _ = run("wait", "hub-closed")
    assert code == 0 and seen[0] == procs.HUB_PROCS and len(clock.sleeps) == 1


def test_pid_waits_for_exit(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "running_processes",
                        lambda **_: [(42, "x", "x")] if len(clock.sleeps) < 2 else [(7, "y", "y")])
    code, _, _ = run("wait", "pid", "42")
    assert code == 0 and len(clock.sleeps) == 2


def test_pid_times_out(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "running_processes", lambda **_: [(42, "x", "x")])
    code, _, err = run("wait", "pid", "42", "--timeout", "5")
    assert code == 124 and "42" in err


def test_file_appears(run, clock, tmp_path):
    target = tmp_path / "ready.txt"
    clock.on_sleep = lambda n: target.write_text("hi", encoding="utf-8") if n == 2 else None
    code, _, _ = run("wait", "file", str(target))
    assert code == 0 and len(clock.sleeps) == 2


def test_file_contains(run, clock, tmp_path):
    target = tmp_path / "log.txt"
    target.write_text("starting\n", encoding="utf-8")
    clock.on_sleep = lambda n: target.write_text("starting\nReady ✓\n", encoding="utf-8") if n == 3 else None
    code, _, _ = run("wait", "file", str(target), "--contains", "Ready ✓")
    assert code == 0 and len(clock.sleeps) == 3


def test_file_contains_times_out(run, clock, tmp_path):
    target = tmp_path / "log.txt"
    target.write_bytes(b"\xff\xfe binary junk")
    code, _, err = run("wait", "file", str(target), "--contains", "Ready", "--timeout", "4")
    assert code == 124 and "Ready" in err


def test_file_missing_times_out_without_traceback(run, clock, tmp_path):
    code, _, err = run("wait", "file", str(tmp_path / "no dir" / "x"), "--timeout", "2")
    assert code == 124 and "Traceback" not in err


def test_zero_timeout_checks_once(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "game_running", lambda **_: True)
    code, _, _ = run("wait", "game-closed", "--timeout", "0")
    assert code == 124 and clock.sleeps == []


def test_ctrl_c_returns_130(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "game_running", lambda **_: True)

    def boom(_s):
        raise KeyboardInterrupt
    monkeypatch.setattr(wait_cmd, "_sleep", boom)
    code, _, err = run("wait", "game-closed")
    assert code == 130 and err.strip().count("\n") == 0 and "Traceback" not in err


def test_unknown_listing_is_not_closed(run, clock, monkeypatch):
    monkeypatch.setattr(procs, "listing_available", lambda **_: False)
    monkeypatch.setattr(procs, "game_running", lambda **_: False)
    monkeypatch.setattr(procs, "find_processes", lambda *_a, **_k: [])
    monkeypatch.setattr(procs, "running_processes", lambda **_: [])
    for argv in (("game-closed",), ("hub-closed",), ("pid", "42")):
        code, _, _ = run("wait", *argv, "--timeout", "4")
        assert code == 124, argv


def test_listing_available_false_when_no_answer(tmp_path):
    assert procs.listing_available(proc=tmp_path / "nothing") is False


@pytest.mark.parametrize("flag,value", [("--timeout", "nan"), ("--timeout", "inf"), ("--timeout", "-1"),
                                        ("--interval", "0"), ("--interval", "-2"), ("--interval", "inf"),
                                        ("--interval", "abc")])
def test_bad_numbers_rejected(run, clock, capsys, flag, value):
    with pytest.raises(SystemExit) as e:
        run("wait", "game-closed", flag, value)
    assert e.value.code == 2
    assert "seconds" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-5", "x"])
def test_bad_pid_rejected(run, clock, capsys, value):
    with pytest.raises(SystemExit) as e:
        run("wait", "pid", value)
    assert e.value.code == 2
    assert "PID" in capsys.readouterr().err or "process id" in capsys.readouterr().err
