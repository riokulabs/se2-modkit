"""Block costs by the game's own mass rule (no Blender).

How SE2 turns a recipe into a cost (the block's mass from its occupied cells and density, then the recipe scaled to
that mass):

    mass  = Density.MassCurveModifier * sqrt(n) * log10(n) + MinBlockMass(5)     n = occupied grid cells
    scale = mass / sum(recipe amount * item MassPerUnit), rounded half up, at least 1
    cost  = every recipe amount * scale

So a recipe is a MIX the game multiplies by a whole number. The volume rule here: a block costs its vanilla SOURCE
block's in-game cost (the source recipe x the source's own game scale) x (our mass / the source's mass), both masses
from the game's formula at the block's density, each item rounded half up and at least 1; then the block's own
`kit` (the machinery the source does not have) is added. The recipe is written with these final amounts, so the
game's scale on it stays 1 (`cost()["game_scale"]`; a mod's test should assert it).

A source row (a mod's table of the vanilla blocks it clones) is a dict:
    {"cells": (x, y, z), "density": "MostlySolid", "critical": (("SteelPlate", 3), ...), "optional": (...),
     "time": build ticks, "file": the SDK recipe file, ...}
Items are keys of `ITEMS` (or of the table passed as `items`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

__all__ = ["DENSITY", "ITEMS", "MIN_BLOCK_MASS", "add", "cells", "cost", "fmt", "game_scale", "markdown", "mass",
           "occupied", "round_amount", "set_mass", "source_scale"]

# Identifiers of vanilla content that mods reference, as .def files do, with the game constants the mass rule needs.
MIN_BLOCK_MASS = 5        # System/Configurations/CubeBlockMassConfiguration.def
DENSITY = {               # Blocks/Shared/Density/*.def: name -> (guid, MassCurveModifier)
    "Hollow": ("3dca2cf9-e9b5-4df4-99ed-f7d12f2fce7b", 7),
    "MostlyHollow": ("d8adcfdc-f8e2-467e-9d27-78deae4057da", 11),
    "MostlySolid": ("0dc50007-32a5-4b11-97e5-22a34d7c3d3c", 20),
    "Solid": ("0177a842-4cd2-4318-963a-900824601d83", 35),
}
# Vanilla items: key -> (ItemDefinition GUID, MassPerUnit kg, the SDK file under
# GameData/Vanilla/Assets that defines it). Some ItemDefinitions live in partial definitions (a Copy diff whose
# DefinitionType is ItemDefinition).
ITEMS = {
    "SteelPlate": ("292eba9e-e5f8-4951-8038-e476a677b584", 35, "Components/Simple/SteelPlate/ItemSteelPlate.def"),
    "Construction": ("824e331b-a798-479e-9276-5d35dc02df35", 5,
                     "Components/Simple/ConstructionComp/ItemConstructionComponent.def"),
    "ElectronicParts": ("3df97616-e714-4ddc-baa6-d8fa2df800ce", 5,
                        "Components/Simple/ElectronicParts/ItemElectronicParts.partialdef"),
    "Motor": ("13fe6b89-4d27-4291-88c0-d0ba06065fa0", 25, "Components/Simple/Motor/ItemMotor.def"),
    "Wire": ("e1f56096-b043-4f8e-aca0-40423c54b603", 20, "Components/Simple/Wire/ItemWire.def"),
    "Glass": ("f882a509-434f-4d05-ad2d-41491f6591b5", 20, "Components/Simple/Glass/ItemGlass.def"),
    "Motherboard": ("828c2c1e-25e0-44c7-870a-a20810725726", 40,
                    "Components/Complex/Motherboard/MotherboardItem Base_Client_FloatingObjectItem.partialdef"),
    "HardDrive": ("616013e6-bbe2-44df-b762-9bcb79771238", 80, "Components/Complex/HardDrive/HardDrivepartial.partialdef"),
    "PowerModule": ("c8f857ea-30eb-44bd-82bc-9957d764a38e", 250,
                    "Components/Complex/PowerModule/PowerModuleItem Base_Client_FloatingObjectItem.partialdef"),
    "ChromeCoating": ("8d847ff1-7dae-473e-8234-83170492af3f", 15,
                      "Components/Simple/ChromeCoating/ChromeCoatingItem Base_Client_FloatingObjectItem.partialdef"),
    "RadioComm": ("8d182ce6-7806-49bd-ac72-827bb697d24a", 85,
                  "Components/Complex/Radio-commModule/Radio-commModuleItem Base_Client_FloatingObjectItem.partialdef"),
    "HeavyDutyPlate": ("471510bf-8796-4252-b2c4-536782f91fd6", 120,
                       "Components/Complex/Heavy-dutyPlate/Heavy-dutyPlateItem Base_Client_FloatingObjectItem.partialdef"),
    "IonChamber": ("1a7feb54-b4e3-4f5e-b850-2639f33ce251", 30, "Components/Complex/IonChamber/IonChamber_Item.def"),
    "GoldThread": ("d92a5083-ff39-4fd9-a876-fef754d08c13", 5,
                   "Components/Simple/GoldThread/GoldThreadItem_FloatingObjectItem.partialdef"),
    "Compressor": ("119b310d-0bc2-4139-87f3-62fc012423b5", 90, "Components/Complex/Compressor/Compressor_Item.def"),
}

Amounts = Sequence[tuple[str, int]]


def mass(n: int, density: str) -> float:
    """The game's block mass for n occupied cells."""
    return DENSITY[density][1] * math.sqrt(n) * math.log10(n) + MIN_BLOCK_MASS


def cells(box: Sequence[int]) -> int:
    return box[0] * box[1] * box[2]


def round_amount(x: float) -> int:
    """Half up, at least 1."""
    return max(1, int(math.floor(x + 0.5)))


def set_mass(amounts: Amounts, items: Mapping = ITEMS) -> float:
    return sum(a * items[i][1] for i, a in amounts)


def game_scale(m: float, critical: Amounts, optional: Amounts, items: Mapping = ITEMS) -> int:
    """What the game multiplies a recipe by: mass / recipe mass, rounded half up, at least 1."""
    return max(1, int(math.floor(m / set_mass(tuple(critical) + tuple(optional), items) + 0.5)))


def source_scale(source: Mapping, items: Mapping = ITEMS) -> int:
    """The game's scale on a vanilla source block's own recipe."""
    return game_scale(mass(cells(source["cells"]), source["density"]), source["critical"], source["optional"], items)


def occupied(spec, report: Mapping | None = None) -> int:
    """Occupied cells: the measured placement occupancy (build_report.json) when built, else the cell box."""
    e = (report or {}).get("blocks", {}).get(spec.name, {}).get("placement", {})
    return e.get("occupied") or cells(spec.cells)


def add(amounts: Amounts, extra: Amounts) -> list[tuple[str, int]]:
    """amounts + extra, summing an item already present (order kept, new items appended)."""
    out = [list(x) for x in amounts]
    for i, a in extra:
        for x in out:
            if x[0] == i:
                x[1] += a
                break
        else:
            out.append([i, a])
    return [(x[0], x[1]) for x in out]


def cost(spec, source: Mapping, *, kit: tuple[Amounts, Amounts] = ((), ()), report: Mapping | None = None,
         density: str | None = None, items: Mapping = ITEMS, source_name: str | None = None) -> dict:
    """The volume rule for one block cloned from `source` (module docstring), plus its `kit` (critical, optional).
    `density`: the block's own, when it differs from the source's. Returns {"source", "n", "n_src", "density",
    "mass", "mass_src", "ratio", "source_scale", "critical", "optional", "time", "game_scale", "kit"}."""
    n, n_src = occupied(spec, report), cells(source["cells"])
    dens = density or source["density"]
    m, m_src = mass(n, dens), mass(n_src, dens)
    r = m / m_src
    k = source_scale(source, items)
    crit = add([(i, round_amount(a * k * r)) for i, a in source["critical"]], kit[0])
    opt = add([(i, round_amount(a * k * r)) for i, a in source["optional"]], kit[1])
    return {"source": source_name or getattr(spec, "copy_from", None), "n": n, "n_src": n_src, "density": dens,
            "mass": round(m, 1), "mass_src": round(m_src, 1), "ratio": round(r, 3), "source_scale": k,
            "critical": crit, "optional": opt, "time": source.get("time"),
            "game_scale": game_scale(m, crit, opt, items), "kit": kit}


def fmt(amounts: Amounts) -> str:
    return ", ".join(f"{a} {i}" for i, a in amounts) or "-"


def markdown(table: Mapping[str, Mapping] | Iterable[tuple[str, Mapping]]) -> str:
    """A Markdown table of {block name: cost(...)}."""
    rows = ["| Block | Source | Cells ours / source | Mass ours / source (kg) | Critical | Optional |",
            "|---|---|---|---|---|---|"]
    for name, c in (table.items() if isinstance(table, Mapping) else table):
        rows.append(f"| {name} | {c['source']} (x{c['source_scale']} in game) | {c['n']} / {c['n_src']} | "
                    f"{c['mass']} / {c['mass_src']} | {fmt(c['critical'])} | {fmt(c['optional'])} |")
    return "\n".join(rows)
