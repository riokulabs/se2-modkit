"""`defs.guids` (stable asset GUIDs) and `defs.material_guids` (material slot -> vanilla material GUID)."""

import json
import uuid

import pytest

from hookfix import make_hooks
from se2modkit.defs import material_guids as mg
from se2modkit.defs.guids import Guids

A = "3f2b8a1e-5c4d-4e6f-8a7b-9c0d1e2f3a4b"
B = "0a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d"
# What the mods' existing Guids.save wrote for this store (captured once from the original implementation).
SAVED = ('{\n "dir/XX_Mod": "0a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d",\n'
         ' "dir/XX_Mod/XX_Box": "3f2b8a1e-5c4d-4e6f-8a7b-9c0d1e2f3a4b"\n}\n')


def test_guid_derivation_stable(tmp_path):
    p = tmp_path / "asset_guids.json"
    p.write_text(json.dumps({"dir/XX_Mod/XX_Box": A, "dir/XX_Mod": B}), encoding="utf-8")
    G = Guids(p)
    assert G["dir/XX_Mod/XX_Box"] == A and "dir/XX_Mod" in G and "dir/XX_Mod/New" not in G
    G.save()
    assert p.read_bytes() == SAVED.encode()
    new = G["dir/XX_Mod/New"]
    assert uuid.UUID(new).version == 4 and G["dir/XX_Mod/New"] == new
    G.save()
    again = Guids(p)
    assert again["dir/XX_Mod/New"] == new and again["dir/XX_Mod/XX_Box"] == A
    assert [q.name for q in tmp_path.iterdir()] == ["asset_guids.json"]   # no temp file left behind


def test_guids_missing_file_starts_empty(tmp_path):
    G = Guids(tmp_path / "none.json")
    assert G.d == {} and not (tmp_path / "none.json").exists()


def test_material_guids_slot_prefix_from_hooks(tmp_path):
    h = make_hooks(tmp_path)
    paint = mg.MATERIAL_GUIDS[mg.LASTLOD_FALLBACK][0]
    assert mg.guid(h, "XX_Box_LastLOD") == paint
    with pytest.raises(KeyError):   # another mod's prefix is not this mod's LastLOD slot
        mg.guid(h, "YY_Box_LastLOD")
    other = make_hooks(tmp_path / "yy")
    other.prefix = "YY_"
    assert mg.guid(other, "YY_Box_LastLOD") == paint
    assert mg.guid(h, "BrushedMetal_Subpart") == mg.MATERIAL_GUIDS["BrushedMetal"][0]


def test_material_slots_and_damaged_slots_from_hooks(tmp_path):
    h = make_hooks(tmp_path)
    off = mg.MATERIAL_GUIDS["EmissiveOff"]
    with pytest.raises(KeyError):
        mg.guid(h, "XX_Lamp")
    h.material_slots["XX_Lamp"] = off
    h.damaged_slots["XX_Lamp"] = "EmissiveOffBroken"
    assert mg.guid(h, "XX_Lamp") == off[0]
    assert mg.damaged_slot(h, "XX_Lamp") == "EmissiveOffBroken"
    assert mg.damaged_slot(h, "XX_Lamp_Subpart") == "EmissiveOffBroken"
    assert mg.damaged_slot(h, "Chrome") == "ChromeDeformed"
    assert mg.damaged_slot(h, "Rubber") == "Rubber"
    other = make_hooks(tmp_path / "other")
    assert "XX_Lamp" not in mg.material_table(other) and "XX_Lamp" not in mg.damaged_table(other)
    assert list(mg.damaged_table(h))[-1] == "XX_Lamp"   # generic rows first, the mod's appended


def test_material_tables_are_wellformed():
    for slot, (g, rel) in mg.MATERIAL_GUIDS.items():
        assert str(uuid.UUID(g)) == g and rel.endswith((".def", ".partialdef")), slot
    for src, dst in mg.DAMAGED_SLOTS.items():
        assert src in mg.MATERIAL_GUIDS and dst in mg.MATERIAL_GUIDS, src
