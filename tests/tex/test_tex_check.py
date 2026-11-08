"""`tex.check.check_mod` on synthetic sets: a fresh set passes; sizes, channels, references and copied game textures
fail."""

from __future__ import annotations

import json
import shutil

import pytest
from texfake import write

from se2modkit.tex import check, png, scaffold


def _set(mod, ix, size=16):
    dest, _slots, _w = scaffold.new_set(mod, "Hull", "Painted", ix, size=size)
    return dest


def _msgs(rep, level="error"):
    return [(p.path, p.message) for p in rep.problems if p.level == level]


def test_a_fresh_set_passes(ix, sdk, mod):
    _set(mod, ix)
    rep = check.check_mod(mod, ix, [sdk["vanilla"]])
    assert rep.sets == ["Assets/Textures/Hull"] and rep.problems == [] and rep.presets_checked


def test_no_sets_no_problems(mod, ix):
    rep = check.check_mod(mod, ix)
    assert rep.sets == [] and rep.problems == []


def test_size_rules(ix, mod):
    dest = _set(mod, ix)
    png.flat(dest / "Hull_roughness.png", 32, 128)          # power of two, but not the set's size
    rep = check.check_mod(mod, ix)
    assert any("share one size" in m and "16x16 (6)" in m and "32x32 (1)" in m for _p, m in _msgs(rep))
    png.flat(dest / "Hull_roughness.png", 24, 128)
    assert any("powers of two" in m for _p, m in _msgs(check.check_mod(mod, ix)))


def test_channel_rules(ix, mod):
    dest = _set(mod, ix)
    png.flat(dest / "Hull_normal.png", 16, 128)            # gray normal map
    png.flat(dest / "Hull_metal.png", 16, (0, 0, 0))      # colored single-value map
    rep = check.check_mod(mod, ix)
    assert ("Assets/Textures/Hull/Hull_normal.png", "is a normal map but has no RGB channels") in _msgs(rep)
    assert any(p.endswith("Hull_metal.png") and "grayscale" in m for p, m in _msgs(rep, "warning"))


def test_a_dark_normal_map_fails_when_pillow_is_there(ix, mod):
    pytest.importorskip("PIL")
    dest = _set(mod, ix)
    png.flat(dest / "Hull_normal.png", 16, (128, 128, 20))
    assert any("tangent-space normal map" in m for _p, m in _msgs(check.check_mod(mod, ix)))


def test_references_must_resolve(ix, sdk, mod):
    dest = _set(mod, ix)
    (dest / "Hull_ao.png.meta").unlink()
    (dest / "Hull_ao.png").unlink()
    ct = dest / "Hull_nr.comptex"
    d = json.loads(ct.read_text(encoding="utf-8"))
    d["$Value"]["PresetReference"] = "{G}11111111-1111-4111-8111-111111111111"
    ct.write_text(json.dumps(d), encoding="utf-8")
    rep = check.check_mod(mod, ix)
    msgs = _msgs(rep)
    assert any(p.endswith("Hull_add.comptex") and "(ao) GUID" in m and "resolves to no file" in m for p, m in msgs)
    assert any(p.endswith("Hull_nr.comptex") and "not in the Mod SDK" in m for p, m in msgs)


def test_an_sdk_asset_referenced_by_guid_is_fine(ix, sdk, mod):
    dest = _set(mod, ix)
    ct = dest / "Hull_cm.comptex"
    sdk_albedo_guid = json.loads((sdk["albedo"].with_name("Painted_albedo.png.meta")).read_text())["$Value"]["AssetID"]
    d = json.loads(ct.read_text(encoding="utf-8"))
    for s in d["$Value"]["PresetModel"]["TextureSourceModels"][:3]:
        s["Texture"] = "{G}" + sdk_albedo_guid
    ct.write_text(json.dumps(d), encoding="utf-8")
    rep = check.check_mod(mod, ix)
    assert _msgs(rep) == [] and any("Hull_albedo.png" in p for p, _m in _msgs(rep, "warning"))   # now unused


def test_input_count_must_match_the_preset(ix, mod):
    dest = _set(mod, ix)
    ct = dest / "Hull_cm.comptex"
    d = json.loads(ct.read_text(encoding="utf-8"))
    d["$Value"]["PresetModel"]["TextureSourceModels"].pop()
    ct.write_text(json.dumps(d), encoding="utf-8")
    assert any("lists 4 sources but its preset has 5" in m for _p, m in _msgs(check.check_mod(mod, ix)))


def test_without_the_sdk_references_in_the_mod_still_resolve(ix, mod):
    _set(mod, ix)
    rep = check.check_mod(mod, None)
    assert rep.problems == [] and not rep.presets_checked


def test_a_copied_game_texture_fails(ix, sdk, mod):
    dest = _set(mod, ix)
    shutil.copyfile(sdk["albedo"], dest / "Hull_albedo.png")
    rep = check.check_mod(mod, ix, [sdk["vanilla"]])
    assert any("exact copy of the game texture Painted/Painted_albedo.png" in m for _p, m in _msgs(rep))
    stray = mod / "Assets" / "Other" / "x.png"
    write(stray, sdk["albedo"].read_bytes())
    rep = check.check_mod(mod, ix, [sdk["vanilla"]])
    assert any(p == "Assets/Other/x.png" for p, _m in _msgs(rep))   # anywhere in the mod, not only in sets


def test_report_json(ix, mod):
    dest = _set(mod, ix)
    png.flat(dest / "Hull_ao.png", 8, 255)
    data = json.loads(check.to_json(check.check_mod(mod, ix)))
    assert data["sets"] == ["Assets/Textures/Hull"] and data["problems"][0]["level"] == "error"
