"""`pipeline.placer`: the offline emulation of SE2's block placer on synthetic grids (no Blender, no game): targeting,
the snap / mount-point / free-position steps, validation, and the mod's mount-snap hook."""

import pytest

from se2modkit.pipeline import placer as PE


def _wall():
    """A 4 x 4 x 1 cell plate at z = 0 whose -z face (Forward) mounts everywhere."""
    pf = PE.Prefab("XX_Wall", (0, 0, 0), (3, 3, 0), ["Forward", "Backward"])
    return PE.placed(pf, PE.ROT_ID, (0, 0, 0))


def _cube():
    return PE.Prefab("XX_Cube", (0, 0, 0), (0, 0, 0), list(PE.DIRS))


def test_cs_div_truncates_toward_zero():
    assert (PE.cs_div(7, 2), PE.cs_div(-7, 2), PE.cs_div(7, -2), PE.cs_div(-7, -2)) == (3, -3, -3, 3)


def test_target_hits_first_occupied_cell_and_face():
    g = PE.Grid([_wall()])
    hit, nrm, pp, dist = g.target((0.25, 0.25, -2.0), (0.0, 0.0, 1.0))
    assert hit == (1, 1, 0) and nrm == (0, 0, -1) and pp == (1, 1, -1)
    assert dist == pytest.approx(2.0 - PE.HALF)
    assert g.target((0.25, 0.25, -2.0), (0.0, 0.0, -1.0)) is None                  # looking away


def test_place_cube_on_wall_validates():
    g = PE.Grid([_wall()])
    r = PE.emulate(g, _cube(), PE.ROT_ID, (0.5, 0.5, -1.5), (0.5, 0.5, 0.0))
    assert r["valid"] and r["mount"] and r["cells"] == {(2, 2, -1)} and r["hit_block"] == "XX_Wall"


def test_place_without_mount_is_invalid():
    pf = PE.Prefab("XX_Bare", (0, 0, 0), (3, 3, 0), [])                          # no mount points at all
    g = PE.Grid([PE.placed(pf, PE.ROT_ID, (0, 0, 0))])
    r = PE.emulate(g, _cube(), PE.ROT_ID, (0.5, 0.5, -1.5), (0.5, 0.5, 0.0))
    assert not r["valid"] and not r["mount"]


def test_free_position_moves_off_occupied_cells():
    """A cube aimed where another already sits moves to the nearest empty offset."""
    g = PE.Grid([_wall(), PE.placed(_cube(), PE.ROT_ID, (2, 2, -1), name="XX_Other")])
    cells, valid, _ = g.place(_cube(), PE.ROT_ID, (2, 2, -1), (0, 0, -1))
    assert cells != {(2, 2, -1)} and g.empty(cells) and valid


def test_snap_hook_runs_after_the_placer():
    seen = []

    def snap(grid, prefab, R, result):
        seen.append(result["cells"])
        return dict(result, snapped=True)
    g = PE.Grid([_wall()])
    r = PE.emulate(g, _cube(), PE.ROT_ID, (0.5, 0.5, -1.5), (0.5, 0.5, 0.0), snap=snap)
    assert r["snapped"] and seen == [{(2, 2, -1)}]
    assert PE.emulate(g, _cube(), PE.ROT_ID, (0.5, 0.5, -1.5), (0.5, 0.5, 3.0), snap=snap)["snapped"]
    assert PE.emulate(g, _cube(), PE.ROT_ID, (9.0, 9.0, -1.5), (9.0, 9.0, 0.0), snap=snap) is None   # no target


def test_mounted_for_round_trip():
    pf = PE.Prefab("XX_Long", (0, 0, 0), (0, 0, 2), ["Forward"])
    b = PE.placed(pf, PE.ROT_YAW180, (5, 0, 7))
    assert PE.mounted_for(pf, PE.ROT_YAW180, b.cells) == (5, 0, 7)
