"""`defs.block_chain`: Copy chains, overrides and the block view over hand-written synthetic definitions."""

import json
from pathlib import Path

import pytest

from se2modkit.defs import block_chain as bc

# Synthetic GUIDs and types (hand-written, not from any game file).
G_BASE = "10000000-0000-4000-8000-000000000001"
G_COPY = "10000000-0000-4000-8000-000000000002"
G_BIND = "20000000-0000-4000-8000-000000000001"
G_SCOMP = "20000000-0000-4000-8000-000000000002"
G_SPREFAB = "20000000-0000-4000-8000-000000000003"
G_BDEF = "20000000-0000-4000-8000-000000000004"
G_KIND = "20000000-0000-4000-8000-000000000005"
T = "Game2:Keen.Game2.Test.{}ObjectBuilder"
DIFF = "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff"


def _w(root: Path, rel: str, data: dict) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def _def(root, rel, typ, value):
    _w(root, rel, {"$Bundles": {}, "$Type": T.format(typ), "$Value": value})


def _partial(root, rel, kind, base, typ, manipulator):
    _w(root, rel, {"$Bundles": {}, "$Type": DIFF, "$Value": {
        "BaseDefinition": base, "PartialDefinitionKind": kind, "DefinitionType": T.format(typ),
        "Manipulator": manipulator}})


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "Assets"
    _def(r, "Templates/Base.def", "WidgetDefinition", {"Guid": G_BASE, "A": 1, "List": ["x"], "Nested": {"P": 1}})
    _partial(r, "Things/Copy.partialdef", "Copy", G_BASE, "WidgetDefinition", {
        "Guid": G_COPY, "B": 2, "List": {"$DeltaEncoded": True, "Added": ["y"], "Removed": ["x"]},
        "Nested": {"Q": 2}})
    _partial(r, "Things/Override.partialdef", "Override", G_COPY, "WidgetDefinition", {"A": 5})
    return r


def test_block_chain_resolves_synthetic_chain(root):
    idx = bc.Index(root)
    assert idx.resolve(G_COPY) == {"Guid": G_COPY, "A": 5, "B": 2, "List": ["y"], "Nested": {"P": 1, "Q": 2}}
    assert idx.resolve(G_BASE) == {"Guid": G_BASE, "A": 1, "List": ["x"], "Nested": {"P": 1}}
    assert [e.guid for e in idx.lineage(G_COPY)] == [G_COPY, G_BASE]
    assert idx.type_of(G_COPY) == "WidgetDefinition"
    assert idx.rel(idx.by_guid[G_COPY].path) == "Things/Copy.partialdef"


def test_keyed_list_merge():
    base = [{"Key": "a", "Value": {"v": 1}}, {"Key": "b", "Value": {"v": 2}}]
    delta = {"$DeltaEncoded": True, "Keys": ["a", "n", "b"], "Removed": [],
             "Changed": [{"Index": 0, "Kind": "Update", "Value": {"w": 9}},
                         {"Index": 1, "Kind": "Insert", "Value": {"v": 3}}]}
    assert bc.merge(base, delta) == [{"Key": "a", "Value": {"v": 1, "w": 9}}, {"Key": "n", "Value": {"v": 3}},
                                     {"Key": "b", "Value": {"v": 2}}]


def test_block_view(root):
    _def(root, "Blocks/Widgets/250/Widget250.def", "PrefabBindingDefinition",
         {"Guid": G_BIND, "ServerComposite": G_SCOMP})
    _def(root, "Blocks/Widgets/250/Widget250_Server.def", "PrefabDefinition",
         {"Guid": G_SPREFAB, "_entity": {"Definition": G_SCOMP}})
    _def(root, "Blocks/Widgets/250/Widget250_Composite.def", "CompositeDefinition", {"Guid": G_SCOMP, "Components": [
        {"Key": "k1", "Value": {"Type": "Game2:Keen.Game2.Test.ItemConverterComponent", "Definition": G_BDEF}}]})
    _def(root, "Blocks/Widgets/250/Widget250_Block.def", "FunctionalBlockDefinition",
         {"Guid": G_BDEF, "BlockKind": G_KIND, "UIData": {"Name": "Widget"}, "PCU": 3})
    idx = bc.Index(root)
    blocks = bc.Blocks(idx)
    assert [g for g, _ in blocks.bindings()] == [G_BIND]
    info = blocks.describe(G_BIND)
    assert info["server_prefab"] == G_SPREFAB and info["block_def"] == G_BDEF and info["ui_name"] == "Widget"
    assert info["archetype"] == "production (item converter)"


def test_index_without_sdk_is_one_sentence(tmp_path):
    from fake_env import make_env

    from se2modkit.env.core import EnvError
    env = make_env(tmp_path, sdk_assets=None)
    with pytest.raises(EnvError, match="Mod SDK Vanilla assets not found"):
        bc.default_root(env)


def test_chain_cli_resolve(run, root):
    code, out, err = run("chain", "--root", str(root), "resolve", G_COPY)
    assert code == 0, err
    head, body = out.split("\n", 1)
    assert head == "# Things/Copy.partialdef  [WidgetDefinition]"
    assert json.loads(body)["A"] == 5
    code, out, _ = run("chain", "--root", str(root), "lineage", G_COPY)
    assert code == 0 and "override Things/Override.partialdef" in out
    code, _, err = run("chain", "--root", str(root), "resolve", "nope")
    assert code == 1 and "not found" in err and "Traceback" not in err
