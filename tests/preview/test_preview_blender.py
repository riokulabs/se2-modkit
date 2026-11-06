"""The Blender half of the previews (`se2mod blender run tests/run_blender_tests.py -- -m blender tests/preview`): a toy
block's 3/4 shot, its icon and the material stand-ins. Skipped outside Blender."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

bpy = pytest.importorskip("bpy")

from se2modkit.preview import base, icons, mats, turntable  # noqa: E402

pytestmark = pytest.mark.blender


@dataclass
class Spec:
    name: str
    cells: tuple
    family: str = "box"


def _built(name="XX_Box"):
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(bpy.data.materials.new("PaintedMetalColorable"))
    coll = bpy.data.collections.new(name + "_LOD0")
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)
    return {name: {"lods": [(coll, [ob])]}}


def test_turntable_shot_and_icon(tmp_path):
    built = _built()
    old = base.settings()
    try:
        base.configure(tmp_path / "set", fast=True, only=["XX_Box_3q_front"])
        turntable.render_set(built, {"XX_Box": Spec("XX_Box", (4, 4, 4))}, parts=("turntable",))
    finally:
        base.configure(old["out"] or tmp_path, fast=old["fast"], only=old["only"])
    assert (tmp_path / "set/XX_Box_3q_front.png").is_file()
    assert not (tmp_path / "set/XX_Box_ortho_front.png").exists()
    out = icons.icon("XX_Box", base.painted(built, "XX_Box", 0, "keen"), tmp_path / "icons/XX_Box_Icon.png")
    img = bpy.data.images.load(str(out))
    assert tuple(img.size) == (200, 200) and img.channels == 4


def test_material_stand_ins():
    assert mats.keen_key("PaintedMetalColorable.001", "blue") == "PMC:blue"
    assert mats.keen_key("BrushedMetal_Subpart") == "Brushed"
    m = mats.get("PMC:blue")
    assert m.name.startswith("pv_PMC:blue") and mats.get("PMC:blue") is m
    assert mats.get("NoSuchKey").name.startswith("pv_NoSuchKey")
    scaffold = "4050183d-1340-4643-b8b9-124981f2121d"
    assert mats.key_for_guid(scaffold) == scaffold and mats.get(scaffold) is not None


def _tiny_png(path, rgba):
    im = bpy.data.images.new(path.stem, 8, 8)
    im.pixels[:] = list(rgba) * 64
    im.filepath_raw = str(path)
    im.file_format = "PNG"
    im.save()
    bpy.data.images.remove(im)
    return str(path)


def test_sdk_source_map_material(tmp_path, monkeypatch):
    """A Principled BSDF from source maps (tiny PNGs written here): paint through the color mask, AO on the base
    color, roughness and metalness as they are, the normal map into a tangent Normal Map with green not flipped."""
    from se2modkit.preview import sdkmat
    monkeypatch.setitem(mats._PATHS, "texcache", str(tmp_path / "cache"))
    S = sdkmat.Source
    maps = {r: S(path=_tiny_png(tmp_path / f"m_{r}.png", v)) for r, v in (
        ("albedo", (0.5, 0.5, 0.5, 1)), ("metal", (1, 1, 1, 1)), ("roughness", (0.3, 0.3, 0.3, 1)),
        ("normal", (0.5, 0.5, 1, 1)), ("ao", (0.9, 0.9, 0.9, 1)), ("colormask", (1, 1, 1, 1)))}
    maps["emissive"] = S(value=0.0)
    m = mats.sdk_pbr_mat("pv_SDK:Test:blue", sdkmat.Material("Test", "PBRMaterialDefinitionObjectBuilder", maps),
                         "PMC:blue", "blue")
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    linked = {lk.to_socket.name: lk.from_node for lk in nt.links if lk.to_node == b}
    assert set(linked) >= {"Base Color", "Metallic", "Roughness", "Normal"}
    assert linked["Normal"].bl_idname == "ShaderNodeNormalMap" and linked["Normal"].space == "TANGENT"
    feeding = [lk.from_node for lk in nt.links if lk.to_node == linked["Normal"]]
    assert [n.bl_idname for n in feeding] == ["ShaderNodeTexImage"]   # OpenGL green: straight in
    assert feeding[0].image.colorspace_settings.name == "Non-Color"
    paint = [n for n in nt.nodes if n.bl_idname == "ShaderNodeMix" and n.inputs[0].is_linked]
    assert paint and abs(paint[0].inputs[7].default_value[2] - mats.PAINTS["blue"][2] / mats.PAINT_ALBEDO_REF) < 1e-4
    assert not b.inputs["Emission Strength"].default_value
    assert all(p.name.startswith("s1024_") for p in (tmp_path / "cache").glob("*.png"))
