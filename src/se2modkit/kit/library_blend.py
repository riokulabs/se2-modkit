"""Blender side of `se2mod kit library` (run only through the job queue, never directly):

  blender -b --python library_blend.py -- probe LIBRARY.blend OUT.json
      opens the library read only (never saved) and writes OUT.json: every image (its path, whether it is packed,
      whether the file it points at exists here), linked libraries, and each material's and node group's nodes and
      links, so the preview materials can follow how Keen wires its maps;
  blender -b --python library_blend.py -- register FOLDER NAME
      adds FOLDER as the asset library NAME in the user's Blender preferences (or updates its path) and saves them.
"""

import json
import os
import sys

import bpy


def _value(v):
    try:
        return [round(float(x), 4) for x in v]
    except TypeError:
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            return str(v)


def _node(n):
    d = {"type": n.bl_idname, "name": n.name}
    if n.label:
        d["label"] = n.label
    img = getattr(n, "image", None)
    if img is not None:
        d["image"] = img.name
        d["colorspace"] = img.colorspace_settings.name
    for attr in ("space", "blend_type", "data_type", "operation", "mode", "interpolation", "projection"):
        if hasattr(n, attr):
            d[attr] = str(getattr(n, attr))
    tree = getattr(n, "node_tree", None)
    if tree is not None:
        d["group"] = tree.name
    unlinked = {}
    for s in n.inputs:
        if not s.is_linked and hasattr(s, "default_value") and s.enabled:
            unlinked[s.identifier] = _value(s.default_value)
    if unlinked:
        d["inputs"] = unlinked
    return d


def _tree(nt):
    return {"nodes": [_node(n) for n in nt.nodes],
            "links": [[l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier]
                      for l in nt.links]}


def probe(blend, out):
    bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False)
    images = []
    for im in bpy.data.images:
        path = im.filepath
        absp = bpy.path.abspath(path) if path else ""
        images.append({"name": im.name, "filepath": path, "abspath": absp, "packed": im.packed_file is not None,
                       "source": im.source, "exists": bool(absp) and os.path.exists(absp),
                       "colorspace": im.colorspace_settings.name})
    data = {
        "blend": blend,
        "blender": bpy.app.version_string,
        "images": images,
        "libraries": [{"name": lib.name, "filepath": lib.filepath,
                       "exists": os.path.exists(bpy.path.abspath(lib.filepath))} for lib in bpy.data.libraries],
        "materials": {m.name: dict(_tree(m.node_tree), asset=m.asset_data is not None)
                      for m in bpy.data.materials if m.use_nodes and m.node_tree},
        "node_groups": {g.name: _tree(g) for g in bpy.data.node_groups},
        "counts": {"objects": len(bpy.data.objects), "materials": len(bpy.data.materials),
                   "node_groups": len(bpy.data.node_groups), "images": len(bpy.data.images)},
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    missing = [i for i in images if not i["packed"] and i["filepath"] and not i["exists"]]
    print(f"library probe: {len(images)} images, {sum(i['packed'] for i in images)} packed, {len(missing)} missing")


def register(folder, name):
    libs = bpy.context.preferences.filepaths.asset_libraries
    lib = next((lib for lib in libs if lib.name == name), None)
    if lib is None:
        bpy.ops.preferences.asset_library_add(directory=folder)
        lib = libs[-1]
        lib.name = name
    lib.path = folder
    bpy.ops.wm.save_userpref()
    print(f"asset library {name!r} -> {folder} (saved to the Blender preferences)")


def main(argv):
    if argv[:1] == ["probe"] and len(argv) == 3:
        probe(argv[1], argv[2])
    elif argv[:1] == ["register"] and len(argv) == 3:
        register(argv[1], argv[2])
    else:
        raise SystemExit("usage: library_blend.py -- probe BLEND OUT.json | register FOLDER NAME")


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
