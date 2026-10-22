"""Type facts: what the validators need to know about the game's types, read once from a decompiled game.

`build_facts(decompiled_dir, game_version)` reads a decompiled tree in the `ilspycmd -p` layout
(`<Assembly>/<Namespace>/<Type>.cs`, plus `<Assembly>/Properties/AssemblyInfo.cs`) and, when it exists, the sibling
tree `<real decompiled_dir>-extra` (more assemblies decompiled the same way). It keeps NAMES and FLAGS only, never code:
per type its assembly, the file it was found in (relative), the declaration's kind and line, [ServerOnly], abstract,
the base list as written, the file's namespace and using directives, the declared type of each [Definition] member,
each public instance field as (name, declared type, initialised: "" | "null" | "set") and the kinds of the types the
file declares; per assembly whether it is a client assembly ([assembly: ClientAssembly] or a name ending in "Client");
and the [NameMigration(version, old, new)] renames.

Facts describe the game, so they are never committed (Keen's EULA): `write_facts` writes only into
`<user cache>/facts/<game_version>.json` and refuses a cache dir inside a git work tree. `TypeIndex.from_facts(path)`
loads them for the validators (`validate.clientserver`, `validate.schema`).

TypeIndex.classify(type) -> {"type", "assembly", "client", "server_only", "file", "line", "reason"}:
  client      = the type's assembly is a client assembly (ComponentTypeUtility.IsClientType);
  server_only = [ServerOnly] on the class or on any base class (inherited; interfaces are not walked, as
                GetCustomAttribute does not search them).
A type the facts do not define raises UnknownType: never treated as safe.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

__all__ = ["FORMAT", "FactsError", "MissingDecompile", "TypeIndex", "UnknownType", "build_facts", "decompile_roots",
           "facts_path", "in_git_work_tree", "load_facts", "write_facts"]

FORMAT = 1
DCS_COMPONENT = "Keen.VRage.DCS.Components.Component"
NOT_ASSEMBLIES = {"sdk-pipeline"}          # an Editor pipeline decompile that may sit inside the tree
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")


class FactsError(Exception):
    """A facts problem, reported as one plain sentence."""


class MissingDecompile(FactsError, FileNotFoundError):
    pass


class UnknownType(Exception):
    pass


# ------------------------------------------------------------------------------------------------ reading the decompile
CLASS_RE = re.compile(r"^(?:\s*)(?:public|internal|private|protected)?\s*(?:(?:sealed|abstract|static|partial|unsafe)\s+)*"
                      r"(class|struct|interface|record)\s+(\w+)\s*(?:<[^>{]*>)?\s*(?::\s*([^{\n]+))?", re.M)
# a [Definition] member: the attribute, maybe more attributes, then "<mods> Type name"
DEFINITION_MEMBER_RE = re.compile(r"^\s*\[Definition\]\s*\n(?:\s*\[[^\n]*\]\s*\n)*\s*"
                                  r"(?:(?:public|private|protected|internal|new|readonly|static|required)\s+)*"
                                  r"([\w.]+(?:<[^>\n]*>)?\??)\s+\w+\s*(?:;|=|\{)", re.M)
NAME_MIGRATION_RE = re.compile(r'\[NameMigration\("[^"]*",\s*"([\w.]+)",\s*"([\w.]+)"\)\]')
SERVER_ONLY_RE = re.compile(r"^\s*\[(?:[^\]]*,\s*)?ServerOnly(?:Attribute)?\s*(?:,[^\]]*)?\]", re.M)
FIELD_RE = re.compile(
    r"^\tpublic\s+(?:(?:new|required)\s+)*(?!static\b|const\b|event\b|override\b|abstract\b|virtual\b|readonly\s+static\b)"
    r"([\w.]+(?:<[^;=\n]*>)?(?:\[\])*\??)\s+(\w+)\s*(?:=\s*([^;\n]+))?;\s*$", re.M)
KINDS_RE = re.compile(r"(?:^|\s)(class|struct|interface|record\s+struct|record|enum)\s+(\w+)\b", re.M)


def decompile_roots(decompiled_dir: Path) -> list[Path]:
    """The decompiled tree, then its `-extra` sibling (named after the real target of a link) when it exists."""
    d = Path(decompiled_dir)
    extra = Path(os.path.realpath(d) + "-extra")
    return [d] + ([extra] if extra.is_dir() else [])


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _init_marker(init: str) -> str:
    init = init.strip()
    return "null" if init.startswith("null") else ("set" if init else "")


def _file_facts(full: str, src: str) -> dict:
    """The declaration of `full` in its file, plus the file-wide fields and declared kinds (names and flags only)."""
    name = full.rsplit(".", 1)[1]
    out: dict = {}
    for m in CLASS_RE.finditer(src):
        if m.group(2) != name:
            continue
        head = src[:m.start()]
        block = []                     # the attribute lines directly above the declaration
        for line in reversed(head.splitlines()):
            if not line.strip().startswith("["):
                break
            block.append(line)
        attrs = "\n".join(reversed(block))
        ns = re.search(r"^namespace\s+([\w.]+)", src, re.M)
        out = {"kind": m.group(1), "line": head.count("\n") + 1, "server_only": bool(SERVER_ONLY_RE.search(attrs)),
               "abstract": bool(re.search(r"\babstract\s+class\b", m.group(0))), "bases": (m.group(3) or "").strip(),
               "usings": re.findall(r"^using\s+([\w.]+)\s*;", src, re.M),
               "ns": ns.group(1) if ns else full.rsplit(".", 1)[0],
               "definition_members": [x.group(1) for x in DEFINITION_MEMBER_RE.finditer(src)]
               if "[Definition]" in src else []}
        break
    ctor = re.search(r"^\tpublic " + re.escape(name) + r"\(\)\s*\n\t\{\n(.*?)^\t\}", src, re.M | re.S)
    ctor_body = ctor.group(1) if ctor else ""
    fields = []
    for m in FIELD_RE.finditer(src):
        typ, member, init = m.group(1), m.group(2), _init_marker(m.group(3) or "")
        if not init and re.search(r"^\t\t(?:this\.)?" + re.escape(member) + r"\s*=\s*(?!null\b)", ctor_body, re.M):
            init = "set"
        fields.append([member, typ, init])
    kinds: dict[str, str] = {}
    for m in KINDS_RE.finditer(src):
        k = m.group(1)
        kinds.setdefault(m.group(2), "struct" if k.startswith("record") and "struct" in k else k)
    head = {"decl": out} if out else {"error": f"no declaration of {name}"}
    return {**head, "fields": fields, "kinds": kinds}


def build_facts(decompiled_dir: Path, game_version: str) -> dict:
    """Facts of every type in the decompiled tree (and its `-extra` sibling). Raises MissingDecompile when the tree is
    absent or holds no assembly."""
    roots = decompile_roots(Path(decompiled_dir))
    if not roots[0].is_dir():
        raise MissingDecompile(f"The decompiled game is missing: {roots[0]}. Set SE2_DECOMPILED or decompiled_dir in "
                               "the se2modkit user config to the decompiled tree (<Assembly>/<Namespace>/<Type>.cs).")
    base = Path(os.path.realpath(roots[0])).parent
    hits: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    client: set[str] = set()
    migrations: dict[str, str] = {}
    for root in roots:
        for asm in sorted(os.listdir(root)):
            d = Path(root) / asm
            if asm in NOT_ASSEMBLIES or not d.is_dir():
                continue
            info = d / "Properties" / "AssemblyInfo.cs"
            if asm.endswith("Client") or (info.is_file()
                                          and re.search(r"\[assembly:\s*ClientAssembly(?:Attribute)?\b", _read(info))):
                client.add(asm)
            for ns in sorted(os.listdir(d)):
                nd = d / ns
                if ns == "Properties" or not nd.is_dir():
                    continue
                for f in sorted(os.listdir(nd)):
                    if f.endswith(".cs"):
                        hits[f"{ns}.{f[:-3]}"].append((asm, nd / f))
    if not hits:
        raise MissingDecompile(f"No decompiled assemblies under {roots[0]} (expected <Assembly>/<Namespace>/<Type>.cs).")
    types: dict[str, dict] = {}
    for full, where in sorted(hits.items()):
        if len(where) > 1:
            types[full] = {"assemblies": sorted(a for a, _ in where)}
            for _, p in where:
                _migrations(_read(p), full, migrations)
            continue
        asm, p = where[0]
        src = _read(p)
        _migrations(src, full, migrations)
        rel = os.path.relpath(os.path.realpath(p), base).replace(os.sep, "/")
        types[full] = {"assembly": asm, "file": rel, **_file_facts(full, src)}
    return {"format": FORMAT, "game_version": game_version, "source": Path(os.path.realpath(roots[0])).name,
            "client_assemblies": sorted(client), "migrations": dict(sorted(migrations.items())), "types": types}


def _migrations(src: str, full: str, out: dict[str, str]) -> None:
    if "NameMigration(" not in src:
        return
    ns = full.rsplit(".", 1)[0]
    for m in NAME_MIGRATION_RE.finditer(src):
        old, new = m.group(1), m.group(2)
        out["." in old and old or f"{ns}.{old}"] = "." in new and new or f"{ns}.{new}"


# ------------------------------------------------------------------------------------------------ the user cache only
def in_git_work_tree(path: Path) -> bool:
    """`path` (or the nearest existing parent) lies inside a git work tree (a `.git` dir or file above it)."""
    p = Path(os.path.realpath(Path(path).expanduser()))
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return True
    return False


def facts_path(game_version: str, *, cache_dir: Path | None = None) -> Path:
    if not isinstance(game_version, str) or not _VERSION_RE.fullmatch(game_version) or ".." in game_version:
        raise FactsError(f"Game version {game_version!r} is not a plain version string (e.g. 4.0.0.3155).")
    if cache_dir is None:
        from ..env.config import user_cache_dir
        cache_dir = user_cache_dir()
    return Path(cache_dir) / "facts" / f"{game_version}.json"


def write_facts(facts: dict, *, cache_dir: Path | None = None) -> Path:
    """Write `facts` to `<cache_dir>/facts/<game_version>.json` (default: the se2modkit user cache). Refuses a cache dir
    inside a git work tree: type facts describe the game and are never committed."""
    path = facts_path(facts.get("game_version"), cache_dir=cache_dir)
    if in_git_work_tree(path.parent):
        raise FactsError(f"Refusing to write type facts to {path.parent}: it is inside a git work tree, and facts are "
                         "never committed. Point XDG_CACHE_HOME (or LOCALAPPDATA) outside any repository.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(facts, fh, separators=(",", ":"), sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)              # atomic: a parallel test worker never reads a half-written file
    return path


def load_facts(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise FactsError(f"Cannot read type facts {path}: {e}. Rebuild them: se2mod facts build.") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("types"), dict):
        raise FactsError(f"Type facts {path} are in another format (want {FORMAT}); rebuild them: se2mod facts build.")
    return data


# ------------------------------------------------------------------------------------------------ the index
def _candidates(d: dict, name: str) -> list[str]:
    """C# simple-name lookup from a declaration: as written (if qualified), the declaring namespace and each enclosing
    namespace, then the file's using directives."""
    out = [name] if "." in name else []
    parts = d["ns"].split(".")
    out += [".".join(parts[:i] + [name]) for i in range(len(parts), 0, -1)]
    return out + [f"{u}.{name}" for u in d["usings"]]


class TypeIndex:
    """Component type classification and declaration lookup over a facts table (`build_facts` / `load_facts`)."""

    def __init__(self, facts: dict):
        self.facts = facts
        self.types: dict[str, dict] = facts.get("types") or {}
        self.by_name = self.types               # full name -> entry (membership: the type is in the decompile)
        self.client_assemblies = set(facts.get("client_assemblies") or ())
        self.migrations = dict(facts.get("migrations") or {})
        self.game_version = facts.get("game_version")
        self.source = facts.get("source") or "the type facts"
        self._cache: dict[str, dict] = {}
        self._decls: dict[str, dict] = {}
        self._def_map: dict[str, list[str]] | None = None

    @classmethod
    def from_facts(cls, path: Path) -> TypeIndex:
        return cls(load_facts(Path(path)))

    @classmethod
    def from_decompiled(cls, decompiled_dir: Path, game_version: str = "unknown") -> TypeIndex:
        return cls(build_facts(Path(decompiled_dir), game_version))

    def migrate(self, full: str) -> str:
        """The current name of a type the data may still name by an old one ([NameMigration])."""
        seen = set()
        while full in self.migrations and full not in seen and full not in self.types:
            seen.add(full)
            full = self.migrations[full]
        return full

    def entry(self, full: str) -> dict | None:
        return self.types.get(full)

    def file_facts(self, full: str) -> tuple[list, dict] | None:
        """(fields, kinds) of the one file that defines `full`: its public instance fields as [name, type, init] and
        {declared type name: kind}; None when the facts do not define it or several assemblies do."""
        e = self.types.get(full)
        if e is None or "assemblies" in e:
            return None
        return e.get("fields") or [], e.get("kinds") or {}

    def _decl(self, full: str) -> dict | None:
        """The declaration facts of `full` ({assembly, file, line, kind, server_only, abstract, bases, usings, ns,
        definition_members}), None when the facts do not define it; UnknownType when ambiguous or
        without a declaration."""
        if full in self._decls:
            return self._decls[full]
        e = self.types.get(full)
        if e is None:
            return None
        if "assemblies" in e:
            raise UnknownType(f"{full} is defined in several assemblies: {e['assemblies']}")
        d = e.get("decl")
        if d is None:
            raise UnknownType(f"{full}: {e.get('error') or 'no declaration'} in {e.get('file')}")
        out = {"kind": "class", "line": 0, "server_only": False, "abstract": False, "bases": "", "usings": [],
               "ns": full.rsplit(".", 1)[0], "definition_members": [], **d,
               "assembly": e.get("assembly"), "file": e.get("file")}
        self._decls[full] = out
        return out

    def _base(self, d: dict) -> str | None:
        """The resolved full name of the declaration's base CLASS, or None (no base / external / interface)."""
        if d["kind"] != "class" or not d["bases"]:
            return None
        first = re.split(r",(?![^<]*>)", d["bases"])[0].strip()
        first = re.sub(r"<.*$", "", first).strip()
        for cand in _candidates(d, first):
            if cand in self.types:
                try:
                    bd = self._decl(cand)
                except UnknownType:
                    return None
                return cand if bd and bd["kind"] == "class" else None
        return None   # a framework base (System.Object, ...): not in the decompile, carries no ServerOnly

    def _resolve_name(self, d: dict, name: str) -> str | None:
        name = re.sub(r"<.*$", "", name.strip().rstrip("?"))
        for cand in _candidates(d, name):
            if cand in self.types:
                return cand
        return None

    def _own_definition_type(self, full: str) -> str | None:
        """The type of the class's own [Definition] member, or None."""
        d = self._decl(full)
        for name in d["definition_members"]:
            t = self._resolve_name(d, name)
            if t:
                return t
        return None

    def is_component(self, full: str) -> bool:
        """Derives from Keen.VRage.DCS.Components.Component."""
        cur, seen = full, set()
        while cur and cur not in seen:
            if cur == DCS_COMPONENT:
                return cur != full
            seen.add(cur)
            try:
                d = self._decl(cur)
                cur = self._base(d) if d else None
            except UnknownType:
                return False
        return False

    def component_for_definition(self, definition_class: str) -> str:
        """The component whose [Definition] member (nearest up its class chain) is exactly `definition_class` (what a
        composite entry with "Type": null takes). Raises UnknownType when none or several non-abstract ones match."""
        if self._def_map is None:
            own: dict[str, dict] = {}
            self._def_map = defaultdict(list)
            for full, e in self.types.items():
                if "assemblies" in e:
                    continue
                try:
                    d = self._decl(full)
                except UnknownType:
                    continue
                if d and d["kind"] == "class":
                    own[full] = d
            for full, d in own.items():
                if d["abstract"] or not self.is_component(full):
                    continue
                cur, seen, t = full, set(), None
                while cur and cur not in seen and cur in own:   # nearest [Definition] up the class chain
                    seen.add(cur)
                    t = self._own_definition_type(cur)
                    if t:
                        break
                    cur = self._base(own[cur])
                if t:
                    self._def_map[t].append(full)
        definition_class = self.migrate(definition_class)
        hits = self._def_map.get(definition_class) or []
        if len(hits) != 1:
            raise UnknownType(f"component for definition class {definition_class}: "
                              f"{'none' if not hits else 'ambiguous ' + str(sorted(hits))} in the decompile")
        return hits[0]

    def classify(self, type_string: str) -> dict:
        if type_string in self._cache:
            return self._cache[type_string]
        full = self.migrate((type_string or "").split(":", 1)[-1])
        if not full or "`" in full or "[" in full:
            raise UnknownType(f"{type_string!r}: not a plain component type name")
        d = self._decl(full)
        if d is None:
            raise UnknownType(f"{type_string}: not defined in the decompile ({self.source})")
        server_only, why, seen = d["server_only"], [], {full}
        if server_only:
            why.append(f"[ServerOnly] on {full}")
        b = self._base(d)
        while b and b not in seen and not server_only:
            seen.add(b)
            bd = self._decl(b)
            if bd["server_only"]:
                server_only = True
                why.append(f"[ServerOnly] inherited from {b} ({bd['file']}:{bd['line']})")
            b = self._base(bd)
        client = d["assembly"] in self.client_assemblies
        why.insert(0, f"assembly {d['assembly']} is {'a client' if client else 'not a client'} assembly")
        r = dict(type=full, assembly=d["assembly"], client=client, server_only=server_only, file=d["file"],
                 line=d["line"], reason="; ".join(why))
        self._cache[type_string] = r
        return r
