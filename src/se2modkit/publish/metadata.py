"""The SE2 Steam Workshop metadata, written the way the SE2 Mod HUB writes it.

Everything here is re-implemented from the observed behavior of the 4.0 game and SDK; no game code is copied.
What it reproduces:

* What the HUB uploads: the item description {Title = Name, Description = Summary, Visibility = Public,
  Tags = tags + "UGCModTypeTag", PreviewFilePath} and the mod metadata {Id, ProjectID, Version = the .vrgproj
  Version}.
* At upload the mod metadata gets Id = the item id, ProjectID = {Guid, ModId = item id, ServiceId = "Steam"} and the
  description as its BaseMetadata; the Workshop metadata wraps it as {Id, PreviewUrl, FullMetadata, Author (the
  SteamID), IsReferenceToRemote, LastLocallyUpdated} in that member order, serialized as the archive JSON in UTF-8.
* The item update: the order of the Workshop calls, the VRageProjectID key-value tag (the project GUID with '-'
  -> '_'), the description set only when it is not blank, and an empty change note. An item whose metadata is empty
  is treated as deleted by the game.
* Item id -> GUID: two little-endian unsigned 64-bit values (0, id).
* The archive envelope $Bundles / $Type / $Value, indented, with relaxed escaping; dates as round-trip ("o")
  strings, versions as their dotted string or null; members a struct leaves unset (Id, Dependencies) are null.
* Ground truth for the byte layout (CRLF, 2-space indent, no trailing newline): the game's own
  .steam-ugc-metadata cache files under the Proton prefix's AppData/Roaming/SpaceEngineers2/Temp/UGCContentCache.
"""
from __future__ import annotations

import datetime as _dt
import unicodedata
import uuid
from dataclasses import dataclass, field

KV_TAG_KEY = "VRageProjectID"
SERVICE_STEAM = "Steam"
MOD_TYPE_TAG = "UGCModTypeTag"
# The Mod HUB's fixed set of SE2 mod tags.
MOD_TAGS = ("ModTagArmor", "ModTagAudio", "ModTagAsteroid", "ModTagBlock", "ModTagCharacter",
            "ModTagMaterial", "ModTagParticles", "ModTagSkybox", "ModTagOther")
MAX_METADATA_BYTES = 5000          # Steamworks k_cchDeveloperMetadataMax
DEFAULT_TIME = "0001-01-01T00:00:00.0000000"   # default(DateTime).ToString("o")
VISIBILITIES = ("Unlisted", "FriendsOnly", "Private", "Public")   # the game's visibility names, in its order
# ERemoteStoragePublishedFileVisibility values (Steamworks); the game maps its visibility to them by name
STEAM_VISIBILITY = {"Public": 0, "FriendsOnly": 1, "Private": 2, "Unlisted": 3}


def guid_to_steam_kv(guid: str) -> str:
    """VRageProjectID KV-tag value: the GUID's "D" form with '-' replaced by '_' (SteamKV.GuidToSteam)."""
    return str(uuid.UUID(guid)).replace("-", "_")


def item_id_to_guid(item_id: int) -> str:
    """UGCHelper.GuidFromULong: the 16 bytes of (0UL, id) little-endian, read as a .NET Guid."""
    if not 0 <= item_id < 2 ** 64:
        raise ValueError("item id out of range")
    raw = (0).to_bytes(8, "little") + item_id.to_bytes(8, "little")
    return str(uuid.UUID(bytes_le=raw))


def guid_to_item_id(guid: str) -> int:
    raw = uuid.UUID(guid).bytes_le
    if raw[:8] != b"\0" * 8:
        raise ValueError(f"{guid} is not a UGC item GUID")
    return int.from_bytes(raw[8:], "little")


def dotnet_now_o(now: _dt.datetime | None = None) -> str:
    """DateTime.Now.ToString("o"): local time, 7 fractional digits, numeric UTC offset."""
    now = (now or _dt.datetime.now()).astimezone()
    off = now.utcoffset() or _dt.timedelta()
    sign = "-" if off < _dt.timedelta() else "+"
    off = abs(off)
    hh, rem = divmod(int(off.total_seconds()), 3600)
    return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond:06d}0{sign}{hh:02d}:{rem // 60:02d}"


# ---------------------------------------------------------------------------------------------------------------
# A writer that reproduces System.Text.Json's Utf8JsonWriter(Indented=true, UnsafeRelaxedJsonEscaping) output.

_SHORT_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _needs_escape(ch: str) -> bool:
    cp = ord(ch)
    if ch in _SHORT_ESCAPES or cp < 0x20 or 0x7F <= cp <= 0x9F:
        return True
    if cp > 0xFFFF:           # the inbox encoders only allow BMP code points; astral chars become surrogate escapes
        return True
    # Categories the .NET encoder never allows (unassigned, surrogate, private use, line/paragraph separator).
    return ch != " " and unicodedata.category(ch) in ("Cn", "Cs", "Co", "Zl", "Zp", "Zs")


def json_string(s: str) -> str:
    out = []
    for ch in s:
        if not _needs_escape(ch):
            out.append(ch)
        elif ch in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[ch])
        else:
            data = ch.encode("utf-16-be")
            for i in range(0, len(data), 2):
                out.append("\\u%04X" % int.from_bytes(data[i:i + 2], "big"))
    return '"' + "".join(out) + '"'


def has_uncertain_chars(s: str) -> bool:
    """True if s contains characters whose escaping we could not verify against .NET (non-ASCII format chars etc.)."""
    return any(ord(c) > 0x7E and unicodedata.category(c) in ("Cf", "Mn", "Me") for c in s)


class Raw(str):
    """A pre-formatted JSON scalar (numbers, true/false/null)."""


def _render(value, indent: int, nl: str) -> str:
    pad = "  " * indent
    if value is None:
        return "null"
    if isinstance(value, Raw):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = ("," + nl).join("  " * (indent + 1) + _render(v, indent + 1, nl) for v in value)
        return "[" + nl + inner + nl + pad + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = ("," + nl).join("  " * (indent + 1) + json_string(k) + ": " + _render(v, indent + 1, nl)
                                for k, v in value.items())
        return "{" + nl + inner + nl + pad + "}"
    raise TypeError(type(value))


def render_json(value, newline: str = "\r\n") -> str:
    return _render(value, 0, newline)


# ---------------------------------------------------------------------------------------------------------------

@dataclass
class WorkshopMetadataInput:
    item_id: int
    project_guid: str
    author_steam_id: int
    title: str
    summary: str                  # what the HUB calls "Summary" -> BaseMetadata.Description (<= 250 chars)
    visibility: str               # Keen UGCVisiblity name
    tags: list[str]               # ModTag* names, without UGCModTypeTag
    project_version: str | None = None   # .vrgproj "Version" (null in all workspace mods)
    preview_file: str = "thumb.jpg"
    last_locally_updated: str = field(default_factory=dotnet_now_o)
    bundles: dict[str, str] = field(default_factory=lambda: {"Game2": "4.0.0.3151", "System.Runtime": "1.0.0.0",
                                                             "VRage": "4.0.0.3151"})
    dependencies: list[int] = field(default_factory=list)   # required Workshop items; [] -> null like the HUB


def hub_tags(tags: list[str]) -> list[str]:
    """tags.Append("UGCModTypeTag").Distinct(): order kept, duplicates dropped."""
    out: list[str] = []
    for t in list(tags) + [MOD_TYPE_TAG]:
        if t not in out:
            out.append(t)
    return out


def build_metadata_object(m: WorkshopMetadataInput) -> dict:
    if m.visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {VISIBILITIES}")
    mod_id = {"Id": item_id_to_guid(m.item_id), "ServiceName": SERVICE_STEAM}
    base = {
        "Id": None,                       # the HUB's description struct never gets an Id on the upload path
        "Title": m.title,
        "Description": m.summary,
        "Visiblity": m.visibility,        # sic: Keen's spelling
        "Tags": hub_tags(m.tags),
        "IsSubscribed": False,
        "VotesUp": 0,
        "VotesDown": 0,
        "Rating": Raw("0"),               # float 0 -> Utf8JsonWriter writes 0
        "IsOwner": False,
        "NumSubscriptions": 0,
        "Size": 0,
        "OwnerName": None,
        "CreatedAt": DEFAULT_TIME,
        "UpdatedAt": DEFAULT_TIME,
        "WorkshopURL": None,
        "PreviewFilePath": m.preview_file or None,
        # a list of {Id = item GUID, ServiceName}; the HUB leaves it null.
        "Dependencies": [{"Id": item_id_to_guid(d), "ServiceName": SERVICE_STEAM} for d in m.dependencies] or None,
    }
    full = {
        "$Type": "VRage:Keen.VRage.Core.Services.Data.ModMetadata",
        "Id": dict(mod_id),
        "ProjectID": {"Guid": str(uuid.UUID(m.project_guid)), "ModId": dict(mod_id), "ServiceId": SERVICE_STEAM},
        "Version": m.project_version,
        "BaseMetadata": base,
    }
    value = {
        "Id": m.item_id,
        "PreviewUrl": None,
        "FullMetadata": full,
        "Author": m.author_steam_id,
        "IsReferenceToRemote": False,
        "LastLocallyUpdated": m.last_locally_updated,
    }
    return {
        "$Bundles": dict(sorted(m.bundles.items())),   # SortedSet<BundleInfo>, ordinal by name
        "$Type": "VRage:Keen.VRage.Steam.Data.SteamUgcMetadata",
        "$Value": value,
    }


def build_metadata_json(m: WorkshopMetadataInput, newline: str = "\r\n") -> str:
    text = render_json(build_metadata_object(m), newline)
    n = len(text.encode("utf-8"))
    if n > MAX_METADATA_BYTES:
        raise ValueError(f"metadata is {n} bytes; Steam's limit is {MAX_METADATA_BYTES} (shorten the summary/title)")
    return text
