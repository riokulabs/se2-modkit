"""`procgroup.run_all`: parallel children in their own process groups, stopped together, never left behind."""

import os
import sys
import time

import pytest

from se2modkit import procgroup as PG

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")


def _py(code):
    return [sys.executable, "-c", code]


def _gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_all_succeed(tmp_path):
    jobs = [PG.Job(_py(f"print('job {i}')"), tmp_path / f"{i}.log") for i in range(3)]
    res = PG.run_all(jobs, timeout=60)
    assert [r.code for r in res] == [0, 0, 0] and not any(r.stopped for r in res)
    assert (tmp_path / "2.log").read_text(encoding="utf-8").strip() == "job 2"


def test_a_failure_stops_the_others_and_their_children(tmp_path):
    grandchild = tmp_path / "grandchild.pid"
    sleeper = _py("import subprocess, sys, time\n"
                  f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                  f"open({str(grandchild)!r}, 'w').write(str(p.pid))\n"
                  "time.sleep(120)\n")
    jobs = [PG.Job(sleeper, tmp_path / "sleep.log"), PG.Job(_py("import time, sys; time.sleep(1); sys.exit(3)"),
                                                          tmp_path / "fail.log")]
    t0 = time.monotonic()
    res = PG.run_all(jobs, timeout=60, grace=2)
    assert time.monotonic() - t0 < 20
    assert res[1].code == 3 and not res[1].stopped
    assert res[0].code is None and res[0].stopped
    assert _gone(res[0].pid)
    deadline = time.monotonic() + 5
    while not _gone(int(grandchild.read_text())) and time.monotonic() < deadline:   # the group went with it
        time.sleep(0.1)
    assert _gone(int(grandchild.read_text()))


def test_timeout_stops_everything(tmp_path):
    res = PG.run_all([PG.Job(_py("import time; time.sleep(120)"), tmp_path / "a.log")], timeout=0.5, grace=2)
    assert res[0].stopped and res[0].code is None and _gone(res[0].pid)
