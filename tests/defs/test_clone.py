"""`defs.clone`: clone a vanilla block chain into a mod (own GUIDs, own kind), on a hand-written synthetic SDK."""

import json

import pytest

from hookfix import Spec, make_hooks, read
from se2modkit.defs import assetio, block_chain as bc, clone
from se2modkit.defs.guids import Guids

# Synthetic GUIDs and types (hand-written, not from any game file).
BIND = "30000000-0000-4000-8000-000000000001"
SCOMP = "30000000-0000-4000-8000-000000000002"
SPREFAB = "30000000-0000-4000-8000-000000000003"
CCOMP = "30000000-0000-4000-8000-000000000004"
CPREFAB = "30000000-0000-4000-8000-000000000005"
BDEF = "30000000-0000-4000-8000-000000000006"
KIND = "30000000-0000-4000-8000-000000000007"
MODEL = "30000000-0000-4000-8000-000000000008"
DETECT = "30000000-0000-4000-8000-000000000009"
BIND_BASE = "40000000-0000-4000-8000-000000000002"
TEMPLATE = "40000000-0000-4000-8000-000000000001"   # outside the cloned folder: never rewired
T = "Game2:Keen.Game2.Test.{}"
DIFF = "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff"
SRC = "Blocks/Widgets/250"


def _w(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _def(root, rel, typ, value):
    _w(root, rel, {"$Bundles": {}, "$Type": T.format(typ), "$Value": value})


@pytest.fixture
def sdk(tmp_path):
    r = tmp_path / "sdk" / "Assets"
    _def(r, "Templates/BindingTemplate.def", "PrefabBindingDefinition", {"Guid": BIND_BASE})
    _w(r, f"{SRC}/Widget250.partialdef", {"$Bundles": {}, "$Type": DIFF, "$Value": {
        "BaseDefinition": BIND_BASE, "PartialDefinitionKind": "Copy",
        "DefinitionType": T.format("PrefabBindingDefinition"),
        "Manipulator": {"Guid": BIND, "ServerComposite": SCOMP, "ClientPrefab": CPREFAB}}})
    _def(r, f"{SRC}/Widget250_Server.def", "PrefabDefinition", {"Guid": SPREFAB, "_entity": {"Definition": SCOMP}})
    _def(r, f"{SRC}/Widget250_ServerComposition.def", "CompositeDefinition", {"Guid": SCOMP, "Components": [
        {"Key": "k1", "Value": {"Type": T.format("CubeBlockComponent"), "Definition": BDEF}},
        {"Key": "k2", "Value": {"Type": T.format("BlockModelComponent"), "Definition": MODEL}}]})
    _def(r, f"{SRC}/Widget250_Client.def", "PrefabDefinition", {"Guid": CPREFAB, "_entity": {"Definition": CCOMP}})
    _def(r, f"{SRC}/Widget250_ClientComposition.def", "CompositeDefinition", {"Guid": CCOMP, "Components": []})
    _w(r, f"{SRC}/Widget250_Block.partialdef", {"$Bundles": {}, "$Type": DIFF, "$Value": {
        "BaseDefinition": TEMPLATE, "PartialDefinitionKind": "Copy", "DefinitionType": T.format("CubeBlockDefinition"),
        "Manipulator": {"Guid": BDEF, "UIData": {"Name": "Widget"}}}})
    _def(r, "Templates/BlockTemplate.def", "CubeBlockDefinition", {"Guid": TEMPLATE, "BlockKind": KIND,
                                                                   "UIData": {"Name": "?"}})
    _w(r, f"{SRC}/Widget250_BlockModelDefinition.partialdef", {"$Bundles": {}, "$Type": DIFF, "$Value": {
        "BaseDefinition": TEMPLATE, "PartialDefinitionKind": "Copy", "DefinitionType": T.format("BlockModelDefinition"),
        "Manipulator": {"Guid": MODEL, "Dummies": []}}})
    _def(r, f"{SRC}/Sub/Widget250_DamageDetectorDefinition.def", "DetectorDefinition", {"Guid": DETECT})
    _def(r, "UI/Kinds/Widget/WidgetBlockKindDefinition.def", "BlockKindDefinition", {"Guid": KIND, "Name": "Widget"})
    return bc.Index(r)


def _spec():
    return Spec(name="XX_Widget", vanilla_source=SRC, copy_from="Widget250")


def test_clone_remaps_references(tmp_path, sdk, capsys):
    h = make_hooks(tmp_path / "mod", clone_drop={"Widget250": ("DamageDetectorDefinition.def",)})
    G = Guids(h.paths.asset_guids)
    info = clone.clone(h, _spec(), G, sdk)
    root = h.paths.assets / "XX_Mod" / "XX_Widget"
    cmap = read(h.paths.clone_map)
    m = cmap["maps"]["XX_Widget"]["guids"]
    assert info["block_def"] == m[BDEF] and info["kind"] == m[KIND] and info["model_def"] == m[MODEL]
    assert info["server_prefab"] == m[SPREFAB] and info["client_prefab"] == m[CPREFAB]
    assert info["vanilla"] == {"block_def": BDEF, "server_prefab": SPREFAB, "client_prefab": CPREFAB, "kind_guid": KIND}
    assert info["block_def_file"] == "XX_Mod/XX_Widget/XX_Widget_Block.partialdef"
    assert info["kind_file"] == "XX_Mod/XX_Widget/UI/XX_Widget_BlockKindDefinition.def"
    assert info["model_def_file"] == "XX_Mod/XX_Widget/XX_Widget_BlockModelDefinition.partialdef"
    assert info["binding"] == f"{SRC}/Widget250.partialdef" and cmap["maps"]["XX_Widget"]["info"] == info
    comp = read(root / "XX_Widget_ServerComposition.def")["$Value"]
    assert [c["Value"]["Definition"] for c in comp["Components"]] == [m[BDEF], m[MODEL]]
    block = read(root / "XX_Widget_Block.partialdef")["$Value"]
    assert block["BaseDefinition"] == TEMPLATE and block["Manipulator"]["Guid"] == m[BDEF]
    # the drop rule removed the detector (and nothing else); every definition and folder has its .meta
    assert not (root / "Sub" / "XX_Widget_DamageDetectorDefinition.def").exists()
    for p in root.rglob("*"):
        if p.suffix in (".def", ".partialdef"):
            assert assetio.meta_id(p) == assetio.own_guid(read(p)), p
    for d in (root, root / "UI", root / "Sub", root.parent):
        rel = d.relative_to(h.paths.assets).as_posix()
        assert assetio.meta_id(d) == G["dir/" + rel], rel
    assert read(root / "UI.meta")["$Bundles"] == h.bundle_stamps[0]
    out = capsys.readouterr().out
    assert "$ se2mod copy XX_Mod UI/Kinds/Widget --from WidgetBlockKindDefinition" in out
    assert "dropped XX_Mod/XX_Widget/Sub/XX_Widget_DamageDetectorDefinition.def" in out


def test_clone_ensure_kind_and_clone_all_skips(tmp_path, sdk, capsys):
    h = make_hooks(tmp_path / "mod")
    out = clone.clone_all(h, [_spec()], sdk)
    info = out["XX_Widget"]
    block = read(h.paths.assets / info["block_def_file"])["$Value"]["Manipulator"]
    assert block["BlockKind"] == info["kind"]          # inherited kind written by ensure_kind
    assert clone.is_cloned(h, _spec()) and clone.entry(h, _spec())["info"] == info
    saved = read(h.paths.asset_guids)
    assert "dir/XX_Mod/XX_Widget" in saved
    capsys.readouterr()
    assert clone.clone_all(h, [_spec()], sdk) == out
    assert "skip XX_Widget: already cloned" in capsys.readouterr().out


def test_clone_also_renames_from_hooks(tmp_path, sdk):
    h = make_hooks(tmp_path / "mod", clone_also={"Widget250": lambda name: [f"DamageDetector={name}Detector"]})
    clone.clone(h, _spec(), Guids(h.paths.asset_guids), sdk)
    sub = h.paths.assets / "XX_Mod" / "XX_Widget" / "Sub"
    assert [p.name for p in sub.glob("*.def")] == ["XX_Widget_XX_WidgetDetectorDefinition.def"]


def test_clone_needs_a_kind(tmp_path, sdk):
    (sdk.root / "UI/Kinds/Widget/WidgetBlockKindDefinition.def").unlink()
    with pytest.raises(clone.CloneError, match="no BlockKind"):
        clone.clone(make_hooks(tmp_path / "mod"), _spec(), Guids(tmp_path / "g.json"), bc.Index(sdk.root))


def test_clone_all_checks_hooks_and_guid_store(tmp_path, sdk):
    import dataclasses
    from se2modkit.pipeline.hooks import HooksError
    h = make_hooks(tmp_path / "mod")
    h.prefix = "bad"
    with pytest.raises(HooksError, match="prefix"):
        clone.clone_all(h, [_spec()], sdk)
    h = make_hooks(tmp_path / "mod")
    h.paths = dataclasses.replace(h.paths, asset_guids=None)
    with pytest.raises(clone.CloneError, match="asset GUID store"):
        clone.clone_all(h, [_spec()], sdk)
    with pytest.raises(ValueError, match="asset GUID store"):
        Guids(None)
