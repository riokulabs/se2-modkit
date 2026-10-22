"""Run by test_loop_normals.py: blender -b --python blender_loop_normals.py -- <out.json>

Builds three Parts through objects.part_to_object and writes their corner normals:
  plain:   a 12-sided swept tube, no hook layer (the exporter's default shading);
  hooked:  the same tube with every corner's radial normal in the hook layer;
  partial: the same tube with only the first ring's faces hooked (the rest must match plain).
Also records whether the hook attribute is left on the mesh (it must not be)."""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))  # repo src/

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from se2modkit.model import geom as G  # noqa: E402
from se2modkit.model import objects as OB  # noqa: E402
from se2modkit.model.normals import LOOP_NORMAL_LAYER  # noqa: E402

N, R, L = 12, 0.05, 0.4


def tube(name, hook=None):
    """A smooth open tube along x, 3 rings (two segments, the second one twisted 25 deg: sheared quads)."""
    p = G.Part(name)
    bm = p.bm
    rings = []
    for i, x in enumerate((0.0, L / 2, L)):
        tw = math.radians(25) * max(0, i - 1)
        rings.append([bm.verts.new((x, R * math.cos(2 * math.pi * k / N + tw), R * math.sin(2 * math.pi * k / N + tw)))
                      for k in range(N)])
    lay, rid = G.round_id(bm)
    nl = G.loop_normal_layer(bm) if hook else None
    mi = p.mi("Default")
    for s, (r0, r1) in enumerate(zip(rings, rings[1:])):
        for k in range(N):
            j = (k + 1) % N
            f = bm.faces.new((r0[k], r1[k], r1[j], r0[j]))
            f.material_index = mi
            f[lay] = rid
            if nl is not None and hook(s):
                for lp in f.loops:
                    co = lp.vert.co
                    lp[nl] = Vector((0.0, co.y, co.z)).normalized()
    return p


coll = bpy.data.collections.new("t")
bpy.context.scene.collection.children.link(coll)
out = {}
for name, hook in (("plain", None), ("hooked", lambda s: True), ("partial", lambda s: s == 0)):
    ob = OB.part_to_object(tube(name, hook), coll, name)
    me = ob.data
    out[name] = {"normals": [list(c.vector) for c in me.corner_normals],
                 "co": [list(me.vertices[lp.vertex_index].co) for lp in me.loops],
                 "face": [p.index for p in me.polygons for _ in p.loop_indices],
                 "attr_left": LOOP_NORMAL_LAYER in me.attributes}
with open(sys.argv[sys.argv.index("--") + 1], "w") as f:
    json.dump(out, f)
