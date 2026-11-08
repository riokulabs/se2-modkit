"""`tex.scaffold.new_set` and `tex.png` on a synthetic SDK: placeholder maps are ours, the .comptex reuses only the
vanilla preset GUIDs."""

from __future__ import annotations

import json
import zlib

import pytest
from texfake import BUNDLES

from se2modkit.preview import sdkmat
from se2modkit.tex import png, scaffold


def _pixels(path):
    """Decompressed rows of one of our flat PNGs (filter byte + pixels)."""
    data = path.read_bytes()
    i = data.index(b"IDAT")
    n = int.from_bytes(data[i - 4:i], "big")
    return zlib.decompress(data[i + 4:i + 4 + n])


def test_flat_png_header_and_pixels(tmp_path):
    g = png.flat(tmp_path / "a.png", 8, 77)
    h = png.header(g)
    assert (h.width, h.height, h.bit_depth, h.channels) == (8, 8, 8, 1)
    assert _pixels(g) == (b"\x00" + bytes([77]) * 8) * 8
    c = png.flat(tmp_path / "b.png", 4, (128, 128, 255))
    assert png.header(c).channels == 3 and _pixels(c)[:4] == b"\x00\x80\x80\xff"
    (tmp_path / "x.png").write_bytes(b"nope")
    assert png.header(tmp_path / "x.png") is None and png.header(tmp_path / "missing.png") is None


def test_layout_reads_presets_and_sources(ix, sdk):
    packs = scaffold.layout(ix, "Painted")
    assert [(p.slot, p.suffix, p.preset) for p in packs] == [
        ("ColorMetalTexture", "cm", sdk["presets"]["cm"]), ("NormalRoughnessTexture", "nr", sdk["presets"]["nr"]),
        ("ExtensionsTexture", "add", sdk["presets"]["ext"])]
    assert [(r, k) for r, _d, k, _c in packs[2].inputs] == [("ao", "texture"), ("emissive", "none"),
                                                           ("colormask", "texture"), ("alphamask", "constant")]
    with pytest.raises(scaffold.SetError, match="No vanilla material named 'Nope'"):
        scaffold.layout(ix, "Nope")


def test_new_set_writes_our_maps_and_comptex(ix, sdk, mod):
    dest, slots, written = scaffold.new_set(mod, "Hull", "Painted", ix, size=64)
    assert dest == mod / "Assets" / "Textures" / "Hull"
    names = sorted(f.name for f in dest.iterdir() if f.suffix != ".meta")
    assert names == ["Hull_add.comptex", "Hull_albedo.png", "Hull_ao.png", "Hull_cm.comptex", "Hull_colormask.png",
                     "Hull_emissive.png", "Hull_metal.png", "Hull_normal.png", "Hull_nr.comptex",
                     "Hull_roughness.png"]
    # every new file and folder has a .meta; folders included
    assert all(f.with_name(f.name + ".meta").is_file() for f in dest.iterdir() if f.suffix != ".meta")
    assert (mod / "Assets" / "Textures.meta").is_file() and (mod / "Assets" / "Textures" / "Hull.meta").is_file()
    # placeholders: flat OpenGL normal, gray albedo, full color mask (the vanilla one is paintable)
    assert _pixels(dest / "Hull_normal.png")[:4] == b"\x00\x80\x80\xff"
    assert _pixels(dest / "Hull_colormask.png")[:2] == b"\x00\xff"
    assert png.header(dest / "Hull_albedo.png").width == 64
    # no vanilla bytes anywhere
    vanilla = sdk["albedo"].read_bytes()
    assert all(f.read_bytes() != vanilla for f in dest.glob("*.png"))
    # the .comptex reuses the vanilla preset GUID and names only our own PNGs
    ids = {sdkmat._asset_id(str(m)): m.name[:-5] for m in dest.glob("*.meta")}
    preset, sources = sdkmat.parse_comptex(dest / "Hull_cm.comptex")
    assert preset == sdk["presets"]["cm"]
    assert [ids.get(s) if s else c for s, c in sources] == ["Hull_albedo.png"] * 3 + ["Hull_metal.png", None]
    _p, ext = sdkmat.parse_comptex(dest / "Hull_add.comptex")
    assert [ids.get(s) if s else c for s, c in ext] == ["Hull_ao.png", "Hull_emissive.png", "Hull_colormask.png", 1.0]
    raw = json.loads((dest / "Hull_cm.comptex").read_text(encoding="utf-8"))
    assert raw["$Bundles"] == BUNDLES and raw["$Value"]["Rotation"] == "None"
    assert slots == {k: sdkmat._asset_id(str(dest / f"Hull_{s}.comptex.meta"))
                     for k, s in (("ColorMetalTexture", "cm"), ("NormalRoughnessTexture", "nr"),
                                  ("ExtensionsTexture", "add"))}
    assert set(written) >= {dest / "Hull_cm.comptex", dest / "Hull_albedo.png"}


@pytest.mark.parametrize("kw, msg", [({"size": 100}, "power of two"), ({"name": "1bad"}, "must start with a letter")])
def test_new_set_rejects_bad_input(ix, mod, kw, msg):
    args = {"name": "Hull", "size": 64, **kw}
    with pytest.raises(scaffold.SetError, match=msg):
        scaffold.new_set(mod, args["name"], "Painted", ix, size=args["size"])


def test_new_set_never_overwrites(ix, mod):
    scaffold.new_set(mod, "Hull", "Painted", ix, size=8)
    with pytest.raises(scaffold.SetError, match="already exists"):
        scaffold.new_set(mod, "Hull", "Painted", ix, size=8)
