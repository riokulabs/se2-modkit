"""Tell incremental builds about reads they cannot see (pipeline.incremental records the rest automatically).

Blender's own loaders run in C, so Python's audit hook never sees their files. `bpy.data.libraries.load` is wrapped
for you; for the others call these instead of Blender directly (same arguments, same result):

    from se2modkit.pipeline import inputs
    img = inputs.load_image("//textures/panel.png")                    # bpy.data.images.load
    inputs.import_scene("fbx", "//ref/vent.fbx")                       # bpy.ops.import_scene.fbx(filepath=...)
    inputs.import_scene("obj", "//ref/duct.obj")                       # bpy.ops.wm.obj_import(filepath=...)
    inputs.append("//kit.blend", "Object", "Bolt")                     # bpy.ops.wm.append
    inputs.read("some/file.bin")                                       # anything else: just record it
    inputs.read("//parts")                                             # a folder: every file under it, and its listing

Each records the file as a read of the block being built (its content hash goes into the block's fragment; a change
makes the block stale). Reads that cannot be seen at all must go in `Runner(inputs=fn(spec) -> paths)`: a bare
`exists()` / `stat()` check, environment variables outside the hashed prefixes, files read by C extensions.
"""

from __future__ import annotations

import os
import sys

from .incremental import READ_EVENT

__all__ = ["append", "import_scene", "load_image", "read"]


def _abs(path) -> str:
    p = os.fsdecode(path)
    try:
        import bpy
        p = bpy.path.abspath(p)       # "//" is relative to the open .blend
    except ImportError:
        pass
    return os.path.abspath(p)


def read(path) -> str:
    """Record `path` as read by the block being built; returns its absolute path. A folder is walked, as
    `Runner(inputs=...)` does: every file under it is recorded, and each folder's listing, so a file added, removed or
    changed anywhere below makes the block stale."""
    p = _abs(path)
    if os.path.isdir(p):
        for here, dirs, files in os.walk(p):       # os.walk's listings are recorded by the read hook itself
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                sys.audit(READ_EVENT, os.path.join(here, name))
        return p
    sys.audit(READ_EVENT, p)
    return p


def load_image(filepath, **kw):
    """`bpy.data.images.load(filepath, **kw)`, recorded."""
    import bpy
    return bpy.data.images.load(read(filepath), **kw)


# Formats whose importer Blender 4+ moved to bpy.ops.wm.<name> (the old bpy.ops.import_scene.obj is gone).
_WM_IMPORTERS = {"obj": "obj_import", "stl": "stl_import", "ply": "ply_import", "usd": "usd_import",
                 "abc": "alembic_import", "alembic": "alembic_import"}


def import_scene(fmt: str, filepath, **kw):
    """Blender's importer for `fmt`, recorded: `bpy.ops.import_scene.<fmt>(filepath=..., **kw)` for fbx and gltf,
    `bpy.ops.wm.obj_import` for obj (likewise stl, ply, usd, abc/alembic: Blender 4+ importers)."""
    import bpy
    fmt = fmt.lower()
    op = getattr(bpy.ops.wm, _WM_IMPORTERS[fmt]) if fmt in _WM_IMPORTERS else getattr(bpy.ops.import_scene, fmt)
    return op(filepath=read(filepath), **kw)


def append(blend, kind: str, name: str, **kw):
    """`bpy.ops.wm.append` of `kind`/`name` (e.g. "Object", "Bolt") from the .blend file `blend`, recorded."""
    import bpy
    path = read(blend)
    return bpy.ops.wm.append(filepath=f"{path}/{kind}/{name}", directory=f"{path}/{kind}/", filename=name, **kw)
