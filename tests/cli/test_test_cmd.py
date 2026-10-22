"""`se2mod test`: runs a mod's pytest suite in a subprocess and prints one summary line plus failures only."""

import re

import pytest
from minimod import make_mod

SUMMARY = re.compile(r"^(\d+) passed, (\d+) failed, (\d+) skipped.* in [\d.]+s")


def summary(out):
    for line in out.splitlines():
        m = SUMMARY.match(line)
        if m:
            return tuple(int(x) for x in m.groups())
    raise AssertionError(f"no summary line in:\n{out}")


def test_whole_suite_by_path_serial(run, tmp_path):
    mod = make_mod(tmp_path / "Mini Mod")
    code, out, err = run("test", str(mod), "--serial")
    assert code == 0, out + err
    assert summary(out) == (5, 0, 0)
    assert len(out.strip().splitlines()) == 1  # nothing but the summary on success


def test_fast_deselects_registered_slow_marker(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    code, out, err = run("test", str(mod), "--fast", "--serial")
    assert code == 0, out + err
    assert summary(out) == (4, 0, 0)
    assert "1 deselected" in out
    assert "not build and not slow" in out


def test_failure_shows_section_and_log_pointer(run, tmp_path):
    mod = make_mod(tmp_path / "m", failing=True)
    code, out, err = run("test", str(mod), "--serial")
    assert code == 1
    assert summary(out) == (6, 1, 0)
    assert "left is not two" in out
    assert "FAILED Tests/test_fail.py::test_broken" in out
    m = re.search(r"full log: (.+\.log)", out)
    assert m, out
    log = m.group(1).strip()
    assert "/test-logs/m-" in log
    assert "test_broken" in open(log, encoding="utf-8").read()


def test_failure_output_is_capped(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    body = "".join(f"def test_f{i}():\n    assert {i} == -1, 'x' * 50\n\n\n" for i in range(60))
    (mod / "Tests" / "test_many.py").write_text(body, encoding="utf-8")
    code, out, err = run("test", str(mod), "--serial", "--max-lines", "40")
    assert code == 1
    assert summary(out)[1] == 60
    lines = out.splitlines()
    assert len(lines) <= 43
    assert any("more lines" in ln and "full log:" in ln for ln in lines)


def test_parallel_uses_xdist_when_available(run, tmp_path):
    pytest.importorskip("xdist")
    mod = make_mod(tmp_path / "m")
    code, out, err = run("test", str(mod))
    assert code == 0, out + err
    assert summary(out) == (5, 0, 0)
    assert "-n " in out and "loadfile" in out


def test_module_selection(run, tmp_path):
    mod = make_mod(tmp_path / "m", failing=True)
    code, out, err = run("test", str(mod), "--module", "pkg.top", "--serial")
    assert code == 0, out + err
    assert summary(out) == (2, 0, 0)  # test_top only: the failing file is not selected


def test_block_selection(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    code, out, err = run("test", str(mod), "--block", "top", "--fast", "--serial")
    assert code == 0, out + err
    assert summary(out) == (1, 0, 0) and "1 deselected" in out  # test_top minus its slow test


def test_changed_clean_tree_runs_nothing(run, tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    code, out, err = run("test", str(mod), "--changed")
    assert code == 0
    assert "nothing to run" in out
    assert "passed" not in out


def test_changed_unmapped_file_nothing_unless_fallback(run, tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    (mod / "Source/Model/pkg/lonely.py").write_text("X = 5\n", encoding="utf-8")
    code, out, err = run("test", str(mod), "--changed")
    assert code == 0 and "nothing to run" in out and "lonely.py" in out
    code, out, err = run("test", str(mod), "--changed", "--fallback-full", "--serial")
    assert code == 0, out + err
    assert summary(out) == (5, 0, 0)


def test_changed_maps_through_imports(run, tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    (mod / "Source/Model/pkg/base.py").write_text("VALUE = 1  # edited\n", encoding="utf-8")
    code, out, err = run("test", str(mod), "--changed", "--serial")
    assert code == 0, out + err
    assert summary(out) == (3, 0, 0)  # test_mid (1) + test_top (2)


def test_full_rejects_selectors(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    code, out, err = run("test", str(mod), "--full", "--changed")
    assert code == 2
    assert "--full" in err and "Traceback" not in err
    assert len(err.strip().splitlines()) == 1


def test_errors_are_one_sentence(run, tmp_path):
    code, out, err = run("test", str(tmp_path / "missing"))
    assert code == 1 and "Traceback" not in err and len(err.strip().splitlines()) == 1
    (tmp_path / "empty").mkdir()
    code, out, err = run("test", str(tmp_path / "empty"))
    assert code == 1 and "Tests" in err and len(err.strip().splitlines()) == 1
    code, out, err = run("test", str(make_mod(tmp_path / "nogit")), "--changed")
    assert code == 1 and "git" in err and len(err.strip().splitlines()) == 1
    code, out, err = run("test", str(tmp_path / "nogit"), "--module", "pkg.nope")
    assert code == 1 and "pkg.nope" in err and len(err.strip().splitlines()) == 1


def test_unparseable_output_prints_tail(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    (mod / "Tests" / "conftest.py").write_text("import sys\nprint('boom')\nsys.exit(3)\n", encoding="utf-8")
    code, out, err = run("test", str(mod), "--serial")
    assert code != 0
    assert "could not read pytest's summary" in out
    assert "full log:" in out


def test_list_prints_selection_without_running(run, tmp_path):
    mod = make_mod(tmp_path / "m", failing=True, repo=True)
    (mod / "Source/Model/pkg/mid.py").write_text("from . import base\n# edit\n", encoding="utf-8")
    code, out, err = run("test", str(mod), "--changed", "--fast", "--list")
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].startswith("static selection")
    assert lines[1:3] == ["Tests/test_mid.py", "Tests/test_top.py"]
    assert "-m 'not build and not slow'" in lines[-1] and "pytest" in lines[-1]
    assert "passed" not in out


# --- edge cases: gate note, deleted files, exit codes ------------------------------------------------------------

import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

from se2modkit.cli import testrun_cmd  # noqa: E402

GATE = "static selection — `se2mod test --full` is the gate"


def test_selective_runs_print_the_gate_note(run, tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    code, out, err = run("test", str(mod), "--changed")
    assert code == 0 and "nothing to run" in out and GATE in out
    code, out, err = run("test", str(mod), "--module", "pkg.mid", "--serial")
    assert code == 0 and GATE in out
    code, out, err = run("test", str(mod), "--serial")
    assert GATE not in out


def test_deleted_file_is_a_change_not_nothing(run, tmp_path):
    mod = make_mod(tmp_path / "m", repo=True)
    (mod / "Source/Model/pkg/lonely.py").unlink()
    code, out, err = run("test", str(mod), "--changed")
    assert code == 0
    assert "no changed files" not in out
    assert "lonely.py (deleted)" in out and "nothing to run" in out


def test_nonzero_exit_after_summary_is_not_success(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    conf = mod / "Tests/conftest.py"
    conf.write_text(conf.read_text(encoding="utf-8") + (
        "\n\ndef pytest_unconfigure(config):\n    import os, sys\n"
        "    print('INTERNALERROR> boom after the summary')\n    sys.stdout.flush()\n    os._exit(3)\n"),
        encoding="utf-8")
    code, out, err = run("test", str(mod), "--serial")
    assert code == 3
    first = out.splitlines()[0]
    assert first.startswith("5 passed, 0 failed") and "FAILED (pytest exit 3)" in first
    assert "INTERNALERROR> boom after the summary" in out  # the tail is shown for odd exits


def test_exit_5_reads_no_tests_ran(run, tmp_path):
    mod = tmp_path / "m"
    (mod / "Tests").mkdir(parents=True)
    (mod / "Tests" / "helper.py").write_text("X = 1\n", encoding="utf-8")
    code, out, err = run("test", str(mod), "--serial")
    assert code == 5
    assert out.splitlines()[0].startswith("no tests ran in ")
    assert "FAILED" not in out


def test_logs_keep_newest_20_per_mod(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    logs = tmp_path / "home" / ".cache" / "se2modkit" / "test-logs"
    code, out, err = run("test", str(mod), "--serial", "--module", "pkg.mid")
    assert code == 0, out + err
    existing = list(logs.glob("m-*.log"))
    assert len(existing) == 1, list(logs.iterdir())
    for i in range(25):
        p = logs / f"m-20200101-0000{i:02d}.log"
        p.write_text("old\n", encoding="utf-8")
        os.utime(p, (1_600_000_000 + i, 1_600_000_000 + i))
    other = logs / "m-x-20200101-000000.log"
    other.write_text("other mod\n", encoding="utf-8")
    os.utime(other, (1_500_000_000, 1_500_000_000))
    code, out, err = run("test", str(mod), "--serial", "--module", "pkg.mid")
    assert code == 0
    mine = [p for p in logs.iterdir() if p.name != other.name]
    assert len(mine) == 20
    assert other.exists()
    assert existing[0].exists()  # the newest real logs survive; the oldest fakes go
    assert not (logs / "m-20200101-000000.log").exists()


CHILD = '''\
import pathlib, signal, sys, time
marker, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
def handler(signum, frame):
    marker.write_text(signal.Signals(signum).name)  # stands in for a fixture's teardown
    sys.exit(0)
signal.signal(signal.SIGINT, handler if mode == "handle" else signal.SIG_IGN)
signal.signal(signal.SIGTERM, handler if mode == "term" else signal.SIG_IGN)
for i in range(30):
    print(f"child line {i}", flush=True)
time.sleep(60)
'''


def _child(tmp_path, mode, log, timeout):
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    marker = tmp_path / "marker"
    code, text, timed_out = testrun_cmd._run([sys.executable, str(script), str(marker), mode], tmp_path, log, timeout)
    return code, text, timed_out, marker


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_timeout_interrupts_first_so_the_child_tears_down(tmp_path):
    code, text, timed_out, marker = _child(tmp_path, "handle", tmp_path / "x.log", 1.5)
    assert timed_out and marker.read_text() == "SIGINT"
    assert "child line 29" in text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_timeout_escalates_to_sigterm(tmp_path, monkeypatch):
    monkeypatch.setattr(testrun_cmd, "GRACE_SECONDS", 0.5)
    code, text, timed_out, marker = _child(tmp_path, "term", None, 1.0)
    assert timed_out and marker.read_text() == "SIGTERM"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_timeout_escalates_to_sigkill(tmp_path, monkeypatch):
    monkeypatch.setattr(testrun_cmd, "GRACE_SECONDS", 0.5)
    start = time.monotonic()
    code, text, timed_out, marker = _child(tmp_path, "ignore", None, 1.0)
    assert timed_out and not marker.exists() and code == -signal.SIGKILL
    assert time.monotonic() - start < 10


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_ctrl_c_sends_sigint_to_the_group(tmp_path, monkeypatch):
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    marker = tmp_path / "marker"
    real = subprocess.Popen

    class Interrupted(real):
        def communicate(self, *a, **kw):
            if kw.get("timeout") is not None and not getattr(self, "_hit", False):
                self._hit = True
                time.sleep(0.8)  # let the child install its handlers
                raise KeyboardInterrupt
            return super().communicate(*a, **kw)

    monkeypatch.setattr(testrun_cmd.subprocess, "Popen", Interrupted)
    with pytest.raises(KeyboardInterrupt):
        testrun_cmd._run([sys.executable, str(script), str(marker), "handle"], tmp_path, None, 30)
    assert marker.read_text() == "SIGINT"


def test_timeout_prints_tail(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    (mod / "Tests" / "test_hang.py").write_text("import time\n\n\ndef test_hang():\n    time.sleep(60)\n",
                                               encoding="utf-8")
    conf = mod / "Tests/conftest.py"
    conf.write_text(conf.read_text(encoding="utf-8") + (
        "\n\ndef pytest_collection_finish(session):\n"
        "    tr = session.config.pluginmanager.get_plugin('terminalreporter')\n"
        "    for i in range(30):\n        tr.write_line(f'hang line {i}')\n"), encoding="utf-8")
    code, out, err = run("test", str(mod), "--serial", "--module", "pkg.mid", "--block", "hang", "--timeout", "2")
    assert code == 1
    assert "did not finish within 2 s" in err
    assert "hang line 29" in out and "hang line 5" not in out


def test_gate_selection_passes_the_plugin_option(run, tmp_path):
    """`--gate NAME` runs only that shared gate: the plugin's `--se2-gate` deselects every other test."""
    mod = make_mod(tmp_path / "m")
    code, out, err = run("test", str(mod), "--gate", "json", "--gate", "guids", "--list")
    assert code == 0, out + err
    assert "--se2-gate json --se2-gate guids" in out


def test_unknown_gate_is_one_sentence(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    code, out, err = run("test", str(mod), "--gate", "nope", "--list")
    assert code == 2 and "Unknown gate 'nope'" in err and "json" in err and "Traceback" not in err


def test_gate_and_import_selection_do_not_mix(run, tmp_path):
    mod = make_mod(tmp_path / "m")
    code, _, err = run("test", str(mod), "--gate", "json", "--module", "pkg.mid")
    assert code == 2 and "--gate" in err
