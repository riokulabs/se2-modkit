from __future__ import annotations

from dataclasses import dataclass

import pytest

from se2modkit.pipeline import menu


@dataclass(eq=False)
class B:
    name: str
    cells: tuple
    family: str = "box"
    variant: str = ""


def _blocks():
    return [B("XX_Box_2x1", (20, 10, 10)), B("XX_Box_1x1", (10, 10, 10)), B("XX_Tag", (1, 2, 1))]


def test_menu_kind_owner():
    blocks = _blocks()
    big, small, tag = blocks
    m = menu.Menu.from_groups({"Tools": {"Box": ["XX_Box_1x1", "XX_Box_2x1"], "Tag": ["XX_Tag"]}}, blocks)
    assert m.kind_owner(big) is small and m.kind_owner(small) is small and m.kind_owner(tag) is tag
    assert m.owns_kind(small) and not m.owns_kind(big)
    assert m.group(big) == "Tools" and m.kind_key(big) == "Box"
    assert (m.priority(big), m.priority(tag)) == (1, 2)
    assert m.kind_blocks(small) == [small, big]          # DisplaySize order: 2.5 m, 5.0 m
    assert menu.display_size(big) == 5.0 and m.display_size(small) == 2.5
    assert m.owners() == [small, tag]


def test_menu_rules_like_a_family_mod():
    blocks = [B("XX_Rack42_Open", (4, 42, 6), "rack", "Open"), B("XX_Rack24_Open", (4, 24, 6), "rack", "Open"),
              B("XX_Rack42_Door", (4, 42, 6), "rack", "Door"), B("XX_Srv_A", (3, 1, 2), "server", "A")]
    m = menu.Menu(blocks=blocks, group_of=lambda s: s.family, kind_of=lambda s: s.variant,
                  orders=lambda g: {"rack": ("Open", "Door"), "server": ("A",)}[g])
    assert m.kind_owner(blocks[1]) is blocks[0]          # default owner: the kind's first catalogue block
    assert m.kind_blocks(blocks[0]) == [blocks[1], blocks[0]]
    assert m.priority(blocks[2]) == 2 and m.priority(blocks[3]) == 1


def test_menu_errors_are_plain():
    blocks = _blocks()
    with pytest.raises(menu.MenuError, match="lacks: XX_Gone"):
        menu.Menu.from_groups({"G": {"K": ["XX_Gone"]}}, blocks)
    with pytest.raises(menu.MenuError, match="listed twice"):
        menu.Menu.from_groups({"G": {"K": ["XX_Tag"], "L": ["XX_Tag"]}}, blocks)
    m = menu.Menu.from_groups({"G": {"K": ["XX_Tag"]}}, blocks)
    with pytest.raises(menu.MenuError, match="XX_Box_1x1 is in no menu group"):
        m.group(blocks[1])
    r = menu.Menu(blocks=blocks, group_of=lambda s: "G", orders={"G": ("XX_Tag",)})
    with pytest.raises(menu.MenuError, match="not in the order"):
        r.priority(blocks[0])
