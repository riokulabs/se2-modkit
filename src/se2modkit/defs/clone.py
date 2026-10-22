"""Clone the vanilla definition chain of each of a mod's blocks into `Assets/<Mod>/<block>/`.

A block spec needs `name` (the new block), `vanilla_source` (its SDK folder, e.g. "Blocks/Widgets/250") and
`copy_from` (the vanilla file prefix, e.g. "Widget250"). Per block, two copies (`se2mod copy`, in-process) share one
GUID map, kept under the block's name in the mod's keyed `.clonemap.json`, so the same vanilla folder can be cloned
once per block with its own GUIDs:
  1. the vanilla BlockKind (the build-menu tile) -> <name>/UI/<name>_BlockKindDefinition.def, so the clone gets its
     own tile instead of becoming an extra size of the vanilla one;
  2. the block's SDK folder, recursively, into <name>/. The kind was mapped first, so the block definition's
     BlockKind reference is rewired by the copy itself. Files with a second prefix in the same folder are renamed in
     the SAME copy (`hooks.clone_also`), so definitions that reference each other are rewired together; files a clone
     must not keep are deleted after it (`hooks.clone_drop`).
Every cloned file gets a .meta whose AssetID is its own definition GUID; every folder gets a .meta with a stable
AssetID from the mod's asset GUID store. The compositions are cloned untouched (behaviour stays vanilla until the
staging step patches them). The block's key GUIDs and files are recorded in .clonemap.json as maps[<name>].info.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from . import block_chain as bc
from .assetio import own_guid, read_asset, write_meta
from .block_chain import DefsError
from .guids import Guids

if TYPE_CHECKING:
    from ..env.core import SE2Env
    from ..pipeline.hooks import ModHooks

__all__ = ["BlockSpec", "CloneError", "clone", "clone_all", "dest_rel", "drop_files", "ensure_kind", "entry",
           "is_cloned", "load_map", "write_metas"]


class CloneError(DefsError):
    """The clone step cannot go on; one plain sentence."""


class BlockSpec(Protocol):
    name: str
    vanilla_source: str
    copy_from: str


def _rel(p: Path, root: Path) -> str:
    return Path(p).relative_to(root).as_posix()


def dest_rel(hooks: ModHooks, spec: BlockSpec) -> str:
    return f"{hooks.name}/{spec.name}"


def drop_files(hooks: ModHooks, spec: BlockSpec) -> list[str]:
    """Delete the block's `hooks.clone_drop` files (and their .meta) from its clone; returns the mod-relative paths."""
    assets = hooks.paths.assets
    suffixes = tuple(hooks.clone_drop.get(spec.copy_from, ()))
    out = []
    for dp, _, fs in os.walk(assets / dest_rel(hooks, spec)):
        for f in fs:
            if suffixes and f.endswith(suffixes):
                p = Path(dp) / f
                p.unlink()
                meta = Path(f"{p}.meta")
                if meta.exists():
                    meta.unlink()
                out.append(_rel(p, assets))
    return out


def load_map(hooks: ModHooks) -> dict:
    p = hooks.paths.clone_map
    return read_asset(p) if p.exists() else {}


def entry(hooks: ModHooks, spec: BlockSpec, cmap: dict | None = None) -> dict | None:
    cmap = load_map(hooks) if cmap is None else cmap
    return (cmap.get("maps") or {}).get(spec.name)


def is_cloned(hooks: ModHooks, spec: BlockSpec, cmap: dict | None = None) -> bool:
    e = entry(hooks, spec, cmap)
    return bool(e and e.get("info"))


def _block_info(spec: BlockSpec, idx: bc.Index) -> dict:
    blocks = bc.Blocks(idx)
    want = f"{spec.vanilla_source}/{spec.copy_from}.partialdef"
    hits = [(g, e) for g, e in blocks.bindings() if idx.rel(e.path) == want]
    if len(hits) != 1:
        raise CloneError(f"{spec.name}: binding {want} not found in the SDK ({len(hits)} hits).")
    return blocks.describe(hits[0][0])


def _copy(hooks: ModHooks, idx: bc.Index, src: str, from_: str, to: str, dest: str, key: str, recursive: bool,
          also: Iterable[str] = ()) -> None:
    """`se2mod copy` into this mod, in-process (one copy implementation: `cli.mods_cmd.copy_defs`)."""
    from ..cli.mods_cmd import copy_defs
    also = list(also)
    cmd = ["copy", hooks.name, src, "--from", from_, "--to", to, "--dest", dest, "--map-key", key]
    cmd += ["-r"] if recursive else []
    for a in also:
        cmd += ["--also", a]
    print(f"$ se2mod {' '.join(cmd)}", flush=True)
    copy_defs(hooks.paths.root, idx.root, argparse.Namespace(
        src=src, from_=from_, to=to, dest=dest, map_key=key, recursive=recursive, also=also or None, force=False))


def _find_def(root: Path, guid: str | None) -> Path | None:
    for dp, _, fs in os.walk(root):
        for f in sorted(fs):
            if f.endswith((".def", ".partialdef")):
                p = Path(dp) / f
                if own_guid(read_asset(p)) == guid:
                    return p
    return None


def write_metas(hooks: ModHooks, root: Path, G: Guids) -> int:
    """.meta for every cloned definition (AssetID = own GUID) and every folder (stable AssetID), never overwriting."""
    assets = hooks.paths.assets
    stamps = hooks.stamps()
    root = Path(root)
    n = 0
    folders = [root] + [Path(dp) / d for dp, ds, _ in os.walk(root) for d in ds]
    if root.parent != assets:   # Assets/<Mod> itself
        folders.append(root.parent)
    for d in folders:
        if not Path(f"{d}.meta").exists():
            write_meta(d, G["dir/" + _rel(d, assets)], stamps)
            n += 1
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = Path(dp) / f
            if f.endswith((".def", ".partialdef")) and not Path(f"{p}.meta").exists():
                g = own_guid(read_asset(p))
                if not g:
                    raise CloneError(f"{p}: no own GUID (an Override?).")
                write_meta(p, g, stamps)
                n += 1
    return n


def clone(hooks: ModHooks, spec: BlockSpec, G: Guids, idx: bc.Index) -> dict:
    """Clone one block (both copies, the drops, the metas); returns its clone-map info."""
    info = _block_info(spec, idx)
    kind_path = info.get("kind_path")
    if not kind_path:
        raise CloneError(f"{spec.name}: the vanilla block has no BlockKind.")
    kind_dir = os.path.dirname(kind_path)
    others = sorted(p.name for p in (idx.root / kind_dir).iterdir()
                    if p.suffix in (".def", ".partialdef") and p.name != os.path.basename(kind_path))
    if others:
        raise CloneError(f"{kind_dir} holds more than the kind ({others}); copy the kind by hand.")
    kind_stem = Path(kind_path).stem
    dest = dest_rel(hooks, spec)
    done = {c["src"] for c in (entry(hooks, spec) or {}).get("copies", [])}
    if kind_dir not in done:   # 1. the kind first, so step 2 rewires the block definition's BlockKind reference
        _copy(hooks, idx, kind_dir, kind_stem, f"{spec.name}_BlockKindDefinition", f"{dest}/UI", spec.name, False)
    if spec.vanilla_source not in done:
        also_fn = hooks.clone_also.get(spec.copy_from)
        also = also_fn(spec.name) if also_fn else []
        _copy(hooks, idx, spec.vanilla_source, spec.copy_from, spec.name, dest, spec.name, True, also)
        for rel in drop_files(hooks, spec):
            print(f"dropped {rel}")
    cmap = load_map(hooks)
    e = cmap["maps"][spec.name]
    m = e["guids"]
    assets = hooks.paths.assets
    root = assets / dest
    rel = lambda p: _rel(p, assets) if p else None   # noqa: E731
    new = {k: m.get(info.get(k) or "") for k in ("block_def", "server_prefab", "client_prefab", "kind_guid")}
    bmd = [f for f in os.listdir(root) if f.endswith("_BlockModelDefinition.partialdef")]
    if len(bmd) != 1:
        raise CloneError(f"{spec.name}: expected one *_BlockModelDefinition.partialdef, found {bmd}.")
    bmd_path = root / bmd[0]
    e["info"] = {
        "binding": info["binding_path"], "archetype": info.get("archetype"),
        "block_def": new["block_def"], "block_def_file": rel(_find_def(root, new["block_def"])),
        "kind": new["kind_guid"], "kind_file": rel(_find_def(root / "UI", new["kind_guid"])),
        "server_prefab": new["server_prefab"], "client_prefab": new["client_prefab"],
        "model_def": own_guid(read_asset(bmd_path)), "model_def_file": rel(bmd_path),
        "vanilla": {k: info.get(k) for k in ("block_def", "server_prefab", "client_prefab", "kind_guid")},
    }
    missing = [k for k, v in e["info"].items() if v is None]
    if missing:
        raise CloneError(f"{spec.name}: could not resolve {missing}.")
    hooks.paths.clone_map.write_text(json.dumps(cmap, indent=2) + "\n", encoding="utf-8", newline="\n")
    n = write_metas(hooks, root, G)
    print(f"cloned {spec.name}: {sum(c['definitions'] for c in e['copies'])} definitions, {n} metas")
    return e["info"]


def ensure_kind(hooks: ModHooks, info: dict) -> None:
    """Point the cloned block definition at the cloned kind. The copy rewires an explicit BlockKind; a definition that
    inherits its kind from its Copy base gets it written here, otherwise the clone would show up as an extra size of
    the vanilla tile."""
    p = hooks.paths.assets / info["block_def_file"]
    d = read_asset(p)
    m = d["$Value"]["Manipulator"]
    if m.get("BlockKind") != info["kind"]:
        m["BlockKind"] = info["kind"]
        p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"  {info['block_def_file']}: BlockKind = {info['kind']}")


def clone_all(hooks: ModHooks, specs: Iterable[BlockSpec], idx: bc.Index | None = None,
              env: SE2Env | None = None) -> dict:
    """Clone every spec not cloned yet (the clone map says which), then point each at its kind. `idx` defaults to the
    Mod SDK's vanilla Assets found from `env`; the folder .meta files carry `hooks.stamps(env)`."""
    hooks.check()
    hooks.stamps(env)
    if hooks.paths.asset_guids is None:
        raise CloneError(f"{hooks.name} has no asset GUID store path (ModPaths.asset_guids); build its paths from "
                         "the project.")
    if idx is None:
        if env is None:
            raise CloneError("Pass the environment (or a block_chain.Index) to find the Mod SDK's vanilla Assets.")
        idx = bc.Index(bc.default_root(env))
    G = Guids(hooks.paths.asset_guids)
    out = {}
    for spec in specs:
        if is_cloned(hooks, spec):
            print(f"skip {spec.name}: already cloned (.clonemap.json)")
            out[spec.name] = entry(hooks, spec)["info"]
        else:
            out[spec.name] = clone(hooks, spec, G, idx)
            G.save()
        ensure_kind(hooks, out[spec.name])
    G.save()
    return out
