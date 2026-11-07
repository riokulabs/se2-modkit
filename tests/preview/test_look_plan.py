"""`se2mod look`'s plain-Python half: the job it queues, rounds, views, materials, before cameras and the finish step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from se2modkit.preview import PreviewError, look, shots

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(path: Path, value=120, size=(160, 90)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (value,) * 3).save(path)
    return path


def _job(tmp_path, **kw):
    args = dict(source=tmp_path / "drive.blend", block="XX_Drive", out_dir=tmp_path / "look" / "XX_Drive" / "r1",
                stage="look", rnd=1, views=look.views_for("look"), set_name="neighbors")
    args.update(kw)
    (tmp_path / "drive.blend").write_bytes(b"BLENDER")
    return look.build_job(**args)


def test_look_job_is_medium_priority(tmp_path):
    job = _job(tmp_path)
    assert job["load"] == "medium" and job["priority"] is True
    argv = look.queue_argv(Path("/j.json"), ["blender", "-b"], name="look XX_Drive r1", priority=True)
    assert argv[argv.index("--load") + 1] == "medium" and "--priority" in argv
    assert "--priority" not in look.queue_argv(Path("/j.json"), ["blender"], name="x", priority=False)
    assert argv[argv.index("--") + 1:] == ["blender", "-b"]


def test_round_defaults_to_next_free(tmp_path):
    assert look.next_round(tmp_path / "none") == 1
    for r in ("r1", "r2", "r7", "notes"):
        (tmp_path / r).mkdir()
    assert look.next_round(tmp_path) == 8


def test_views_look_expands_to_required_set():
    assert look.views_for("look") == list(shots.REQUIRED["look"])
    assert look.views_for("look", interior=True)[-1] == "inside"
    assert look.views_for("concept") == list(shots.REQUIRED["concept"])
    assert look.views_for("3q_rear,top") == ["3q_back", "top"]
    with pytest.raises(PreviewError):
        look.views_for("diagonal")


def test_before_reuses_cameras(tmp_path):
    before = tmp_path / "r1"
    for v in ("3q_front", "top"):
        png = _png(before / f"{shots.shot_name('XX_Drive', 'look', 1, v)}.png")
        shots.save_camera(png, [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 7], [0, 0, 0, 1]], 14.5)
    cams = look.before_cameras(before, "XX_Drive", ["3q_front", "top"])
    assert cams["3q_front"]["camera"]["lens"] == 14.5 and cams["top"]["camera"]["matrix"][2][3] == 7
    with pytest.raises(PreviewError, match="left"):
        look.before_cameras(before, "XX_Drive", ["3q_front", "left"])
    job = _job(tmp_path, out_dir=tmp_path / "r2", rnd=2, views=["3q_front", "top"], before=before)
    assert job["before"]["3q_front"]["camera"]["lens"] == 14.5
    assert job["before"]["top"]["png"].endswith("XX_Drive_look_r1_top.png")


def test_unmapped_material_is_magenta_and_listed(monkeypatch):
    from se2modkit.preview import mats
    monkeypatch.setitem(mats._PATHS, "sdk_materials", False)   # the flat stand-ins, whether or not an SDK is here
    assert look.material_key("PaintedMetalColorable.001", {}) == ("PMC:keen", False)
    assert look.material_key("BrushedMetal", {}) == ("Brushed", False)
    assert look.material_key("BellSteel", {"BellSteel": "Chrome"}) == ("Chrome", False)
    assert look.material_key("Material.012", {}) == (look.UNMAPPED, True)
    assert mats.SIMPLE[look.UNMAPPED][0] == (1.0, 0.0, 1.0)


class _SdkMat:
    def path(self, role):
        return None   # no colour mask: drawn as is, no player paint


def test_a_slot_the_sdk_has_maps_for_is_drawn_from_them(monkeypatch, tmp_path):
    """A vanilla slot outside the stand-in tables (the hydrogen thruster cone) is drawn from its SDK maps, mapped or
    met as it is, never refused or left magenta."""
    from se2modkit.preview import mats
    monkeypatch.setattr(mats, "sdk_material", lambda name: _SdkMat() if name == "HydrogenThrusterCone" else None)
    assert look.material_key("rb_Cone", {"rb_Cone": "HydrogenThrusterCone"}) == ("SDK:HydrogenThrusterCone", False)
    assert look.material_key("HydrogenThrusterCone.001", {}) == ("SDK:HydrogenThrusterCone", False)
    assert look.material_key("Material.012", {}) == (look.UNMAPPED, True)
    assert _job(tmp_path, materials={"rb_Cone": "HydrogenThrusterCone"})["materials"] == {"rb_Cone": "HydrogenThrusterCone"}
    with pytest.raises(PreviewError, match="NoSuchSlot"):
        _job(tmp_path, materials={"rb_X": "NoSuchSlot"})


def test_a_slot_only_the_sdk_knows_is_refused_without_the_sdk(monkeypatch, tmp_path):
    from se2modkit.preview import mats
    monkeypatch.setitem(mats._PATHS, "sdk_materials", False)
    with pytest.raises(PreviewError, match="Mod SDK"):
        _job(tmp_path, materials={"rb_Cone": "HydrogenThrusterCone"})


def test_source_kinds(tmp_path):
    for ext in (".blend", ".glb", ".gltf", ".fbx"):
        assert look.source_kind(tmp_path / f"x{ext}") == ext[1:]
    with pytest.raises(PreviewError, match="GLB"):
        look.source_kind(tmp_path / "x.step")


def test_job_refuses_a_source_that_is_not_there(tmp_path):
    with pytest.raises(PreviewError):
        look.build_job(source=tmp_path / "gone.blend", block="X", out_dir=tmp_path / "o", stage="look", rnd=1,
                       views=["top"], set_name="hull")


def test_close_up_gets_locator(tmp_path):
    out = tmp_path / "r1"
    over = _png(out / "XX_Drive_fix_r1_3q_front.png", size=(400, 300))
    _png(out / "XX_Drive_fix_r1_3q_front_close-gimbal.png", size=(400, 300))
    (out / "close.json").write_text(json.dumps({"XX_Drive_fix_r1_3q_front_close-gimbal":
                                                {"overview": over.name, "rect": [100, 100, 200, 180]}}))
    report = look.finish(out, block="XX_Drive", stage="fix", rnd=1)
    assert report["problems"] == []
    assert (out / "XX_Drive_fix_r1_3q_front_close-gimbal.png.locator").is_file()
    assert (out / "sheet.png").is_file()
    assert json.loads((out / "report.json").read_text())["problems"] == []


def test_finish_lists_what_keeps_a_set_back(tmp_path):
    out = tmp_path / "r1"
    for v in shots.REQUIRED["look"]:
        if v != "mount":
            _png(out / f"XX_Drive_look_r1_{v}.png")
    (out / "report.json").write_text(json.dumps({"seconds": 41.0, "unmapped_materials": ["Material.012"]}))
    report = look.finish(out, block="XX_Drive", stage="look", rnd=1)
    assert any("missing view: mount" in p for p in report["problems"])
    assert any("unmapped material" in p for p in report["problems"])
    assert report["seconds"] == 41.0


def test_finish_accepts_a_before_after_set(tmp_path):
    out = tmp_path / "r2"
    m = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 5], [0, 0, 0, 1]]
    for v in shots.REQUIRED["look"]:
        for extra in ("before", "after"):
            png = _png(out / f"{shots.shot_name('XX_Drive', 'look', 2, v, extra)}.png")
            shots.save_camera(png, m, 14.5)
    assert look.finish(out, block="XX_Drive", stage="look", rnd=2)["problems"] == []


def test_finish_lists_geometry_failures(tmp_path):
    out = tmp_path / "r1"
    for v in shots.REQUIRED["look"]:
        _png(out / f"XX_Drive_look_r1_{v}.png")
    (out / "geocheck.json").write_text(json.dumps({
        "ok": False, "tris": 50000, "parts": 3,
        "failures": [{"check": "load_path", "parts": ["bracket"], "detail": "1 part(s) don't reach a mount"}],
        "warnings": [{"check": "untagged", "parts": ["a"], "detail": "1 part(s) have no role"}]}))
    report = look.finish(out, block="XX_Drive", stage="look", rnd=1)
    assert report["problems"] == ["geometry: load_path (bracket): 1 part(s) don't reach a mount"]
    assert report["geocheck"]["warnings"] == 1


def test_job_carries_the_geometry_check_settings(tmp_path):
    job = _job(tmp_path, tris_budget=29000)
    assert job["geocheck"] is True and job["tris_budget"] == 29000
    assert _job(tmp_path, geocheck=False)["geocheck"] is False


def test_finish_survives_a_torn_geocheck(tmp_path):
    out = tmp_path / "r1"
    for v in shots.REQUIRED["look"]:
        _png(out / f"XX_Drive_look_r1_{v}.png")
    (out / "geocheck.json").write_text('{"ok": fal')
    report = look.finish(out, block="XX_Drive", stage="look", rnd=1)
    assert report["geocheck"] == {"ok": False, "unreadable": True}
    assert any("geocheck.json unreadable" in p for p in report["problems"])


def test_close_part_matches_by_name_or_base_name():
    assert look.close_hits(["Nozzle.001", "Body", "NozzleRing"], "Nozzle") == ["Nozzle.001"]
    assert look.close_hits(["Body"], "Body") == ["Body"]
    assert look.close_hits(["Body"], "Nozle") == []


def test_finish_lists_a_close_up_that_matched_nothing(tmp_path):
    out = tmp_path / "r1"
    _png(out / "XX_Drive_fix_r1_3q_front.png")
    (out / "report.json").write_text(json.dumps({"close_missing": ["Nozle"]}))
    report = look.finish(out, block="XX_Drive", stage="fix", rnd=1)
    assert report["problems"] == ["close-up: no part named 'Nozle'"]
    assert json.loads((out / "report.json").read_text())["close_missing"] == ["Nozle"]


def test_sheet_leaves_out_the_before_copies(tmp_path, monkeypatch):
    from se2modkit.preview import compose
    out = tmp_path / "r2"
    m = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 5], [0, 0, 0, 1]]
    for extra in ("before", "after"):
        shots.save_camera(_png(out / f"XX_Drive_fix_r2_top_{extra}.png"), m, 14.5)
    _png(out / "XX_Drive_fix_r2_front.png")
    seen = []
    monkeypatch.setattr(compose, "make_sheet", lambda paths, *a, **kw: seen.extend(Path(p).name for p in paths))
    assert look.finish(out, block="XX_Drive", stage="fix", rnd=2)["problems"] == []
    assert seen == ["XX_Drive_fix_r2_front.png", "XX_Drive_fix_r2_top_after.png"]
