"""Incremental model builds: a key per block over every input of its build, and the per-block report fragments.

A block is stale when any input changed since its fragment was written (`Runner` rebuilds only those; `--force`
rebuilds everything). The inputs, each hashed on its own so a stale block can say why (`block_inputs`):

    runner    the callables the runner calls for the block (builder, collision, extras, audits, placement, catalogue),
              by module and name, with their defaults and closure values
    spec      the block's catalogue entry
    hooks     the hooks tables the block's family (or name) reads; mod-wide tables whole
    sources   the content of every mod source file those callables live in and, transitively, import (static import
              graph resolved on the model source, the callables' folders and every non-library sys.path entry), plus
              loaded mod modules no block's graph explains; any mention of a dynamic-import tool (importlib,
              __import__, runpy, exec ...; aliased or not) or a sys.path edit that has not run yet makes it every
              `.py` file of the mod tree
    modkit    this se2modkit: its version and the content of every file in the package
    project   the mod's `[tool.se2modkit]` profile, style overrides and prefix
    runtime   Python, platform, Blender and VRAGE Tools add-on versions, the game version and Mod SDK build id (from
              se2mod's SE2MODKIT_GAME_VERSION / SE2MODKIT_SDK_BUILDID, else discovered in-process the same way, so a
              script started outside se2mod gets the same key) and the `SE2MODKIT_*`, `SE2_*`, `<prefix>*` environment
    data      the files and folders `Runner(inputs=fn(spec) -> paths)` names (data a builder reads)

Besides the key, the fragment records the files the block's build read (an audit hook installed when se2modkit.pipeline
is imported, which `se2mod blender run` does before the build script; see `recording`) with their hashes: the files
the block's own builder code opened while it built (that block only), the files the process read outside any block's
build (every block), and what other code read while some block built (a shared helper, a cached loader, mod code it
imported lazily and what that code read at import: that block and every later one of the process; by import event, so
a .pyc load counts). A read is keyed by its content when it was read; a file the block wrote itself by what it left
there. Only declared outputs never count (`Outputs`: the build's own files and the mod's `Runner(outputs=...)` globs),
nor does the run's private temp folder (`run_scope`): a file the build writes and reads stays an input (`rewritten`
names those the build changed after a block read them). With `--export` it also records the key its models were exported
under and a hash of its Models/<block>/ folder: a read file that changed or a model folder that differs makes the block
stale too.

Anything that cannot be hashed deterministically (an object whose repr carries a memory address) gets a random value,
so its block is rebuilt every time: when unsure, rebuild. Nothing is keyed loosely.

Fragments live in the user cache (`<cache>/builds/<mod>-<hash of the report path>/<block>.json`, or under
`$SE2MODKIT_BUILD_STATE`), never in the mod: {"version", "block", "key", "inputs", "entry", "reads", "export_key",
"export_files", "seconds"}. One build of a mod runs at a time (`build_lock`, also in the build state folder).
`entry` is the block's build_report.json entry as JSON stores it, so a report merged from fragments is byte-identical to
one a full build writes.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import enum
import functools
import hashlib
import inspect
import json
import os
import re
import sys
import sysconfig
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from .. import buildstate as BS

__all__ = ["FRAGMENT_VERSION", "LOCK_ENV", "LockHeld", "Outputs", "STATE_ENV", "Sources", "Store", "WRITTEN_FILE",
           "addon_digest", "block_inputs", "build_lock", "canon", "code_files", "entry_digest", "export_fresh",
           "files_digest", "fragment", "hooks_view", "input_paths", "install_bpy_wrappers", "install_read_hook", "key",
           "lazy_imports", "lock_path", "models_digest", "modkit_digest", "module_closure", "new_run", "normalise",
           "output_glob", "process_reads", "read_hashes", "reads_changed", "recording", "rewritten", "run_scope", "Seen",
           "block_reads", "call_builder", "OwnCode", "own_code", "path_forms", "SEVERAL_WRITERS",
           "stale_reasons", "touched_by", "written"]

FRAGMENT_VERSION = 2
STATE_ENV = BS.STATE_ENV
# Environment names (after SE2MODKIT_ or the mod prefix) that never change a model: the render tag, the windowed
# run's quit switch, where the fragments live.
_ENV_IGNORED = frozenset({"PREVIEW", "NO_QUIT", "BUILD_STATE", "BUILD_LOCK"})
# Tables keyed by family: a block reads only its own family's entry.
_FAMILY_TABLES = frozenset({"keep_whole", "roles", "dent", "jag", "plans", "spark_kind", "per_zone", "analogue",
                            "leaf_frame", "spawned_subparts", "core_parts", "scaffold_zone_boxes"})
_ADDRESS = re.compile(r" at 0x[0-9A-Fa-f]+")
# Any mention of these in mod code (a name, an attribute, an import, a string passed to getattr) counts as a dynamic
# import, called or not, aliased or not: conservative on purpose.
_DYNAMIC_WORDS = frozenset({"importlib", "import_module", "__import__", "runpy", "run_path", "run_module",
                            "spec_from_file_location", "load_source", "imp", "exec", "eval", "execfile",
                            "zipimport", "pkgutil", "load_module", "exec_module", "SourceFileLoader"})


_PAUSED = [0]   # >0 while this module reads files itself (hashing them is not the build reading them)


@contextlib.contextmanager
def paused():
    """Reads inside are not recorded (the key computation reading sources, the hashing of recorded files)."""
    _PAUSED[0] += 1
    try:
        yield
    finally:
        _PAUSED[0] -= 1


def _quiet(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        with paused():
            return fn(*a, **k)
    return wrapper



class Unstable(Exception):
    """An input with no deterministic text (its repr carries a memory address)."""


# ------------------------------------------------------------------------------------------------ canonical text
def canon(obj, files: set[Path] | None = None) -> str:
    """Deterministic text of `obj` for hashing; the source files of callables met on the way go into `files`.
    Raises `Unstable` for an object whose only text is an address-carrying repr."""
    return _canon(obj, set() if files is None else files, set())


def _canon(obj, files: set[Path], stack: set[int]) -> str:
    if obj is None or isinstance(obj, (bool, int, str)):
        return f"{type(obj).__name__}:{obj!r}"
    if isinstance(obj, float):
        return f"float:{obj!r}"
    if isinstance(obj, (bytes, bytearray)):
        return f"bytes:{hashlib.sha256(obj).hexdigest()}"
    if isinstance(obj, Path):
        return f"path:{obj.as_posix()}"
    if isinstance(obj, enum.Enum):
        return f"enum:{type(obj).__module__}.{type(obj).__qualname__}.{obj.name}"
    if id(obj) in stack:
        return "cycle"
    stack.add(id(obj))
    try:
        return _canon_container(obj, files, stack)
    finally:
        stack.discard(id(obj))


def _canon_container(obj, files: set[Path], stack: set[int]) -> str:
    c = functools.partial(_canon, files=files, stack=stack)
    if isinstance(obj, Mapping):
        items = sorted((c(k), c(v)) for k, v in obj.items())
        return f"{type(obj).__name__}{{" + ",".join(f"{k}={v}" for k, v in items) + "}"
    if isinstance(obj, (list, tuple)):
        return f"{type(obj).__name__}[" + ",".join(c(v) for v in obj) + "]"
    if isinstance(obj, (set, frozenset)):
        return f"{type(obj).__name__}(" + ",".join(sorted(c(v) for v in obj)) + ")"
    if type(obj).__module__ == "numpy" and hasattr(obj, "tobytes") and hasattr(obj, "dtype"):
        return f"ndarray:{obj.dtype}:{getattr(obj, 'shape', ())}:{hashlib.sha256(obj.tobytes()).hexdigest()}"
    if callable(obj) and (isinstance(obj, type) or not dataclasses.is_dataclass(obj)):
        fn = _canon_callable(obj, files, c)
        if fn is not None:
            return fn
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        _add_module_file(type(obj).__module__, files)
        body = ",".join(f"{f.name}={c(getattr(obj, f.name, None))}" for f in dataclasses.fields(obj))
        return f"{_qual(type(obj))}({body})"
    state = getattr(obj, "__dict__", None)
    if isinstance(state, dict) and not isinstance(obj, type):
        _add_module_file(type(obj).__module__, files)
        return f"{_qual(type(obj))}({c(state)})"
    slots = [s for k in type(obj).__mro__ for s in getattr(k, "__slots__", ()) if isinstance(s, str)]
    if slots:
        return f"{_qual(type(obj))}(" + ",".join(f"{s}={c(getattr(obj, s, None))}" for s in slots) + ")"
    text = repr(obj)
    if _ADDRESS.search(text):
        raise Unstable(f"{_qual(type(obj))} has no stable text ({text[:60]})")
    return f"{_qual(type(obj))}:{text}"


def _qual(t) -> str:
    return f"{getattr(t, '__module__', '?')}.{getattr(t, '__qualname__', getattr(t, '__name__', '?'))}"


def _add_module_file(module: str | None, files: set[Path]) -> None:
    f = getattr(sys.modules.get(module or ""), "__file__", None)
    if f:
        files.add(Path(f))


def _canon_callable(obj, files: set[Path], c) -> str | None:
    if isinstance(obj, functools.partial):
        return f"partial({c(obj.func)},{c(obj.args)},{c(obj.keywords)})"
    if inspect.ismethod(obj):
        return f"method({c(obj.__self__)},{c(obj.__func__)})"
    if isinstance(obj, type):
        _add_module_file(obj.__module__, files)
        return f"type:{_qual(obj)}"
    code = getattr(obj, "__code__", None)
    if code is not None:
        files.add(Path(code.co_filename))
        cells = [cell.cell_contents if _cell_full(cell) else None for cell in (obj.__closure__ or ())]
        return (f"fn:{_qual(obj)}:{c(obj.__defaults__)}:{c(obj.__kwdefaults__)}:{c(code.co_freevars)}"
                f":{c(cells)}")
    if inspect.isbuiltin(obj) or type(obj).__name__ in ("builtin_function_or_method", "method-wrapper", "ufunc"):
        return f"builtin:{getattr(obj, '__module__', '?')}.{getattr(obj, '__qualname__', getattr(obj, '__name__', '?'))}"
    return None   # a callable instance: its state below


def _cell_full(cell) -> bool:
    try:
        cell.cell_contents  # noqa: B018
        return True
    except ValueError:
        return False


def key(inputs: Mapping[str, str]) -> str:
    """The block key: one hash over its named input hashes."""
    return hashlib.sha256(canon(dict(inputs)).encode("utf-8")).hexdigest()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unstable() -> str:
    return "unstable:" + os.urandom(16).hex()


# ------------------------------------------------------------------------------------------------ source files
def _library_dirs() -> list[Path]:
    """Folders of code that is not the mod's: the standard library, site-packages and se2modkit itself (hashed whole
    as `modkit`)."""
    out = []
    for k in ("stdlib", "platstdlib", "purelib", "platlib"):
        p = sysconfig.get_paths().get(k)
        if p:
            out.append(Path(p).resolve())
    out.append(Path(__file__).resolve().parent.parent)
    out.append(Path(sys.prefix).resolve())
    out.append(Path(sys.base_prefix).resolve())
    return out


def _within(p: Path, dirs: Iterable[Path]) -> bool:
    """`p` is one of `dirs` or inside one (absolute paths, compared as text: fast)."""
    ps = os.fspath(p)
    for d in dirs:
        ds = os.fspath(d)
        if ps == ds or ps.startswith(ds if ds.endswith(os.sep) else ds + os.sep):
            return True
    return False


def _candidates(name: str, level: int, here: Path, search: list[Path]) -> list[Path]:
    parts = [x for x in name.split(".") if x]
    if level:
        base = here.parent
        for _ in range(level - 1):
            base = base.parent
        bases = [base]
    else:
        bases = search
    out = []
    for b in bases:
        if not parts:
            out.append(b / "__init__.py")
        for i in range(1, len(parts) + 1):
            out.append(b.joinpath(*parts[:i], "__init__.py"))
            out.append(b.joinpath(*parts[:i - 1], parts[i - 1] + ".py"))
    return out


def _dynamic(node) -> bool:
    """Whether the node so much as mentions a dynamic-import tool (`_DYNAMIC_WORDS`)."""
    if isinstance(node, ast.Name):
        return node.id in _DYNAMIC_WORDS
    if isinstance(node, ast.Attribute):
        return node.attr in _DYNAMIC_WORDS
    if isinstance(node, ast.alias):
        return any(part in _DYNAMIC_WORDS for part in node.name.split("."))
    if isinstance(node, ast.ImportFrom):
        return any(part in _DYNAMIC_WORDS for part in (node.module or "").split("."))
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in _DYNAMIC_WORDS          # getattr(importlib, "import_module"), getattr(x, "__import__")
    return False


def _is_sys_path(node) -> bool:
    """`sys.path` (or a bare `path` imported from sys)."""
    return (isinstance(node, ast.Attribute) and node.attr == "path" and isinstance(node.value, ast.Name)
            and node.value.id == "sys")


def _path_edit(node) -> bool:
    """A statement or call that changes sys.path: sys.path.insert/append/extend/remove..., sys.path[...] = ...,
    sys.path = ..., sys.path += ..."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _is_sys_path(node.func.value):
        return True
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(_is_sys_path(t) or (isinstance(t, ast.Subscript) and _is_sys_path(t.value)) for t in targets)
    return False


_PARSED: dict[str, tuple[tuple[tuple[str, int], ...], bool, int]] = {}   # content hash -> (imports, dynamic, path)


def _top_level(tree) -> set[int]:
    """ids of the nodes that run when the module is imported (not inside a function or class body)."""
    out: set[int] = set()
    todo = list(tree.body)
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        out.add(id(n))
        todo.extend(ast.iter_child_nodes(n))
    return out


def _imports(body: bytes, filename: str) -> tuple[tuple[tuple[str, int], ...], bool, int]:
    """((module name, level) of every import statement, whether the file imports dynamically or does not parse,
    how it edits sys.path: 0 never, 1 only at module level (done once it is imported), 2 inside a function);
    cached by content."""
    h = hashlib.sha256(body).hexdigest()
    if h in _PARSED:
        return _PARSED[h]
    try:
        tree = ast.parse(body, filename=filename)
    except (SyntaxError, ValueError):
        _PARSED[h] = ((), True, 0)
        return _PARSED[h]
    top = _top_level(tree)
    names: list[tuple[str, int]] = []
    dynamic, path = False, 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [(a.name, 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names += [(base, node.level)] + [(f"{base}.{a.name}" if base else a.name, node.level)
                                             for a in node.names if a.name != "*"]
        if _dynamic(node):
            dynamic = True
        if _path_edit(node):
            path = max(path, 1 if id(node) in top else 2)
    _PARSED[h] = (tuple(names), dynamic, path)
    return _PARSED[h]


@_quiet
def module_closure(roots: Iterable[Path], search: Iterable[Path], *, loaded: set[Path] | None = None,
                   exclude: Iterable[Path] = ()) -> tuple[list[Path], bool]:
    """(the `.py` files `roots` import, transitively, that resolve inside the `search` folders (roots included),
    whether any of them imports dynamically or cannot be parsed). Static: every import statement counts, wherever it
    sits (inside functions too). A sys.path edit counts as dynamic too, unless it sits at module level of a module in
    `loaded` (it already ran, so the folder it adds is among the search folders the caller took from sys.path)."""
    loaded = loaded or set()
    exclude = [Path(x).resolve() for x in exclude]
    search = [Path(s).resolve() for s in search]
    queue = [Path(r).resolve() for r in roots]
    seen: set[Path] = set()
    dynamic = False
    while queue:
        f = queue.pop()
        if f in seen or f.suffix != ".py" or not f.is_file():
            continue
        seen.add(f)
        try:
            names, dyn, path = _imports(f.read_bytes(), str(f))
        except OSError:
            names, dyn, path = (), True, 0
        dynamic |= dyn or path == 2 or (path == 1 and f not in loaded)
        for name, level in names:
            for cand in _candidates(name, level, f, search):
                if cand.is_file():
                    c = cand.resolve()
                    if c not in seen and _within(c, search) and not _within(c, exclude):
                        queue.append(c)
    return sorted(seen), dynamic


def _expand(files: Iterable[Path]) -> set[Path]:
    out: set[Path] = set()
    for f in files:
        f = Path(f)
        if f.is_dir():
            out |= {p for p in f.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
            out.add(f / ".dir")   # an empty folder still counts (as "missing" below)
        else:
            out.add(f)
    return out


@_quiet
def files_digest(files: Iterable[Path], *, relative_to: Path | None = None) -> str:
    """One hash over (path, content) of each file; a folder is every file under it; a missing file counts as
    "missing"."""
    h = hashlib.sha256()
    for p in sorted(_expand(files), key=lambda p: p.as_posix()):
        name = p.as_posix()
        if relative_to is not None:
            try:
                name = p.relative_to(relative_to).as_posix()
            except ValueError:
                pass
        try:
            body = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            body = "missing"
        h.update(f"{name}\0{body}\n".encode())
    return h.hexdigest()


@functools.lru_cache(maxsize=1)
def modkit_digest() -> str:
    """This se2modkit: its version and every file of the package (code, profiles, kit data, compat table)."""
    from .. import __version__
    pkg = Path(__file__).resolve().parent.parent
    files = [p for p in pkg.rglob("*") if p.is_file() and "__pycache__" not in p.parts
             and p.suffix not in (".pyc", ".pyo")]
    return _digest(f"{__version__}\0{files_digest(files, relative_to=pkg)}")


def _script_files() -> list[Path]:
    """The build script Blender runs (`--python FILE`), when there is one."""
    argv = sys.argv
    out = []
    for i, a in enumerate(argv[:-1]):
        if a in ("--python", "-P"):
            out.append(Path(argv[i + 1]))
    return out


_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".mypy_cache",
                        ".pytest_cache", ".ruff_cache"})


def _mod_code(p: Path, lib: list[Path]) -> bool:
    return p.suffix == ".py" and p.is_file() and not _within(p, lib)


def loaded_files(lib: list[Path] | None = None) -> set[Path]:
    """The source files of every loaded module that is not library code (the mod's, the build script's)."""
    lib = _library_dirs() if lib is None else lib
    out = set()
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f:
            p = Path(f).resolve()
            if _mod_code(p, lib):
                out.add(p)
    return out


def path_dirs(lib: list[Path] | None = None) -> list[Path]:
    """Every sys.path folder that is not library code (where the mod's imports resolve, sys.path edits included)."""
    lib = _library_dirs() if lib is None else lib
    out = []
    for e in sys.path:
        try:
            d = Path(e or os.getcwd()).resolve()
        except (OSError, RuntimeError):
            continue
        if d.is_dir() and not _within(d, lib) and d not in out:
            out.append(d)
    return out


def _tree_py(root: Path, lib: list[Path]) -> set[Path]:
    out = set()
    for p in root.rglob("*.py"):
        if not (_SKIP_DIRS & set(p.relative_to(root).parts)) and not _within(p.resolve(), lib):
            out.add(p.resolve())
    return out


class Sources:
    """What the `sources` input of every block of one run shares: the library folders, the loaded mod modules, the
    import search path, and the mod modules no block's static import graph explains (`unexplained`: loaded by some
    means the graph cannot see, so every block hashes them)."""

    @_quiet
    def __init__(self, source_root: Path | None, mod_root: Path | None = None, explained_roots: Iterable[Path] = ()):
        self.lib = _library_dirs()
        self.source_root = source_root.resolve() if source_root else None
        self.mod_root = mod_root.resolve() if mod_root else None
        self.loaded = loaded_files(self.lib) | {p.resolve() for p in _script_files() if p.is_file()}
        roots = self.roots(explained_roots)
        explained, _ = module_closure(roots, self.search(roots), loaded=self.loaded, exclude=self.lib)
        self.unexplained = sorted(self.loaded - set(explained))

    def roots(self, files: Iterable[Path]) -> list[Path]:
        return sorted({p.resolve() for p in (*files, *_script_files()) if _mod_code(Path(p).resolve(), self.lib)})

    def search(self, roots: list[Path]) -> list[Path]:
        return sorted({*(r.parent for r in roots), *([self.source_root] if self.source_root else []),
                       *path_dirs(self.lib)})

    @_quiet
    def digest(self, files: set[Path]) -> str:
        """The `sources` input of one block: its callables' mod files and the build script, everything they import
        (resolved on the model source, their folders and sys.path), and the unexplained loaded modules. When unsure
        (a dynamic import, a sys.path edit that has not run), every `.py` file of the mod tree."""
        roots = self.roots(files)
        found, dynamic = module_closure(roots, self.search(roots), loaded=self.loaded, exclude=self.lib)
        found = {*found, *self.unexplained}
        if dynamic:
            for top in {d for d in (self.mod_root, self.source_root) if d is not None and d.is_dir()}:
                found |= _tree_py(top, self.lib)
        rel = self.mod_root or self.source_root
        # a module this process loaded before its file was edited still runs the old code: unsure, so rebuild
        edited = ":edited:" + os.urandom(16).hex() if any(edited_since_loaded(f) for f in found) else ""
        return files_digest(found, relative_to=rel) + (":dyn" if dynamic else "") + edited


def sources_digest(files: set[Path], source_root: Path | None, mod_root: Path | None = None) -> str:
    """`Sources(...).digest(files)` for one block on its own."""
    return Sources(source_root, mod_root, files).digest(files)


# ------------------------------------------------------------------------------------------------ the inputs
def hooks_view(hooks, spec) -> dict:
    """The hooks configuration a block's build reads: family tables cut to its family, block tables to its name,
    the rest whole (run state left out)."""
    fam, name = getattr(spec, "family", None), getattr(spec, "name", None)
    view = {}
    for f in dataclasses.fields(hooks):
        if not f.compare:
            continue
        v = getattr(hooks, f.name)
        if f.name in _FAMILY_TABLES and isinstance(v, Mapping):
            v = {fam: v[fam]} if fam in v else {}
        elif f.name == "damaged_tris_x" and isinstance(v, Mapping):
            v = {k: v[k] for k in ("default", fam) if k in v}
        elif f.name == "unfractured" and isinstance(v, Mapping):
            v = {k: v[k] for k in (name, fam) if k in v}
        elif f.name == "standins" and isinstance(v, Mapping):
            v = {name: v[name]} if name in v else {}
        elif f.name == "min_share_exempt":
            v = {x for x in v if isinstance(x, tuple) and x[:1] == (fam,)}
        view[f.name] = v
    return view


@_quiet
def project_digest(root: Path | None) -> str:
    """The mod's style profile, style overrides and prefix from its `[tool.se2modkit]` table ("none" outside a
    project)."""
    if root is None:
        return "none"
    from ..project import ProjectError, load_project
    try:
        project = load_project(Path(root))
    except ProjectError as e:
        return "none" if str(e).startswith("No pyproject.toml") else _digest(f"error:{e}")
    mod = project.containing(Path(root))
    if mod is None:
        return "none"
    return _digest(canon({"profile": mod.profile, "style": mod.style, "prefix": mod.prefix}))


ADDON = "vrage_tools"   # Keen's VRAGE Tools add-on (export)


@functools.lru_cache(maxsize=1)
def addon_digest() -> str:
    """Keen's VRAGE Tools add-on: its bl_info version and the content of its folder ("none" outside Blender or
    when it is not installed)."""
    try:
        import addon_utils
    except ImportError:
        return "none"
    try:
        mods = list(addon_utils.modules())
    except Exception:  # noqa: BLE001 (an add-on listing that breaks: unsure, rebuild)
        return _unstable()
    for m in mods:
        if m.__name__ == ADDON or m.__name__.endswith("." + ADDON):
            folder = Path(m.__file__).resolve().parent
            version = getattr(m, "bl_info", {}).get("version")
            return _digest(f"{version!r}\0{files_digest([folder], relative_to=folder)}")
    return "none"


def runtime_digest(prefix: str, environ: Mapping[str, str] | None = None) -> str:
    """Python, platform, Blender and VRAGE Tools versions, the game version and Mod SDK build id (`se2mod` passes them
    as SE2MODKIT_GAME_VERSION / SE2MODKIT_SDK_BUILDID; a build started any other way discovers them itself, so the
    key is the same either way) and the hashed environment."""
    environ = os.environ if environ is None else environ
    try:
        import bpy
        blender = bpy.app.version_string
    except ImportError:
        blender = None
    heads = ("SE2MODKIT_", "SE2_") + ((prefix,) if prefix else ())
    env = {}
    for k in sorted(environ):
        head = next((h for h in heads if k.startswith(h)), None)
        if head is not None and k[len(head):] not in _ENV_IGNORED and k not in _VERSION_VARS:
            env[k] = environ[k]
    game, sdk = (environ.get(v) for v in _VERSION_VARS)
    if game is None or sdk is None:      # a build started outside se2mod: the same values, discovered here
        found = discovered_versions()
        game, sdk = game if game is not None else found[0], sdk if sdk is not None else found[1]
    return _digest(canon({"python": list(sys.version_info[:3]), "platform": sys.platform, "blender": blender,
                          "addon": addon_digest(), "game": game, "sdk": sdk, "env": env}))


_VERSION_VARS = ("SE2MODKIT_GAME_VERSION", "SE2MODKIT_SDK_BUILDID")


@functools.lru_cache(maxsize=1)
def discovered_versions() -> tuple[str, str]:
    """(game version, Mod SDK build id) as `se2mod` would pass them, found by the same discovery ("none" for what is
    not installed); used when a build script runs outside se2mod, so both launch paths give the same key."""
    from ..env.core import discover_env
    try:
        with paused():
            e = discover_env()
    except Exception:  # noqa: BLE001 (discovery that breaks: unsure, so the key cannot match a recorded one)
        return _unstable(), _unstable()
    return str(e.game_version or "none"), str(e.sdk_buildid or "none")


def block_inputs(hooks, spec, callables: Iterable[Callable | None], *, data: Iterable[Path] = (),
                 environ: Mapping[str, str] | None = None, sources: Sources | None = None) -> dict[str, str]:
    """The named input hashes of one block (see the module docstring); `sources` is the run's shared `Sources`."""
    files: set[Path] = set()
    out: dict[str, str] = {}
    for name, value in (("runner", [f for f in callables if f is not None]), ("spec", spec),
                        ("hooks", hooks_view(hooks, spec))):
        try:
            out[name] = _digest(canon(value, files))
        except Unstable:
            out[name] = _unstable()
    paths = getattr(hooks, "paths", None)
    source_root = getattr(paths, "source_model", None)
    if sources is None:
        root = getattr(paths, "root", None)
        sources = Sources(Path(source_root) if source_root else None, Path(root) if root else None, files)
    out["sources"] = sources.digest(files)
    out["modkit"] = modkit_digest()
    out["project"] = project_digest(getattr(paths, "root", None))
    out["runtime"] = runtime_digest(getattr(hooks, "prefix", ""), environ)
    out["data"] = files_digest(data)
    return out


def stale_reasons(inputs: Mapping[str, str], fragment: dict | None) -> list[str]:
    """Why a block with these inputs needs a build given its last fragment: [] when it is fresh."""
    if not fragment:
        return ["never built"]
    if fragment.get("version") != FRAGMENT_VERSION or not isinstance(fragment.get("inputs"), dict):
        return ["old fragment"]
    old = fragment["inputs"]
    changed = sorted(k for k in set(inputs) | set(old) if inputs.get(k) != old.get(k) or
                     str(inputs.get(k, "")).startswith("unstable:"))
    if not changed and fragment.get("key") != key(inputs):
        changed = ["key"]
    return changed


# ------------------------------------------------------------------------------------------------ files a build reads
# `sys.addaudithook` sees every open (builtins.open, io.open_code for imports, os.open) and every directory listing
# (os.listdir, os.scandir: glob, Path.iterdir/glob, os.walk). Recorded: each file with its content hash ("absent" when
# it was opened but did not exist), each listed folder with a hash of its sorted entry names. A later run that finds one
# different rebuilds the block. Attribution:
#   * read while a block builds along an own path (every frame between the read and the runner's call into the
#     builder, `call_builder`, is the builder's own code, `own_code`, or I/O plumbing, `_PLUMBING`): that block only
#     (a data file only XX_A's builder reads never rebuilds XX_B);
#   * read while a block builds with any other frame on that path (a helper, even one in the builder's file, a shared
#     module, a cache, a wrapper around the builder, a shared helper the builder called back into itself through,
#     code compiled from a string, another library helper), or by the catalogue, hooks, and
#     code a block loads lazily (a module first imported while a block builds, by import event so a .pyc load counts,
#     and the files its module-level code reads while it is being imported): that block and every block the process
#     builds after it, which may use what was read without reading it again (`lazy_imports`);
#   * read outside any block (import time, the catalogue, hooks): every block (`process_reads`).
# A read is hashed when it is made (what the code that read it saw), except a file a block wrote itself, hashed at the
# block's end (what it left: a rewrite in place settles). A read outside any block keeps what it saw when the mod's code
# made it, when the run's build wrote the path since, and for every read of a build process's first run (import time):
# a table loaded at import, or a folder listed then, holds that content however a block, in this process or in another
# parallel worker, changes it later, and that wins over the block's own record of the same file. A write made later in
# the run by a block or worker leaves the reader with the old digest, so it rebuilds on the next run. In a process that
# runs the build more than once, a read the mod's code made in an earlier run and not again (a module-level table, a
# cache) is keyed as unknown, and a module whose source changed since it was loaded makes the sources input unstable:
# such a block rebuilds rather than being taken for fresh. Reads no mod code is behind (nothing but library frames on
# the stack) are not the build's and are not recorded outside a block.
# Left out: Python's own library and site-packages, se2modkit itself, Blender's install and user folders, the run's
# private temp folder (`run_scope`; the system temp folder is not left out), the build's outputs (`Outputs`: models,
# report, fragments and the mod's declared `Runner(outputs=...)` globs; a path the build merely writes is not one),
# bytecode, and listings the import system's finders make (imports are the `sources` input; a module's own listing
# made while it is imported counts).
# Blender's C-level loaders are not audited: `bpy.data.libraries.load` is wrapped (`install_bpy_wrappers`); for images,
# imports and appends use `pipeline.inputs`. Not visible at all: a bare exists()/stat() check, environment variables
# outside the hashed prefixes, reads done by C extensions (sqlite, numpy.load's C side is fine: it opens through Python)
# -- name those with `Runner(inputs=...)`.
_RECORDERS: list[set[str]] = []
_IMPORT_BASE: list[int] = []      # per recorder: the import-system frames on the stack when it started
_OWN: list = []                   # per recorder: the block's own code (`OwnCode`)
_CALLS: list[list] = []           # per recorder: the frames of the runner's calls into the builder (`call_builder`)
_CTX: list[tuple] = []            # per recorder: how its reads are keyed (`_context`)
_LAZY_DIGESTS: dict[str, str] = {}  # this run: a lazy read's digest when it was first read (what a cache holds)
_PROCESS: set[str] = set()        # read outside any block's build
_PROCESS_SEEN: dict[str, tuple[int, object, str | None]] = {}  # such a read -> (generation, what it saw: a file's
                                  # digest or a folder's entry names, the mod code that made it), at its first read of
                                  # a generation
_GENERATION = [0]                 # bumped when a run ends: the reads before a run and during it are one generation
_LAZY_READS: set[str] = set()     # read while some block built, but not by its own code: .py files, what module-level
                                  # code read at import, reads made by shared helpers (catalogue, hooks, a cache)
_LAZY_SEEN: dict[str, tuple[int, str | None]] = {}  # such a read -> (generation of its last read, the code that made it)
_LOADED: dict[str, str] = {}      # a mod source file -> its content hash when its module was last loaded
_LAZY_MODULES: set[str] = set()   # modules first imported while some block built (a .pyc load opens no .py)
_WRITTEN: set[str] = set()        # paths opened for writing (or renamed onto) while a run was active
_RUNS = [0]                       # >0 while a run is active (`run_scope`)
_PRIVATE_TMP: list[str] = []      # every private temp folder this process's runs used
_HOOKED = False
_WRITE_FLAGS = os.O_WRONLY | getattr(os, "O_CREAT", 0) | getattr(os, "O_TRUNC", 0) | getattr(os, "O_APPEND", 0)
READ_EVENT = "se2modkit.read"     # sys.audit(READ_EVENT, path): a read the hook cannot see (pipeline.inputs)
WRITTEN_FILE = ".written.json"    # in a worker's fragment folder: the paths that worker wrote (for the merge run)


def _import_depth() -> int:
    """How many import-system frames are on the stack (more than when the block started: module code is running)."""
    n, f = 0, sys._getframe(1)
    while f is not None:
        fn = f.f_code.co_filename
        if "importlib" in fn and "_bootstrap" in fn:
            n += 1
        f = f.f_back
    return n


_FRAME_LIB: dict[str, bool] = {}   # code file -> library code (stdlib, site-packages, se2modkit, Blender's own)


def _library_file(name: str) -> bool:
    if name not in _FRAME_LIB:
        if name.startswith("<"):                    # <frozen importlib._bootstrap>, <string>
            _FRAME_LIB[name] = True
        else:
            with paused():
                dirs = _frame_lib_dirs()
            p = os.path.abspath(name)
            _FRAME_LIB[name] = _within(Path(p), dirs)
    return _FRAME_LIB[name]


@functools.lru_cache(maxsize=1)
def _frame_lib_dirs() -> tuple[Path, ...]:
    out = list(_library_dirs())
    try:
        import bpy
        out.append(Path(bpy.app.binary_path).resolve().parent)
        for kind in ("LOCAL", "SYSTEM", "USER"):
            rp = bpy.utils.resource_path(kind)
            if rp:
                out.append(Path(rp).resolve())
    except (ImportError, AttributeError):
        pass
    return tuple(out)


# Code between a builder and a read that only moves bytes: open/io, codecs and encodings, os and os.path, pathlib (with
# the glob and fnmatch it lists folders through), json, tomllib, csv, and se2modkit's own read reporting
# (pipeline.inputs, this module's hook and bpy wrapper). It keeps nothing between calls, so a read made through it is
# still the builder's own.
_PLUMBING = frozenset({"io", "_pyio", "codecs", "encodings", "os", "posixpath", "ntpath", "genericpath", "stat",
                       "pathlib", "glob", "fnmatch", "json", "tomllib", "csv"})
_PLUMBING_FILES: dict[str, bool] = {}


def _plumbing(name: str) -> bool:
    """Whether a code file is I/O plumbing (`_PLUMBING`): the standard library's (frozen or not) or se2modkit's
    read reporting. Anything else (mod code, other library code, code compiled from a string) is not."""
    if name in _PLUMBING_FILES:
        return _PLUMBING_FILES[name]
    out = False
    if name.startswith("<frozen ") and name.endswith(">"):
        out = name[8:-1].split(".")[0] in _PLUMBING
    elif not name.startswith("<"):
        p = os.path.abspath(name)
        here = os.path.dirname(os.path.abspath(__file__))
        if p in (os.path.join(here, "inputs.py"), os.path.abspath(__file__)):
            out = True
        else:
            for k in ("stdlib", "platstdlib"):
                d = sysconfig.get_paths().get(k)
                if d and p.startswith(os.path.abspath(d) + os.sep):
                    top = p[len(os.path.abspath(d)) + 1:].split(os.sep)[0]
                    out = top.removesuffix(".py") in _PLUMBING
                    break
    _PLUMBING_FILES[name] = out
    return out


def _own_path(own: OwnCode) -> bool:
    """Whether the read was made along the builder's own path: walking out from the read, every frame up to the
    runner's call into the builder (`call_builder`) is the builder's own code or I/O plumbing, and the frame that call
    entered is the builder's root. Then the read is the block's alone. Any other frame anywhere on that path (shared
    mod code, a wrapper around the builder, a shared helper that called back into the builder, a helper compiled from
    a string, a standard-library helper that may keep what it read) makes it count for later blocks too, as does a
    read with no runner call below it."""
    if own.root is None or not _CALLS or not _CALLS[-1]:
        return False
    calls = _CALLS[-1]
    f, prev = sys._getframe(1), None
    while f is not None:
        if any(f is c for c in calls):
            return prev is not None and prev.f_code is own.root
        c = f.f_code
        if id(c) not in own.ids and not _plumbing(c.co_filename):
            return False
        f, prev = f.f_back, f
    return False


def call_builder(fn, *args, **kwargs):
    """Call a block's builder the way the runner does: `fn(*args, **kwargs)`, with this call's frame recorded for the
    block being recorded, so `_own_path` knows where the builder's own path starts."""
    calls = _CALLS[-1] if _CALLS else None
    if calls is not None:
        calls.append(sys._getframe())
    try:
        return fn(*args, **kwargs)
    finally:
        if calls is not None:
            calls.pop()


def _caller_frame():
    """The nearest frame that is not library code (the mod code that made the read), or None."""
    f = sys._getframe(1)
    while f is not None:
        if not _library_file(f.f_code.co_filename):
            return f
        f = f.f_back
    return None


def _caller_file() -> str | None:
    """The code file of the nearest frame that is not library code, or None."""
    f = _caller_frame()
    return os.path.abspath(f.f_code.co_filename) if f is not None else None


def _loaded_source(entry: str) -> None:
    """A module's code is being loaded from `entry` (its .py, or its cached .pyc): remember what its source holds now,
    so a later run of this process can tell that the module running is not what is on disk any more."""
    src = entry
    if entry.endswith((".pyc", ".pyo")):
        try:
            import importlib.util
            src = importlib.util.source_from_cache(entry)
        except (ValueError, NotImplementedError):
            return
    with paused():
        if _library_file(src):
            return
        try:
            _LOADED[os.path.realpath(src)] = _sha(Path(src).read_bytes())
        except OSError:
            pass


def edited_since_loaded(path) -> bool:
    """Whether the mod source file `path` changed since this process last loaded its module (the module in memory
    is then not what the file holds: a long-lived process, such as a Blender session that runs the build twice)."""
    real = os.path.realpath(os.fspath(path))
    if real not in _LOADED:
        return False
    with paused():
        try:
            return _sha(Path(real).read_bytes()) != _LOADED[real]
        except OSError:
            return True


def _note(entry: str) -> None:
    if entry.endswith((".py", ".pyc", ".pyo")):
        _loaded_source(entry)
    if not _RECORDERS:
        # a read no mod code is behind (the interpreter's, Blender's or a test runner's own housekeeping) is not part
        # of the build: every read the build script causes has the script's frame on the stack
        caller = _caller_file()
        if caller is None:
            return
        _PROCESS.add(entry)
        seen = _PROCESS_SEEN.get(entry)
        if (seen is None or seen[0] != _GENERATION[0]) and not entry.endswith((".py", ".pyc", ".pyo")):
            with paused():
                if not _library_file(entry.removeprefix("dir:")):
                    _PROCESS_SEEN[entry] = (_GENERATION[0], _snapshot(entry), caller)
        return
    # per block only when the read comes straight from the block's own builder code; from anywhere else (a shared
    # helper, the catalogue, hooks, a cached loader, module code running at import) it counts for this block and every
    # later block of the process, which may use what was read without reading it again
    frame = _caller_frame()
    caller = os.path.abspath(frame.f_code.co_filename) if frame is not None else None
    lazy = entry.endswith(".py") or _import_depth() > _IMPORT_BASE[-1] or not _own_path(_OWN[-1])
    if lazy:
        _LAZY_READS.add(entry)
        _LAZY_SEEN[entry] = (_GENERATION[0], caller)
    for r in _RECORDERS:
        r.add(entry)
    # the digest of what is read now (a write made later, by another block or worker, then shows up as a change)
    with paused():
        k, digest = _keyed(entry, _CTX[-1])
    if k is not None and digest is not None:
        for r in _RECORDERS:
            r.digests.setdefault(k, digest)
        if lazy:
            _LAZY_DIGESTS.setdefault(k, digest)


def _wrote(entry: str) -> None:
    if _RUNS[0]:
        _WRITTEN.add(entry)
    for r in _RECORDERS:
        r.written.add(entry)


_IMPORT_MACHINERY = ("importlib/_bootstrap.py", "importlib/_bootstrap_external.py", "zipimport.py", "pkgutil.py")


def _import_machinery(name: str) -> bool:
    """Whether a code file is the import system's own: its finders and loaders (frozen or not), zipimport, pkgutil
    and the package metadata finder (each lists sys.path folders to find a module or a distribution)."""
    if name.startswith(("<frozen importlib", "<frozen zipimport")):
        return True
    n = name.replace("\\", "/")
    return _library_file(name) and (n.endswith(_IMPORT_MACHINERY) or "/importlib/metadata/" in n)


def _from_import_system() -> bool:
    """Whether the listing being audited was made by the import system itself: the code that called os.listdir or
    os.scandir is a finder of the import machinery. Only that immediate caller counts: a module whose top-level code
    lists a folder while it is being imported has import frames further down the stack, and its listing is its own."""
    f = sys._getframe(2)
    return f is not None and _import_machinery(f.f_code.co_filename)


def _audit(event, args):
    if _PAUSED[0]:
        return
    try:
        if event == "import":
            if _RECORDERS and args and isinstance(args[0], str):   # first needed while a block builds
                _LAZY_MODULES.add(args[0])
                for r in _RECORDERS:
                    r.add("module:" + args[0])
        elif event == "open":
            path, mode, flags = (tuple(args) + (None, None))[:3]
            if path is None or isinstance(path, int):
                return
            p = os.path.abspath(os.fsdecode(path))
            if (mode is not None and any(c in str(mode) for c in "wax")) or (
                    mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS):    # "r+" still reads
                _wrote(p)
                return
            _note(p)
        elif event == "os.rename":           # os.rename and os.replace (an atomic write lands here)
            if len(args) > 1 and not isinstance(args[1], int):
                _wrote(os.path.abspath(os.fsdecode(args[1])))
        elif event in ("os.listdir", "os.scandir"):
            path = args[0] if args else None
            if isinstance(path, int) or _from_import_system():
                return
            _note("dir:" + os.path.abspath(os.fsdecode(path if path is not None else ".")))
        elif event == READ_EVENT and args:
            _note(os.path.abspath(os.fsdecode(args[0])))
    except Exception:  # noqa: BLE001,S110 (an audit hook must never break the build)
        pass


def install_read_hook() -> None:
    """Start recording reads (once per process; audit hooks cannot be removed, so this one is cheap and never
    raises). Inside Blender it also wraps `bpy.data.libraries.load`."""
    global _HOOKED
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED = True
    install_bpy_wrappers()


def install_bpy_wrappers() -> bool:
    """Wrap `bpy.data.libraries.load` (a Python method of Blender's, so it can be wrapped) to report its file as a
    read. Blender's other loaders are C functions: use `pipeline.inputs` for them. Returns whether bpy is there."""
    try:
        import bpy
    except ImportError:
        return False
    t = bpy.types.BlendDataLibraries
    if not getattr(t.load, "_se2_read", False):
        orig = t.load

        def load(*a, **k):
            # Blender hands the method over unbound (its collection proxy is not a BlendDataLibraries instance), and
            # the original only binds through that proxy: put it back for the one lookup, then wrap again.
            rest = a[1:] if a and isinstance(a[0], t) else a
            filepath = rest[0] if rest else k.get("filepath")
            if filepath:
                sys.audit(READ_EVENT, os.path.abspath(bpy.path.abspath(os.fsdecode(filepath))))
            t.load = orig
            try:
                fn = bpy.data.libraries.load
            finally:
                t.load = load
            return fn(*rest, **k)
        load._se2_read = True
        t.load = load
    return True


class Seen(set):
    """What one block read ("path", "dir:path", "module:name"), with `digests` ({entry: digest when first read}) and
    `written` (the paths it opened for writing)."""

    def __init__(self):
        super().__init__()
        self.digests: dict[str, str] = {}
        self.written: set[str] = set()


@contextlib.contextmanager
def recording(own: Iterable = (), *, outputs=(), mod_dir: Path | None = None):
    """Record what the block reads; yields a `Seen`. `own` is the block's own code (`own_code`, or code objects, the
    first the root): a read made along an own path (`_own_path`: only the builder's own code and I/O plumbing between
    the runner's `call_builder` and the read) counts for this block only; any other read counts for this block and
    every later one of the process (`lazy_imports`). Each read is hashed as it is made (`outputs` and `mod_dir` as for
    `read_hashes`), so `block_reads` keys a file by what the block saw, not by what a later write left there."""
    install_read_hook()
    with paused():
        ctx = _context(outputs, mod_dir)
    seen = Seen()
    _RECORDERS.append(seen)
    _IMPORT_BASE.append(_import_depth())
    _OWN.append(OwnCode.of(own))
    _CALLS.append([])
    _CTX.append(ctx)
    try:
        yield seen
    finally:
        _RECORDERS.remove(seen)
        _IMPORT_BASE.pop()
        _OWN.pop()
        _CALLS.pop()
        _CTX.pop()


class OwnCode:
    """A block's own builder code (`own_code`): the root code object (the builder function's) and every code object
    nested in it, compared by identity (two code objects can be equal while coming from different files)."""

    def __init__(self, root=None, codes=()):
        self.root = root
        self.codes = tuple(dict.fromkeys([*([root] if root is not None else []), *codes]))
        self.ids = frozenset(id(c) for c in self.codes)      # the objects stay alive in `codes`

    @classmethod
    def of(cls, own) -> OwnCode:
        """`own` as an OwnCode: one already, or code objects (the first is the root)."""
        if isinstance(own, OwnCode):
            return own
        codes = list(own)
        return cls(codes[0] if codes else None, codes)

    def __contains__(self, code) -> bool:
        return id(code) in self.ids

    def __iter__(self):
        return iter(self.codes)

    def __len__(self) -> int:
        return len(self.codes)

    def __eq__(self, other) -> bool:
        return isinstance(other, OwnCode) and other.ids == self.ids and id(other.root) == id(self.root)

    def __hash__(self) -> int:
        return hash(self.ids)


def own_code(fn) -> OwnCode:
    """The block's own code: the builder function, unwrapped through `__wrapped__` only (`functools.wraps`
    decorators), and every function, lambda or generator nested inside it. No closure is followed: a function the
    builder (or a wrapper) closes over is other code. When any layer of that chain, the outer object included, is not
    a plain Python function (a partial, a bound method, a callable object, a `functools.cache` or `lru_cache`
    wrapper, which can hand one block's result to another block without running the builder), the builder has no
    own code, so all its reads count the shared way. A read counts for one block only along an own path
    (`_own_path`), which a Python wrapper's frame breaks too: the unwrapping names the builder, but the wrapper is
    still other code."""
    f = fn
    for _ in range(32):
        if not inspect.isfunction(f):
            return OwnCode()
        w = getattr(f, "__wrapped__", None)
        if w is None:
            break
        f = w
    else:
        return OwnCode()
    code = f.__code__
    out, stack = [], [code]
    while stack:
        c = stack.pop()
        if all(c is not o for o in out):
            out.append(c)
            stack.extend(k for k in c.co_consts if inspect.iscode(k))
    return OwnCode(code, out)


def code_files(fn) -> set[str]:
    """The source file of the block's own code (`own_code`), for messages and tests; empty when it has none."""
    return {os.path.abspath(c.co_filename) for c in own_code(fn)}


_TMP_VARS = ("TMPDIR", "TEMP", "TMP")


@contextlib.contextmanager
def run_scope():
    """One build run: forgets the memoised digests, records the paths the process opens for writing (`written`), and
    points TMPDIR / TEMP / TMP and `tempfile` at a private folder made for the run (removed afterwards). Only that
    folder is left out of the reads, never the system temp folder, so a direct run and a `build-models` run treat a
    file in /tmp the same way. Yields the private folder."""
    import shutil
    import tempfile
    install_read_hook()
    new_run()
    _WRITTEN.clear()
    _LAZY_DIGESTS.clear()
    with paused():
        d = tempfile.mkdtemp(prefix="se2modkit-run-")
    for form in (os.path.abspath(d), os.path.realpath(d)):
        if form not in _PRIVATE_TMP:
            _PRIVATE_TMP.append(form)
    saved_env, saved_tempdir = {k: os.environ.get(k) for k in _TMP_VARS}, tempfile.tempdir
    for k in _TMP_VARS:
        os.environ[k] = d
    tempfile.tempdir = d
    _RUNS[0] += 1
    try:
        yield Path(d)
    finally:
        _RUNS[0] -= 1
        _GENERATION[0] += 1
        tempfile.tempdir = saved_tempdir
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        with paused():
            shutil.rmtree(d, ignore_errors=True)


def written() -> set[str]:
    """The paths the process opened for writing (or renamed a file onto) during the current run. They stay inputs
    (a file the build writes and reads is keyed by its content like any other); the merge run of parallel workers
    uses them to tell the build's own writes from an edit made while it ran (`reads_changed(ignore=...)`)."""
    return set(_WRITTEN)


def _glob_re(pattern: str) -> re.Pattern:
    """A mod-relative glob as a regex on "/"-joined relative paths: `*` and `?` stay inside one folder, `**` crosses
    folders."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def output_glob(pattern: str) -> str:
    """`pattern` checked and normalised ("/"-separated, relative to the mod); raises ValueError otherwise."""
    p = str(pattern).replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    p = p.rstrip("/")
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
        raise ValueError(f"Runner outputs= takes globs relative to the mod folder (like \"Source/Model/*.log\"), "
                         f"not {pattern!r}.")
    return p


class Outputs:
    """What a build writes, so never an input: folders (a path inside one is covered: models, report, fragments),
    exact paths (`files`) and the mod's declared `Runner(outputs=...)` globs (relative to `root`; a glob that names a
    folder covers everything under it). A path the build merely opened for writing is not one of them."""

    def __init__(self, dirs: Iterable[Path] = (), files: Iterable[str] = (), globs: Iterable[str] = (),
                 root: Path | None = None):
        self.dirs = sorted({Path(d).resolve() for d in dirs})
        fs = set()
        for f in files:
            fs.add(os.path.abspath(f))
            with contextlib.suppress(OSError, RuntimeError):
                fs.add(os.path.realpath(f))
        self.files = frozenset(fs)
        self.globs = tuple(sorted({output_glob(g) for g in globs}))
        self._res = [_glob_re(g) for g in self.globs]
        self.roots = []
        if root is not None and self.globs:
            self.roots = sorted({os.path.abspath(root), os.path.realpath(root)})
        self.token = (tuple(map(str, self.dirs)), self.files, self.globs, tuple(self.roots))

    def covers(self, path) -> bool:
        s = os.fspath(path)
        if s in self.files or _within(Path(s), self.dirs):
            return True
        for root in self.roots:
            if s.startswith(root + os.sep):
                parts = s[len(root) + 1:].split(os.sep)
                for i in range(1, len(parts) + 1):
                    rel = "/".join(parts[:i])
                    if any(r.fullmatch(rel) for r in self._res):
                        return True
        return False


def _as_outputs(outputs) -> Outputs:
    return outputs if isinstance(outputs, Outputs) else Outputs(dirs=outputs or ())


def _ignored_dirs() -> list[Path]:
    out = list(_library_dirs())
    try:
        import bpy
        out.append(Path(bpy.app.binary_path).resolve().parent)
        for kind in ("LOCAL", "SYSTEM", "USER"):
            rp = bpy.utils.resource_path(kind)
            if rp:
                out.append(Path(rp).resolve())
    except (ImportError, AttributeError):
        pass
    out += [Path(d) for d in _PRIVATE_TMP]
    out += [Path(x).resolve() for x in ("/proc", "/sys", "/dev")]
    return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@_quiet
def entry_digest(entry: str, outputs=()) -> str | None:
    """The current digest of a recorded read: a file's content hash, a folder's sorted entry names (outputs,
    __pycache__ and temp files left out), "absent" when it does not exist; None when it is not a recordable input
    (a file key that is a folder)."""
    outputs = _as_outputs(outputs)
    snap = _snapshot(entry)
    return _names_digest(entry[4:], snap, outputs) if isinstance(snap, tuple) else snap


def _snapshot(entry: str) -> str | tuple[str, ...] | None:
    """What a read sees now: a file's content hash, a folder's entry names (a tuple), "absent", "unreadable"; None
    for a file key that is a folder."""
    if entry.startswith("dir:"):
        try:
            return tuple(os.listdir(entry[4:]))
        except (FileNotFoundError, NotADirectoryError):
            return "absent"
        except OSError:
            return "unreadable"
    p = Path(entry)
    if p.is_dir():
        return None
    try:
        return _sha(p.read_bytes())
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"


def _names_digest(d: str, names: Iterable[str], outputs: Outputs) -> str:
    """A folder listing's digest: its sorted entry names, outputs, __pycache__ and temp files left out."""
    keep = sorted(n for n in names if n != "__pycache__" and not (n.startswith(".") and n.endswith(".tmp"))
                  and not outputs.covers(os.path.join(d, n)) and not outputs.covers(Path(d, n).resolve()))
    return "names:" + _sha("\n".join(keep).encode("utf-8", "surrogateescape"))


_CLASSIFIED: dict[tuple, str | None] = {}   # (raw entry, context) -> entry key or None (not an input)
_DIGESTS: dict[tuple, str | None] = {}      # (entry, outputs, mtime_ns, size, inode) -> digest, within this process
_CONTEXTS: dict[tuple, int] = {}            # a (mod dir, outputs, ignored folders) context -> a small id for the memos


def new_run() -> None:
    """Forget the memoised classifications and digests (a run starts: files may have changed since the last one)."""
    _CLASSIFIED.clear()
    _DIGESTS.clear()
    _CONTEXTS.clear()


def _context_id(ctx: tuple) -> int:
    return _CONTEXTS.setdefault(ctx, len(_CONTEXTS))


def _classify(raw: str, ignored: list[Path], outputs: Outputs, mod_dir: Path | None) -> str | None:
    is_dir = raw.startswith("dir:")
    body = raw[4:] if is_dir else raw
    if body.endswith((".pyc", ".pyo")):
        return None
    a = Path(os.path.abspath(body))
    r = a.resolve()
    if outputs.covers(a) or outputs.covers(r) or "__pycache__" in r.parts:
        return None
    inside = mod_dir is not None and (_within(a, [mod_dir]) or _within(r, [mod_dir]))
    if not inside and (_within(r, ignored) or _within(a, ignored)):
        return None
    return ("dir:" if is_dir else "") + a.as_posix()


def _cached_digest(entry: str, outputs: Outputs, oid: int) -> str | None:
    """`entry_digest`, memoised on the file's (or folder's) mtime, size and inode."""
    try:
        st = os.stat(entry[4:] if entry.startswith("dir:") else entry)
        k = (entry, oid, st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return entry_digest(entry, outputs)
    if k not in _DIGESTS:
        _DIGESTS[k] = entry_digest(entry, outputs)
    return _DIGESTS[k]


def _context(outputs=(), mod_dir: Path | None = None) -> tuple:
    """(outputs, mod dir, ignored folders, classification memo id, digest memo id) for `_keyed`."""
    ignored = _ignored_dirs()
    outputs = _as_outputs(outputs)
    mod_dir = Path(mod_dir).resolve() if mod_dir is not None else None
    return (outputs, mod_dir, ignored, _context_id((str(mod_dir), outputs.token, tuple(ignored))),
            _context_id(("outputs", outputs.token)))


def _keyed(raw: str, ctx: tuple) -> tuple[str | None, str | None]:
    """(entry key, current digest) of a recorded read; (None, None) when it is not an input."""
    outputs, mod_dir, ignored, cid, oid = ctx
    if raw.startswith("module:"):
        return None, None
    try:
        ck = (raw, cid)
        if ck not in _CLASSIFIED:
            _CLASSIFIED[ck] = _classify(raw, ignored, outputs, mod_dir)
        entry = _CLASSIFIED[ck]
        if entry is None:
            return None, None
        return entry, _cached_digest(entry, outputs, oid)
    except (OSError, ValueError, RuntimeError):
        return None, None


@_quiet
def read_hashes(paths: Iterable[str], *, outputs=(), mod_dir: Path | None = None) -> dict[str, str]:
    """{entry: digest} of the recorded reads that are inputs (see the section comment): files and folders outside
    library, Blender and the run's temp folders (a path inside `mod_dir` always counts), not covered by `outputs`
    (an `Outputs`, or folders), no bytecode."""
    ctx = _context(outputs, mod_dir)
    out = {}
    for raw in sorted(_module_files(paths)):
        entry, digest = _keyed(raw, ctx)
        if entry is not None and digest is not None:
            out[entry] = digest
    return out


@_quiet
def block_reads(seen: Seen, *, outputs=(), mod_dir: Path | None = None) -> dict[str, str]:
    """`read_hashes` of what one block read, each keyed by its digest when the block read it, except for files the
    block wrote itself, keyed by what it left there (a file it rewrites in place settles). A write made after the read
    by a later block or another worker leaves the old digest, so the block rebuilds on the next run."""
    out = read_hashes(seen, outputs=outputs, mod_dir=mod_dir)
    own = path_forms(getattr(seen, "written", ()))
    for e, digest in getattr(seen, "digests", {}).items():
        if e in out and not touched_by(e, own):
            out[e] = digest
    return out


@_quiet
def input_paths(paths: Iterable[str], *, outputs=(), mod_dir: Path | None = None) -> set[str]:
    """The `paths` that would count as inputs if read (not covered by `outputs`, not in library or temp folders): of
    the paths a worker wrote, those the merge run must know about (`reads_changed(ignore=...)`, `rewritten`)."""
    ignored = _ignored_dirs()
    outputs = _as_outputs(outputs)
    mod_dir = Path(mod_dir).resolve() if mod_dir is not None else None
    out = set()
    for p in paths:
        try:
            if _classify(p, ignored, outputs, mod_dir) is not None:
                out.add(os.path.abspath(p))
        except (OSError, ValueError, RuntimeError):
            continue
    return out


def _module_files(paths: Iterable[str]) -> set[str]:
    """`paths` with each "module:NAME" entry replaced by that module's source file."""
    out = set()
    for p in paths:
        if p.startswith("module:"):
            f = getattr(sys.modules.get(p[7:]), "__file__", None)
            if f:
                out.add(f[:-1] if f.endswith((".pyc", ".pyo")) else f)
        else:
            out.add(p)
    return out


def _unknown() -> str:
    """The digest of a read whose value cannot be told any more: it never matches, so the block rebuilds."""
    return "unknown:" + os.urandom(16).hex()


def _by_mod(caller: str | None, mod: Path | None) -> bool:
    """Whether the code file that made a read lies in the mod folder."""
    return caller is not None and mod is not None and (_within(Path(caller), [mod]) or
                                                       _within(Path(os.path.realpath(caller)), [mod]))


def lazy_imports(*, outputs=(), mod_dir: Path | None = None, written: Iterable[str] = ()) -> dict[str, str]:
    """What some block of this process read through code other than its own builder code (mod code it loaded lazily,
    what that code read at import, what a shared helper or a cached loader read): a later block may use it without
    reading it again, so every block built after that counts it, keyed by its digest when it was first read this run
    (what a cache holds), except for the `written` paths (the current block wrote them: what it left there).
    In a process that runs more than one build: a read the mod's code made in an earlier run and not again in this one
    is keyed as unknown (a cache may still hold what it read then, which cannot be told now), so the blocks rebuild
    on the next run; a module whose source changed since it was loaded is unknown too."""
    out = read_hashes([*list(_LAZY_READS), *("module:" + m for m in list(_LAZY_MODULES))], outputs=outputs,
                      mod_dir=mod_dir)
    own = path_forms(written)
    for e, digest in list(_LAZY_DIGESTS.items()):
        if e in out and not touched_by(e, own):
            out[e] = digest
    mod = Path(mod_dir).resolve() if mod_dir is not None else None
    for raw, (gen, caller) in list(_LAZY_SEEN.items()):
        is_dir = raw.startswith("dir:")
        k = ("dir:" if is_dir else "") + Path(os.path.abspath(raw[4:] if is_dir else raw)).as_posix()
        if k in out and k not in _LAZY_DIGESTS and gen != _GENERATION[0] and not k.endswith(".py") \
                and not touched_by(k, own) and _by_mod(caller, mod):
            out[k] = _unknown()
    for k in out:
        if k.endswith(".py") and edited_since_loaded(k):
            out[k] = _unknown()
    return out


@_quiet
def process_reads(*, outputs=(), mod_dir: Path | None = None, pinned: set[str] | None = None) -> dict[str, str]:
    """What the process read outside any block's build (import time, the catalogue, hooks), same rule as
    `read_hashes`, Python sources left out: importing the mod's modules is the `sources` input, attributed per block.
    Each read is keyed by what it saw when it was made (a table loaded at import keeps that content however a block,
    in this process or in another parallel worker, rewrites the file later; a folder listed at import keeps the names
    it listed), so the blocks built from it rebuild on the next run when the file changed. In a process that runs more
    than one build (a Blender session, a test process):
      * a read made in this run (or since the last one ended) by code outside the mod folder is keyed by what is
        there now, unless this run's build wrote the path since; reads the mod's code makes, and every read of the
        first run of a build process (Blender started on the build script), keep what they saw;
      * a read the mod's code made in an earlier run and not again in this one is keyed as unknown: what it gave may
        still be in use (a module-level table), and that cannot be told now, so the block rebuilds on the next run.
    `pinned` receives the keys that are keyed by what was seen (or unknown): they win over the block's own record of
    the same file (a block that rewrote its own import-time input still rebuilds once)."""
    raw = [p for p in list(_PROCESS) if not p.endswith(".py")]
    out = read_hashes(raw, outputs=outputs, mod_dir=mod_dir)
    outs = _as_outputs(outputs)
    wrote = path_forms(_WRITTEN)
    gen = _GENERATION[0]
    first = gen == 0 and bool(_script_files())     # the first run of a build process Blender started on a script
    mod = Path(mod_dir).resolve() if mod_dir is not None else None
    for e in raw:
        seen = _PROCESS_SEEN.get(e)
        is_dir = e.startswith("dir:")
        k = ("dir:" if is_dir else "") + Path(os.path.abspath(e[4:] if is_dir else e)).as_posix()
        if seen is None or k not in out:
            continue
        at, snap, caller = seen
        if at != gen:
            if not _by_mod(caller, mod):
                continue
            digest = _unknown()
        elif snap is None or not (first or touched_by(k, wrote) or _by_mod(caller, mod)):
            continue
        elif is_dir:
            digest = _names_digest(k[4:], snap, outs) if isinstance(snap, tuple) else snap
        elif isinstance(snap, tuple):
            continue
        else:
            digest = snap
        out[k] = digest
        if pinned is not None:
            pinned.add(k)
    return out


def path_forms(paths: Iterable[str]) -> set[str]:
    out = set()
    for p in paths:
        out.add(os.path.abspath(p))
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            out.add(os.path.realpath(p))
    return out


# A read's digest when the file was written by more than one parallel worker in the run: what a block saw cannot be told,
# so it never matches and the block rebuilds on the next run.
SEVERAL_WRITERS = "written-by-several-workers"


def touched_by(entry: str, written_forms: set[str]) -> bool:
    """Whether a recorded read is one of the `written_forms` paths (`path_forms`), or a folder listing holding one."""
    if entry.startswith("dir:"):
        d = entry[4:]
        return any(os.path.dirname(w) in (d, os.path.realpath(d)) for w in written_forms)
    return entry in written_forms or os.path.realpath(entry) in written_forms


@_quiet
def reads_changed(reads: Mapping[str, str] | None, outputs=(), ignore: Iterable[str] = ()) -> list[str]:
    """The recorded reads whose digest is different now (changed, gone, created, a folder's names changed). Left out:
    a read that is now an output (`outputs`: declared, or the build's own folders), and the `ignore` paths (a merge
    run's check skips what the build itself wrote: those blocks rebuild on the next run instead)."""
    outputs = _as_outputs(outputs)
    skip = path_forms(ignore)
    out = []
    for e, digest in sorted((reads or {}).items()):
        body = e.removeprefix("dir:")
        if outputs.covers(os.path.abspath(body)) or outputs.covers(Path(body).resolve()):
            continue
        if skip and touched_by(e, skip):
            continue
        if entry_digest(e, outputs) != digest:
            out.append(e)
    return out


@_quiet
def rewritten(fragments: Mapping[str, dict | None], written_paths: Iterable[str], outputs=()) -> dict[str, list[str]]:
    """{read: [blocks]}: the files (and folders) this run's build wrote whose content now differs from what a block's
    fragment recorded for it. Those blocks rebuild on the next run because of the build's own write; a file written
    every run (a shared log) makes that happen every run."""
    outputs = _as_outputs(outputs)
    forms = path_forms(written_paths)
    out: dict[str, list[str]] = {}
    if not forms:
        return out
    for name, frag in sorted(fragments.items()):
        for e, digest in sorted(((frag or {}).get("reads") or {}).items()):
            if touched_by(e, forms) and entry_digest(e, outputs) != digest:
                out.setdefault(e, []).append(name)
    return out


def normalise(entry: dict) -> dict:
    """The entry as JSON stores it (tuples become lists): a fresh build and a merge then write the same bytes.
    Raises TypeError / ValueError for an entry JSON cannot hold."""
    return json.loads(json.dumps(entry, sort_keys=True, allow_nan=True))


# ------------------------------------------------------------------------------------------------ the store
def _safe(name: str) -> str:
    return BS.safe_name(name)


class Store:
    """The fragments of one mod's report, one JSON file per block."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @classmethod
    def for_report(cls, report: Path, mod_name: str, environ: Mapping[str, str] | None = None) -> Store:
        return cls(BS.mod_state_dir(report, mod_name, environ))

    def path(self, block: str) -> Path:
        return self.root / f"{_safe(block)}.json"

    @_quiet
    def load(self, block: str) -> dict | None:
        try:
            data = json.loads(self.path(block).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("block") == block else None

    def save(self, block: str, fragment: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self.path(block)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(fragment, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, dest)

    @_quiet
    def load_written(self) -> set[str]:
        """The paths a parallel worker wrote (`WRITTEN_FILE` in its fragment folder), for the merge run."""
        try:
            data = json.loads((self.root / WRITTEN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {p for p in data.get("written", ()) if isinstance(p, str)} if isinstance(data, dict) else set()

    def save_written(self, paths: Iterable[str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self.root / WRITTEN_FILE
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"written": sorted(set(paths))}, f, indent=1)
            f.write("\n")
        os.replace(tmp, dest)

    @_quiet
    def blocks(self) -> list[str]:
        """The block names of every fragment here."""
        out = []
        for p in sorted(self.root.glob("*.json")) if self.root.is_dir() else ():
            try:
                name = json.loads(p.read_text(encoding="utf-8")).get("block")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(name, str) and self.path(name) == p:
                out.append(name)
        return out


def fragment(block: str, inputs: Mapping[str, str], entry: dict, *, seconds: float, reads: Mapping[str, str] = (),
             export_key: str | None = None, export_files: str | None = None) -> dict:
    """A block's fragment. `export_key` is the key the block's models were exported under (None: not exported) and
    `export_files` the hash of its Models/<block>/ folder right after that export."""
    return {"version": FRAGMENT_VERSION, "block": block, "key": key(inputs), "inputs": dict(inputs), "entry": entry,
            "reads": dict(reads or {}), "export_key": export_key, "export_files": export_files,
            "seconds": round(float(seconds), 3)}


@_quiet
def export_fresh(fragment: dict | None, inputs: Mapping[str, str], models_dir: Path) -> bool:
    """The block's models in `models_dir` are what an export under its current key wrote, untouched since."""
    if not fragment or fragment.get("export_key") != key(inputs):
        return False
    d = Path(models_dir)
    return (d / "export.json").is_file() and fragment.get("export_files") == models_digest(d)


# Files in Models/<block>/ that are not the export's own output (--check-placement writes this one afterwards).
NOT_EXPORTED = frozenset({"placement_exported.json"})


@_quiet
def models_digest(models_dir: Path) -> str:
    """Hash of a block's exported files (Models/<block>/, `NOT_EXPORTED` left out)."""
    d = Path(models_dir)
    files = [p for p in _expand([d]) if p.name not in NOT_EXPORTED]
    return files_digest(files, relative_to=d)


# ------------------------------------------------------------------------------------------------ the build lock
LOCK_ENV = "SE2MODKIT_BUILD_LOCK"


class LockHeld(Exception):
    """Another build of the same mod is running; the message is one sentence."""


def lock_path(report: Path, environ: Mapping[str, str] | None = None) -> Path:
    """The lock file of one mod's builds (by its report path), in the build state folder, never in the mod."""
    return BS.base(environ) / "locks" / f"{BS.report_tag(report)}.lock"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return True   # cannot tell cheaply: a lock is taken over only when removed
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def build_lock(report: Path, what: str, environ: Mapping[str, str] | None = None):
    """Hold the mod's build lock for the block. A lock left by a process that is gone is taken over. A child of the
    holder (`SE2MODKIT_BUILD_LOCK` = the lock path) runs under its parent's lock."""
    environ = os.environ if environ is None else environ
    path = lock_path(report, environ)
    if environ.get(LOCK_ENV) == str(path) and path.is_file():
        yield path
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                with paused():
                    pid = int(path.read_text(encoding="utf-8").split()[0])
            except (OSError, ValueError, IndexError):
                pid = 0
            if _alive(pid):
                raise LockHeld(f"Another build of {what} is running (process {pid}); wait for it to finish, or "
                               f"delete {path} if it is gone.") from None
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\n")
        break
    else:
        raise LockHeld(f"Cannot take the build lock of {what} ({path}); delete it if no build is running.")
    try:
        yield path
    finally:
        try:
            with paused():
                mine = path.read_text(encoding="utf-8").split()[0] == str(os.getpid())
            if mine:
                path.unlink()
        except (OSError, IndexError):
            pass
