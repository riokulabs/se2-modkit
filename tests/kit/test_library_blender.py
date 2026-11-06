"""The Blender half of `se2mod kit library --relink` on a tiny synthetic library (`se2mod blender run
tests/run_blender_tests.py`): images pointing at another machine's folders, one of which exists here. Skipped outside
Blender."""

import json

import pytest

bpy = pytest.importorskip("bpy")

from se2modkit.kit import library_blend as LB  # noqa: E402

pytestmark = pytest.mark.blender


def _library(tmp_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for name in ("Found_albedo", "Lost_normal"):
        im = bpy.data.images.new(name, 4, 4)
        im.source = "FILE"
        im.filepath = f"//../../../Elsewhere/Vanilla/Content/{name}.png"
        im.use_fake_user = True
    lib = tmp_path / "cache" / "Lib-1234"
    lib.mkdir(parents=True)
    blend = lib / "Lib.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    return blend


def test_relink_points_found_images_here_and_saves_a_copy(tmp_path):
    blend = _library(tmp_path)
    before = blend.read_bytes()
    here = tmp_path / "game" / "Found_albedo.png"
    here.parent.mkdir()
    png = bpy.data.images.new("png", 4, 4)
    png.filepath_raw, png.file_format = str(here), "PNG"
    png.save()
    bpy.data.images.remove(png)
    mapping = tmp_path / "relink.json"
    mapping.write_text(json.dumps({"//../../../Elsewhere/Vanilla/Content/Found_albedo.png": str(here)}))
    out = tmp_path / "cache" / "Lib-1234-relinked" / "Lib.blend"
    result = tmp_path / "result.json"
    try:
        LB.relink(str(blend), str(mapping), str(out), str(result))
        r = json.loads(result.read_text())
        assert [x["name"] for x in r["relinked"]] == ["Found_albedo"]
        assert r["missing"] == ["//../../../Elsewhere/Vanilla/Content/Lost_normal.png"]
        assert blend.read_bytes() == before                  # the library it read is never saved over
        bpy.ops.wm.open_mainfile(filepath=str(out), load_ui=False)
        assert bpy.path.abspath(bpy.data.images["Found_albedo"].filepath) == str(here)
        with pytest.raises(SystemExit):
            LB.relink(str(blend), str(mapping), str(blend), str(result))
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)
