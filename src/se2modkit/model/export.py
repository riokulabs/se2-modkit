"""FBX / collision glTF export through Keen's Blender VRAGE Tools add-on (falls back to bpy for FBX)."""
import json

import bpy


def select_only(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.hide_set(False)
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def vrt_ops():
    try:
        import addon_utils
        if not addon_utils.check("vrage_tools")[1]:
            addon_utils.enable("vrage_tools", default_set=False)
        from vrage_tools.functions import fn_operators
        _ = bpy.context.scene.vrt
        return fn_operators
    except Exception as e:  # noqa: BLE001
        print("WARNING: VRAGE Tools not available:", e)
        return None


def export_fbx(objs, path, ops):
    select_only(objs)
    if ops:
        bpy.context.scene.vrt.export_limit = "SELECTED_OBJECTS"
        ops.export_fbx_quick(path)
    else:  # identical settings to vrage_tools.functions.fn_operators.export_fbx_quick
        bpy.ops.export_scene.fbx(filepath=path, check_existing=False, use_selection=True,
                                 object_types={"EMPTY", "MESH", "ARMATURE", "OTHER"},
                                 use_custom_props=True, apply_scale_options="FBX_SCALE_ALL")


def add_rigid_bodies(objs):
    scene = bpy.context.scene
    if scene.rigidbody_world is None:
        bpy.ops.rigidbody.world_add()
    for ob in objs:
        if ob.rigid_body:
            continue
        bpy.context.view_layer.objects.active = ob
        for o in bpy.context.view_layer.objects:
            o.select_set(o == ob)
        bpy.ops.rigidbody.object_add(type="PASSIVE")
        ob.rigid_body.collision_shape = "CONVEX_HULL"
        ob.rigid_body.friction = 0.5
        ob.rigid_body.restitution = 0.0


def export_collision(objs, path, ops):
    if not ops:
        print("WARNING: skipping collision glTF (needs VRAGE Tools, i.e. windowed Blender)")
        return None
    add_rigid_bodies(objs)
    select_only(objs)
    bpy.context.scene.vrt.export_limit = "SELECTED_OBJECTS"
    bpy.context.scene.msft_physics_exporter_props.enabled = True  # the add-on's hook clears it after each export
    ops.export_gltf_physics_quick(path)
    with open(path) as f:
        g = json.load(f)
    return {
        "extensionsUsed": g.get("extensionsUsed", []),
        "colliders": len(g.get("extensions", {}).get("MSFT_collision_primitives", {}).get("colliders", [])),
        "nodes": [n.get("name") for n in g.get("nodes", [])],
    }
