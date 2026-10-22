"""Preview renders (Eevee) and the block icon."""
import math
import os

import bpy
from mathutils import Vector

from .config import EC, ES
from .materials import set_emission


def setup(scene, res=(1800, 1200)):
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = eng
            break
        except TypeError:
            continue
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.film_transparent = False
    try:
        scene.eevee.taa_render_samples = 48
    except AttributeError:
        pass
    try:
        scene.view_settings.view_transform = "AgX"
    except TypeError:
        pass
    world = bpy.data.worlds.get("PreviewWorld") or bpy.data.worlds.new("PreviewWorld")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.035, 0.04, 0.05, 1)
    bg.inputs["Strength"].default_value = 1.0
    if not nt.nodes.get("SE2_Studio"):
        # studio environment for reflections: a bright soft "softbox" overhead fading to dark below; camera rays
        # still see the neutral background (Light Path: Is Camera Ray), so chrome reads as polished metal
        tc = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.name = "SE2_Studio"
        ramp.color_ramp.elements[0].position = 0.35
        ramp.color_ramp.elements[0].color = (0.02, 0.022, 0.025, 1)
        ramp.color_ramp.elements[1].position = 0.85
        ramp.color_ramp.elements[1].color = (1.6, 1.6, 1.65, 1)
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = -1.0, 1.0
        env = nt.nodes.new("ShaderNodeBackground")
        lp = nt.nodes.new("ShaderNodeLightPath")
        mix = nt.nodes.new("ShaderNodeMixShader")
        out = nt.nodes.get("World Output")
        nt.links.new(tc.outputs["Generated"], sep.inputs[0])
        nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
        nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], env.inputs["Color"])
        nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
        nt.links.new(env.outputs["Background"], mix.inputs[1])
        nt.links.new(bg.outputs["Background"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    if not bpy.data.objects.get("Key"):
        for name, rot, energy in (("Key", (50, 0, 35), 4.0), ("Rim", (-60, 0, 200), 2.0), ("Fill", (70, 0, -110), 1.0)):
            ld = bpy.data.lights.new(name, "SUN")
            ld.energy = energy
            lo = bpy.data.objects.new(name, ld)
            lo.rotation_euler = [math.radians(a) for a in rot]
            scene.collection.objects.link(lo)
    cam = bpy.data.objects.get("PreviewCam")
    if not cam:
        cam = bpy.data.objects.new("PreviewCam", bpy.data.cameras.new("PreviewCam"))
        scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def look(cam, loc, target, lens=50, ortho=None):
    cam.location = Vector(loc)
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = lens
    if ortho:
        cam.data.type, cam.data.ortho_scale = "ORTHO", ortho
    else:
        cam.data.type = "PERSP"


def shoot(scene, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return path


def show_only(collections_visible):
    """Render visibility per collection name."""
    for c in bpy.data.collections:
        c.hide_render = c.name not in collections_visible


def lights(status, group2):
    """Preview emission state: status strips (EmissiveOff, state-driven in game) and the second light group (`group2`)."""
    set_emission(ES, status)
    set_emission(EC, group2)


TURNTABLE = [  # (name, camera location, target) for a 4.0 m long block; +Y is the working face
    ("front_3q", (5.2, 7.6, 3.6), (0, 0.15, -0.1)),
    ("rear_3q", (-5.6, -7.0, 3.4), (0, -0.2, -0.1)),
    ("front", (0.0, 10.5, 0.5), (0, 0.0, 0.0)),
    ("back", (0.0, -10.5, 0.5), (0, 0.0, 0.0)),
    ("left", (-10.5, 0.0, 0.5), (0, 0.0, 0.0)),
    ("right", (10.5, 0.0, 0.5), (0, 0.0, 0.0)),
    ("top", (0.3, 0.0, 11.0), (0, 0.0, 0.0)),
    ("bottom", (0.3, 0.0, -11.0), (0, 0.0, 0.0)),
]


def turntable(scene, cam, prefix, views=None, lens=50):
    out = []
    for name, loc, tgt in TURNTABLE:
        if views and name not in views:
            continue
        look(cam, loc, tgt, lens)
        out.append(shoot(scene, f"{prefix}_{name}.png"))
    return out


def icon(scene, cam, path, size=200):
    """Vanilla-style block icon: transparent background, 3/4 view from front-right-above, tight framing."""
    old = (scene.render.resolution_x, scene.render.resolution_y, scene.render.film_transparent)
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.film_transparent = True
    # vanilla icons: 3/4 view from above, working face toward the front-left, object ~70 % of the frame
    look(cam, (-4.6, 5.6, 4.0), (0, 0.0, -0.05), ortho=5.7)
    rd = scene.render
    stamps = {a: getattr(rd, a) for a in dir(rd) if a.startswith("use_stamp")}
    for a in stamps:  # no metadata text chunks in the icon PNG
        try:
            setattr(rd, a, False)
        except (AttributeError, TypeError):
            pass
    shoot(scene, path)
    for a, v in stamps.items():
        try:
            setattr(rd, a, v)
        except (AttributeError, TypeError):
            pass
    scene.render.resolution_x, scene.render.resolution_y, scene.render.film_transparent = old
    strip_icc(path)
    return path


def strip_icc(path):
    """Re-save the PNG without any embedded ICC profile (the project validator rejects some sRGB profiles)."""
    im = bpy.data.images.load(path, check_existing=False)
    im.reload()
    # Blender does not embed ICC profiles in PNGs; verify with PIL in the build report (see build_model.py).
    bpy.data.images.remove(im)
