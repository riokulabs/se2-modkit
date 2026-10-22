"""Build-menu (G-screen) grouping, the vanilla way (no Blender).

The G-screen tree is Category -> Subcategory -> BlockGroup (one tile) -> BlockKind (a tile inside the group) -> the
kind's blocks (the size selector). How the game fills it:
  * a block joins a kind through its own CubeBlockDefinition.BlockKind; the game orders a kind's blocks by display
    size (the block's largest cell extent x 0.25 m) and the tile lists them as sizes;
  * a kind lists its groups in BlockKindDefinition.BlockGroups; each group shows its kinds by OrderingPriority
    (0 = last), then name, then GUID; a kind without blocks is skipped;
  * a group lists its subcategories, a subcategory its categories; a subcategory shows multi-kind groups first, then
    sorts by the group's Order (0 = last), then name.

A kind is owned by one of its blocks: the owner's cloned BlockKind joins the group and carries the kind's name,
description and OrderingPriority; the other sizes point their block definition's BlockKind at the owner's kind. A
non-owner's own cloned kind is left inert (no groups, no blocks).

`Menu` holds a mod's rules. Either a table (`Menu.from_groups`: group -> {kind -> [block names, any order]}, the kind
owned by its first block, priorities in table order) or rules (`group_of`, `kind_of`, `owner_of` and the kind order
per group), for mods whose kinds follow from block fields.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["CATEGORIES", "Menu", "MenuError", "display_size"]

# Identifiers of vanilla content that mods reference, as .def files do: G-screen category name -> its GUID.
CATEGORIES = {
    "Tools": "b544c2c1-2a3b-41fb-85eb-ac95b38df8a2",
    "Automation": "300a06f0-7d80-469c-bd3e-b19152cb6b7b",
}


class MenuError(KeyError):
    """A block or kind the menu rules do not place; the message is one plain sentence."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def display_size(spec) -> float:
    """The block's display size: its largest cell extent x 0.25 m (the size order inside a kind)."""
    return max(spec.cells) * 0.25


@dataclass
class Menu:
    """A mod's build-menu rules over its catalogue `blocks` (specs with `name` and `cells`).

    group_of(spec) -> group key; kind_of(spec) -> kind key (default: the block name, one kind per block);
    owner_of(spec) -> the spec owning its kind (default: the kind's first block in catalogue order);
    orders: {group key -> kind keys in menu order} or fn(group key) -> that sequence (priority = index + 1)."""
    blocks: Sequence[Any]
    group_of: Callable[[Any], str]
    kind_of: Callable[[Any], str] = field(default=lambda spec: spec.name)
    owner_of: Callable[[Any], Any] | None = None
    orders: Mapping[str, Sequence[str]] | Callable[[str], Sequence[str]] = field(default_factory=dict)

    @classmethod
    def from_groups(cls, groups: Mapping[str, Mapping[str, Sequence[str]]], blocks: Iterable) -> Menu:
        """From a table group -> {kind -> [block names]}: each kind owned by its first listed block, kinds ordered
        as listed."""
        blocks = list(blocks)
        where: dict[str, tuple[str, str, str]] = {}
        for g, kinds in groups.items():
            for k, names in kinds.items():
                if not names:
                    raise MenuError(f"Menu kind {k!r} of group {g!r} lists no block.")
                for n in names:
                    if n in where:
                        raise MenuError(f"Block {n} is listed twice in the menu table ({where[n][0]} / {where[n][1]} "
                                        f"and {g} / {k}).")
                    where[n] = (g, k, names[0])
        by_name = {b.name: b for b in blocks}
        missing = sorted(n for n in where if n not in by_name)
        if missing:
            raise MenuError(f"The menu table names blocks the catalogue lacks: {', '.join(missing)}.")

        def look(spec, i):
            try:
                return where[spec.name][i]
            except KeyError:
                raise MenuError(f"{spec.name} is in no menu group.") from None
        return cls(blocks=blocks, group_of=lambda s: look(s, 0), kind_of=lambda s: look(s, 1),
                   owner_of=lambda s: by_name[look(s, 2)], orders={g: list(kinds) for g, kinds in groups.items()})

    def group(self, spec) -> str:
        """The group whose tile shows this block's kind."""
        return self.group_of(spec)

    def kind_key(self, spec) -> str:
        """The kind this block is a size of."""
        return self.kind_of(spec)

    def kind_owner(self, spec):
        """The catalogue block whose cloned BlockKind holds this block."""
        if self.owner_of is not None:
            return self.owner_of(spec)
        key = (self.group(spec), self.kind_key(spec))
        for b in self.blocks:
            if (self.group(b), self.kind_key(b)) == key:
                return b
        raise MenuError(f"{spec.name}: its kind {key[1]!r} has no block in the catalogue.")

    def owns_kind(self, spec) -> bool:
        return self.kind_owner(spec) is spec

    def order(self, group: str) -> Sequence[str]:
        o = self.orders(group) if callable(self.orders) else self.orders.get(group)
        if o is None:
            raise MenuError(f"Menu group {group!r} has no kind order.")
        return o

    def priority(self, spec) -> int:
        """OrderingPriority of the block's kind inside its group (1..n; vanilla treats 0 as last)."""
        g, k = self.group(spec), self.kind_key(spec)
        order = list(self.order(g))
        if k not in order:
            raise MenuError(f"{spec.name}: kind {k!r} is not in the order of group {g!r} ({', '.join(order)}).")
        return order.index(k) + 1

    def kind_blocks(self, owner) -> list:
        """The catalogue blocks that share owner's kind, in DisplaySize order (the game's size order)."""
        return sorted((b for b in self.blocks if self.kind_owner(b) is owner), key=display_size)

    def owners(self) -> list:
        """Every kind owner, in catalogue order."""
        return [b for b in self.blocks if self.owns_kind(b)]

    display_size = staticmethod(display_size)
