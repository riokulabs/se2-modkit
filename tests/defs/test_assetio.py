"""`defs.assetio`: definition / .meta JSON helpers (byte-compatible with the files the mods already commit)."""

import pytest

from hookfix import STAMPS, VRAGE
from se2modkit.defs import assetio

G1 = "11111111-2222-4333-8444-555555555555"
G2 = "66666666-7777-4888-8999-aaaaaaaaaaaa"


def test_assetio_round_trip_lf(tmp_path):
    p = tmp_path / "deep" / "XX_Box.def"
    data = {"$Bundles": VRAGE, "$Type": "T", "$Value": {"Guid": G1, "Name": "Café", "List": [1, 2]}}
    assetio.write_asset(p, data)
    raw = p.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"]\n  }\n}\n")
    assert b'"Caf\\u00e9"' in raw   # ASCII-escaped, as json.dump writes it
    assert raw.startswith(b'{\n  "$Bundles": {\n    "System.Runtime": "1.0.0.0",')
    assert assetio.read_asset(p) == data
    p.write_bytes(b"\xef\xbb\xbf" + raw)   # the SDK writes a BOM on some files
    assert assetio.read_asset(p) == data
    assert assetio.read_json is assetio.read_asset and assetio.write_json is assetio.write_asset


def test_write_meta_uses_given_stamps(tmp_path):
    p = tmp_path / "XX_Box"
    assetio.write_meta(p, G1, STAMPS)
    meta = assetio.read_asset(tmp_path / "XX_Box.meta")
    assert meta == {"$Bundles": VRAGE, "$Type": assetio.META_TYPE, "$Value": {"AssetID": G1, "InternalItems": []}}
    assert assetio.meta_id(p) == G1 and assetio.meta_id(str(p)) == G1
    assert assetio.meta_id(tmp_path / "missing") is None


@pytest.mark.parametrize("value,want", [
    ({"Guid": G1}, G1),
    ({"PartialDefinitionKind": "Copy", "Manipulator": {"Guid": G2}, "BaseDefinition": G1}, G2),
    ({"PartialDefinitionKind": "Override", "Manipulator": {"Guid": G2}}, None),
    ({}, None),
])
def test_own_guid(value, want):
    assert assetio.own_guid({"$Value": value}) == want


def test_body():
    m = {"Guid": G2}
    assert assetio.body({"$Value": {"PartialDefinitionKind": "Copy", "Manipulator": m}}) is m
    v = {"Guid": G1}
    assert assetio.body({"$Value": v}) is v


def test_mods_cmd_shares_own_guid():
    from se2modkit.cli import mods_cmd
    assert mods_cmd.own_guid is assetio.own_guid
