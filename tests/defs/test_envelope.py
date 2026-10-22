from se2modkit.defs.envelope import envelope, write_json

# What `se2mod.py write_json(path, envelope(...))` wrote before the port, for the same input.
LEGACY = (
    b'{\n  "$Bundles": {\n    "System.Runtime": "1.0.0.0",\n    "VRage": "4.0.0.3155"\n  },\n'
    b'  "$Type": "VRage:Keen.Test.Thing",\n  "$Value": {\n    "Guid": "0000",\n    "Name": "T\\u00fcr",\n'
    b'    "List": [\n      1,\n      null,\n      true\n    ],\n    "Empty": {}\n  }\n}\n'
)


def test_envelope_matches_legacy_format(tmp_path):
    out = tmp_path / "sub" / "x.def"
    data = envelope("VRage:Keen.Test.Thing", {"Guid": "0000", "Name": "Tür", "List": [1, None, True], "Empty": {}},
                    {"System.Runtime": "1.0.0.0", "VRage": "4.0.0.3155"})
    write_json(out, data)
    assert out.read_bytes() == LEGACY


def test_envelope_keys_in_order():
    assert list(envelope("T", {}, {"VRage": "1"})) == ["$Bundles", "$Type", "$Value"]
