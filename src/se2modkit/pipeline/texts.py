"""A mod's own en-US texts (no Blender): every display string the package stages resolves as a localisation key.

Why. Every Name / Description / Tooltip / DisplayName in SE2 is a LocKey (one member, TextId) looked up in the culture
texts. A value that is no key logs, at world load, "Unrecognized value '<text>'" (the game's definition validation)
and, each time the UI shows it, "LOCALIZATION WARNING: Could not find key <text> in the culture set en-US".

How. A CultureInfoAssetDefinition on the vanilla en-US CultureId (vanilla MainMenuData/Texts/en-US.def) whose one
Resource (ContentId "<Mod>Texts") points at <Mod>/Texts/en-US/<Mod>.loc-texts, a flat JSON object key -> text;
the game merges every definition of a culture (content IDs unique per culture).

Keys ARE the English texts. The base culture's strings are read once at game start, while a mod's World-set
definitions arrive with the world, so the in-game lookup of a mod key can miss; a miss evaluates to the key itself,
so an identifier key would show the identifier and an English key shows the right text either way. A key may never
be a vanilla key: the game loads texts last-writer-wins, so it would rename a vanilla thing
(`write_texts` and `stage` refuse).

`stage(hooks, G, ...)` runs after the rest of the package is staged: it reads back every staged definition, collects
its display strings (`display_strings`: the ROOT_KEYS members and UIData.Name; Overrides carry none) and stages the
.loc-texts file and the culture definition.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from .. import stage as ST
from ..defs.assetio import body, envelope, read_asset

if TYPE_CHECKING:
    from ..defs.guids import Guids
    from .hooks import ModHooks

__all__ = ["CONTENT_SUFFIX", "EN_US", "ROOT_KEYS", "T_CULTURE", "TextsError", "def_file", "def_type",
           "display_strings", "loc_file", "loc_texts", "stage", "staged_strings", "vanilla_keys", "write_texts"]

EN_US = "f59b9226-51d4-4fe7-9479-dfd24c60a387"   # MainMenuData/Texts/CultureIdentifiers/ID_en-US.def
CONTENT_SUFFIX = "Texts"
T_CULTURE = "VRage:Keen.VRage.Library.Localization.CultureInfoAssetDefinitionObjectBuilder"
# definition type (last segment of $Type / DefinitionType) -> its LocKey members at the root
ROOT_KEYS = {"BlockKindDefinition": ("Name", "Description"), "BlockGroupDefinition": ("Name",),
             "SubcategoryDefinition": ("Name", "Tooltip"), "ItemDefinition": ("DisplayName", "Description")}


class TextsError(RuntimeError):
    """The texts cannot be written; the message is plain sentences."""


def loc_file(hooks: ModHooks) -> str:
    return f"{hooks.name}/Texts/en-US/{hooks.name}.loc-texts"


def def_file(hooks: ModHooks) -> str:
    return f"{hooks.name}/Texts/{hooks.name}_en-US.def"


def def_type(data: dict) -> str:
    """'BlockKindDefinition' for a .def ($Type ...ObjectBuilder) or a partialdef (its DefinitionType)."""
    v = data.get("$Value") or {}
    t = v.get("DefinitionType") if "PartialDefinitionKind" in v else data.get("$Type", "")
    t = (t or "").rsplit(".", 1)[-1]
    return t[:-len("ObjectBuilder")] if t.endswith("ObjectBuilder") else t


def display_strings(data: dict) -> list[tuple[str, str]]:
    """[(member, text)] of one definition's LocKey members (ROOT_KEYS, UIData.Name); none for an Override."""
    v = data.get("$Value") or {}
    if v.get("PartialDefinitionKind") == "Override":
        return []
    b = body(data)
    out = [(k, b[k]) for k in ROOT_KEYS.get(def_type(data), ()) if isinstance(b.get(k), str) and b[k]]
    ui = b.get("UIData")
    if isinstance(ui, dict) and isinstance(ui.get("Name"), str) and ui["Name"]:
        out.append(("UIData.Name", ui["Name"]))
    return out


def staged_strings(root: str | os.PathLike) -> dict[str, list[str]]:
    """{text: [rel path, ...]} over every .def / .partialdef under root (the staged package)."""
    root = Path(root)
    found: dict[str, list[str]] = {}
    for dp, dirs, fs in os.walk(root):
        dirs.sort()
        for f in sorted(fs):
            if f.endswith((".def", ".partialdef")):
                p = Path(dp) / f
                for _, text in display_strings(read_asset(p)):
                    found.setdefault(text, []).append(p.relative_to(root).as_posix())
    return found


def vanilla_keys(sdk_assets: str | os.PathLike) -> set[str]:
    """Every key of the SDK's vanilla .loc-texts: all cultures' files under GameData/Vanilla (its Assets/ and Content/;
    `sdk_assets` is GameData/Vanilla/Assets). Unreadable files are skipped; none at all is a TextsError."""
    van = Path(sdk_assets).parent
    keys: set[str] = set()
    files = sorted(van.glob("*/**/*.loc-texts"))
    if not files:
        raise TextsError(f"No vanilla .loc-texts files under {van}: point sdk_assets at the Mod SDK's "
                         "GameData/Vanilla/Assets (see `se2mod env`).")
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            keys |= set(data)
    return keys


_read_vanilla_keys = vanilla_keys   # (stage's keyword argument shadows the name)


def loc_texts(strings: Iterable[str]) -> dict[str, str]:
    """The .loc-texts body: key == the English text, sorted (stable output)."""
    return {k: k for k in sorted(set(strings))}


def write_texts(entries: Iterable[str], hooks: ModHooks, out: str | os.PathLike, *,
                vanilla_keys: Iterable[str]) -> Path:
    """Write `out/<Mod>/Texts/en-US/<Mod>.loc-texts` for the display strings `entries`; refuse (TextsError, nothing
    written) any entry that is a vanilla text key. Returns the written path."""
    entries = list(entries)
    shadow = sorted(set(entries) & set(vanilla_keys))
    if shadow:
        raise TextsError(f"These display strings are vanilla text keys and would rename vanilla things: "
                         f"{', '.join(shadow)}. Rename them in the mod.")
    path = Path(out) / loc_file(hooks)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(loc_texts(entries), f, indent=4, ensure_ascii=False)
        f.write("\n")
    return path


def stage(hooks: ModHooks, G: Guids, *, vanilla_keys: Iterable[str] | None = None,
          sdk_assets: str | os.PathLike | None = None) -> dict:
    """Stage the en-US texts of every display string staged so far, and the culture definition that loads them.
    The vanilla keys come from `vanilla_keys`, else from the Mod SDK's `sdk_assets` (`SE2Env.sdk_assets`).
    Returns {"file", "definition", "guid", "keys"}."""
    if vanilla_keys is None:
        if sdk_assets is None:
            raise TextsError("Staging texts needs the vanilla text keys: pass the Mod SDK's assets folder "
                             "(sdk_assets, see `se2mod env`) or vanilla_keys.")
        vanilla_keys = _read_vanilla_keys(sdk_assets)
    strings = staged_strings(hooks.paths.staged)
    loc = loc_file(hooks)
    write_texts(strings, hooks, hooks.paths.staged, vanilla_keys=vanilla_keys)
    ST.stage_meta(hooks, loc, G[loc])
    guid = G["def/texts/en-US"]
    ST.stage_def(hooks, def_file(hooks), envelope(T_CULTURE, {
        "Guid": guid, "CultureId": EN_US,
        "Resources": [{"Key": G["key/texts/en-US/resource"],
                       "Value": {"ContentId": hooks.name + CONTENT_SUFFIX, "Asset": "{G}" + G[loc]}}]},
        hooks.stamps()[0]))
    return {"file": loc, "definition": def_file(hooks), "guid": guid, "keys": len(strings)}
