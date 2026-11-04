from se2modkit.model import limits


def test_welder400_box_limits():
    r = limits.model_limits((2.5, 4.0, 2.5))
    assert r["lods"][0]["max_tris"] == 26133


def test_cell_group_limit():
    assert limits.MAX_CELL_GROUPS == 128
    assert limits.cell_groups_error("XX_Box", 1) is None
    assert limits.cell_groups_error("XX_Box", 127) is None
    msg = limits.cell_groups_error("XX_Hull", 128)
    assert msg.startswith("XX_Hull: its occupied cells form 128 groups") and "occupancy" in msg
