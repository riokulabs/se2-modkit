"""The Rioku decal atlas element table (`atlas/rioku.toml`): our own emblems and signage, never vanilla art."""

from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import KIT_DIR

__all__ = ["Atlas", "AtlasError", "Element", "default", "load"]

DEFAULT = KIT_DIR / "atlas" / "rioku.toml"


class AtlasError(ValueError):
    """The element table is inconsistent."""


@dataclass(frozen=True)
class Element:
    name: str
    family: str                                # elements of one family are the same artwork at different scales
    source: str                                # SVG, relative to the kit directory
    rect: tuple[int, int, int, int]            # x0, y0, x1, y1 in px, origin top-left
    size: tuple[float, float]                  # m, the drawn size the cell is made for
    scale: float = 1.0                         # texel density over the atlas's px_per_m


@dataclass(frozen=True)
class Atlas:
    name: str                                  # stem of the built texture files
    material: str                              # the material a mod defines for the texture
    size: tuple[int, int]                      # px
    px_per_m: float                            # texel density of scale-1 elements
    elements: dict[str, Element]

    def uv(self, name: str) -> tuple[float, float, float, float]:
        """(u0, v0, u1, v1) of an element; v runs up from the texture's bottom edge, as in Blender."""
        x0, y0, x1, y1 = self.elements[name].rect
        w, h = self.size
        return (x0 / w, 1 - y1 / h, x1 / w, 1 - y0 / h)

    def pick(self, family: str, drawn: float) -> Element:
        """The smallest element of `family` that keeps at least `px_per_m` when drawn `drawn` m across (its larger
        side), else the largest."""
        els = sorted((e for e in self.elements.values() if e.family == family), key=lambda e: max(e.size) * e.scale)
        if not els:
            raise KeyError(family)
        return next((e for e in els if max(e.size) * e.scale >= drawn - 1e-9), els[-1])


def load(path: Path | None = None) -> Atlas:
    """Read and check an element table: every rect inside the texture, no overlaps, the sources present and each
    rect matching its size at the atlas's texel density."""
    path = DEFAULT if path is None else Path(path)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    head = data.get("atlas", {})
    try:
        w, h = (int(v) for v in head["size"])
        ppm = float(head["px_per_m"])
        name, material = str(head["name"]), str(head["material"])
    except (KeyError, TypeError, ValueError) as e:
        raise AtlasError(f"{path.name}: [atlas] needs name, material, size and px_per_m ({e})") from None
    elements: dict[str, Element] = {}
    for raw in data.get("element", []):
        try:
            el = Element(name=raw["name"], family=raw.get("family", raw["name"]), source=raw["source"],
                         rect=tuple(int(v) for v in raw["rect"]), size=tuple(float(v) for v in raw["size"]),
                         scale=float(raw.get("scale", 1.0)))
        except (KeyError, TypeError, ValueError) as e:
            raise AtlasError(f"{path.name}: an element needs name, source, rect and size ({e!r})") from None
        if len(el.rect) != 4 or len(el.size) != 2:
            raise AtlasError(f"{path.name}: element {el.name} needs rect = [x0, y0, x1, y1] and size = [w, h]")
        x0, y0, x1, y1 = el.rect
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise AtlasError(f"{path.name}: element {el.name} rect {el.rect} is outside the {w}x{h} texture")
        for other in elements.values():
            a = other.rect
            if not (x1 <= a[0] or a[2] <= x0 or y1 <= a[1] or a[3] <= y0):
                raise AtlasError(f"{path.name}: element {el.name} overlaps {other.name}")
        for got, want in ((x1 - x0, el.size[0]), (y1 - y0, el.size[1])):
            if abs(got - want * ppm * el.scale) > 0.5:
                raise AtlasError(f"{path.name}: element {el.name} is {got} px for {want} m; px_per_m {ppm} x scale "
                                 f"{el.scale} wants {want * ppm * el.scale:g} px")
        if not (path.parent.parent / el.source).is_file() and not (KIT_DIR / el.source).is_file():
            raise AtlasError(f"{path.name}: element {el.name} source {el.source} is missing")
        if el.name in elements:
            raise AtlasError(f"{path.name}: element {el.name} is listed twice")
        elements[el.name] = el
    return Atlas(name=name, material=material, size=(w, h), px_per_m=ppm, elements=elements)


@functools.lru_cache(maxsize=1)
def default() -> Atlas:
    """The packaged Rioku atlas, read once per process."""
    return load()
