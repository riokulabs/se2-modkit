"""Synthetic blocks for the pipeline tests (shared by conftest.py and the test modules): `make_spec`, `boxes_of` and a
two-zone `toy_plan` for the family "box"."""

from __future__ import annotations

from dataclasses import dataclass

from se2modkit.pipeline import fracture as FR

BOX_LO, BOX_HI = (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)


@dataclass
class SyntheticSpec:
    """The block fields the pipeline reads: its name, family and size (m), and a free variant."""
    name: str
    family: str = "box"
    size: tuple = (1.0, 1.0, 1.0)
    variant: str = ""


def make_spec(family="box", size=(1.0, 1.0, 1.0), name=None, variant=""):
    return SyntheticSpec(name or f"XX_{family.capitalize()}", family, tuple(size), variant)


def boxes_of(spec):
    """The collision dict of a spec: one regular physics box filling its size (centred Blender frame)."""
    h = tuple(s / 2 for s in spec.size)
    return {"regular": [("body", tuple(-v for v in h), h)], "occupancy": []}


def toy_plan(spec, boxes):
    """Two zones split at x = 0: Fracture_01 on +x (critical), Fracture_02 on -x."""
    zones = [FR.Zone("Fracture_01", [((0, 0, 0), (1, 0, 0))], "+x half", critical=True),
             FR.Zone("Fracture_02", [((0, 0, 0), (-1, 0, 0))], "-x half")]
    return FR.Plan(spec, spec.family, zones, [FR.Seam((0, 0, 0), (1, 0, 0), 0, 1, snap=0.05)])
