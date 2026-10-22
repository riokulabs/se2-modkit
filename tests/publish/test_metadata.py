"""Keen metadata JSON and the VRageProjectID KV tag, byte for byte.

The expected strings are written out by hand from the game's member order and the byte layout of the game's own
.steam-ugc-metadata files (CRLF, 2-space indent, ': ', no trailing newline, relaxed escaping); see
se2modkit/publish/metadata.py.
"""
import json
from pathlib import Path

import pytest

from se2modkit.publish.metadata import (MAX_METADATA_BYTES, Raw, WorkshopMetadataInput, build_metadata_json,
                                 guid_to_item_id, guid_to_steam_kv, hub_tags, item_id_to_guid, json_string,
                                 render_json)

FIXTURES = Path(__file__).parent / "fixtures"


def test_kv_tag_is_guid_with_underscores():
    assert guid_to_steam_kv("60cec301-8978-4c07-9b97-8a94a445d05d") == "60cec301_8978_4c07_9b97_8a94a445d05d"
    assert guid_to_steam_kv("60CEC301-8978-4C07-9B97-8A94A445D05D") == "60cec301_8978_4c07_9b97_8a94a445d05d"


@pytest.mark.parametrize("item,guid", [
    (3638615223, "00000000-0000-0000-b7dc-e0d800000000"),   # Large Airlock's .vrgproj ModId (installed item)
    (3717735048, "00000000-0000-0000-8822-98dd00000000"),   # a cached workshop item's BaseMetadata.Id
    (0, "00000000-0000-0000-0000-000000000000"),
])
def test_item_id_guid_roundtrip(item, guid):
    assert item_id_to_guid(item) == guid
    assert guid_to_item_id(guid) == item


def test_hub_tags_appends_type_tag_once():
    assert hub_tags(["ModTagBlock"]) == ["ModTagBlock", "UGCModTypeTag"]
    assert hub_tags(["ModTagBlock", "UGCModTypeTag", "ModTagBlock"]) == ["ModTagBlock", "UGCModTypeTag"]
    assert hub_tags([]) == ["UGCModTypeTag"]


@pytest.mark.parametrize("raw,expected", [
    ('a"b\\c', '"a\\"b\\\\c"'),
    ("x\r\ny\tz", '"x\\r\\ny\\tz"'),
    ("\x1b", '"\\u001B"'),
    ("café ’quote’ – •", '"café ’quote’ – •"'),  # kept raw
    ("3 m", '"3\\u202Fm"'),             # narrow no-break space (Zs) is escaped, as the game writes it
    ("\U0001F44D", '"\\uD83D\\uDC4D"'),      # astral chars -> surrogate pair escapes
    ("<b>&'", "\"<b>&'\""),                   # relaxed escaping keeps HTML-sensitive characters
])
def test_json_string_escaping(raw, expected):
    assert json_string(raw) == expected


EXPECTED_NEW = (
    '{\r\n'
    '  "$Bundles": {\r\n'
    '    "Game2": "4.0.0.3151",\r\n'
    '    "System.Runtime": "1.0.0.0",\r\n'
    '    "VRage": "4.0.0.3151"\r\n'
    '  },\r\n'
    '  "$Type": "VRage:Keen.VRage.Steam.Data.SteamUgcMetadata",\r\n'
    '  "$Value": {\r\n'
    '    "Id": 3900000001,\r\n'
    '    "PreviewUrl": null,\r\n'
    '    "FullMetadata": {\r\n'
    '      "$Type": "VRage:Keen.VRage.Core.Services.Data.ModMetadata",\r\n'
    '      "Id": {\r\n'
    '        "Id": "00000000-0000-0000-0147-75e800000000",\r\n'
    '        "ServiceName": "Steam"\r\n'
    '      },\r\n'
    '      "ProjectID": {\r\n'
    '        "Guid": "60cec301-8978-4c07-9b97-8a94a445d05d",\r\n'
    '        "ModId": {\r\n'
    '          "Id": "00000000-0000-0000-0147-75e800000000",\r\n'
    '          "ServiceName": "Steam"\r\n'
    '        },\r\n'
    '        "ServiceId": "Steam"\r\n'
    '      },\r\n'
    '      "Version": null,\r\n'
    '      "BaseMetadata": {\r\n'
    '        "Id": null,\r\n'
    '        "Title": "Demo Mod by Rioku Labs",\r\n'
    '        "Description": "",\r\n'
    '        "Visiblity": "Unlisted",\r\n'
    '        "Tags": [\r\n'
    '          "ModTagBlock",\r\n'
    '          "UGCModTypeTag"\r\n'
    '        ],\r\n'
    '        "IsSubscribed": false,\r\n'
    '        "VotesUp": 0,\r\n'
    '        "VotesDown": 0,\r\n'
    '        "Rating": 0,\r\n'
    '        "IsOwner": false,\r\n'
    '        "NumSubscriptions": 0,\r\n'
    '        "Size": 0,\r\n'
    '        "OwnerName": null,\r\n'
    '        "CreatedAt": "0001-01-01T00:00:00.0000000",\r\n'
    '        "UpdatedAt": "0001-01-01T00:00:00.0000000",\r\n'
    '        "WorkshopURL": null,\r\n'
    '        "PreviewFilePath": "thumb.jpg",\r\n'
    '        "Dependencies": null\r\n'
    '      }\r\n'
    '    },\r\n'
    '    "Author": 76561190000000001,\r\n'
    '    "IsReferenceToRemote": false,\r\n'
    '    "LastLocallyUpdated": "2026-10-20T05:10:19.0179622-07:00"\r\n'
    '  }\r\n'
    '}'
)


def _input(**kw):
    base = dict(item_id=3900000001, project_guid="60cec301-8978-4c07-9b97-8a94a445d05d",
                author_steam_id=76561190000000001, title="Demo Mod by Rioku Labs", summary="",
                visibility="Unlisted", tags=["ModTagBlock"], project_version=None,
                last_locally_updated="2026-10-20T05:10:19.0179622-07:00")
    base.update(kw)
    return WorkshopMetadataInput(**base)


def test_metadata_matches_hand_written_fixture_byte_for_byte():
    assert build_metadata_json(_input()) == EXPECTED_NEW
    assert (FIXTURES / "metadata_new_item.json").read_bytes() == EXPECTED_NEW.encode("utf-8")


def test_metadata_version_and_lf_newlines():
    text = build_metadata_json(_input(project_version="1.2.0"), newline="\n")
    assert '"Version": "1.2.0",\n' in text
    assert "\r" not in text


def test_metadata_parses_back_and_has_keen_shape():
    obj = json.loads(build_metadata_json(_input()))
    assert obj["$Type"] == "VRage:Keen.VRage.Steam.Data.SteamUgcMetadata"
    assert list(obj["$Value"]) == ["Id", "PreviewUrl", "FullMetadata", "Author", "IsReferenceToRemote",
                                   "LastLocallyUpdated"]
    full = obj["$Value"]["FullMetadata"]
    assert list(full) == ["$Type", "Id", "ProjectID", "Version", "BaseMetadata"]
    assert full["Id"] == full["ProjectID"]["ModId"]


def test_metadata_size_limit():
    with pytest.raises(ValueError, match="5000"):
        build_metadata_json(_input(summary="x" * MAX_METADATA_BYTES))


def test_metadata_rejects_bad_visibility():
    with pytest.raises(ValueError):
        build_metadata_json(_input(visibility="Hidden"))


def _local_game_caches():
    """The game's own UGC metadata cache, found the way modkit finds everything (Proton prefix, native config)."""
    import os

    from se2modkit.env.core import discover_env
    env = discover_env()
    roots = []
    if env.prefix is not None:
        roots.append(env.prefix / "drive_c/users/steamuser/AppData/Roaming/SpaceEngineers2/Temp/UGCContentCache/Steam")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    roots.append((Path(xdg) if xdg else Path.home() / ".config") / "SpaceEngineers2/Temp/UGCContentCache/Steam")
    return [f for r in roots if r.exists() for f in sorted(r.glob("*/.steam-ugc-metadata"))]


@pytest.mark.skipif(not _local_game_caches(), reason="no local SE2 UGC cache to compare against")
def test_writer_reproduces_the_games_own_files():
    """Read-only: re-render every metadata file the game itself wrote and compare bytes."""
    files = _local_game_caches()
    for f in files:
        data = f.read_bytes()
        nl = "\r\n" if b"\r\n" in data else "\n"
        obj = json.loads(data, parse_float=Raw)
        assert render_json(obj, nl).encode("utf-8") == data, f
