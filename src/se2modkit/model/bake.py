"""LastLOD bake (Art Omnibus p9-10): high = second-to-last LOD (LOD3), low = last LOD (LOD4 box).

128 x 128 PNGs named <Model>_LastLOD_{albedo,ao,metal,normal,roughness,colormask,emissive,alphamask}, the same
set vanilla Welder400 ships. Scalar maps are baked with the "emit trick": every material temporarily emits
its own metal / roughness / colour-mask value and the bake pass is EMIT (that is how the Omnibus describes the
metal and colormask maps). Cycles bake runs headless as well as windowed.
"""
import os

import bpy

from .config import C, DK, EC, EM, ES, PREVIEW

SIZE = 128

# per-material values for the scalar maps (stand-ins for the library materials)
COLORMASK = {C: 1.0, DK: 0.0}           # Omnibus: Colorable white, Darker 0 (everything else 0)
EMISSIVE = {EM: 1.0, EC: 1.0, ES: 1.0}


def _image(name, path, noncolor):
    img = bpy.data.images.get(name)
    if img:
        bpy.data.images.remove(img)
    img = bpy.data.images.new(name, SIZE, SIZE, alpha=False, float_buffer=False)
    if noncolor:
        img.colorspace_settings.name = "Non-Color"
    img.filepath_raw = path
    img.file_format = "PNG"
    return img


def _target_node(ob, img):
    for slot in ob.material_slots:
        nt = slot.material.node_tree
        n = nt.nodes.get("SE2_BakeTarget") or nt.nodes.new("ShaderNodeTexImage")
        n.name = "SE2_BakeTarget"
        n.image = img
        nt.nodes.active = n


def _emit_override(mats, value_of):
    """Make every material emit value_of(material name) (grey); returns a restore function."""
    saved = []
    for m in mats:
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        if not bsdf:
            continue
        v = value_of(m.name)
        saved.append((bsdf, tuple(bsdf.inputs["Emission Color"].default_value),
                      bsdf.inputs["Emission Strength"].default_value))
        bsdf.inputs["Emission Color"].default_value = (v, v, v, 1)
        bsdf.inputs["Emission Strength"].default_value = 1.0

    def restore():
        for bsdf, col, s in saved:
            bsdf.inputs["Emission Color"].default_value = col
            bsdf.inputs["Emission Strength"].default_value = s
    return restore


def bake_lastlod(high_objs, low, out_dir, prefix, flip_green=False):
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    prev_engine = scene.render.engine
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.device = "CPU"
    bk = scene.render.bake
    bk.use_selected_to_active = True
    bk.cage_extrusion = 0.02
    bk.max_ray_distance = 1.6
    bk.margin = 2
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in high_objs:
        o.hide_render = False
        o.select_set(True)
    low.hide_render = False
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    high_mats = {s.material for o in high_objs for s in o.material_slots if s.material}
    files = {}

    def run(kind, bake_type, noncolor, pass_filter=None, emit=None):
        path = os.path.join(out_dir, f"{prefix}_{kind}.png")
        img = _image(f"{prefix}_{kind}", path, noncolor)
        _target_node(low, img)
        restore = _emit_override(high_mats, emit) if emit else None
        kw = {"type": bake_type}
        if pass_filter:
            kw["pass_filter"] = pass_filter
        if bake_type == "NORMAL":
            kw.update(normal_space="TANGENT", normal_r="POS_X", normal_g="NEG_Y" if flip_green else "POS_Y",
                      normal_b="POS_Z")
        bpy.ops.object.bake(**kw)
        if restore:
            restore()
        img.save()
        files[kind] = path

    # the low-poly box encloses the high-poly: keep it out of the rays, or AO bakes black
    for attr in ("visible_diffuse", "visible_glossy", "visible_shadow", "visible_transmission", "visible_volume_scatter"):
        if hasattr(low, attr):
            setattr(low, attr, False)
    if scene.world is None:
        scene.world = bpy.data.worlds.new("BakeWorld")
    scene.world.light_settings.distance = 0.6  # AO reach (m)
    run("albedo", "DIFFUSE", False, {"COLOR"})
    run("normal", "NORMAL", True)
    run("ao", "AO", True)
    run("metal", "EMIT", True, emit=lambda n: PREVIEW.get(n, ((0, 0, 0, 1), 0.0, 0.5, 0))[1])
    run("roughness", "EMIT", True, emit=lambda n: PREVIEW.get(n, ((0, 0, 0, 1), 0.0, 0.5, 0))[2])
    run("colormask", "EMIT", True, emit=lambda n: COLORMASK.get(n, 0.0))
    run("emissive", "EMIT", True, emit=lambda n: EMISSIVE.get(n, 0.0))
    # alphamask: the box is fully opaque
    path = os.path.join(out_dir, f"{prefix}_alphamask.png")
    img = _image(f"{prefix}_alphamask", path, True)
    img.pixels[:] = [1.0] * (SIZE * SIZE * 4)
    img.save()
    files["alphamask"] = path
    scene.render.engine = prev_engine
    return files
