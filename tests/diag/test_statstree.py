from se2modkit.diag import statstree


def test_to_number():
    assert statstree.to_number("5.28GiB") == 5.28 * 1024**3
    assert abs(statstree.to_number("500.28ms") - 0.50028) < 1e-12
    assert statstree.to_number("12.5%") == 0.125
    assert statstree.to_number("1,011.90KiB") == 1011.90 * 1024
    assert statstree.to_number("7") == 7 and statstree.to_number("unavailable") is None


def test_parse_nesting(fix):
    nodes = statstree.parse_stats_log((fix / "demo_Stats.log").read_text())
    by = {n["path"]: n for n in nodes}
    assert by["GC/GC Total Pause Duration"]["text"] == "500.28ms"
    assert by["Performance/Main Thread/Draw"]["number"] == 0.0025
    assert by["Performance/CPU Load"]["number"] == 0.125
    assert by["Cable Trays: Empty"]["text"] == "3"          # a name with ':' keeps it
    assert by["Native Allocators/Heap Allocation Count"]["number"] is None
    assert by["Performance"]["text"] is None


def test_compare_and_render(fix):
    a = statstree.parse_stats_log((fix / "demo_Stats.log").read_text())
    b = statstree.parse_stats_log((fix / "demo2_Stats.log").read_text())
    rows = {r["path"]: r for r in statstree.compare(a, b)}
    assert rows["GC/GC Total Pause Duration"]["flag"] == "changed"
    assert rows["GC/GC Total"]["flag"] == ""
    assert rows["Native Allocators"]["flag"] == "only in A"
    assert rows["Performance/Worker Count"]["flag"] == "only in B"
    lines = statstree.render([{"path": "Memory", "kind": "None"}, {"path": "Memory/GC", "text": "1GiB"},
                              {"path": "Memory/Hidden", "value": 2.0, "unit": "s", "hidden": True}])
    assert lines == ["Memory:", "  GC: 1GiB"]


def test_render_hides_children_of_hidden():
    lines = statstree.render([{"path": "P", "kind": "None"}, {"path": "P/Total", "hidden": True, "kind": "None"},
                              {"path": "P/Total/Time", "value": 1.0, "unit": "s"}, {"path": "P/Draw", "text": "2ms"}])
    assert lines == ["P:", "  Draw: 2ms"]
