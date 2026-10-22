"""Type facts: names and flags read from a decompiled tree, kept in the user cache only, and the TypeIndex over them."""

import json
import subprocess

import pytest
from synth import write_decompile

from se2modkit.validate import facts as F


@pytest.fixture
def decompile(tmp_path):
    return write_decompile(tmp_path / "decompiled-0.0.0.1")


def test_build_facts_classifies_from_the_decompile(decompile):
    f = F.build_facts(decompile, "0.0.0.1")
    assert f["game_version"] == "0.0.0.1" and f["format"] == F.FORMAT
    assert set(f["client_assemblies"]) == {"Gfx.Client", "Tools"}       # name ends in Client / [assembly: ClientAssembly]
    t = F.TypeIndex(f)
    k = t.classify("Sim:Mod.Sim.ServerThingComponent")
    assert (k["client"], k["server_only"]) == (False, True) and "inherited from Mod.Sim.ServerBaseComponent" in k["reason"]
    assert t.classify("Gfx.Client:Mod.Gfx.RenderThingComponent")["client"]
    assert t.classify("Tools:Mod.Tools.ToolComponent")["client"]
    assert t.classify("Sim:Mod.Sim.OldThingComponent")["type"] == "Mod.Sim.ServerThingComponent"   # NameMigration
    assert t.component_for_definition("Mod.Sim.PlainDefinition") == "Mod.Sim.PlainComponent"
    assert t.is_component("Mod.Sim.PlainComponent") and not t.is_component("Mod.Sim.PlainDefinition")
    with pytest.raises(F.UnknownType):
        t.classify("Sim:Mod.Sim.NoSuchComponent")


def test_facts_hold_names_not_code(decompile):
    """Members are (name, declared type, initialised?) only: no initializer text or method body leaves the decompile."""
    f = F.build_facts(decompile, "0.0.0.1")
    ob = f["types"]["Mod.Sim.PlainDefinitionObjectBuilder"]
    assert [m[0] for m in ob["fields"]] == ["Name", "Note", "Count", "Label", "Empty", "Nested"]
    assert {m[0]: m[2] for m in ob["fields"]} == {"Name": "", "Note": "", "Count": "", "Label": "set", "Empty": "null",
                                                  "Nested": ""}
    assert ob["kinds"]["Inner"] == "struct"
    assert '"x"' not in json.dumps(f)


def test_missing_decompile_is_named(tmp_path):
    with pytest.raises(F.MissingDecompile, match="no-such-decompile"):
        F.build_facts(tmp_path / "no-such-decompile", "0.0.0.1")


def test_extra_sibling_tree_is_read(tmp_path, decompile):
    extra = tmp_path / "decompiled-0.0.0.1-extra" / "Audio.Client" / "Mod.Audio"
    extra.mkdir(parents=True)
    (extra / "SoundComponent.cs").write_text("namespace Mod.Audio;\n\npublic class SoundComponent\n{\n}\n")
    t = F.TypeIndex(F.build_facts(decompile, "0.0.0.1"))
    assert t.classify("Audio.Client:Mod.Audio.SoundComponent")["client"]


def test_facts_written_to_cache(tmp_path, decompile):
    cache = tmp_path / "cache"
    p = F.write_facts(F.build_facts(decompile, "0.0.0.1"), cache_dir=cache)
    assert p == cache / "facts" / "0.0.0.1.json" == F.facts_path("0.0.0.1", cache_dir=cache)
    t = F.TypeIndex.from_facts(p)
    assert t.classify("Sim:Mod.Sim.ServerThingComponent")["server_only"]
    assert t.game_version == "0.0.0.1"


def test_facts_refuse_repo_path(tmp_path, decompile):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(F.FactsError, match="git work tree"):
        F.write_facts(F.build_facts(decompile, "0.0.0.1"), cache_dir=repo / "cache")
    assert not (repo / "cache").exists()


def test_facts_refuse_odd_version(tmp_path, decompile):
    with pytest.raises(F.FactsError):
        F.write_facts(F.build_facts(decompile, "../x"), cache_dir=tmp_path / "cache")


def test_from_facts_rejects_other_formats(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"format": 999, "types": {}}))
    with pytest.raises(F.FactsError, match="format"):
        F.TypeIndex.from_facts(p)
