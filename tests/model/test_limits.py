from se2modkit.model import limits


def test_welder400_box_limits():
    r = limits.model_limits((2.5, 4.0, 2.5))
    assert r["lods"][0]["max_tris"] == 26133
