"""Offline emulation of two SE2 load-time definition checks that a definition-level schema gate must cover.

Works on a `validate.clientserver.Universe` and a `validate.facts.TypeIndex` (type facts: names only, read once from
the user's own decompiled game):

    from se2modkit.validate import clientserver as csw, schema as ds
    u = csw.Universe([sdk_assets, mod_assets], cached_roots=[sdk_assets])
    s = ds.Schema(type_index)
    errs = ds.nonnull_errors(u, s, guids) + ds.composition_type_errors(u, s, guids)

What it reproduces of the game's load (game 4.0):

1. The non-null check, run on every member of every loaded definition's object builder, nested members included
   (the log names them "$Root.ConsumedResource.Type"): a reference-typed member whose declaration is NOT a nullable
   reference type (`T?`) must not be null; value-typed members are skipped. Fatal: the definition set is not loaded,
   the world does not load.
   Here: the definition is resolved fully (Copy BaseDefinition chain + Override partialdefs, Universe.resolve); its
   object builder class (<definition class>ObjectBuilder) and its base classes come from the facts; each public
   instance field is checked: T without `?` and not a value type -> the resolved value must be non-null (an absent
   member takes its initializer: one that is not `null` counts as set, a parameterless constructor assignment too). A
   dict value is recursed into with its declared (or $Type) type. Lists are not entered. A member whose type cannot be
   classified (a framework type outside the known lists) is skipped and counted in Schema.unknown (so a too-wide skip
   is visible), never failed.

2. The component type match: for a composite component with a Type, the nearest [Definition] member type up the
   component's class chain must be EXACTLY the referenced definition's class ("Definition X type doesn't match
   expected Y"; an interface: implemented). "Type": null takes the component of the definition's class, which
   matches by construction.

`content_view` and `stale_outputs` look at a BUILT package (the SDK's Content/) as the game loads it.
"""

from __future__ import annotations

import json
import os
import re

from . import clientserver as csw
from .facts import UnknownType, _candidates

__all__ = ["PARTIAL_DIFF", "Schema", "UNRESOLVED", "composition_type_errors", "content_view", "format_errors",
           "nonnull_errors", "stale_outputs"]

VALUE_PRIMITIVES = {
    "bool", "byte", "sbyte", "short", "ushort", "int", "uint", "long", "ulong", "float", "double", "decimal", "char",
    "nint", "nuint", "Guid", "TimeSpan", "DateTime", "DateTimeOffset", "Half", "ImmutableArray", "Nullable",
    "ValueTuple", "KeyValuePair", "ReadOnlyMemory", "Memory", "Span", "ReadOnlySpan"}
REFERENCE_FRAMEWORK = {
    "string", "object", "Type", "List", "Dictionary", "HashSet", "SortedDictionary", "SortedList", "IList",
    "IReadOnlyList", "IDictionary", "IReadOnlyDictionary", "ICollection", "IEnumerable", "ISet", "Array", "Uri",
    "ImmutableList", "ImmutableDictionary", "ImmutableHashSet", "Version"}
UNRESOLVED = "unresolved-type"      # an error detail prefix: the check could not read the type (never a pass)


class Schema:
    """Member and kind lookup over the type facts, through a `validate.facts.TypeIndex`."""

    def __init__(self, types):
        self.t = types
        self.unknown = {}          # (owner class, member type) -> count: members the check could not classify
        self._members, self._kinds = {}, {}

    # ----------------------------------------------------------------------------------------- type classification
    def resolve_name(self, owner, name):
        """Full name of a type written `name` inside `owner`'s file (nested `A.B` -> (`A` full, 'B')), or None."""
        d = self.t._decl(owner)
        name = re.sub(r"<.*$", "", name.strip().rstrip("?")).rstrip("[]")
        first, _, rest = name.partition(".")
        for cand in _candidates(d, name):
            if cand in self.t.by_name:
                return cand
        if rest:                                   # Outer.Inner: a nested type in Outer's file
            for cand in _candidates(d, first):
                if cand in self.t.by_name:
                    return cand + "+" + rest
        return None

    def kind(self, full):
        """'class' | 'struct' | 'interface' | 'enum' | 'record' for a decompiled type (nested: 'Outer+Inner'), or None."""
        if full in self._kinds:
            return self._kinds[full]
        outer, _, inner = full.partition("+")
        ff = self.t.file_facts(outer)
        k = None
        if ff is not None:
            name = (inner or outer.rsplit(".", 1)[1]).rsplit(".", 1)[-1]
            k = ff[1].get(name)
        self._kinds[full] = k
        return k

    def is_value_type(self, owner, type_str):
        """True / False, or None when the decompile cannot tell (an unknown framework type)."""
        if type_str.endswith("[]"):
            return False
        base = re.sub(r"<.*$", "", type_str.rstrip("?")).rsplit(".", 1)[-1]
        if base in VALUE_PRIMITIVES:
            return True
        if base in REFERENCE_FRAMEWORK:
            return False
        full = self.resolve_name(owner, type_str)
        k = self.kind(full) if full else None
        if k is None:
            return None
        return k in ("struct", "enum")

    # ------------------------------------------------------------------------------------------------- members
    def members(self, full):
        """[(name, type, init, declaring class)] of `full`'s instance fields, base classes first; init is "" (none),
        "null" or "set"."""
        if full in self._members:
            return self._members[full]
        out, chain, cur, seen = [], [], full, set()
        while cur and cur not in seen and "+" not in cur:
            seen.add(cur)
            try:
                d = self.t._decl(cur)
            except UnknownType:
                d = None
            if d is None:
                break
            chain.append(cur)
            cur = self.t._base(d)
        for cls in reversed(chain):
            for member, typ, init in (self.t.file_facts(cls) or ([], {}))[0]:
                out = [x for x in out if x[0] != member] + [(member, typ, init, cls)]
        self._members[full] = out
        return out

    def null_paths(self, cls, value, path="$Root", depth=0):
        """[(path, member, declared type, declaring class)] of non-nullable members that are null in `value`."""
        out = []
        if depth > 8 or not isinstance(value, dict):
            return out
        for member, typ, init, owner in self.members(cls):
            present = member in value
            v = value.get(member)
            nullable = typ.endswith("?")
            vt = self.is_value_type(owner, typ)
            if vt is None:
                self.unknown[(owner, typ)] = self.unknown.get((owner, typ), 0) + 1
            elif not vt and not nullable:
                unset = (not present and (not init or init.startswith("null"))) or (present and v is None)
                if unset:
                    out.append((f"{path}.{member}", member, typ, owner))
                    continue
            if isinstance(v, dict) and not csw._g(v.get("Guid")):      # a nested struct / object (not a definition)
                if "$Type" in v:                                          # polymorphic: the written type
                    sub = self.t.migrate(str(v["$Type"]).split(":", 1)[-1])
                    body = v["$Value"] if isinstance(v.get("$Value"), dict) else v
                else:
                    sub, body = self.resolve_name(owner, typ), v
                if sub and "+" not in sub and sub in self.t.by_name and self.kind(sub) in ("class", "struct"):
                    out += self.null_paths(sub, body, f"{path}.{member}", depth + 1)
        return out

    def object_builder(self, definition_class):
        """The decompiled object builder class of a definition class (through [NameMigration]s), or None."""
        for c in (definition_class, self.t.migrate(definition_class)):
            for ob in (c + "ObjectBuilder", self.t.migrate(c + "ObjectBuilder")):
                if ob in self.t.by_name:
                    return ob
        return None

    # ------------------------------------------------------------------------------------- component definitions
    def expected_definition(self, component_full):
        """RuntimeComponentInfo.For(type).Definition: the nearest [Definition] member type up the class chain."""
        cur, seen = self.t.migrate(component_full), set()
        while cur and cur not in seen:
            seen.add(cur)
            d = self.t._decl(cur)
            if d is None:
                return None
            t = self.t._own_definition_type(cur)
            if t:
                return t
            cur = self.t._base(d)
        return None

    def is_or_implements(self, cls, iface):
        """`cls` implements interface `iface` somewhere up its class chain (by declared base lists)."""
        cur, seen = cls, set()
        while cur and cur not in seen:
            seen.add(cur)
            d = self.t._decl(cur)
            if d is None:
                return False
            for b in re.split(r",(?![^<]*>)", d["bases"] or ""):
                if b.strip() and self.t._resolve_name(d, b) == iface:
                    return True
            cur = self.t._base(d)
        return False


def nonnull_errors(u, schema, guids):
    """[(guid, origin, path, detail)] for the definitions `guids` (live only: Abstract templates are not validated)."""
    errs = []
    for g in sorted(guids):
        if not u.live(g):
            continue
        cls = u.full_type_of(g)
        if not cls:
            continue
        ob = schema.object_builder(cls)
        if ob is None:
            errs.append((g, u.origin(g), "$Root", f"{UNRESOLVED}: no object builder for {cls} in the decompile"))
            continue
        for path, member, typ, owner in schema.null_paths(ob, u.resolve(g)):
            errs.append((g, u.origin(g), path, f"The member was declared as non-nullable but has the value null: "
                                               f"{member} ({typ}, declared on {owner.rsplit('.', 1)[1]})"))
    return errs


def composition_type_errors(u, schema, guids):
    """[(guid, origin, path, detail)] for the EntityCompositeDefinitions among `guids`: a component's Definition must be
    exactly the class the component type expects."""
    errs = []
    for g in sorted(guids):
        if not u.live(g) or u.type_of(g) != csw.COMPOSITE:
            continue
        for i, (key, typ, dfn) in enumerate(csw.components(u.resolve(g))):
            if not typ or not dfn:
                continue
            path = f"$Root.Components[{i}].Definition"
            actual = u.full_type_of(dfn)
            actual = schema.t.migrate(actual) if actual else None
            if actual is None:
                errs.append((g, u.origin(g), path, f"component {key}: Definition {dfn} is no loaded definition"))
                continue
            comp = schema.t.migrate(typ.split(":", 1)[-1])
            try:
                expected = schema.expected_definition(comp)
            except UnknownType as ex:
                errs.append((g, u.origin(g), path, f"component {key} {comp}: {ex}"))
                continue
            if expected is None:
                errs.append((g, u.origin(g), path, f"component {key} {comp} declares no [Definition] but references "
                                                   f"{actual} ('Unexpected ... Definition')"))
                continue
            d = schema.t._decl(expected)
            ok = schema.is_or_implements(actual, expected) if d and d["kind"] == "interface" else actual == expected
            if not ok:
                errs.append((g, u.origin(g), path, f"Definition {actual} type doesn't match expected {expected} "
                                                   f"(component {key} {comp.rsplit('.', 1)[1]}, definition {dfn})"))
    return errs


def format_errors(errs, limit=30):
    out = [f"{g} [{origin}] {path}: {detail}" for g, origin, path, detail in errs[:limit]]
    if len(errs) > limit:
        out.append(f"... {len(errs) - limit} more")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------- the build as the game loads it
PARTIAL_DIFF = "VRage:Keen.VRage.ContentPipeline.Definitions.PartialDefinitionDiff"


def content_view(u, assets_root, content_root):
    """The mod's definitions as the game loads its BUILT package (Content/), as a Universe derived from `u` (which holds
    the mod's `assets_root`). The SDK builder writes each asset to Content/<same path>.def holding only the asset's own
    members (a Copy partialdef's Manipulator: no BaseDefinition), while the definition index it regenerates
    (Content/definitionsets.vrb, contentcache.vrb) carries the base and the class. So the game's definition is: base
    and class as in the index (taken here from the asset, which is what the caches are generated from), members as in
    the Content .def. A stale Content .def (an asset changed but its output not rewritten) then resolves exactly as in
    game: 2026-10-20 the open racks' CubeBlock-shaped .def on their new PowerableBlockDefinition base gave Density /
    RecipeEfficiency / ConsumedResource.Type null and a CubeBlockComponent on a PowerableBlockDefinition
    (SpaceEngineers2_261020_182754 log). Returns (universe, [asset paths whose Content .def is missing])."""
    files, missing = [], []
    root = os.path.abspath(assets_root)
    for g, e in list(u.entries.items()):
        origin = os.path.abspath(str(e[4]))
        if not origin.startswith(root + os.sep):
            continue
        rel = os.path.relpath(origin, root)
        out = os.path.join(content_root, os.path.splitext(rel)[0] + ".def")
        if not os.path.exists(out):
            missing.append(rel)
            continue
        with open(out, encoding="utf-8-sig") as fh:
            built = json.load(fh).get("$Value") or {}
        if e[0] == "copy":
            files.append({"$Type": PARTIAL_DIFF, "$Value": {
                "BaseDefinition": e[3], "PartialDefinitionKind": "Copy", "Manipulator": built,
                "DefinitionType": "X:" + u.full_types[g]}})
        else:
            files.append({"$Type": "X:" + u.full_types[g] + "ObjectBuilder", "$Value": built})
    return u.derive(*files, origin="<Content>"), missing


def stale_outputs(assets_root, content_root):
    """[(asset rel path, why)] for every .def/.partialdef asset whose Content .def is missing, of another class than the asset declares, or not holding the asset's own members. Read-only."""
    out = []
    for dp, _, fs in os.walk(assets_root):
        for f in fs:
            if not f.endswith((".def", ".partialdef")):
                continue
            src = os.path.join(dp, f)
            rel = os.path.relpath(src, assets_root)
            with open(src, encoding="utf-8-sig") as fh:
                d = json.load(fh)
            v = d.get("$Value") or {}
            kind = v.get("PartialDefinitionKind")
            if kind == "Override":
                continue                 # patches another definition: no output of its own
            if kind == "Copy":
                want_type, want = csw.full_type(v.get("DefinitionType")), v.get("Manipulator") or {}
            else:
                want_type, want = csw.full_type(d.get("$Type")), v
            dst = os.path.join(content_root, os.path.splitext(rel)[0] + ".def")
            if not os.path.exists(dst):
                out.append((rel, "no Content output"))
                continue
            with open(dst, encoding="utf-8-sig") as fh:
                b = json.load(fh)
            if csw.full_type(b.get("$Type")) != want_type:
                out.append((rel, f"Content class {csw.short_type(b.get('$Type'))} != asset {want_type.rsplit('.', 1)[-1]}"))
            else:
                bv = b.get("$Value") or {}
                lost = sorted(k for k, x in want.items() if x is not None and k not in bv)
                if lost:
                    out.append((rel, f"Content output lacks {lost}"))
    return sorted(out)
