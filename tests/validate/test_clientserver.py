"""The offline client/server composition walk over a synthetic load (hand-written prefabs, compositions, a binding)."""

import copy
import json

from synth import CLIENT_TYPE, SERVER_TYPE, G, facts, universe_files, write_tree

from se2modkit.validate import clientserver as csw
from se2modkit.validate.facts import TypeIndex


def _universe(tmp_path, **kw):
    return csw.Universe([str(write_tree(tmp_path / "Assets", universe_files(**kw)))])


def test_walk_resolves_synthetic_universe(tmp_path):
    u = _universe(tmp_path)
    w = csw.walk(u)
    assert list(w.bindings) == [G["binding"]]
    assert G["server_comp"] in w.server.composites and G["client_comp"] in w.client.composites
    assert G["client_comp"] not in w.server.composites
    assert csw.validate(w, TypeIndex(facts())) == []


def test_client_type_on_server_side_fails(tmp_path):
    u = _universe(tmp_path, server_types=(SERVER_TYPE, CLIENT_TYPE))
    errs = csw.validate(csw.walk(u), TypeIndex(facts()))
    assert [(e[0], e[1], e[2]) for e in errs] == [("client-type-on-server", "server", G["server_comp"])]
    assert "Mod.Gfx.RenderThingComponent" in errs[0][3][0]
    assert "binding" in csw.format_errors(errs, u)


def test_server_only_type_on_client_side_fails(tmp_path):
    u = _universe(tmp_path, client_types=(CLIENT_TYPE, SERVER_TYPE))
    errs = csw.validate(csw.walk(u), TypeIndex(facts()))
    assert [(e[0], e[2]) for e in errs] == [("server-only-on-client", G["client_comp"])]


def test_unknown_type_is_an_error_never_a_pass(tmp_path):
    u = _universe(tmp_path, server_types=("Sim:Mod.Sim.Nope",))
    errs = csw.validate(csw.walk(u), TypeIndex(facts()))
    assert [e[0] for e in errs] == ["unknown-type"]


def test_binding_without_server_prefab_fails(tmp_path):
    files = universe_files()
    del files["Mini/Block/Block_ServerPrefab.def"]
    u = csw.Universe([str(write_tree(tmp_path / "Assets", files))])
    errs = csw.validate(csw.walk(u), TypeIndex(facts()))
    assert [e[0] for e in errs] == ["binding"]


def test_derive_and_load_errors(tmp_path):
    root = write_tree(tmp_path / "Assets", universe_files())
    (root / "Broken.def").write_text('{"$Type": ')
    dup = json.loads((root / "Mini/Block/Block_Plain.def").read_text())
    (root / "Dup.def").write_text(json.dumps(dup))
    u = csw.Universe([str(root)])
    codes = {e[0] for e in csw.walk(u).errors}
    assert {"unparseable", "duplicate-guid"} <= codes
    clean = csw.Universe([str(write_tree(tmp_path / "Clean", universe_files()))])
    comp = copy.deepcopy(clean.resolve(G["server_comp"]))     # resolve() shares its cached value
    comp["Components"].append({"Key": G["k3"], "Value": {"Type": CLIENT_TYPE, "Definition": None}})
    bad = clean.derive({"$Type": "VRage:Keen.VRage.DCS.Definitions.EntityCompositeDefinitionObjectBuilder",
                        "$Value": comp})
    assert [e[0] for e in csw.validate(csw.walk(bad), TypeIndex(facts()))] == ["client-type-on-server"]
    assert csw.validate(csw.walk(clean), TypeIndex(facts())) == []      # the original is untouched


def test_later_roots_replace_earlier_files(tmp_path):
    a = write_tree(tmp_path / "A", universe_files(server_types=(CLIENT_TYPE,)))
    b = write_tree(tmp_path / "B", {k: v for k, v in universe_files().items() if "ServerComposition" in k})
    u = csw.Universe([str(a), str(b)])
    assert csw.validate(csw.walk(u), TypeIndex(facts())) == []


def test_cached_root_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root = write_tree(tmp_path / "Vanilla", universe_files())
    first = csw.Universe([str(root)], cached_roots=[str(root)])
    assert list((tmp_path / "cache" / "se2modkit" / "universe").glob("universe-*.pickle"))
    second = csw.Universe([str(root)], cached_roots=[str(root)])
    assert second.entries.keys() == first.entries.keys()
    assert csw.unresolved(csw.walk(second)) == {}
