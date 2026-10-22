"""Definition and `.meta` JSON helpers shared by the clone step and the staging writer (no Blender).

The bundle stamps are never module constants: `write_meta` takes the `(vrage, game)` pair (`ModHooks.stamps()`).
Files are written as the mods' committed files are: 2-space indented, ASCII-escaped JSON, one final LF.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .envelope import envelope, write_json as _write_json

__all__ = ["META_TYPE", "PARTIAL", "body", "envelope", "meta_id", "own_guid", "read_asset", "read_json",
           "write_asset", "write_json", "write_meta"]

META_TYPE = "VRage:Keen.VRage.ContentPipeline.Metafiles.MetaData"
PARTIAL = "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff"

StrPath = str | os.PathLike


def read_asset(path: StrPath) -> dict:
    """A definition / .meta / .model file (a leading BOM is fine)."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_asset(path: StrPath, data) -> None:
    """Write `data` (creating parent folders) in the committed-file format."""
    _write_json(Path(path), data)


read_json = read_asset     # the names the mods' pipelines use
write_json = write_asset


def _meta(path: StrPath) -> Path:
    return Path(os.fspath(path) + ".meta")


def write_meta(path: StrPath, asset_id: str, stamps: tuple[dict, dict]) -> None:
    """`<path>.meta` holding only the AssetID (the SDK build fills in hashes and processor info)."""
    write_asset(_meta(path), envelope(META_TYPE, {"AssetID": asset_id, "InternalItems": []}, stamps[0]))


def meta_id(path: StrPath) -> str | None:
    """The AssetID of `<path>.meta`, or None when there is none."""
    m = _meta(path)
    return read_asset(m)["$Value"]["AssetID"] if m.exists() else None


def own_guid(data: dict) -> str | None:
    """GUID a definition file defines: Manipulator.Guid for a Copy partialdef, $Value.Guid for a .def, None for an
    Override partialdef (it patches something else)."""
    v = data.get("$Value") or {}
    if v.get("PartialDefinitionKind") == "Copy":
        return (v.get("Manipulator") or {}).get("Guid")
    if "PartialDefinitionKind" in v:
        return None
    return v.get("Guid")


def body(data: dict) -> dict:
    """The editable value of a definition (the Manipulator of a partialdef)."""
    v = data["$Value"]
    return v["Manipulator"] if "PartialDefinitionKind" in v else v
