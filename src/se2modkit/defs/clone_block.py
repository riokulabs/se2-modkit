"""clone_block: clone one vanilla SE2 block into a mod as a NEW block (own GUIDs, own menu entry).

`se2mod clone-block MOD BLOCK --to NEWNAME [--name "Display name"] [--no-unlock] [--dry-run]`:
  1. `se2mod copy -r` of the block's SDK source folder (binding, server/client prefabs and composites,
     block/model/breakable/... definitions, Subparts/) under new GUIDs;
  2. a copy of the block's BlockKind (the build-menu tile), so the clone does NOT become an extra size variant of
     the vanilla kind;
  3. the clone's block definition gets `BlockKind` = the new kind (+ `UIData.Name` if --name);
  4. the server prefab and kind are added to an Override of ProgressionInitialUnlockConfiguration (merged into an
     existing one if the mod already has it), so the block is available at start.
BLOCK is a binding name or path fragment as `se2mod chain find` shows it, e.g. Refinery750. Then `se2mod build MOD`.

Notes:
  * The whole source folder is cloned. Some folders hold more than one block (a rotor and its head, a suspension
    and its wheel prefab); all are cloned and cross-wired, which is what you want for mechanical pairs.
  * Definitions the block borrows from OTHER folders (another size's folder, Templates/, Shared/) are not cloned;
    they stay shared with vanilla, and are listed.
  * Models, icons and textures are referenced by asset id and are NOT copied: the clone uses the vanilla art until
    you swap `Model`/`UIData.Icon`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..defs.envelope import envelope
from .assetio import body, own_guid
from ..env.core import SE2Env
from ..env.versions import bundle_stamps
from . import block_chain as bc
from .block_chain import DefsError

INITIAL_UNLOCK = bc.INITIAL_UNLOCK
_DIFF = "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff"
_UNLOCK_TYPE = "Game2:Keen.Game2.Simulation.GameSystems.Progression.ProgressionInitialUnlockConfiguration"


def load(p: Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


def save(p: Path, data: dict) -> None:
    Path(p).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def _own_guid(data: dict) -> str | None:
    return own_guid(data)


def find_defining_file(root: Path, guid: str | None) -> Path | None:
    for p in sorted(Path(root).rglob("*")):
        if p.suffix in (".def", ".partialdef") and _own_guid(load(p)) == guid:
            return p
    return None


def clone_block(env: SE2Env, mod: Path | None, block: str, *, to: str, name: str | None = None,
                no_unlock: bool = False, dry_run: bool = False, idx: bc.Index | None = None) -> None:
    """Clone `block` into `mod` (a mod directory; may be None with `dry_run`). Prints what it does."""
    from ..cli.mods_cmd import copy_defs
    idx = idx or bc.Index(bc.default_root(env))
    blocks = bc.Blocks(idx)
    hits = [(g, e) for g, e in blocks.bindings() if block.lower() in idx.rel(e.path).lower()]
    exact = [(g, e) for g, e in hits if e.path.stem.lower() == block.lower()]
    hits = exact or hits
    if len(hits) != 1:
        raise DefsError(f"{block!r} matches {len(hits)} blocks"
                        + (": " + ", ".join(idx.rel(e.path) for _, e in hits[:12]) if hits else "")
                        + " (be more specific).")
    bguid, bentry = hits[0]
    info = blocks.describe(bguid)
    src_dir = bentry.path.parent
    src_rel = idx.rel(src_dir)
    prefix = bentry.path.stem
    kind_path = info.get("kind_path")
    print(f"block      {info['binding_path']}  ({info.get('ui_name')}, archetype: {info['archetype']})")
    print(f"source     {src_rel}  (files named *{prefix}* become *{to}*)")
    print(f"block kind {kind_path or '(none)'}")
    shared = sorted({c["def_path"] for c in info["server_components"] + info["client_components"]
                     if c["def_path"] and not c["def_path"].startswith(src_rel + "/")})
    if shared:
        print("shared definitions (NOT cloned; still vanilla):")
        for s in shared:
            print(f"   {s}")
    siblings = [blocks.describe(g) for g, e in blocks.bindings()
                if g != bguid and e.path.parent.is_relative_to(src_dir)]
    for s in siblings:
        if s.get("kind_guid") and s.get("kind_guid") != info.get("kind_guid"):
            print(f"! {s['binding_path']} is cloned too and keeps the vanilla kind {s['kind_path']}: "
                  "it will show up as an extra size of that vanilla tile. Copy that kind as well (or drop "
                  "its BlockGroups) if that matters.")
    if dry_run:
        return
    if mod is None:
        raise DefsError("Name the mod to clone into.")
    mod = Path(mod)
    dest = to
    # The flat .clonemap.json maps each vanilla GUID to ONE new GUID per mod, so cloning the same vanilla folder
    # twice into one mod would hand both clones the same GUIDs.
    cmap = mod / ".clonemap.json"
    if cmap.exists():
        already = set(load(cmap))
        mine = {_own_guid(load(p)) for p in src_dir.rglob("*") if p.suffix in (".def", ".partialdef")}
        if already & {g.lower() for g in mine if g}:
            raise DefsError(f"{src_rel} was already cloned into {mod.name} (see .clonemap.json); a second clone of "
                            "the same source would reuse its GUIDs. Use a separate mod, or edit the first clone.")
    copy_defs(mod, idx.root, argparse.Namespace(src=src_rel, from_=prefix, to=to, dest=dest, recursive=True,
                                                force=False))
    guid_map = load(cmap)

    new_kind = None
    if kind_path:
        kind_dir = (idx.root / kind_path).parent
        others = [p for p in kind_dir.glob("*") if p.suffix in (".def", ".partialdef")
                  and p.name != Path(kind_path).name]
        if others:
            raise DefsError(f"{idx.rel(kind_dir)} holds more than the kind ({', '.join(p.name for p in others)}); "
                            "copy the kind by hand.")
        kind_name = Path(kind_path).stem.replace("BlockKindDefinition", "")
        copy_defs(mod, idx.root, argparse.Namespace(src=idx.rel(kind_dir), from_=kind_name or "#none#", to=to,
                                                    dest=f"{dest}/UI", recursive=False, force=False))
        guid_map = load(cmap)
        new_kind = guid_map.get(info["kind_guid"])
        kf = find_defining_file(mod / "Assets" / dest / "UI", new_kind)
        if name and kf:
            d = load(kf)
            body(d)["Name"] = name
            save(kf, d)

    new_def = guid_map.get(info.get("block_def", ""))
    df = find_defining_file(mod / "Assets" / dest, new_def) if new_def else None
    if df:
        d = load(df)
        b = body(d)
        if new_kind:
            b["BlockKind"] = new_kind
        if name:
            b.setdefault("UIData", {})["Name"] = name
        save(df, d)
        print(f"edited     {df.relative_to(mod).as_posix()}: BlockKind={new_kind}"
              + (f", Name={name!r}" if name else ""))
    else:
        print("! block definition not found among the cloned files; set BlockKind by hand")

    new_server = guid_map.get(info["server_prefab"])
    if not no_unlock and new_server:
        ov = mod / "Assets" / "Override of ProgressionInitialUnlockConfiguration.partialdef"
        if ov.exists():
            d = load(ov)
        else:  # the same file `se2mod override MOD c7b8e669-...` writes
            d = envelope(_DIFF, {"BaseDefinition": INITIAL_UNLOCK, "PartialDefinitionKind": "Override",
                                 "Manipulator": {}, "DefinitionType": _UNLOCK_TYPE, "PriorityOverride": False},
                         bundle_stamps(env)[1])
        man = d["$Value"].setdefault("Manipulator", {})
        for field, g in (("Blocks", new_server), ("BlockKinds", new_kind)):
            if not g:
                continue
            lst = man.setdefault(field, {"$DeltaEncoded": True, "Removed": [], "Added": []})
            if g not in lst["Added"]:
                lst["Added"].append(g)
        save(ov, d)
        print(f"unlock     server prefab {new_server}, kind {new_kind} -> {ov.relative_to(mod).as_posix()}")
    print(f"done. next: se2mod build {mod.name}")
