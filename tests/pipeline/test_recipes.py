from __future__ import annotations

import math
from dataclasses import dataclass

from se2modkit.pipeline import recipes as R

# A made-up vanilla source (not a real block's numbers): 10 x 10 x 10 cells, Mostly Solid.
SOURCE = {"cells": (10, 10, 10), "density": "MostlySolid", "critical": (("SteelPlate", 2), ("Motor", 1)),
          "optional": (("SteelPlate", 1),), "time": 1000}


@dataclass
class B:
    name: str
    cells: tuple
    copy_from: str = "Src"


def test_game_mass_formula():
    assert R.mass(1000, "MostlySolid") == 20 * math.sqrt(1000) * 3 + 5
    assert R.cells((2, 3, 4)) == 24
    assert R.round_amount(0.2) == 1 and R.round_amount(2.5) == 3


def test_volume_rule_keeps_game_scale_one():
    src_mass = R.mass(1000, "MostlySolid")
    k = R.source_scale(SOURCE)
    assert k == round(src_mass / (3 * 35 + 25))
    same = R.cost(B("XX_Same", (10, 10, 10)), SOURCE)
    assert same["critical"] == [("SteelPlate", 2 * k), ("Motor", k)] and same["game_scale"] == 1
    half = R.cost(B("XX_Half", (10, 10, 5)), SOURCE, kit=((("Motor", 2), ("Wire", 1)), ()))
    assert half["ratio"] < 1 and ("Wire", 1) in half["critical"] and half["source"] == "Src"
    assert dict(half["critical"])["Motor"] == R.round_amount(k * half["ratio"]) + 2


def test_occupied_from_report_and_markdown():
    rep = {"blocks": {"XX_A": {"placement": {"occupied": 7}}}}
    assert R.occupied(B("XX_A", (2, 2, 2)), rep) == 7 and R.occupied(B("XX_B", (2, 2, 2)), rep) == 8
    md = R.markdown({"XX_A": R.cost(B("XX_A", (2, 2, 2)), SOURCE, report=rep)})
    assert md.splitlines()[2].startswith("| XX_A | Src (x") and R.fmt(()) == "-"
    assert R.add([("A", 1)], [("A", 2), ("B", 1)]) == [("A", 3), ("B", 1)]
