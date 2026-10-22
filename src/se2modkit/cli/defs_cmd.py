"""`se2mod defs ...`, `se2mod chain ...` and `se2mod clone-block ...`: read and clone vanilla definitions."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..defs import block_chain as bc
from ..defs import clone_block as cb
from ..defs import query, se2defs
from . import register
from .mods_cmd import _resolve


def _defs_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", type=Path, default=None,
                   help="content root to scan (default: the game's GameData/Vanilla/Content, or $SE2_CONTENT)")
    sub = p.add_subparsers(dest="defs_cmd", metavar="SUBCOMMAND", required=True)
    s = sub.add_parser("types", help="count definition $Types (optionally filtered by substring)")
    s.add_argument("filter", nargs="?")
    s = sub.add_parser("type", help="list files whose root $Type contains SUBSTR")
    s.add_argument("substr")
    s = sub.add_parser("guid", help="which file defines GUID, and which files reference it")
    s.add_argument("guid")
    s = sub.add_parser("show", help="print a definition (file path or GUID), bounded; --path selects a sub-tree")
    s.add_argument("guid", metavar="PATH_OR_GUID")
    s.add_argument("--path", help="sub-tree to print: dotted keys and list indexes, e.g. Components.0.Name")
    s.add_argument("--max", type=int, default=query.DEFAULT_MAX, metavar="N",
                   help=f"print at most N lines (default {query.DEFAULT_MAX})")
    s = sub.add_parser("grep", help="list files whose text matches REGEX (case-insensitive)")
    s.add_argument("regex")
    sub.add_parser("mods", help="summarize content mods in the Steam Workshop folder")


@register("defs", "Explore definition (.def) files: types, type, guid, show, grep, mods", add_args=_defs_args,
          check_game_version=False)
def cmd_defs(args, env) -> int:
    if args.defs_cmd == "mods":
        se2defs.cmd_mods(se2defs.workshop_dir(env))
        return 0
    if args.defs_cmd == "show":  # a file path needs no content root
        query.cmd_show(lambda: args.root or se2defs.default_root(env), args.guid, args.path, args.max)
        return 0
    root = args.root or se2defs.default_root(env)
    if args.defs_cmd == "types":
        se2defs.cmd_types(root, args.filter)
    elif args.defs_cmd == "type":
        se2defs.cmd_type(root, args.substr)
    elif args.defs_cmd == "guid":
        se2defs.cmd_guid(root, args.guid)
    else:
        se2defs.cmd_grep(root, args.regex)
    return 0


def _chain_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", type=Path, default=None, help="SDK Assets root (default: the Mod SDK's Vanilla/Assets)")
    sub = p.add_subparsers(dest="chain_cmd", metavar="SUBCOMMAND", required=True)
    s = sub.add_parser("show", help="definition chain of one block (GUID or binding path fragment)")
    s.add_argument("target")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("resolve", help="one definition fully merged (all Copy bases applied)")
    s.add_argument("guid")
    s.add_argument("--fields", metavar="LIST",
                   help=f"print only these facts, one per line ({','.join(query.FIELDS)}) instead of the JSON")
    s = sub.add_parser("lineage", help="the Copy chain (template bases) of one definition")
    s.add_argument("guid")
    s = sub.add_parser("find", help="PrefabBindings whose path matches REGEX (case-insensitive)")
    s.add_argument("regex")
    s = sub.add_parser("inventory", help="one row per vanilla block (CSV to stdout or --csv, and/or --json)")
    s.add_argument("--csv")
    s.add_argument("--json")


@register("chain", "Resolve vanilla block definition chains from the Mod SDK sources: show, resolve, lineage, "
          "find, inventory", add_args=_chain_args, check_game_version=False)
def cmd_chain(args, env) -> int:
    idx = bc.Index(args.root or bc.default_root(env))
    if args.chain_cmd == "resolve" and args.fields:
        fields = query.parse_fields(args.fields)
        value = idx.resolve(args.guid)
        if value is None:
            raise bc.DefsError(f"{args.guid} is not found in {idx.root}.")
        entry = idx.by_guid[args.guid.lower()]
        for f in fields:
            print(f"{f}: {query.FIELDS[f](value, entry, idx)}")
        return 0
    fn = {"show": bc.cmd_show, "resolve": bc.cmd_resolve, "lineage": bc.cmd_lineage, "find": bc.cmd_find,
          "inventory": bc.cmd_inventory}[args.chain_cmd]
    fn(args, idx)
    return 0


def _clone_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("mod", help="mod name (project here or a workspace_mods folder) or path to a mod directory")
    p.add_argument("block", help="binding name or path fragment as `se2mod chain find` shows it, e.g. Refinery750")
    p.add_argument("--to", required=True, help="new name; replaces the block's prefix in file names")
    p.add_argument("--name", help="UIData.Name / kind Name (a loc key or plain text)")
    p.add_argument("--no-unlock", action="store_true", help="do not add it to the initial-unlock progression")
    p.add_argument("--dry-run", action="store_true", help="only print what would be cloned")
    p.add_argument("--mods-dir", type=Path, help="look the mod up in this mods folder")
    p.add_argument("--root", type=Path, default=None, help="SDK Assets root (default: the Mod SDK's Vanilla/Assets)")


@register("clone-block", "Clone a vanilla block into a mod as a new block (own GUIDs, menu tile and unlock)",
          add_args=_clone_args)
def cmd_clone_block(args, env) -> int:
    mod = None
    if not args.dry_run:
        mod = _resolve(env, str(args.mods_dir / args.mod) if args.mods_dir else args.mod)
    idx = bc.Index(args.root or bc.default_root(env))
    cb.clone_block(env, mod, args.block, to=args.to, name=args.name, no_unlock=args.no_unlock,
                   dry_run=args.dry_run, idx=idx)
    return 0
