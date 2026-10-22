from __future__ import annotations

import json

import pytest

from hookfix import read, write
from se2modkit import stage as ST
from se2modkit.defs.guids import Guids
from se2modkit.pipeline import texts

KIND = "Game2:Keen.Game2.Simulation.StreamedUI.Categories.BlockKindDefinitionObjectBuilder"


def _kind(name, desc):
    return {"$Bundles": {}, "$Type": KIND, "$Value": {"Guid": "00000000-0000-4000-8000-000000000001", "Name": name,
                                                       "Description": desc}}


def test_display_strings():
    assert texts.def_type(_kind("a", "b")) == "BlockKindDefinition"
    assert texts.display_strings(_kind("Box", "A box.")) == [("Name", "Box"), ("Description", "A box.")]
    part = {"$Value": {"PartialDefinitionKind": "Copy", "DefinitionType": "X.ItemDefinition",
                       "Manipulator": {"DisplayName": "Widget", "UIData": {"Name": "ignored?"}}}}
    assert ("DisplayName", "Widget") in texts.display_strings(part)
    assert texts.display_strings({"$Value": {"PartialDefinitionKind": "Override", "Manipulator": {"Name": "x"}}}) == []


def test_write_texts_key_is_text(hooks, tmp_path):
    out = tmp_path / "out"
    path = texts.write_texts(["Zeta", "Alpha", "Zeta"], hooks, out, vanilla_keys=set())
    assert path == out / "XX_Mod/Texts/en-US/XX_Mod.loc-texts"
    assert json.loads(path.read_text(encoding="utf-8")) == {"Alpha": "Alpha", "Zeta": "Zeta"}
    assert path.read_bytes().endswith(b"}\n") and b"\r" not in path.read_bytes()


def test_vanilla_text_key_refused(hooks, tmp_path):
    with pytest.raises(texts.TextsError, match="Welder"):
        texts.write_texts(["Box", "Welder"], hooks, tmp_path, vanilla_keys={"Welder", "Other"})
    assert not (tmp_path / "XX_Mod").exists()


def test_vanilla_keys_from_sdk(tmp_path):
    assets = tmp_path / "GameData" / "Vanilla" / "Assets"
    write(assets / "MainMenuData/Texts/en-US/Blocks.loc-texts", {"Welder": "Welder", "Box": "Box"})
    write(tmp_path / "GameData/Vanilla/Content/Texts/de-DE/x.loc-texts", {"Kiste": "Kiste"})
    (assets / "bad.loc-texts").write_text("{not json", encoding="utf-8")
    assert texts.vanilla_keys(assets) == {"Welder", "Box", "Kiste"}


def test_stage_writes_texts_and_culture_definition(hooks, tmp_path):
    ST.reset(hooks)
    write(hooks.paths.staged / "XX_Mod/UI/XX_Kind.def", _kind("Box Kind", "A kind of box."))
    G = Guids(tmp_path / "guids.json")
    res = texts.stage(hooks, G, vanilla_keys={"Welder"})
    assert res["keys"] == 2 and res["file"] == "XX_Mod/Texts/en-US/XX_Mod.loc-texts"
    d = read(hooks.paths.staged / res["definition"])
    v = d["$Value"]
    assert v["CultureId"] == texts.EN_US and v["Resources"][0]["Value"]["ContentId"] == "XX_ModTexts"
    assert v["Resources"][0]["Value"]["Asset"] == "{G}" + G[res["file"]]
    assert (hooks.paths.staged / (res["file"] + ".meta")).is_file()


def test_stage_needs_vanilla_keys(hooks, tmp_path):
    ST.reset(hooks)
    with pytest.raises(texts.TextsError, match="Mod SDK"):
        texts.stage(hooks, Guids(tmp_path / "g.json"))


def test_vanilla_keys_none_found_is_an_error(tmp_path):
    assets = tmp_path / "GameData" / "Vanilla" / "Assets"
    assets.mkdir(parents=True)
    with pytest.raises(texts.TextsError, match="No vanilla .loc-texts") as e:
        texts.vanilla_keys(assets)
    assert "\n" not in str(e.value)
