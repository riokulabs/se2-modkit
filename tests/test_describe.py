"""`se2modkit.describe` without Blender: roles, box gaps, the analysis on given boxes, targets and the text."""
from types import SimpleNamespace

import pytest

from se2modkit import describe as D


def _part(name, lo, hi, coll="Block", role=None, tris=12):
    return {"name": name, "collection": coll, "role": role or D.role_of(name, coll), "lo": list(lo), "hi": list(hi),
            "tris": tris}


def test_roles_from_name_then_collection():
    assert D.role_of("UCX_Body") == "collider" and D.role_of("Body_LOD1") == "lod"
    assert D.role_of("Body") == "hull" and D.role_of("Port_Front") == "port"
    assert D.role_of("Decal_Warning") == "decal" and D.role_of("Bolt") == "part"
    assert D.role_of("Box", "Collision") == "collider"
    assert D.role_of("Column") == "part"                                  # "col" only as a whole word part
    assert D.role_of("pipe_clamp") == "part" and D.role_of("support_strut") == "part"   # words, not substrings
    assert D.role_of("top_light.001") == "light" and D.role_of("Strut_frame") == "hull"


def test_ship_reason_is_one_rule_in_order():
    def ob(name="Body", type_="MESH", hide_render=False):
        return SimpleNamespace(name=name, type=type_, hide_render=hide_render)
    assert D.ship_reason(ob()) is None
    assert D.ship_reason(ob(type_="CURVE")) is None
    assert D.ship_reason(ob(type_="EMPTY")) == "not geometry (EMPTY)"
    assert D.ship_reason(ob("Cut"), cutters={"Cut"}) == "boolean cutter"
    assert D.ship_reason(ob("Cut", hide_render=True), cutters={"Cut"}) == "render-hidden"
    assert D.ship_reason(ob(hide_render=True), True) == "render-hidden collection"


def test_bbox_gap_is_separation_or_minus_the_shallowest_overlap():
    assert D.bbox_gap((0, 0, 0), (1, 1, 1), (3, 0, 0), (4, 1, 1)) == 2
    assert D.bbox_gap((0, 0, 0), (10, 10, 10), (8, 0, 0), (20, 10, 10)) == -2
    assert D.bbox_gap((0, 0, 0), (1, 1, 1), (1, 0, 0), (2, 1, 1)) == 0


def _scene():
    return [_part("Body", (-500, -500, -500), (500, 500, 500)),
            _part("Lid", (-300, -300, 500), (300, 300, 600)),                         # sits on the body
            _part("Bolt", (2000, 0, 0), (2010, 10, 10)),                              # floats
            _part("Strut_L", (-900, -50, -50), (-700, 50, 50)),                       # mirrored pair on x, 3 mm off
            _part("Strut_R", (703, -50, -50), (903, 50, 50)),
            _part("Pin", (-100, -100, -100), (100, 100, 100), role="part"),           # buried 300 mm in the body
            _part("UCX_Body", (-500, -500, -500), (500, 500, 500))]


def test_analyze_numbers_relates_and_lists():
    body = D.analyze(_scene(), mid=[0, 0, 0])
    by = {p["name"]: p for p in body["parts"]}
    assert [p["name"] for p in body["parts"]] == sorted(by, key=str.lower)            # one collection: by name
    assert by["Lid"]["nearest"]["name"] == "Body" and by["Lid"]["nearest"]["gap_mm"] == 0
    assert by["Body"]["id"] in by["Lid"]["contacts"]
    assert body["floating"] == [by["Bolt"]["id"], by["Strut_L"]["id"], by["Strut_R"]["id"]]
    assert by["UCX_Body"]["nearest"] is None and not by["UCX_Body"]["floating"]       # colliders are left out
    m = by["Strut_L"]["mirror"]
    assert m["axis"] == "x" and m["id"] == by["Strut_R"]["id"] and m["error_mm"] == pytest.approx(3.0)
    assert body["mirror_errors"] == [{"a": by["Strut_L"]["id"], "b": by["Strut_R"]["id"], "axis": "x",
                                      "error_mm": pytest.approx(3.0)}]
    assert by["Body"]["mirror"] is None and by["Body"]["symmetric"] == {"axes": "xyz", "error_mm": 0}
    assert by["Strut_L"]["symmetric"]["axes"] == "yz" and by["Bolt"]["symmetric"] is None
    (ov,) = body["overlaps"]
    assert {ov["a"], ov["b"]} == {by["Body"]["id"], by["Pin"]["id"]} and ov["gap_mm"] == -200
    assert by["Body"]["cell"] == [0, 0, 0] and by["Lid"]["cell"] == [0, 0, pytest.approx(2.2)]


def test_mesh_gap_replaces_the_box_gap_only_when_close():
    asked = []

    def mesh(a, b):
        asked.append({a["name"], b["name"]})
        return 7.5
    body = D.analyze([_part("A", (0, 0, 0), (10, 10, 10)), _part("B", (20, 0, 0), (30, 10, 10)),
                      _part("C", (500, 0, 0), (510, 10, 10))], mesh_gap=mesh)
    assert asked == [{"A", "B"}]
    assert body["parts"][0]["nearest"]["gap_mm"] == 7.5


TARGETS_MD = """# Kit

Some prose.

```targets
[[target]]
name = "nozzle to body length"
a = "Nozzle*.z"   # union of every nozzle
b = "Body.z"
value = 0.6
tol = 0.05

[[target]]
name = "body width"
a = "Body.x"
value = 1000
tol = 1
```

```toml
[[target]]
name = "not a target block"
a = "X.x"
value = 1
tol = 1
```

```targets
[[target]]
name = "missing"
a = "Ghost*.y"
value = 1
tol = 1
```
"""


def test_parse_targets_reads_only_targets_blocks():
    ts = D.parse_targets(TARGETS_MD)
    assert [t["name"] for t in ts] == ["nozzle to body length", "body width", "missing"]
    assert ts[0]["a"] == ("Nozzle*", "z") and ts[0]["b"] == ("Body", "z") and ts[0]["value"] == 0.6
    assert ts[1]["b"] is None


@pytest.mark.parametrize("body,why", [
    ('a = "Body"\nvalue = 1\ntol = 1', "GLOB.AXIS"),
    ('a = "Body.w"\nvalue = 1\ntol = 1', "GLOB.AXIS"),
    ('a = "Body.x"\nvalue = 1', "needs a, value and tol"),
    ('a = "Body.x"\nvalue = "big"\ntol = 1', "numbers"),
    ('a = "Body.x" value', "targets block 1"),
])
def test_parse_targets_says_what_is_wrong(body, why):
    with pytest.raises(D.TargetError, match=why):
        D.parse_targets(f"```targets\n[[target]]\nname = \"t\"\n{body}\n```\n", "kit.md")


def test_check_targets_ratio_length_and_no_match():
    parts = [_part("Body", (0, 0, 0), (1000, 1000, 2000)), _part("Nozzle_A", (0, 0, -1000), (100, 100, 0)),
             _part("Nozzle_B", (0, 0, -1200), (100, 100, -1000))]
    res = {r["name"]: r for r in D.check_targets(D.parse_targets(TARGETS_MD), parts)}
    assert res["nozzle to body length"]["measured"] == pytest.approx(0.6) and res["nozzle to body length"]["ok"]
    assert res["body width"]["ok"] and res["body width"]["measured"] == 1000
    assert not res["missing"]["ok"] and "matches no part" in res["missing"]["why"]


def test_targets_limited_to_collections():
    md = """```targets
[[target]]
name = "industrial bell is the widest part"
collections = ["*_ind"]
a = "*_bell.x"
b = "*.x"
value = 1.0
tol = 0.02

[[target]]
name = "military bell inside the plate"
collections = "*_mil"
a = "*_bell.x"
b = "*.x"
value = 0.88
tol = 0.05
```
"""
    ts = D.parse_targets(md)
    assert ts[0]["collections"] == ["*_ind"] and ts[1]["collections"] == ["*_mil"]
    ind = [_part("i_bell", (0, 0, 0), (1000, 1000, 500), coll="M_ind"),
           _part("i_plate", (100, 100, 500), (900, 900, 600), coll="M_ind")]
    res = D.check_targets(ts, ind)
    assert [r["name"] for r in res] == ["industrial bell is the widest part"] and res[0]["ok"]
    mil = [_part("m_bell", (50, 50, 0), (930, 930, 500), coll="M_mil"),
           _part("m_plate", (0, 0, 500), (1000, 1000, 600), coll="M_mil")]
    res = D.check_targets(ts, ind + mil)  # both families in one run: each target measures its own parts
    assert [r["ok"] for r in res] == [True, True] and res[1]["measured"] == pytest.approx(0.88)
    nested = [_part("m_bell", (50, 50, 0), (930, 930, 500), coll="M_mil"),
              _part("base_plate", (0, 0, 500), (1000, 1000, 600), coll="M_mil_base")]  # a child collection
    for p in nested:
        p["block"] = "M_mil"                       # the collection the run named
    res = D.check_targets(ts, nested)              # the block decides, so the nested base counts
    assert [r["name"] for r in res] == ["military bell inside the plate"] and res[0]["ok"]
    for p in ind:
        p["block"] = "M_ind"
    res = D.check_targets(ts, ind + nested)       # two blocks in one run: each target measures only its own block
    assert [r["ok"] for r in res] == [True, True] and res[0]["measured"] == pytest.approx(1.0)
    assert D.check_targets(ts, [dict(p, block="Other") for p in nested]) == []
    with pytest.raises(D.TargetError, match="list of name patterns"):
        D.parse_targets('```targets\n[[target]]\na = "x.x"\nvalue = 1\ntol = 1\ncollections = 3\n```\n')


def test_finish_and_text():
    body = D.analyze(_scene(), mid=[0, 0, 0])
    rep = D.finish(body, envelope=[2.5, 2.5, 2.5], targets=D.parse_targets(TARGETS_MD), source="toy.blend",
                   collections=["Block"], mid_from="block origin")
    b = rep["block"]
    assert b["parts"] == 7 and b["tris"] == 84 and b["size_mm"] == [2910, 1000, 1100]
    assert b["fill"]["x"] == pytest.approx(116.4) and b["fill"]["volume"] == pytest.approx(20.49, abs=0.01)
    lines = D.text(rep)
    assert lines[0] == "toy.blend [Block]: 7 parts, 84 tris"
    assert any(ln.startswith("#") and "Bolt" in ln and ln.endswith("FLOATING") for ln in lines)
    assert any(ln.startswith("FLOATING (3): ") for ln in lines)
    assert any(ln.startswith("OVERLAP (1): ") and "-200 mm" in ln for ln in lines)
    assert any(ln.startswith("MIRROR-ERROR (1): ") and "(x) 3.0 mm" in ln for ln in lines)
    assert sum(ln.startswith("FAIL target") for ln in lines) == 2                    # no Nozzle or Ghost parts here
    assert all(len(ln) < 400 for ln in lines)
