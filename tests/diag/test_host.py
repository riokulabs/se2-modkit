import os
import sys
import time

import pytest

from se2modkit.diag import host

STAT = ("4242 (VRage (x) 1) S 1 4242 4242 0 -1 4194304 1500 0 7 0 900 100 0 0 20 0 64 0 123456 9000000000 "
        "250000 18446744073709551615 1 1 0 0 0 0 0 4096 17663 0 0 0 17 5 0 0 0 0 0 0 0 0 0 0 0 0 0")


def test_parse_stat_odd_comm():
    s = host.parse_stat(STAT)
    assert s["pid"] == 4242 and s["comm"] == "VRage (x) 1" and s["state"] == "S"
    assert (s["minflt"], s["majflt"], s["utime"], s["stime"], s["threads"], s["starttime"]) == (1500, 7, 900, 100, 64, 123456)
    assert s["rss_pages"] == 250000 and s["processor"] == 5


def test_parse_status_and_io():
    st = host.parse_status("Name:\tModern.bin\nVmSwap:\t  12 kB\nvoluntary_ctxt_switches:\t10\n")
    assert st["Name"] == "Modern.bin" and st["VmSwap"] == "12 kB" and st["voluntary_ctxt_switches"] == "10"
    assert host.parse_io("rchar: 100\nwchar: 5\nread_bytes: 0\n") == {"rchar": 100, "wchar": 5, "read_bytes": 0}


def snap(t, cpu, threads, io=None):
    return {"t": t, "cpu": cpu, "minflt": 0, "majflt": 0, "rss": 1000, "io": io or {"rchar": 0}, "threads": threads}


def th(name, cpu, nvcs=0):
    return {"name": name, "cpu": cpu, "utime": cpu, "stime": 0, "state": "R", "processor": 1, "minflt": 0, "majflt": 0,
            "vcs": 0, "nvcs": nvcs}


def test_diff_snapshots_and_findings():
    tck = host.CLK_TCK
    a = snap(0.0, 0, {1: th("Main Thread", 0), 2: th("Render thread", 0), 3: th("VRageWorker 1", 0), 4: th("VRageWorker 2", 0), 9: th("gone", 0)})
    b = snap(2.0, 5 * tck, {1: th("Main Thread", int(1.2 * tck)), 2: th("Render thread", int(1.9 * tck)),
                            3: th("VRageWorker 1", 0, nvcs=2000), 4: th("VRageWorker 2", 0, nvcs=2000), 5: th("new", 50)},
             io={"rchar": 2048})
    d = host.diff_snapshots(a, b)
    names = [r["name"] for r in d["threads"]]
    assert names[0] == "Render thread" and "gone" not in names and "new" not in names
    assert d["threads"][0]["cpu_pct"] == 95.0 and d["process_cpu_pct"] == 250.0
    g = {x["group"]: x for x in d["groups"]}
    assert g["VRageWorker #"]["threads"] == 2 and g["VRageWorker #"]["nvcs_per_s"] == 2000.0
    assert d["io_per_s"]["rchar"] == 1024
    f = host.thread_findings(d)
    assert any("Render thread" in x and "SpinWait" in x for x in f)
    assert any("VRageWorker #" in x and "involuntary" in x for x in f)


def test_overflow_constants():
    assert abs(host.TIMESPAN_MS_OVERFLOW_S - 922337.2) < 0.1          # 10.67 days
    assert abs(host.X1E7_OVERFLOW_S - 922.337) < 0.01                  # 15.4 min; wraps every 30.7 min
    c = host.clocks()
    assert c["timespan_ms_overflow"] == (c["monotonic_s"] > host.TIMESPAN_MS_OVERFLOW_S)


def test_duration_arg():
    assert host.duration_arg(10) == "00:00:00:10"
    assert host.duration_arg(3725) == "00:01:02:05"
    assert host.duration_arg(0.2) == "00:00:00:01"


def test_run_timeout_kills_child():
    t0 = time.monotonic()
    r = host.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert r.rc == 124 and "timed out" in r.err and time.monotonic() - t0 < 10


@pytest.mark.skipif(sys.platform == "win32", reason="the process list is read from /proc (Linux only)")
def test_find_pids_self():
    me = open(f"/proc/{os.getpid()}/comm").read().strip()
    assert os.getpid() in host.find_pids(me)


def test_parse_perf_report():
    text = """# Overhead  Shared Object  Symbol
    23.50%  libvkd3d-proton-d3d12.so  [.] d3d12_command_list_draw
     5.00%  [kernel.kallsyms]         [k] futex_wait
     2.00%  libvkd3d-proton-d3d12.so  [.] other
"""
    rows = host.parse_perf_report(text)
    assert rows[0] == {"pct": 23.5, "dso": "libvkd3d-proton-d3d12.so", "kind": "user", "symbol": "d3d12_command_list_draw"}
    assert rows[1]["kind"] == "kernel"
    assert host.perf_dso_totals(rows)[0] == {"dso": "libvkd3d-proton-d3d12.so", "pct": 25.5}
