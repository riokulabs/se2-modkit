import io
import threading
import time
from pathlib import Path

from se2modkit.diag import logs

MAIN = "SpaceEngineers2_261020_030623_018_4242.log"


def test_entries_and_continuations(fix):
    ents = logs.read_entries(fix / MAIN)
    first = ents[0]
    assert first.thread == "Main Thread" and first.text.startswith("Timezone")
    cfg = next(e for e in ents if e.text == "Pushing Core Configs")
    assert cfg.extra and cfg.extra[0].startswith('{"$Bundles"')
    exc = next(e for e in ents if e.text.startswith("Exception occurred"))
    assert len(exc.extra) == 2 and "Scene.Tick" in exc.extra[1]
    indented = next(e for e in ents if "Render_Init" in e.text)
    assert indented.text.startswith("LOADING_TIME|Render_Init")


def test_classify_and_siblings(fix):
    p = fix / MAIN
    assert logs.classify(p) == "main"
    assert logs.classify(fix / "SpaceEngineers2_261020_030623_018_4242_Render12.log") == "render12"
    assert logs.classify(Path("other.log")) is None
    assert logs.pid_of(p) == 4242
    assert logs.sibling(p, "render12").name.endswith("_4242_Render12.log")
    found = logs.find_logs(fix)
    assert {x.name for x in found} == {MAIN, "SpaceEngineers2_261019_120000_000_777.log"}
    assert logs.newest_log(fix, pid=777).name == "SpaceEngineers2_261019_120000_000_777.log"


def test_scan_counts(fix):
    ents = logs.read_entries(fix / MAIN)
    sc = logs.scan(ents)["patterns"]
    assert sc["client_on_server"]["count"] == 1
    assert sc["definition_validation"]["count"] == 1
    assert sc["definition_validation_warning"]["count"] == 1
    assert sc["exception"]["count"] == 2          # "Exception occurred" + "NOT FATAL exception"
    assert sc["assertion"]["count"] == 1
    assert sc["missing_loc_key"]["count"] == 2
    assert sc["linuxcompat"]["count"] == 1
    assert sc["device_removal"]["count"] == 0
    assert sc["client_on_server"]["first"]["thread"] == "VRageWorker 3"


def test_scan_since_session(fix):
    ents = logs.read_entries(fix / MAIN)
    sc = logs.scan(ents, since_session=True)
    assert sc["since"] == "last world load"
    assert sc["patterns"]["missing_loc_key"]["count"] == 1     # the "Plugins" one came before the world load
    assert sc["patterns"]["client_on_server"]["count"] == 1


def test_extra_pattern_and_grep(fix):
    ents = logs.read_entries(fix / MAIN)
    sc = logs.scan(ents, extra_patterns=[r"Finished save"])
    assert sc["patterns"]["grep1"]["count"] == 1
    hits = logs.grep(ents, r"Demo\.cs:line")
    assert len(hits) == 1 and hits[0]["text"].startswith("Exception occurred")


def test_tail_is_bounded(tmp_path):
    p = tmp_path / "x.log"
    p.write_text("old line\n")
    out = io.StringIO()

    def writer():
        time.sleep(0.3)
        with open(p, "a") as f:
            f.write("2026-10-20 03:00:00.000 - Main Thread ->  new ERROR line\n")
            f.write("2026-10-20 03:00:00.001 - Main Thread ->  boring\n")
    t = threading.Thread(target=writer)
    t.start()
    start = time.monotonic()
    n = logs.tail(p, "ERROR", 1.2, out, poll=0.05)
    t.join()
    assert n == 1 and "new ERROR line" in out.getvalue() and "old line" not in out.getvalue()
    assert time.monotonic() - start < 3
