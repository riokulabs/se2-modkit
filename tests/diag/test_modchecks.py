from se2modkit.diag import logs, modchecks

MAIN = "SpaceEngineers2_261020_030623_018_4242.log"


def test_log_checks(fix):
    ents = logs.read_entries(fix / MAIN)
    rows = {r["id"]: r for r in modchecks.log_checks(ents, [("DemoMod", "XX_"), ("Other Mod", "")])}
    assert rows["LOG-1"]["status"] == "FAIL" and "1 line" in rows["LOG-1"]["detail"]
    assert rows["LOG-2"]["status"] == "PASS"
    assert rows["XX-LOG"]["status"] == "FAIL" and "DemoMod/Data/Box" in rows["XX-LOG"]["detail"]
    assert rows["OtherMod-LOG"]["status"] == "PASS"


def test_parse_mods():
    assert modchecks.parse_mods(" MyMod:MM_, Other ,") == [("MyMod", "MM_"), ("Other", "")]
    assert modchecks.tag("MyMod", "MM_") == "MM" and modchecks.tag("My Mod", "") == "MyMod"


def test_merge_and_verdict():
    probe = [{"id": "XX-SUB", "title": "t", "status": "PASS", "detail": ""}, {"id": "XY-1", "title": "t", "status": "SKIP", "detail": ""}]
    lg = [{"id": "LOG-1", "title": "t", "status": "FAIL", "detail": "", "source": "log"}]
    rows = modchecks.merge(probe, lg)
    assert [r["id"] for r in rows] == ["LOG-1", "XX-SUB", "XY-1"]
    assert rows[1]["source"] == "game" and rows[0]["source"] == "log"
    assert modchecks.verdict(rows) == "FAIL"
    assert modchecks.verdict(probe) == "PASS"
    assert modchecks.verdict([{"id": "x", "status": "SKIP"}]) == "SKIP"
