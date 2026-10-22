"""The definition schema checks (non-null members, component definition types) over a synthetic load and facts."""

import json

from synth import G, T_PLAIN, schema_facts, universe_files, write_decompile, write_tree

from se2modkit.validate import clientserver as csw
from se2modkit.validate import schema as ds
from se2modkit.validate.facts import TypeIndex, build_facts


def _schema(f=None):
    return ds.Schema(TypeIndex(f or schema_facts()))


def test_nonnull_member_missing_fails(tmp_path):
    u = csw.Universe([str(write_tree(tmp_path / "Assets", universe_files()))])
    s = _schema()
    assert ds.nonnull_errors(u, s, [G["plain"]]) == []
    bad = u.derive({"$Type": T_PLAIN, "$Value": {"Guid": G["plain"], "Count": 1}})
    errs = ds.nonnull_errors(bad, s, [G["plain"]])
    assert [e[2] for e in errs] == ["$Root.Name"]                 # Note is nullable, Count a value, Label initialised
    assert "non-nullable" in ds.format_errors(errs)


def test_unknown_object_builder_is_reported(tmp_path):
    files = {"X.def": {"$Type": "Sim:Mod.Sim.NoSuchObjectBuilder", "$Value": {"Guid": G["plain"]}}}
    u = csw.Universe([str(write_tree(tmp_path / "Assets", files))])
    errs = ds.nonnull_errors(u, _schema(), [G["plain"]])
    assert errs and errs[0][3].startswith(ds.UNRESOLVED)


def test_schema_from_a_built_decompile(tmp_path):
    """The facts built from a decompiled tree carry what the schema needs: members, initialisers, nested kinds."""
    s = ds.Schema(TypeIndex(build_facts(write_decompile(tmp_path / "dec"), "0.0.0.1")))
    names = [m[0] for m in s.members("Mod.Sim.PlainDefinitionObjectBuilder")]
    assert names == ["Name", "Note", "Count", "Label", "Empty", "Nested"]
    assert s.kind("Mod.Sim.PlainDefinitionObjectBuilder+Inner") == "struct"
    paths = [p for p, *_ in s.null_paths("Mod.Sim.PlainDefinitionObjectBuilder", {"Count": 1})]
    assert paths == ["$Root.Name", "$Root.Empty"]                    # Nested is a struct (a value type)
    assert s.expected_definition("Mod.Sim.PlainComponent") == "Mod.Sim.PlainDefinition"


def test_composition_type_mismatch_fails(tmp_path):
    f = build_facts(write_decompile(tmp_path / "dec"), "0.0.0.1")
    files = universe_files()
    comp = files["Mini/Block/Block_ServerComposition.def"]
    comp["$Value"]["Components"] = [{"Key": G["k1"], "Value": {"Type": "Sim:Mod.Sim.PlainComponent",
                                                                "Definition": G["server_prefab"]}}]
    u = csw.Universe([str(write_tree(tmp_path / "Assets", files))])
    errs = ds.composition_type_errors(u, ds.Schema(TypeIndex(f)), [G["server_comp"]])
    assert [e[2] for e in errs] == ["$Root.Components[0].Definition"]
    assert "doesn't match expected Mod.Sim.PlainDefinition" in errs[0][3]


def test_content_view_and_stale_outputs(tmp_path):
    assets = write_tree(tmp_path / "Assets", universe_files())
    content = tmp_path / "Content"
    assert ("Mini/Block/Block_Plain.def", "no Content output") in ds.stale_outputs(assets, content)
    for rel in universe_files():
        p = content / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((assets / rel).read_text())
    assert ds.stale_outputs(assets, content) == []
    plain = json.loads((content / "Mini/Block/Block_Plain.def").read_text())
    del plain["$Value"]["Name"]
    (content / "Mini/Block/Block_Plain.def").write_text(json.dumps(plain))
    assert ds.stale_outputs(assets, content) == [("Mini/Block/Block_Plain.def", "Content output lacks ['Name']")]
    u = csw.Universe([str(assets)])
    cu, missing = ds.content_view(u, assets, content)
    assert missing == []
    assert [e[2] for e in ds.nonnull_errors(cu, _schema(), [G["plain"]])] == ["$Root.Name"]
