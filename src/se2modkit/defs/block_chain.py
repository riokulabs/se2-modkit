"""block_chain: resolve a vanilla SE2 block's full definition chain from the Mod SDK sources.

Compiled vanilla `.def` files under GameData/Vanilla/Content are stored as *deltas* against a template, but the
compiled file no longer says which template. The Mod SDK's source assets (`<Mod SDK>/GameData/Vanilla/Assets`) still
have the `Copy` partialdefs with their `BaseDefinition`, so this module rebuilds the effective (merged) definitions
from those. `se2mod chain show|resolve|lineage|find|inventory` is its command line (`cli/defs_cmd.py`).

  show GUID|NAME     definition chain of one block: binding -> server/client prefab -> composite -> components
                     (C# type + definition) + menu + unlock
  resolve GUID       one definition fully merged (all Copy bases applied)
  lineage GUID       the Copy chain (template bases) of one definition
  inventory          one row per vanilla block (Blocks/ and Armors/ PrefabBindings), CSV and/or JSON
  find REGEX         PrefabBindings whose path matches REGEX (case-insensitive)

NAME is matched against the binding's file path, e.g. `Welder400` or `Blocks/Batteries/250`. Read-only; standard
library only.
"""
from __future__ import annotations

import copy
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from ..env.core import SE2Env, require
from ..project import ProjectError

INITIAL_UNLOCK = "c7b8e669-e76b-4edb-aa18-8067d0340b96"
ASSET_ID_RE = re.compile(r'"AssetID":\s*"([0-9a-f-]{36})"')


class DefsError(ProjectError):
    """A definition lookup failed; reported as one plain sentence."""


def default_root(env: SE2Env) -> Path:
    """The Mod SDK's Vanilla Assets folder (where the Copy partialdefs live)."""
    require(env, "sdk_assets")
    return env.sdk_assets


def short(t):
    """'Game2:Keen.Game2.X.FooDefinitionObjectBuilder' -> 'FooDefinition'."""
    if not t:
        return ""
    t = t.split(":", 1)[-1].rsplit(".", 1)[-1]
    return t[:-len("ObjectBuilder")] if t.endswith("ObjectBuilder") else t


class Entry:
    __slots__ = ("guid", "path", "kind", "type", "raw", "base")

    def __init__(self, guid, path, kind, type_, raw, base=None):
        self.guid, self.path, self.kind, self.type, self.raw, self.base = guid, path, kind, type_, raw, base


class Index:
    def __init__(self, root: Path | None = None):
        """Index every definition under `root` (default: the discovered Mod SDK's Vanilla Assets)."""
        if root is None:
            from ..env.core import discover_env
            root = default_root(discover_env())
        root = Path(root)
        self.root = root
        self.by_guid = {}
        self.overrides = defaultdict(list)
        self.assets = {}          # asset id -> path (from .meta files)
        self._resolved = {}
        for p in root.rglob("*"):
            if p.suffix == ".meta":
                try:
                    with p.open(encoding="utf-8-sig", errors="replace") as fh:
                        head = fh.read(600)
                except OSError:
                    continue
                m = ASSET_ID_RE.search(head)
                if m:
                    self.assets[m.group(1)] = p.with_suffix("")
                continue
            if p.suffix not in (".def", ".partialdef"):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            v = d.get("$Value") or {}
            if p.suffix == ".def":
                g = str(v.get("Guid", "")).lower()
                if g:
                    self.by_guid[g] = Entry(g, p, "def", short(d.get("$Type")), v)
            else:
                kind = v.get("PartialDefinitionKind")
                man = v.get("Manipulator") or {}
                base = str(v.get("BaseDefinition", "")).lower()
                if kind == "Copy" and man.get("Guid"):
                    g = str(man["Guid"]).lower()
                    self.by_guid[g] = Entry(g, p, "copy", short(v.get("DefinitionType")), man, base)
                elif kind == "Override":
                    self.overrides[base].append(Entry(base, p, "override", short(v.get("DefinitionType")), man, base))

    def rel(self, p):
        try:
            return str(Path(p).relative_to(self.root))
        except ValueError:
            return str(p)

    def asset(self, ref):
        """'{G}<id>' -> relative path of the asset with that AssetID (or None)."""
        if not isinstance(ref, str):
            return None
        g = ref[3:] if ref.startswith("{G}") else ref
        p = self.assets.get(g.lower())
        return self.rel(p) if p else None

    def lineage(self, guid):
        out, g = [], (guid or "").lower()
        while g and g in self.by_guid:
            e = self.by_guid[g]
            out.append(e)
            g = e.base if e.kind == "copy" else None
        return out

    def resolve(self, guid):
        g = (guid or "").lower()
        if g in self._resolved:
            return self._resolved[g]
        e = self.by_guid.get(g)
        if e is None:
            return None
        if e.kind == "def":
            val = copy.deepcopy(e.raw)
        else:
            base = self.resolve(e.base)
            val = merge(copy.deepcopy(base) if base is not None else None, e.raw)
        for o in self.overrides.get(g, []):
            val = merge(val, {k: v for k, v in o.raw.items() if k != "Guid"})
        self._resolved[g] = val
        return val

    def type_of(self, guid):
        e = self.by_guid.get((guid or "").lower())
        return e.type if e else ""


def merge(base, delta):
    """Apply an SE2 partialdef manipulator value onto a base value."""
    if isinstance(delta, dict) and delta.get("$DeltaEncoded"):
        if "Keys" in delta:
            # Keyed list [{Key, Value}], as VRage DeltaEncodedCollectionsHelper.DeserializeIntoKeyedList:
            # start from the base list; Update merges into an existing key (skipped if the base lacks
            # it); Removed drops keys; then Insert adds new keys next to their neighbours in `Keys`.
            # Base keys missing from `Keys` are KEPT: only `Removed` deletes.
            keys = [str(k).lower() for k in delta["Keys"]]
            out = [dict(Key=it["Key"], Value=copy.deepcopy(it.get("Value")))
                   for it in (base or []) if isinstance(it, dict) and "Key" in it]
            def pos(k):
                return next((i for i, it in enumerate(out) if str(it["Key"]).lower() == k), -1)
            inserts = []
            for c in delta.get("Changed", []):
                i = c["Index"]
                if c.get("Kind") == "Update":
                    j = pos(keys[i])
                    if j >= 0:
                        out[j]["Value"] = merge(out[j]["Value"], c.get("Value"))
                elif c.get("Kind") == "Insert":
                    inserts.append(c)
            for k in delta.get("Removed", []):
                j = pos(str(k).lower())
                if j >= 0:
                    del out[j]
            for c in inserts:
                i, at = c["Index"], len(out)
                if i == 0:
                    at = 0
                elif i != len(keys) - 1:
                    for d in range(1, len(keys)):
                        if i - d >= 0 and pos(keys[i - d]) >= 0:
                            at = pos(keys[i - d]) + 1
                            break
                        if i + d < len(keys) and pos(keys[i + d]) >= 0:
                            at = pos(keys[i + d])
                            break
                out.insert(at, {"Key": delta["Keys"][i], "Value": copy.deepcopy(c.get("Value"))})
            return out
        if "ModifiedKeys" in delta:  # dictionary: [{$Key, $Value}]
            # (a dictionary serialises either as a JSON object or as a [{$Key, $Value}] list)
            as_obj = isinstance(base, dict)
            items = dict(base) if as_obj else {it["$Key"]: it["$Value"] for it in base or []}
            for k in delta.get("RemovedKeys", []):
                items.pop(k, None)
            mods = delta.get("ModifiedKeys") or []
            mods = mods.items() if isinstance(mods, dict) else [(it["$Key"], it["$Value"]) for it in mods]
            for k, v in mods:
                items[k] = merge(items.get(k), v)
            return items if as_obj else [{"$Key": k, "$Value": v} for k, v in items.items()]
        if "Added" in delta:         # plain set-like list
            out = [x for x in (base or []) if x not in delta.get("Removed", [])]
            return out + [x for x in delta.get("Added", []) if x not in out]
        return delta
    if isinstance(delta, dict) and isinstance(base, dict):
        if "$Type" in delta and base.get("$Type") not in (None, delta["$Type"]):
            return copy.deepcopy(delta)
        out = dict(base)
        for k, v in delta.items():
            out[k] = merge(base.get(k), v)
        return out
    return copy.deepcopy(delta)


# ---------------------------------------------------------------------------------------------
# Block-level view

class Blocks:
    def __init__(self, idx):
        self.idx = idx
        self.prefab_of_composite = {}
        self.kinds, self.groups, self.subcats, self.cats = {}, {}, {}, {}
        self.reward_groups = defaultdict(list)
        for g, e in idx.by_guid.items():
            if e.type == "PrefabDefinition":
                comp = ((idx.resolve(g) or {}).get("_entity") or {}).get("Definition")
                if comp:
                    self.prefab_of_composite.setdefault(str(comp).lower(), g)
            elif e.type == "BlockKindDefinition":
                self.kinds[g] = e
            elif e.type == "BlockGroupDefinition":
                self.groups[g] = e
            elif e.type == "SubcategoryDefinition":
                self.subcats[g] = e
            elif e.type == "CategoryDefinition":
                self.cats[g] = e
            elif e.type == "BlockRewardGroupDefinition":
                txt = json.dumps(idx.resolve(g))
                for m in re.finditer(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", txt):
                    self.reward_groups[m.group(0)].append(e.path.stem)
        self.initial = idx.resolve(INITIAL_UNLOCK) or {}
        self.initial_blocks = {b.lower() for b in self.initial.get("Blocks", [])}
        self.initial_kinds = {b.lower() for b in self.initial.get("BlockKinds", [])}
        self.initial_subcats = {b.lower() for b in self.initial.get("Subcategories", [])}
        self.initial_cats = {b.lower() for b in self.initial.get("Categories", [])}
        # group -> subcategory -> category (reverse of the Key/Value lists)
        self.group_parent = {}
        for g in self.groups:
            for it in (idx.resolve(g) or {}).get("Subcategories", []):
                self.group_parent[g] = str(it.get("Value")).lower()
        self.sub_parent = {}
        for g in self.subcats:
            for it in (idx.resolve(g) or {}).get("Categories", []):
                self.sub_parent[g] = str(it.get("Value")).lower()

    def lod_count(self, ref):
        p = self.idx.asset(ref)
        if not p:
            return None
        try:
            m = json.loads((self.idx.root / p).read_text(encoding="utf-8-sig"))
            return len((m.get("$Value") or {}).get("GeometryAssets") or [])
        except (OSError, ValueError):
            return None

    def bindings(self):
        for g, e in self.idx.by_guid.items():
            if e.type == "PrefabBindingDefinition":
                top = self.idx.rel(e.path).split("/", 1)[0]
                if top in ("Blocks", "Armors"):
                    yield g, e

    def template(self, guid):
        """Name of the first Templates/ base in the Copy chain (skipping the entry itself)."""
        for e in self.idx.lineage(guid)[1:]:
            r = self.idx.rel(e.path)
            if r.startswith("Templates/"):
                return r
        return ""

    def components(self, composite_guid):
        comp = self.idx.resolve(composite_guid) or {}
        tags = {}
        for it in comp.get("TagSlots") or []:
            tags.setdefault(str(it["$Value"]).lower(), []).append(it["$Key"])
        out = []
        for it in comp.get("Components") or []:
            v = it.get("Value") or {}
            dg = (v.get("Definition") or "")
            dg = dg.lower() if isinstance(dg, str) else ""
            out.append({
                "key": it.get("Key"),
                "type": short(v.get("Type")),
                "def": dg,
                "def_type": self.idx.type_of(dg),
                "def_path": self.idx.rel(self.idx.by_guid[dg].path) if dg in self.idx.by_guid else "",
                "def_template": self.template(dg) if dg else "",
                "tags": tags.get(str(it.get("Key")).lower(), []),
            })
        return out

    def menu(self, kind_guid):
        k = (kind_guid or "").lower()
        if k not in self.kinds:
            return {"kind": "", "kind_path": "", "groups": [], "menu": ""}
        kv = self.idx.resolve(k) or {}
        groups = [str(it.get("Value")).lower() for it in kv.get("BlockGroups", [])]
        paths = []
        for gg in groups:
            sub = self.group_parent.get(gg)
            cat = self.sub_parent.get(sub)
            names = [(self.idx.resolve(x) or {}).get("Name", "?") for x in (cat, sub, gg) if x]
            paths.append(" > ".join(names))
        return {"kind": kv.get("Name", ""), "kind_guid": k,
                "kind_path": self.idx.rel(self.kinds[k].path),
                "groups": groups, "menu": " | ".join(paths) if paths else "(no BlockGroups: hidden)"}

    def describe(self, bguid):
        idx = self.idx
        b = idx.resolve(bguid) or {}
        e = idx.by_guid[bguid]
        rel = idx.rel(e.path)
        parts = rel.split("/")
        sc = str(b.get("ServerComposite") or "").lower()
        cp = str(b.get("ClientPrefab") or "").lower()
        cc = str(((idx.resolve(cp) or {}).get("_entity") or {}).get("Definition") or "").lower()
        sp = self.prefab_of_composite.get(sc, "")
        server = self.components(sc)
        client = self.components(cc) if cc else []
        blockdef, model = None, None
        for c in server:
            d = idx.resolve(c["def"]) if c["def"] else None
            if isinstance(d, dict) and "BlockKind" in d and "UIData" in d and blockdef is None:
                blockdef = (c, d)
            if c["type"] == "BlockModelComponent" and model is None and d:
                model = (c, d)
        info = {
            "family": parts[1] if len(parts) > 1 else "",
            "variant": "/".join(parts[2:-1]),
            "name": e.path.stem,
            "binding_path": rel,
            "binding": bguid,
            "binding_template": self.template(bguid),
            "server_prefab": sp,
            "server_composite": sc,
            "client_prefab": cp,
            "client_composite": cc,
            "server_components": server,
            "client_components": client,
        }
        if blockdef:
            c, d = blockdef
            info.update({
                "block_def": c["def"], "block_def_type": c["def_type"],
                "block_def_template": c["def_template"],
                "ui_name": (d.get("UIData") or {}).get("Name", ""),
                "pcu": d.get("PCU"),
                "consumes": ((d.get("ConsumedResource") or {}).get("Amount")),
                "recipe": d.get("Recipe"),
            })
            info.update(self.menu(d.get("BlockKind")))
        if model:
            c, d = model
            dummies, dtypes = [], []
            for it in d.get("Dummies") or []:
                v = it.get("Value") or {}
                dummies.append(v.get("Name") or "")
                te = idx.by_guid.get(str(v.get("Type") or "").lower())
                dtypes.append(te.path.stem if te else "")
            info.update({
                "model": idx.asset(d.get("Model")) or d.get("Model"),
                "model_lods": self.lod_count(d.get("Model")),
                "fracture_model": idx.asset(d.get("FractureModel")) or d.get("FractureModel"),
                "damaged_model": idx.asset(d.get("DamagedModel")) or d.get("DamagedModel"),
                "build_stages": len(d.get("BuildProgressModels") or []),
                "dummies": dummies,
                "dummy_types": dtypes,
                "fractures": len(set(re.findall(r"Fracture_?(\d+)", json.dumps(d)))),
            })
        folder = e.path.parent
        info["model_files"] = len(list(folder.rglob("*.model")))
        info["subparts"] = []
        for c in client:
            if c["type"] == "ConditionalSubPartsComponent" and c["def"]:
                # SubParts: {dummy type GUID -> subpart prefab GUID}; the prefab spawns at each
                # block-model dummy of that type.
                sp_map = (idx.resolve(c["def"]) or {}).get("SubParts") or {}
                if isinstance(sp_map, list):   # a few use the [{Key, Value}] form
                    sp_map = {it.get("Key", it.get("$Key")): it.get("Value", it.get("$Value"))
                              for it in sp_map if isinstance(it, dict)}
                for dt, pf in sp_map.items():
                    pe = idx.by_guid.get(str(pf).lower())
                    info["subparts"].append(pe.path.stem if pe else str(pf))
        # The game unlocks through Blocks, BlockKinds, Subcategories and Categories, in that order.
        how = ""
        if sp in self.initial_blocks:
            how = "block"
        elif info.get("kind_guid") in self.initial_kinds:
            how = "kind"
        else:
            for gg in info.get("groups", []):
                sub = self.group_parent.get(gg)
                if sub in self.initial_subcats:
                    how = "subcategory " + (self.idx.resolve(sub) or {}).get("Name", sub)
                elif self.sub_parent.get(sub) in self.initial_cats:
                    how = "category"
        info["initial_unlock"] = how
        info["reward_groups"] = sorted(set(self.reward_groups.get(sp, [])))
        info["archetype"] = archetype(info)
        if "model" not in info:   # armour: model is a composite armour asset on the ArmorBlockDefinition
            for c in server:
                if c["type"] == "ArmorBlockComponent" and c["def"]:
                    info["model"] = idx.asset((idx.resolve(c["def"]) or {}).get("Model"))
        return info


ARCHETYPE_RULES = [  # (archetype, any-of component types); first match wins. Server+client types.
    ("armor", {"ArmorBlockComponent"}),
    ("wheel-suspension", {"WheelCornerModuleComponent"}),
    ("wheel (prefab, not a block)", {"WheelColliderComponent"}),
    ("door", {"DoorServerComponent"}),
    ("door-subpart (prefab)", {"DoorSegmentComponent"}),
    ("mechanical-base", {"HingeComponent", "PistonComponent"}),
    ("mechanical-top", {"MechanicalTopComponent"}),
    ("connector", {"ConnectorComponent"}),
    ("landing-gear", {"MagneticBlockComponent"}),
    ("thruster", {"ThrusterComponent"}),
    ("turret", {"AutoTargetingComponent"}),
    ("fixed-weapon", {"ProjectileWeaponComponent"}),
    ("warhead", {"TimerExplosiveBlockComponent"}),
    ("tool-integrity (welder/grinder)", {"IntegrityToolComponent"}),
    ("tool-drill", {"DrillComponent"}),
    ("production (item converter)", {"ItemConverterComponent"}),
    ("power/gas generator (item to resource)", {"ItemToResourceConverterComponent"}),
    ("power generator (renewable)", {"SolarGeneratorComponent", "WindTurbineComponent"}),
    ("battery (resource container + cell slots)", {"ResourceContainerInventoryComponent"}),
    ("gas tank (resource container)", {"ResourceContainerComponent"}),
    ("gravity generator", {"GravityGeneratorComponent"}),
    ("gyroscope", {"GyroscopeComponent"}),
    ("cockpit/control seat", {"SeatComponent"}),
    ("lcd", {"LcdMultiPanelComponent"}),
    ("light", {"PointLightComponent", "SpotLightComponent"}),
    ("antenna", {"AntennaComponent"}),
    ("ore detector", {"OreDetectorComponent"}),
    ("automation", {"TimerBlockServerComponent", "SensorComponent", "EventControllerServerComponent",
                    "BlockToolbarServerComponent"}),
    ("sound block", {"SoundBlockServerComponent"}),
    ("respawn/medical", {"RespawnPointComponent"}),
    ("creative terminal (contracts/trade)", {"ContractBlockComponent", "TraderComponent"}),
    ("cargo/storage", {"InventoryComponent"}),
    ("conveyor", {"ConveyorComponent"}),
]


def archetype(info):
    types = {c["type"] for c in info["server_components"] + info["client_components"]}
    for name, need in ARCHETYPE_RULES:
        if types & need:
            return name
    if "BlockModelComponent" in types and "BreakableBlockComponent" not in types and "CubeBlockComponent" in types:
        return "structural (non-breakable: windows)"
    if info.get("block_def_type") == "CubeBlockDefinition":
        return "structural/decor (breakable)"
    if info.get("block_def_type") == "PowerableBlockDefinition":
        return "decor (powerable shell: bed/seat/toilet)"
    if info.get("block_def_type") == "FunctionalBlockDefinition":
        return "functional shell (control panel)"
    return "?"


def comp_label(c):
    s = c["type"] or "?"
    if c["def"]:
        s += f" <- {c['def_type'] or '?'}"
    return s


def cmd_show(args, idx):
    bl = Blocks(idx)
    target = args.target.lower()
    hits = []
    if target in idx.by_guid:
        e = idx.by_guid[target]
        if e.type == "PrefabBindingDefinition":
            hits = [target]
        else:   # a prefab/composite/blockdef: find the binding that uses it
            for g, _ in bl.bindings():
                if target in json.dumps(bl.describe(g)):
                    hits.append(g)
    else:
        hits = [g for g, e in bl.bindings() if target in idx.rel(e.path).lower()]
    if not hits:
        raise DefsError(f"No block binding matches {args.target!r} under {idx.root}.")
    for g in hits[: args.limit]:
        i = bl.describe(g)
        print(f"== {i['binding_path']}   ({i.get('ui_name','')})   archetype: {i['archetype']}")
        print(f"   binding         {i['binding']}   template: {i['binding_template']}")
        print(f"   server prefab   {i['server_prefab']}   composite {i['server_composite']}")
        print(f"   client prefab   {i['client_prefab']}   composite {i['client_composite']}")
        print(f"   block def       {i.get('block_def','')}  [{i.get('block_def_type','')}]  template: {i.get('block_def_template','')}")
        print(f"   menu            {i.get('menu','')}   kind: {i.get('kind','')} ({i.get('kind_path','')})")
        print(f"   unlock          initial={i['initial_unlock'] or '-'}  rewards={', '.join(i['reward_groups']) or '-'}")
        print(f"   PCU {i.get('pcu')}  power {i.get('consumes')}  recipe {i.get('recipe')}")
        print(f"   model           {i.get('model','')}  build stages {i.get('build_stages','')}")
        if i.get("dummies"):
            print(f"   dummies ({len(i['dummies'])})   {', '.join(i['dummies'])}")
        for side in ("server", "client"):
            print(f"   -- {side} components")
            for c in i[f"{side}_components"]:
                tags = f"  tags={','.join(c['tags'])}" if c["tags"] and args.verbose else ""
                where = f"  [{c['def_path']}]" if args.verbose and c["def_path"] else ""
                print(f"      {comp_label(c)}{where}{tags}")
        print()


def cmd_resolve(args, idx):
    v = idx.resolve(args.guid)
    if v is None:
        raise DefsError(f"{args.guid} is not found in {idx.root}.")
    e = idx.by_guid[args.guid.lower()]
    print(f"# {idx.rel(e.path)}  [{e.type}]")
    print(json.dumps(v, indent=2))


def cmd_lineage(args, idx):
    for e in idx.lineage(args.guid):
        print(f"{e.guid}  {e.kind:5} {e.type:40} {idx.rel(e.path)}")
    for o in idx.overrides.get(args.guid.lower(), []):
        print(f"{'':36}  override {idx.rel(o.path)}")


def cmd_find(args, idx):
    rx = re.compile(args.regex, re.I)
    bl = Blocks(idx)
    for g, e in sorted(bl.bindings(), key=lambda x: str(x[1].path)):
        if rx.search(idx.rel(e.path)):
            print(f"{g}  {idx.rel(e.path)}")


def cmd_inventory(args, idx):
    bl = Blocks(idx)
    rows = [bl.describe(g) for g, _ in bl.bindings()]
    rows.sort(key=lambda r: r["binding_path"])
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8", newline="\n")
    cols = ["family", "variant", "name", "ui_name", "archetype", "binding_template", "block_def_template", "block_def_type",
            "kind", "menu", "initial_unlock", "reward_groups", "pcu", "model_lods", "build_stages", "fractures",
            "subparts", "dummy_types", "server_types", "client_types", "binding", "server_prefab",
            "server_composite", "client_prefab", "block_def", "kind_guid", "binding_path", "model"]
    out = open(args.csv, "w", newline="", encoding="utf-8") if args.csv else sys.stdout
    w = csv.writer(out)
    w.writerow(cols)
    for r in rows:
        r = dict(r)
        r["server_types"] = ";".join(c["type"] for c in r["server_components"])
        r["client_types"] = ";".join(c["type"] for c in r["client_components"])
        r["reward_groups"] = ";".join(r["reward_groups"])
        r["menu"] = (r.get("menu") or "").replace("CategoryName", "").replace("SubcategoryName", "")
        r["subparts"] = ";".join(r.get("subparts") or [])
        r["dummy_types"] = ";".join(sorted(set(t for t in r.get("dummy_types") or [] if t)))
        w.writerow([r.get(c, "") for c in cols])
    if args.csv:
        out.close()
        print(f"{len(rows)} blocks -> {args.csv}", file=sys.stderr)
