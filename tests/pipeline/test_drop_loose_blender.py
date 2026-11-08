"""`standins.merged` drops edges and vertices that belong to no face, so no exported model carries them."""
import pytest

bpy = pytest.importorskip("bpy")

from se2modkit.model import objects as OB  # noqa: E402
from se2modkit.pipeline import standins  # noqa: E402

pytestmark = pytest.mark.blender


def _quad_with_loose(name, coll):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (5, 5, 5), (6, 5, 5), (9, 9, 9)],
                   [(4, 5)], [(0, 1, 2, 3)])
    me.materials.append(bpy.data.materials.new(name + "_mat"))   # block meshes always carry a material
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def test_merged_drops_loose_edges_and_vertices():
    coll = OB.new_collection("loose_test")
    src = _quad_with_loose("loose_src", coll)
    ob = standins.merged([src], "loose_merged", coll)
    me = ob.data
    assert len(me.polygons) == 1 and len(me.vertices) == 4 and len(me.edges) == 4
    assert max(v.co.length for v in me.vertices) < 2      # the far loose pieces no longer stretch the bounds


def test_block_size_ignores_loose_vertices():
    from se2modkit.pipeline import export
    coll = OB.new_collection("loose_size_test")
    src = _quad_with_loose("loose_size_src", coll)
    assert export._size([src]) == (1.0, 1.0, 0.0)          # the loose edge at (5..6, 5, 5) and vertex at 9 don't count
