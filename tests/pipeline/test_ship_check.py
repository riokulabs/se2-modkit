"""The plain half of the ship check: the queue-order rule and the report's fail lines and summary."""

from pathlib import Path

from se2modkit.jobqueue import JobRecord
from se2modkit.lease import Lease
from se2modkit.pipeline import ship_check as SC

SCRIPT = Path("/mods/Toy/Source/Model/build_toy.py")
BLEND = "/work/toy.blend"


def _job(i, argv, state="queued", name="", kind=""):
    return JobRecord(id=i, argv=argv, name=name, state=state, kind=kind, cwd="/work")


def _export(i, state="queued", block="XX_Box"):
    return _job(i, ["blender", "--python", str(SCRIPT), "--", block, "--export"], state, name=f"export {block}")


def _save(i, state="queued"):
    return _job(i, ["blender", "-b", "toy.blend", "--python", "save.py"], state, name="save")


def _check(jobs, block="XX_Box", blends=(BLEND,), leases=()):
    return SC.queue_order(jobs, mod="Toy", block=block, script=SCRIPT, blends=blends, leases=leases)


def test_a_save_queued_behind_the_export_fails():
    fail, warn = _check([_export(1), _save(2)])
    assert len(fail) == 1 and "job 2 (save) saves toy.blend but runs after job 1" in fail[0] and not warn


def test_a_save_beside_a_running_export_fails():
    fail, _ = _check([_export(1, "running"), _save(2, "running")])
    assert "alongside" in fail[0]
    fail, _ = _check([_export(1, "running"), _save(2)])          # the export already reads the old file
    assert "after" in fail[0]


def test_a_save_ahead_of_the_export_warns():
    fail, warn = _check([_save(1), _export(2)])
    assert not fail and "save and release before the export" in warn[0]
    fail, warn = _check([_save(1, "running"), _export(2)])
    assert not fail and warn


def test_other_blocks_mods_and_finished_jobs_do_not_count():
    assert _check([_export(1, block="XX_Other"), _save(2)])[0] == []
    other = _job(1, ["blender", "--python", "/mods/Else/Source/Model/build_else.py", "--", "XX_Box"])
    assert _check([other, _save(2)])[0] == []
    assert _check([_export(1, "done"), _save(2)])[0] == []
    assert _check([_export(1, block="XX_*"), _save(2)])[0]            # a prefix that covers the block
    assert _check([_export(1, block="all"), _save(2)])[0]


def test_build_models_and_named_check_jobs_are_readers():
    bm = _job(1, ["python", "-c", "...", "build-models", "Toy", "XX_Box", "--export", "--timeout", "900"])
    assert SC.job_blocks(bm, "Toy", SCRIPT) == ["XX_Box"]
    assert _check([bm, _save(2)])[0]
    fc = _job(1, ["blender"], name="fracture-check:Toy:XX_Box,XX_Lid")
    assert SC.job_blocks(fc, "Toy", SCRIPT) == ["XX_Box", "XX_Lid"]
    assert SC.job_blocks(_save(2), "Toy", SCRIPT) is False


def test_a_live_blender_on_the_file_fails_behind_an_export_and_one_on_no_file_warns():
    live = _job(2, ["blender", "--gpu-device", "{gpu}", BLEND, "--python", "blender_live.py"], kind="blender")
    fail, _ = _check([_export(1, "running"), live])
    assert "holds toy.blend" in fail[0]
    bare = _job(3, ["blender", "--python", "blender_live.py"], kind="blender")
    fail, warn = _check([bare])
    assert not fail and "a live Blender is open (job 3" in warn[0]


def test_a_lease_on_the_file_warns():
    lease = Lease(path=BLEND, owner="alice", since=0.0, ttl=60.0)
    fail, warn = _check([], leases=[lease])
    assert not fail and "leased to alice" in warn[0]


def _report(**kw):
    rep = {"block": "XX_Box", "family": "box", "status": "checked", "error": None,
           "shipping": [{"model": "LOD0", "collection": "XX_Box_LOD0",
                         "objects": [{"name": "XX_Box_LOD0", "tris": 12}]}],
           "collision": ["XX_Box_body"],
           "sources": [{"blend": BLEND, "collection": "Box_v1", "objects": [
               {"name": "body", "path": "Box_v1/body", "type": "MESH", "ships": True, "why": None,
                "viewport_hidden": False},
               {"name": "plate", "path": "Box_v1/plate_standin", "type": "MESH", "ships": False,
                "why": "render-hidden", "viewport_hidden": False}]}],
           "zfight": {}, "fracture": {"block": "XX_Box", "family": "box", "status": "checked", "cell_groups": 1,
                                      "tris": 12, "loose": {}, "seams": [],
                                      "zones": [{"id": "Fracture_01", "label": "all", "critical": False,
                                                 "share": 1.0}]},
           "preview": "/out/XX_Box/exploded.png", "preview_error": None}
    rep.update(kw)
    return rep


def test_a_clean_report_passes_and_lists_what_ships():
    lines = SC.summary(_report())
    assert lines[0].startswith("XX_Box (box): ships Box_v1;") and lines[-1] == "PASS XX_Box"
    assert any("out: plate (render-hidden)" in ln for ln in lines)
    assert not SC.problems(_report()) and not SC.warnings(_report())


def test_coplanar_rows_and_queue_fails_fail():
    row = {"model": "block_LOD0", "a": "XX_Box_LOD0/Paint", "b": "XX_Box_LOD0/Grey", "area_mm2": 1200.0,
           "at": [0.0, 0.5, 0.1], "n": [0, 1, 0]}
    rep = _report(zfight={"coplanar": [row] * 10}, queue={"fail": ["XX_Box: a save"], "warn": []})
    bad = SC.problems(rep)
    assert len(bad) == SC.MAX_ROWS + 2 and "share 1200 mm2" in bad[0] and "and 2 more" in bad[SC.MAX_ROWS]
    assert SC.summary(rep)[-1] == "FAIL XX_Box"


def test_hidden_or_stand_in_sources_that_ship_and_a_missing_preview_warn():
    src = [{"blend": BLEND, "collection": "Box_v1", "objects": [
        {"name": "rib", "path": "Box_v1/rib", "type": "MESH", "ships": True, "why": None, "viewport_hidden": True},
        {"name": "p", "path": "Box_v1/layout_standin_plate", "type": "MESH", "ships": True, "why": None,
         "viewport_hidden": False}]}]
    warn = SC.warnings(_report(sources=src, preview=None, preview_error="no GPU"))
    assert len(warn) == 3 and "hidden in the viewport but ships" in warn[0] and "stand-in" in warn[1]
    assert "no GPU" in warn[2]
    assert SC.summary(_report(sources=src))[-1] == "PASS XX_Box"     # warnings never fail


def test_an_error_report_fails():
    rep = _report(status="error", error="no collection 'Box_v1'", shipping=[], fracture=None, preview=None)
    assert SC.problems(rep) == ["XX_Box: no collection 'Box_v1'"]
    assert SC.warnings(rep) == []
