"""Material stand-ins (names are what matter for export; colours and atlas textures are preview only)."""
import os

import bpy

from .config import CV, FONT, PREVIEW, SUBPART_SUFFIX, VANILLA_ASSETS, P1, S1, S2, T1, T2, T3

_DECALS = os.path.join(VANILLA_ASSETS or "", "BlockMaterials", "Decals")
# albedo / alphamask / normal for preview shading (read-only from the SDK)
ATLAS_TEX = {
    T1: os.path.join(_DECALS, "Trims", T1, f"{T1}_{{}}.png"),
    T2: os.path.join(_DECALS, "Trims", T2, f"{T2}_{{}}.png"),
    T3: os.path.join(_DECALS, "Trims", T3, f"{T3}_{{}}.png"),
    P1: os.path.join(_DECALS, "Atlases", "Stickers", P1, f"{P1}_{{}}.png"),
    S1: os.path.join(_DECALS, "Atlases", "Stickers", "AtlasSticker01", "Merged", f"{S1}_{{}}.png"),
    S2: os.path.join(_DECALS, "Atlases", "Stickers", "AtlasSticker02", "Atlas_Sticker02_{}.png"),
    FONT: os.path.join(_DECALS, "Fonts", "BarlowCondensed", f"{FONT}_{{}}.png"),
}
# Opaque atlases shown with their real albedo in previews, so UV mistakes are visible before the game (a conveyor
# door once looked fine with a flat stand-in colour but sampled the atlas' yellow/black bands in game).
OPAQUE_TEX = {CV: os.path.join(VANILLA_ASSETS or "", "BlockMaterials", "ConveyorsAtlas", "ConveyorsAtlas_albedo.png")}
STENCIL_TINT = {FONT: (0.06, 0.06, 0.065, 1)}
PAINT = (0.62, 0.66, 0.70, 1)  # preview paint tint for colour-masked albedo


def get_mat(name, emissive_on=True):
    m = bpy.data.materials.get(name)
    if m:
        return m
    base = name[:-len(SUBPART_SUFFIX)] if name.endswith(SUBPART_SUFFIX) else name
    col, metal, rough, emit = PREVIEW.get(base, ((0.5, 0.5, 0.5, 1), 0.5, 0.5, 0.0))
    m = bpy.data.materials.new(name)
    m.diffuse_color = col
    m.metallic, m.roughness = metal, rough
    try:
        m.use_nodes = True
    except AttributeError:
        pass
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = col
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Roughness"].default_value = rough
    if base.startswith("PaintedMetal"):          # opaque satin paint: weak, broad reflections (no glassy sheen)
        for key in ("Specular IOR Level", "Specular"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = 0.3
                break
        if "Coat Weight" in bsdf.inputs:
            bsdf.inputs["Coat Weight"].default_value = 0.0
    if emit:
        bsdf.inputs["Emission Color"].default_value = col
        bsdf.inputs["Emission Strength"].default_value = emit
    if name == "PV_Plume":
        bsdf.inputs["Alpha"].default_value = 0.3
        _blend(m)
    if name == "ClearGlass":
        bsdf.inputs["Alpha"].default_value = 0.25
        _blend(m)
    if name in ATLAS_TEX:
        _atlas_shading(m, bsdf, name)
    if base in OPAQUE_TEX:
        im = _img(OPAQUE_TEX[base])
        if im:
            t = m.node_tree.nodes.new("ShaderNodeTexImage")
            t.image = im
            m.node_tree.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    return m


def set_emission(name, strength, color=None):
    for m in (bpy.data.materials.get(name), bpy.data.materials.get(name + SUBPART_SUFFIX)):
        if m:
            b = m.node_tree.nodes["Principled BSDF"]
            b.inputs["Emission Strength"].default_value = strength
            if color:
                b.inputs["Emission Color"].default_value = color
                b.inputs["Base Color"].default_value = color


def _blend(m):
    for attr, val in (("surface_render_method", "DITHERED"), ("blend_method", "CLIP")):
        try:
            setattr(m, attr, val)
        except (AttributeError, TypeError):
            pass


def _img(path, noncolor=False):
    if not os.path.exists(path):
        return None
    im = bpy.data.images.load(path, check_existing=True)
    if noncolor:
        im.colorspace_settings.name = "Non-Color"
    return im


def _atlas_shading(m, bsdf, name):
    pat = ATLAS_TEX[name]
    alb, alpha, nrm = _img(pat.format("albedo")), _img(pat.format("alphamask"), True), _img(pat.format("normal"), True)
    if not (alb and alpha):
        return
    nt = m.node_tree
    ta = nt.nodes.new("ShaderNodeTexImage")
    ta.image = alb
    tm = nt.nodes.new("ShaderNodeTexImage")
    tm.image = alpha
    if name in (S1, S2):  # stickers carry their own colours
        nt.links.new(ta.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        mul = nt.nodes.new("ShaderNodeMixRGB")
        mul.blend_type, mul.inputs[0].default_value = "MULTIPLY", 1.0
        mul.inputs[2].default_value = STENCIL_TINT.get(name, PAINT)
        nt.links.new(ta.outputs["Color"], mul.inputs[1])
        nt.links.new(mul.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(tm.outputs["Color"], bsdf.inputs["Alpha"])
    if nrm:
        tn = nt.nodes.new("ShaderNodeTexImage")
        tn.image = nrm
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tn.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    _blend(m)
