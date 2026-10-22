"""Offline emulation of SE2's client/server composition walk and the validator that fails world load on it.

Build a Universe from the Mod SDK's vanilla Assets plus a mod's Assets roots, optionally derive() it with in-memory
definition files (fixtures), walk() it, then validate() with a TypeIndex over the game's type facts
(`se2modkit.validate.facts`):

    from se2modkit.validate import clientserver as csw
    from se2modkit.validate.facts import TypeIndex
    u = csw.Universe([sdk_assets, mod_assets, staged_assets], cached_roots=[sdk_assets])  # later roots win per path
    w = csw.walk(u)
    errors = csw.validate(w, TypeIndex.from_facts(path))   # [] == nothing this load validator would reject
    print(csw.format_errors(errors, u))                    # each error carries the walk path that reached it
    u2 = u.derive({"$Type": ..., "$Value": {...}})         # a fixture: same GUID replaces, new GUID adds

What it reproduces of the game's world load (game 4.0):

1. Loading composites from bindings:
   * the prefab-by-composition map is built over EVERY PrefabDefinition, so two prefabs on one composition fail the
     load -> error "duplicate-composition";
   * per PrefabBinding: its ServerComposite must be an EntityCompositeDefinition, its ClientPrefab a PrefabDefinition,
     and some prefab must have the ServerComposite as its composition (the "server prefab"); otherwise the load fails
     -> error "binding";
   * the SERVER walk starts at each ServerComposite with its server prefab's object builders and blocks the client
     prefabs; the CLIENT walk starts at each ClientPrefab's composition with its object builders and blocks the server
     prefabs.
2. Walking a composition: a composite is added to its side's set once; for each component, a
   HierarchyComponentObjectBuilder's Children are walked (with the child's own object builders), and the component's
   Definition is followed. Object builders are matched to components by key.
3. Definition dependencies: every member of a definition is followed; a PrefabDefinition value is walked as a
   composition unless its GUID is blocked; any other definition is recursed into.
   OVER-APPROXIMATION (conservative): the walk follows EVERY definition GUID anywhere in the resolved definition body
   (keys and values, nested lists/dicts/structs), a superset of what the game reads. It can only add composites to a
   side, so it errs towards false failures. KNOWN UNDER-APPROXIMATIONS (a false pass is possible only through them):
     * component Configurations (global game configurations reached through component types, not per-definition data);
     * components the game adds at runtime (not data);
     * references computed in a definition's Init rather than serialised as a GUID;
     * GUIDs that resolve to no loaded definition are not followed (they are counted: unresolved(w) and
       unresolved_report(w)).
4. The client-type check that fails world load: a composite in the server set may hold no client type (its assembly
   carries [assembly: ClientAssembly] or its name ends in "Client"); a composite in the client set and NOT in the
   server set (a both-sides composite is judged as server) may hold no [ServerOnly] type (inherited down the class
   chain). Classification comes from the type facts; a component type they do not define is an error
   ("unknown-type"), never a pass.

   Also reported: "missing-definition" (a walked component's Definition is not a loaded definition) and
   "missing-composite" (a prefab or hierarchy child names no loaded composite).

Definitions are resolved the way the SDK content pipeline does: a .def is a full definition; a Copy partialdef is its
BaseDefinition (resolved recursively) with the Manipulator delta applied; Override partialdefs are applied on top of
their target (`defs.block_chain.merge`). A definition whose .meta
carries DefinitionFlagsMetadata "Abstract" (vanilla Templates/) is a base only: never pushed, so the walk neither
starts from nor reaches it.

Component types: a composite entry's "Type" (bundle prefix dropped); a stale name is mapped through the facts'
[NameMigration] renames; "Type": null takes the component whose [Definition] member is exactly the definition's class.

The parsed files of a root listed in `cached_roots` (the SDK's vanilla Assets, ~19,000 files) are cached as a pickle
under `<user cache>/universe/`, keyed by every file's path, mtime and size; SE2MODKIT_NO_CACHE=1 turns it off.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pickle
import re
import sys
from collections import defaultdict

from ..defs.block_chain import merge  # VRage delta-encoded collection merge
from .facts import MissingDecompile, TypeIndex, UnknownType

__all__ = ["BINDING", "COMPOSITE", "PREFAB", "LOAD_ERRORS", "MissingDecompile", "Side", "TypeIndex", "Universe",
           "UnknownType", "Walk", "components", "composition_of", "format_errors", "full_type", "object_builders",
           "short_type", "unresolved", "unresolved_report", "validate", "walk"]

_CACHE_FORMAT = 1
_CACHE_KEEP = 4
with open(os.path.abspath(__file__), "rb") as _fh:
    _LOADER_DIGEST = hashlib.sha256(_fh.read()).hexdigest()   # the loader's own source is part of every cache key


def _cache_dir() -> str:
    from ..env.config import user_cache_dir
    return str(user_cache_dir() / "universe")


GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
PREFAB = "PrefabDefinition"
BINDING = "PrefabBindingDefinition"
COMPOSITE = "EntityCompositeDefinition"
DCS_COMPONENT = "Keen.VRage.DCS.Components.Component"
HIERARCHY_OB = "Keen.VRage.Core.Game.Components.HierarchyComponentObjectBuilder"
LOCAL_ID_FIELD = re.compile(r"^\$?(Key|Id|\w*NodeId)$")


def short_type(t):
    """'VRage:Keen.VRage.DCS.Definitions.PrefabDefinitionObjectBuilder' -> 'PrefabDefinition'."""
    t = (t or "").split(":", 1)[-1].rsplit(".", 1)[-1]
    return t[:-len("ObjectBuilder")] if t.endswith("ObjectBuilder") else t


def full_type(t):
    """'VRage:Keen.VRage.DCS.Definitions.PrefabDefinitionObjectBuilder' -> 'Keen.VRage.DCS.Definitions.PrefabDefinition'
    (the runtime definition class; its object builder is <Class>ObjectBuilder)."""
    t = (t or "").split(":", 1)[-1]
    return t[:-len("ObjectBuilder")] if t.endswith("ObjectBuilder") else t


def _g(x):
    return x.lower() if isinstance(x, str) and GUID_RE.match(x) else None


# ------------------------------------------------------------------------------------------------- definition universe
class Universe:
    """Every definition of the load, resolved. `roots`: Assets directories, lowest priority first; a file at the same
    relative path in a later root replaces the earlier one (the staged package over the mod's Assets). `extra`:
    in-memory definition files ({"$Type", "$Value"} dicts, .def or PartialDefinitionDiff), applied last (fixtures)."""

    def __init__(self, roots=(), extra=(), cached_roots=()):
        cached = {os.path.realpath(str(r)) for r in cached_roots if r}
        files = {}                        # rel -> (d, error, abstract): later roots replace earlier ones per rel
        for root in roots:
            root = str(root)
            files.update(_load_root(root, os.path.realpath(root) in cached))
        self.entries = {}                 # guid -> (kind, type, raw, base, origin)
        self.full_types = {}              # guid -> full definition class name
        self.overrides = defaultdict(list)
        self.abstract = set()             # DefinitionFlags.Abstract: a Copy base only, never pushed or validated
        self.load_errors = []             # (code, None, guid or None, detail): "unparseable", "duplicate-guid"
        for rel, (p, d, err, abstract) in sorted(files.items()):
            if err is not None:
                # The content pipeline would fail the build on it; silently dropping it would hide its definitions
                # from the walk (a reference to them then reads as "not live" and is not followed: a false pass).
                self.load_errors.append(("unparseable", None, None, (err,)))
                continue
            self._add(d, p, abstract, check_duplicate=True)
        for i, d in enumerate(extra):
            self._add(d, f"<extra {i}>")
        self._resolved = {}

    def _add(self, d, origin, abstract=False, check_duplicate=False):
        if not isinstance(d, dict):
            return
        v = d.get("$Value")
        if not isinstance(v, dict):
            return
        kind = v.get("PartialDefinitionKind")

        def dup(g):    # two files defining one GUID: the game throws "already contained" (DefinitionLoader InitInternal)
            if check_duplicate and g in self.entries:
                self.load_errors.append(("duplicate-guid", None, g, (f"{self.entries[g][4]}", f"{origin}")))
        if kind is None:
            g = _g(v.get("Guid"))
            if g:
                dup(g)
                self.entries[g] = ("def", short_type(d.get("$Type")), v, None, origin)
                self.full_types[g] = full_type(d.get("$Type"))
                self._flag(g, abstract)
        elif kind == "Copy":
            g = _g((v.get("Manipulator") or {}).get("Guid"))
            if g:
                dup(g)
                self.entries[g] = ("copy", short_type(v.get("DefinitionType")), v["Manipulator"],
                                   _g(v.get("BaseDefinition")), origin)
                self.full_types[g] = full_type(v.get("DefinitionType"))
                self._flag(g, abstract)
        elif kind == "Override":
            base = _g(v.get("BaseDefinition"))
            if base:
                self.overrides[base].append((v.get("Manipulator") or {}, origin))
                self._resolved = {}

    def _flag(self, g, abstract):
        (self.abstract.add if abstract else self.abstract.discard)(g)
        self._resolved = {}

    def live(self, guid):
        """A definition the game loads (known and not Abstract: an Abstract definition is not loaded, initialized or
        validated)."""
        g = _g(guid)
        return g in self.entries and g not in self.abstract

    def derive(self, *file_dicts, origin="<fixture>"):
        """A copy of this universe with in-memory definition files laid over it (a fixture: a .def replaces the
        definition with its GUID, a Copy partialdef defines one, an Override partialdef patches one). Cheap: the
        loaded files are shared, the resolution cache is not."""
        u = object.__new__(Universe)
        u.entries, u.full_types, u.abstract = dict(self.entries), dict(self.full_types), set(self.abstract)
        u.load_errors = list(self.load_errors)
        u.overrides = defaultdict(list, {k: list(v) for k, v in self.overrides.items()})
        before, n_over = dict(u.entries), {k: len(v) for k, v in u.overrides.items()}
        for d in file_dicts:
            u._add(copy.deepcopy(d), origin)
        changed = {g for g in u.entries if before.get(g) is not u.entries[g]} | \
                  {k for k, v in u.overrides.items() if len(v) != n_over.get(k, 0)}

        def stale(g, seen=()):            # g or a Copy base of it was replaced
            while g and g not in seen:
                if g in changed:
                    return True
                seen += (g,)
                e = u.entries.get(g)
                g = e[3] if e else None
            return False
        u._resolved = {g: v for g, v in self._resolved.items() if not stale(g)}
        return u

    def __contains__(self, guid):
        return _g(guid) in self.entries

    def type_of(self, guid):
        e = self.entries.get(_g(guid))
        if e is None:
            return None
        if e[0] == "copy" and not e[1]:
            return self.type_of(e[3])
        return e[1]

    def full_type_of(self, guid):
        g = _g(guid)
        e = self.entries.get(g)
        if e is None:
            return None
        if e[0] == "copy" and not self.full_types.get(g):
            return self.full_type_of(e[3])
        return self.full_types.get(g)

    def origin(self, guid):
        e = self.entries.get(_g(guid))
        return e[4] if e else None

    def resolve(self, guid, _stack=()):
        g = _g(guid)
        if g in self._resolved:
            return self._resolved[g]
        e = self.entries.get(g)
        if e is None:
            return None
        if g in _stack:
            raise ValueError(f"BaseDefinition cycle through {g}")
        kind, _, raw, base, _ = e
        if kind == "def":
            val = copy.deepcopy(raw)
        else:
            b = self.resolve(base, _stack + (g,)) if base else None
            if base and b is None:
                raise KeyError(f"{g} ({e[4]}): BaseDefinition {base} is not a known definition")
            val = merge(copy.deepcopy(b) if b is not None else None, raw)
            val["Guid"] = raw.get("Guid", g)
        for o, _ in self.overrides.get(g, []):
            val = merge(val, {k: v for k, v in o.items() if k != "Guid"})
        self._resolved[g] = val
        return val

    def guids_of_type(self, t):
        """The live (loaded) definitions of a type."""
        return sorted(g for g in self.entries if g not in self.abstract and self.type_of(g) == t)


def _read_def(p):
    """(path, parsed file, None, abstract) or (path, None, "<path>: <error>", False) for an unreadable one."""
    try:
        with open(p, encoding="utf-8-sig") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as ex:
        return p, None, f"{p}: {ex}", False
    return p, d, None, _abstract(p + ".meta")


def _load_root(root, cache=False):
    """rel -> _read_def(path) for every .def / .partialdef under root. With `cache` (a root nothing here writes, such
    as the SDK's vanilla Assets) the parsed files come from a pickle in the user cache when one exists for exactly
    these inputs: the key hashes the root as given and resolved, every file's relative path with the mtime and size of
    it and of its .meta (present or not), this module's source and the Python version, so any change to what the loader
    reads or how it reads it misses the cache."""
    files = {}
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith((".def", ".partialdef")):
                p = os.path.join(dp, f)
                files[os.path.relpath(p, root)] = p
    if not cache or os.environ.get("SE2MODKIT_NO_CACHE") == "1":
        return {rel: _read_def(p) for rel, p in files.items()}
    h = hashlib.sha256(repr((_CACHE_FORMAT, sys.version_info[:3], root, os.path.realpath(root))).encode())
    h.update(_LOADER_DIGEST.encode())
    for rel in sorted(files):
        for q in (files[rel], files[rel] + ".meta"):
            try:
                st = os.stat(q)
                h.update(f"\0{rel}\0{q[-5:]}\0{st.st_mtime_ns}\0{st.st_size}".encode())
            except OSError:
                h.update(f"\0{rel}\0{q[-5:]}\0-".encode())
    cache_dir = _cache_dir()
    path = os.path.join(cache_dir, f"universe-{h.hexdigest()[:40]}.pickle")
    try:
        with open(path, "rb") as fh:
            return pickle.load(fh)
    except (OSError, EOFError, pickle.UnpicklingError, ValueError, TypeError, AttributeError):
        pass
    out = {rel: _read_def(p) for rel, p in files.items()}
    try:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)            # atomic: a parallel worker never reads a half-written cache
        old = sorted((e for e in os.scandir(cache_dir) if e.name.startswith("universe-") and e.name.endswith(".pickle")),
                     key=lambda e: e.stat().st_mtime_ns, reverse=True)
        for e in old[_CACHE_KEEP:]:      # a new SDK or loader leaves the old entries stale
            os.remove(e.path)
    except OSError:
        pass
    return out


def _abstract(meta_path):
    """The asset's DefinitionFlagsMetadata (its .meta) says Abstract (vanilla templates: "Abstract, SkipValidation")."""
    try:
        with open(meta_path, encoding="utf-8-sig", errors="replace") as fh:
            txt = fh.read()
    except OSError:
        return False
    if "DefinitionFlagsMetadata" not in txt:
        return False
    for it in (json.loads(txt).get("$Value") or {}).get("InternalItems") or []:
        if str(it.get("$Type", "")).endswith("DefinitionFlagsMetadata") and "Abstract" in str(it.get("Flags", "")):
            return True
    return False


def composition_of(prefab):
    return _g(((prefab or {}).get("_entity") or {}).get("Definition"))


def object_builders(entity):
    """{component key: object builder} of an EntityObjectBuilder ({Definition, ObjectBuilders})."""
    obs = (entity or {}).get("ObjectBuilders") or []
    if isinstance(obs, dict):            # serialised as a JSON object
        return {str(k).lower(): v for k, v in obs.items()}
    return {str(it.get("Key")).lower(): it.get("Value") for it in obs if isinstance(it, dict)}


def components(comp):
    """[(key, type, definition guid)] of a resolved EntityCompositeDefinition."""
    out = []
    for it in (comp or {}).get("Components") or []:
        v = it.get("Value") or {}
        out.append((str(it.get("Key")).lower(), v.get("Type"), _g(v.get("Definition"))))
    return out


def _children(ob):
    """The child EntityObjectBuilders of a HierarchyComponentObjectBuilder (a plain or a keyed list)."""
    out = []
    for c in (ob or {}).get("Children") or []:
        if isinstance(c, dict) and "Definition" not in c and isinstance(c.get("Value"), dict):
            c = c["Value"]
        if isinstance(c, dict):
            out.append(c)
    return out


def _guids_in(value, field=None):
    """(field, GUID) for every GUID string in a resolved definition body (keys and values, any depth), in document
    order; `field` is the nearest enclosing non-GUID dict key."""
    if isinstance(value, dict):
        for k, v in value.items():
            if _g(k):
                yield field, _g(k)
            yield from _guids_in(v, field if _g(k) else k)
    elif isinstance(value, list):
        for v in value:
            yield from _guids_in(v, field)
    elif _g(value):
        yield field, _g(value)


# ---------------------------------------------------------------------------------------------------------------- walk
class Side:
    """One side's walk state: the composites and definitions reached on the server or the client."""

    def __init__(self, u, name, blocked):
        self.u, self.name, self.blocked = u, name, blocked
        self.composites = {}               # composite guid -> path (how it was first reached)
        self.definitions = set()
        self.unresolved = defaultdict(set)  # (referring definition type, field) -> GUIDs that are no loaded definition
        self.errors = []
        self._seen = set()                 # (composite, object builders) already walked

    def composition(self, comp_guid, obs, path):              # walk a composition (step 2)
        if comp_guid is None:
            self.errors.append(("missing-composite", self.name, None, path + ("<no Definition>",)))
            return
        # The game returns early once the composite is in the set, so a composite reached a second time with
        # OTHER object builders does not have those builders' hierarchy children walked: which children count depends
        # on the walk order. This walk visits the children of every distinct object-builder set instead (a superset of
        # any order: conservative).
        sig = (comp_guid, json.dumps(obs, sort_keys=True, default=str))
        if sig in self._seen:
            return
        self._seen.add(sig)
        if comp_guid not in self.composites:
            comp = self.u.resolve(comp_guid) if self.u.live(comp_guid) else None
            if comp is None or self.u.type_of(comp_guid) != COMPOSITE:
                self.errors.append(("missing-composite", self.name, comp_guid, path))
                return
            self.composites[comp_guid] = path
        comp = self.u.resolve(comp_guid)
        here = path + (f"composite {comp_guid}",)
        for key, typ, dfn in components(comp):                 # each component: children, then its Definition
            ob = obs.get(key)
            if isinstance(ob, dict) and str(ob.get("$Type", "")).split(":", 1)[-1] == HIERARCHY_OB:
                for i, child in enumerate(_children(ob)):
                    self.composition(_g(child.get("Definition")), object_builders(child),
                                     here + (f"Hierarchy child {child.get('DebugName') or i}",))
            if dfn is not None:
                if not self.u.live(dfn):       # a component definition the load cannot resolve
                    self.errors.append(("missing-definition", self.name, comp_guid,
                                        here + (f"component {key} Definition {dfn} is not a loaded definition",)))
                    continue
                self.fill(dfn, here + (f"{short_type(typ)}.Definition {dfn}",))

    def fill(self, guid, path):                                 # a definition's dependencies (step 3)
        if self.u.type_of(guid) == PREFAB:
            self.prefab(guid, path)
            return
        if guid in self.definitions or not self.u.live(guid):
            return
        self.definitions.add(guid)
        body = self.u.resolve(guid)
        for field, g in _guids_in({k: v for k, v in body.items() if k != "Guid"}):
            if g == guid:
                continue
            if not self.u.live(g):
                # Abstract templates are known, just never pushed: not "unresolved". Keyed-list Keys and node Ids are
                # local identifiers (random GUIDs), not references: a Key that IS a definition is live and followed.
                if g not in self.u and not LOCAL_ID_FIELD.match(str(field)):
                    self.unresolved[(self.u.type_of(guid), field)].add(g)
                continue
            if self.u.type_of(g) == PREFAB:
                self.prefab(g, path)
            else:
                self.fill(g, path + (f"-> {self.u.type_of(g)} {g}",))

    def prefab(self, guid, path):                               # a PrefabDefinition value: walked as a composition
        if guid in self.blocked or not self.u.live(guid):
            return
        p = self.u.resolve(guid)
        self.composition(composition_of(p), object_builders(p.get("_entity")), path + (f"prefab {guid}",))


class Walk:
    def __init__(self, u):
        self.u = u
        self.errors = list(u.load_errors)  # (code, side, guid, path)
        self.bindings = {}                 # binding guid -> (server composite, server prefab, client prefab)
        self.server = self.client = None


def walk(u):
    """Step 1, loading composites from bindings, over the universe."""
    w = Walk(u)
    by_comp = {}
    for p in u.guids_of_type(PREFAB):                           # one prefab per composition (a duplicate fails the load)
        c = composition_of(u.resolve(p))
        if c in by_comp:
            w.errors.append(("duplicate-composition", None, c, (f"prefabs {by_comp[c]} and {p}",)))
        by_comp.setdefault(c, p)
    server_prefabs, client_prefabs = set(), set()
    for b in u.guids_of_type(BINDING):                          # check each binding
        v = u.resolve(b)
        sc, cp = _g(v.get("ServerComposite")), _g(v.get("ClientPrefab"))
        problems = []
        if u.type_of(sc) != COMPOSITE or not u.live(sc):
            problems.append(f"ServerComposite {sc} is not an EntityCompositeDefinition")
        if u.type_of(cp) != PREFAB or not u.live(cp):
            problems.append(f"ClientPrefab {cp} is not a PrefabDefinition")
        if sc not in by_comp:
            problems.append(f"no PrefabDefinition has the ServerComposite {sc} as its composition (no server prefab)")
        if problems:
            w.errors.append(("binding", None, b, (f"binding {b} ({u.origin(b)})",) + tuple(problems)))
            continue
        w.bindings[b] = (sc, by_comp[sc], cp)
        server_prefabs.add(by_comp[sc])
        client_prefabs.add(cp)
    w.server = Side(u, "server", client_prefabs)
    w.client = Side(u, "client", server_prefabs)
    for b, (sc, sp, cp) in sorted(w.bindings.items()):         # then walk both sides
        w.server.composition(sc, object_builders(u.resolve(sp).get("_entity")), (f"binding {b} server",))
        cpv = u.resolve(cp)
        w.client.composition(composition_of(cpv), object_builders(cpv.get("_entity")), (f"binding {b} client {cp}",))
    w.errors += w.server.errors + w.client.errors
    return w


def unresolved(w):
    """{(side, referring definition type, field): GUID count} of the GUIDs the walk met that resolve to no loaded
    definition (assets such as materials and fonts, or definitions a game update moved out of the SDK Assets). They
    are not followed, so a rise here is where a false pass could hide (an under-approximation, see the docstring)."""
    out = {}
    for side in (w.server, w.client):
        for (t, f), gs in side.unresolved.items():
            out[(side.name, t, f)] = len(gs)
    return out


def unresolved_report(w, limit=30):
    rows = sorted(unresolved(w).items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(n for _, n in rows)
    lines = [f"{total} unresolved GUID references (side, referring type, field):"]
    lines += [f"  {n:6d}  {s} {t}.{f}" for (s, t, f), n in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"  ... {len(rows) - limit} more fields")
    return "\n".join(lines)



# -------------------------------------------------------------------------------------------------------- validate
LOAD_ERRORS = ("binding", "duplicate-composition", "unparseable", "duplicate-guid")   # never filtered by `only`


def validate(w, types, only=None):
    """Step 4, the client-type check, over the walk, plus the walk's own load errors.
    `only`: optional predicate on a composite GUID (limit the report to some composites; the walk stays global).
    Returns [(code, side, guid, detail)], [] when the load passes."""
    errs = [e for e in w.errors if only is None or e[2] is None or only(e[2]) or e[0] in LOAD_ERRORS]
    srv, cli = w.server.composites, w.client.composites
    for side, comps in (("server", srv), ("client", {c: p for c, p in cli.items() if c not in srv})):
        for c, path in sorted(comps.items()):
            if only is not None and not only(c):
                continue
            for key, typ, dfn in components(w.u.resolve(c)):
                try:
                    if not typ:      # "Type": null -> the component of the definition's class
                        if dfn is None or w.u.full_type_of(dfn) is None:
                            raise UnknownType(f"component {key}: neither a Type nor a known Definition ({dfn})")
                        typ = types.component_for_definition(w.u.full_type_of(dfn))
                    k = types.classify(typ)
                except UnknownType as ex:
                    errs.append(("unknown-type", side, c, (str(ex),) + path))
                    continue
                if side == "server" and k["client"]:
                    errs.append(("client-type-on-server", side, c,
                                 (f"Client component {k['type']} should not be on server compositions "
                                  f"({k['reason']})",) + path))
                if side == "client" and k["server_only"]:
                    errs.append(("server-only-on-client", side, c,
                                 (f"Server-only component {k['type']} should not be on client compositions "
                                  f"({k['reason']})",) + path))
    return errs


def format_errors(errs, u=None, limit=20):
    out = []
    for code, side, g, detail in errs[:limit]:
        where = f" [{u.origin(g)}]" if u is not None and g else ""
        out.append(f"{code} {side or ''} {g or ''}{where}\n    " + "\n    ".join(map(str, detail)))
    if len(errs) > limit:
        out.append(f"... {len(errs) - limit} more")
    return "\n".join(out)
